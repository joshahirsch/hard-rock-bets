"""Tests for src/gates/market_efficiency_gate.py.

Primary fixture: ``claude/market-efficiency-candidate-spec.md`` §9 -- the
worked demonstration built on the real 2026-07-26 Yankees @ Phillies Hard Rock
quote (+155 / -190) plus four fictional contributing books.

Spec's stated arithmetic: consensus_p = 41.1%, consensus_spread_pp = 0.9 pp,
Hard Rock's +155 implies 39.2%, me_edge_pp = +1.9 pp -- under the original
+3.00 pp GME1 floor, CLEARS the current +1.50 pp floor.

SPEC ARITHMETIC DISCREPANCY (documented, not silently "fixed" -- see
docs/spec.md "Judgment calls"): §9 says the leave-one-out check drops "Book E
(furthest from the median)". Measured against the stated median of 41.1%,
Book D (41.6%, distance 0.5 pp) is actually furthest, not Book E (40.7%,
distance 0.4 pp). This module implements GME4 as the RULE is written in §3/§5
("drop the single contributing book whose no-vig probability is furthest from
the group median"), which drops D and yields +1.78 pp. The rule's VERDICT is
unchanged either way -- both recomputations clear the +1.50 pp floor -- so the
demonstration's conclusion still holds; only its intermediate number differs.
"""

import pytest

from src.gates.market_efficiency_gate import (
    GME0_NEWS_WINDOW_HOURS,
    GME1_EDGE_FLOOR_PP,
    GME1_EDGE_FLOOR_PP_PRE_20260811,
    GME2_CONSENSUS_SPREAD_CEILING_PP,
    GME3_MIN_CONTRIBUTING_BOOKS,
    GME5_REQUIRED_SIGHTINGS,
    BookQuote,
    MEOutcome,
    consensus_probability,
    consensus_spread_pp,
    evaluate_market_efficiency_gate,
    leave_one_out,
    market_efficiency_edge_pp,
)
from src.math.novig import implied_probability

# §9's four fictional contributing books, stated as no-vig probabilities.
SPEC_9_NOVIG = {"book_b": 0.412, "book_c": 0.410, "book_d": 0.416, "book_e": 0.407}
HARD_ROCK_YANKEES_ODDS = 155  # real 2026-07-26 quote used as the anchor


def spec_9_books():
    """Reconstruct §9's books as two-sided quotes hitting the stated no-vig.

    The spec gives only each book's Yankees no-vig figure, so these prices are
    synthesized to reproduce exactly those no-vig values through the real
    proportional-devig formula rather than hard-coding the answer.
    """
    quotes = []
    for key, target in SPEC_9_NOVIG.items():
        # Choose a symmetric-vig pair whose proportional devig equals `target`.
        side = _american_from_probability(target * 1.045)
        opposing = _american_from_probability((1 - target) * 1.045)
        quotes.append(BookQuote(key, side, opposing))
    return quotes


def _american_from_probability(p: float) -> int:
    if p >= 0.5:
        return -int(round(100 * p / (1 - p)))
    return int(round(100 * (1 - p) / p))


def test_constants_match_the_spec_including_the_20260811_change():
    assert GME0_NEWS_WINDOW_HOURS == 3
    assert GME1_EDGE_FLOOR_PP == 1.50                  # lowered 2026-08-11 with G1
    assert GME1_EDGE_FLOOR_PP_PRE_20260811 == 3.00
    assert GME2_CONSENSUS_SPREAD_CEILING_PP == 6.00    # unchanged
    assert GME3_MIN_CONTRIBUTING_BOOKS == 4            # unchanged
    assert GME5_REQUIRED_SIGHTINGS == 2                # unchanged


# ---------------------------------------------------------------------------
# §3 definitions, against §9's stated arithmetic
# ---------------------------------------------------------------------------


def test_consensus_is_the_median_not_the_mean():
    probs = list(SPEC_9_NOVIG.values())
    assert consensus_probability(probs) == pytest.approx(0.411, abs=1e-9)
    # The mean would be 0.41125 -- median is chosen deliberately for robustness.
    assert consensus_probability(probs) != pytest.approx(sum(probs) / len(probs))


def test_consensus_spread_pp_spec_9():
    assert consensus_spread_pp(list(SPEC_9_NOVIG.values())) == pytest.approx(0.9, abs=1e-9)


def test_hard_rock_implied_and_me_edge_spec_9():
    assert implied_probability(HARD_ROCK_YANKEES_ODDS) == pytest.approx(0.392, abs=5e-4)
    edge = market_efficiency_edge_pp(0.411, HARD_ROCK_YANKEES_ODDS)
    assert edge == pytest.approx(1.9, abs=0.05)


def test_spec_9_edge_clears_the_new_floor_and_fails_the_old_one():
    edge = market_efficiency_edge_pp(0.411, HARD_ROCK_YANKEES_ODDS)
    assert edge >= GME1_EDGE_FLOOR_PP
    assert edge < GME1_EDGE_FLOOR_PP_PRE_20260811


def test_leave_one_out_drops_the_book_furthest_from_the_median():
    keys = list(SPEC_9_NOVIG)
    probs = list(SPEC_9_NOVIG.values())
    dropped, recomputed = leave_one_out(probs, keys)
    # Rule as written: |0.416 - 0.411| = 0.005 > |0.407 - 0.411| = 0.004, so
    # book_d is furthest -- NOT book_e as §9's prose asserts. See module docstring.
    assert dropped == "book_d"
    assert recomputed == pytest.approx(0.410, abs=1e-9)
    loo_edge = market_efficiency_edge_pp(recomputed, HARD_ROCK_YANKEES_ODDS)
    assert loo_edge == pytest.approx(1.78, abs=0.05)
    # The VERDICT is unchanged from the spec's stated one: still clears.
    assert loo_edge >= GME1_EDGE_FLOOR_PP


def test_spec_9_prose_alternative_also_clears_so_the_verdict_is_robust():
    # If Book E were dropped as §9's prose says, the consensus would be 41.2%
    # and the edge +2.0 pp -- also clearing. Either reading reaches CLEARED.
    alt = consensus_probability([0.412, 0.410, 0.416])
    assert alt == pytest.approx(0.412, abs=1e-9)
    assert market_efficiency_edge_pp(alt, HARD_ROCK_YANKEES_ODDS) == pytest.approx(
        2.0, abs=0.05
    )


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_spec_9_full_gate_clears_on_a_second_sighting():
    verdict = evaluate_market_efficiency_gate(
        hard_rock_odds=HARD_ROCK_YANKEES_ODDS,
        contributing_books=spec_9_books(),
        news_cause_fact_within_window=False,
        prior_sightings_today=1,
    )
    assert verdict.cleared is True
    assert verdict.outcome is MEOutcome.CLEARED
    assert verdict.final_decision_type == "Market-Efficiency Candidate"
    assert verdict.me_edge_pp == pytest.approx(1.9, abs=0.1)
    assert verdict.consensus_spread_pp == pytest.approx(0.9, abs=0.1)
    assert [r.rule for r in verdict.all_rule_results] == [
        "GME0", "GME1", "GME2", "GME3", "GME4", "GME5", "GME6"
    ]


def test_first_sighting_is_a_watch_not_a_failure():
    verdict = evaluate_market_efficiency_gate(
        hard_rock_odds=HARD_ROCK_YANKEES_ODDS,
        contributing_books=spec_9_books(),
        news_cause_fact_within_window=False,
        prior_sightings_today=0,
    )
    assert verdict.cleared is False
    assert verdict.outcome is MEOutcome.WATCH
    assert verdict.final_decision_type == "Market-Efficiency Watch"
    assert verdict.deciding_rule is None  # it hasn't FAILED anything


def test_gme0_news_cause_bars_the_pathway_entirely():
    verdict = evaluate_market_efficiency_gate(
        hard_rock_odds=HARD_ROCK_YANKEES_ODDS,
        contributing_books=spec_9_books(),
        news_cause_fact_within_window=True,
        prior_sightings_today=1,
    )
    assert verdict.deciding_rule == "GME0"
    assert verdict.final_decision_type == "Pass"


def test_unconfirmable_required_checklist_item_is_treated_as_a_gme0_trigger():
    # §7: "If a REQUIRED item can't be confirmed, treat that the same as a
    # GME0 trigger."
    verdict = evaluate_market_efficiency_gate(
        hard_rock_odds=HARD_ROCK_YANKEES_ODDS,
        contributing_books=spec_9_books(),
        news_cause_fact_within_window=False,
        required_checklist_all_confirmed=False,
        prior_sightings_today=1,
    )
    assert verdict.deciding_rule == "GME0"


def test_gme1_floor_is_the_deciding_rule_for_a_small_edge():
    verdict = evaluate_market_efficiency_gate(
        hard_rock_odds=125,  # implies 44.4%, far above the ~41% consensus
        contributing_books=spec_9_books(),
        news_cause_fact_within_window=False,
        prior_sightings_today=1,
    )
    assert verdict.deciding_rule == "GME1"
    assert verdict.me_edge_pp < GME1_EDGE_FLOOR_PP


def test_gme2_rejects_a_loose_consensus():
    loose = [
        BookQuote("b1", 100, -130),
        BookQuote("b2", 200, -230),
        BookQuote("b3", 150, -180),
        BookQuote("b4", 180, -210),
    ]
    verdict = evaluate_market_efficiency_gate(
        hard_rock_odds=400,
        contributing_books=loose,
        news_cause_fact_within_window=False,
        prior_sightings_today=1,
    )
    assert verdict.consensus_spread_pp > GME2_CONSENSUS_SPREAD_CEILING_PP
    assert verdict.deciding_rule == "GME2"


def test_gme3_requires_four_contributing_books():
    verdict = evaluate_market_efficiency_gate(
        hard_rock_odds=HARD_ROCK_YANKEES_ODDS,
        contributing_books=spec_9_books()[:3],
        news_cause_fact_within_window=False,
        prior_sightings_today=1,
    )
    assert verdict.deciding_rule == "GME3"


def test_hard_rock_is_excluded_from_its_own_consensus():
    books = spec_9_books() + [BookQuote("hardrock", HARD_ROCK_YANKEES_ODDS, -190)]
    verdict = evaluate_market_efficiency_gate(
        hard_rock_odds=HARD_ROCK_YANKEES_ODDS,
        contributing_books=books,
        news_cause_fact_within_window=False,
        prior_sightings_today=1,
    )
    gme3 = [r for r in verdict.all_rule_results if r.rule == "GME3"][0]
    assert "hardrock" not in gme3.detail
    # ... and supplying it trips the fail-closed default, rather than silently
    # polluting the consensus.
    assert verdict.deciding_rule == "GME6"


def test_gme6_fails_closed_on_missing_inputs():
    verdict = evaluate_market_efficiency_gate(
        hard_rock_odds=None,
        contributing_books=spec_9_books(),
        news_cause_fact_within_window=False,
        prior_sightings_today=1,
    )
    assert verdict.cleared is False
    assert "GME6" in {r.rule for r in verdict.all_rule_results if not r.passed}
