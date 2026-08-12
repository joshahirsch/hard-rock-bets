"""Tests for src/math/novig.py.

Fixtures are the worked examples from ``claude/market-math-spec.md`` itself
(§2's minimum-odds example, §8's full reference example) plus the numbers
reused downstream in fair-probability-spec.md §9 and revalidation-spec.md §8.
"""

import pytest

from src.math.novig import (
    CLVClass,
    LineHelpfulness,
    MAX_PAIR_GAP_MINUTES,
    NEUTRAL_CLV_BAND_PP,
    OddsError,
    SUSPICIOUS_OVERROUND,
    canonical_american_odds,
    check_two_sided_pair,
    classify_clv,
    decimal_odds,
    edge_pp,
    expected_value_per_dollar,
    implied_probability,
    is_suspicious_overround,
    line_move_helpfulness,
    line_movement_pts,
    minimum_acceptable_odds,
    no_vig_probability,
    overround,
    price_clv_pp,
    probability_to_fair_odds,
    validate_american_odds,
)

# ---------------------------------------------------------------------------
# §1 -- odds conventions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("odds", [100, -100, 130, -135, 112, 999, -10000])
def test_valid_odds_accepted(odds):
    assert validate_american_odds(odds) == odds


@pytest.mark.parametrize("odds", [0, 50, -50, 99, -99, 130.5, "130", None, True])
def test_malformed_odds_rejected_never_coerced(odds):
    with pytest.raises(OddsError):
        validate_american_odds(odds)


def test_even_money_canonicalizes_to_plus_100():
    # §1: "-100 and +100 are both valid and are the same price (even money);
    # the canonical fair-odds output for p = 0.5 is +100."
    assert canonical_american_odds(-100) == 100
    assert canonical_american_odds(100) == 100
    assert implied_probability(-100) == implied_probability(100) == 0.5


# ---------------------------------------------------------------------------
# §2 -- core formulas, using §8's reference example
# ---------------------------------------------------------------------------


def test_implied_probability_spec_section_8():
    # Guardians -135 -> 0.5745; Twins +112 -> 0.4717.
    assert implied_probability(-135) == pytest.approx(0.5745, abs=5e-5)
    assert implied_probability(112) == pytest.approx(0.4717, abs=5e-5)


def test_decimal_odds_and_identity():
    assert decimal_odds(-135) == pytest.approx(1 + 100 / 135)
    assert decimal_odds(112) == pytest.approx(2.12)
    for o in (-135, 112, 100, -100, 250, -400):
        assert implied_probability(o) == pytest.approx(1 / decimal_odds(o))


def test_overround_and_novig_spec_section_8():
    assert overround(-135, 112) == pytest.approx(1.0462, abs=5e-5)
    assert no_vig_probability(-135, 112) == pytest.approx(0.5491, abs=5e-5)


def test_novig_fair_odds_spec_section_8():
    # §8: no-vig Guardians 0.5491 -> fair ~= -121.8.
    assert probability_to_fair_odds(no_vig_probability(-135, 112)) == pytest.approx(
        -121.8, abs=0.2
    )


def test_novig_rejects_non_positive_vig():
    # §2/§5: an implied sum <= 1.0 is a data error, rejected not normalized.
    with pytest.raises(OddsError):
        no_vig_probability(500, 500)


def test_probability_to_fair_odds_at_even_money():
    assert probability_to_fair_odds(0.5) == 100.0


def test_ev_spec_section_8():
    # §8: with an ILLUSTRATIVE conservative fair p = 0.53, EV at -135 is
    # -7.74% per $1 (the gate refuses).
    assert expected_value_per_dollar(0.53, -135) == pytest.approx(-0.0774, abs=5e-5)


def test_edge_pp_helper():
    assert edge_pp(0.51, -140) == pytest.approx(51 - 58.3333, abs=1e-3)


# ---------------------------------------------------------------------------
# §2 -- minimum acceptable odds (four worked values from three spec documents)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "p_c, edge, expected, source",
    [
        (0.46, 3.00, 133, "market-math-spec.md §2"),
        (0.53, 3.00, 100, "market-math-spec.md §8"),
        (0.51, 3.00, 109, "fair-probability-spec.md §9 Demonstration A"),
        (0.51, 1.50, 103, "revalidation-spec.md §8 IC3 (E lowered 2026-08-11)"),
    ],
)
def test_minimum_acceptable_odds_worked_examples(p_c, edge, expected, source):
    assert minimum_acceptable_odds(p_c, edge) == expected, source


def test_minimum_acceptable_odds_rounds_to_the_safe_side():
    # §2: "rounding always in the safe direction, so the returned price always
    # satisfies the requirement; e.g. p_c = 0.46, E = 3 -> exact +132.56 ->
    # +133, since +132 would violate the edge."
    threshold = 0.46 - 0.03
    assert implied_probability(133) <= threshold
    assert implied_probability(132) > threshold
    # ... and revalidation-spec.md §8's own arithmetic for the E=1.50 case.
    t = 0.51 - 0.015
    assert implied_probability(103) == pytest.approx(0.4926, abs=5e-5)
    assert implied_probability(103) <= t
    assert implied_probability(102) == pytest.approx(0.4950, abs=5e-5)
    assert implied_probability(102) > t


def test_minimum_acceptable_odds_returns_none_when_no_price_qualifies():
    # §2: "If p_c - E/100 <= 0, no price qualifies."
    assert minimum_acceptable_odds(0.02, 3.00) is None
    assert minimum_acceptable_odds(0.03, 3.00) is None


def test_minimum_acceptable_odds_returns_negative_prices_when_warranted():
    result = minimum_acceptable_odds(0.75, 3.00)
    assert result is not None and result < 0
    assert implied_probability(result) <= 0.75 - 0.03


# ---------------------------------------------------------------------------
# §3 -- sign conventions
# ---------------------------------------------------------------------------


def test_price_clv_positive_means_beat_the_close_spec_section_8():
    # §8: executed -135, mock close -142 -> price CLV = +1.23 pp -> POSITIVE.
    assert price_clv_pp(-135, -142) == pytest.approx(1.23, abs=0.01)


def test_price_clv_sign_convention_is_closing_minus_executed():
    # §3 resolution note: beating the close means executed implied < closing
    # implied, so the implementation is closing - executed.
    assert price_clv_pp(150, 120) > 0   # line shortened after we bet -> we beat it
    assert price_clv_pp(120, 150) < 0


def test_line_movement_and_helpfulness():
    assert line_movement_pts(-1.5, -2.5) == pytest.approx(-1.0)
    # Spread: a bigger number in hand is always better.
    assert line_move_helpfulness("spread", -1.5, -2.5) is LineHelpfulness.HELPFUL
    assert line_move_helpfulness("spread", 3.5, 2.5) is LineHelpfulness.HELPFUL
    assert line_move_helpfulness("spread", -2.5, -1.5) is LineHelpfulness.HARMFUL
    assert line_move_helpfulness("spread", -1.5, -1.5) is LineHelpfulness.UNCHANGED
    # Totals.
    assert line_move_helpfulness("total", 8.5, 9.0, "over") is LineHelpfulness.HELPFUL
    assert line_move_helpfulness("total", 8.5, 8.0, "over") is LineHelpfulness.HARMFUL
    assert line_move_helpfulness("total", 8.5, 8.0, "under") is LineHelpfulness.HELPFUL
    assert line_move_helpfulness("total", 8.5, 9.0, "under") is LineHelpfulness.HARMFUL
    # Total with an unidentifiable side.
    assert line_move_helpfulness("total", 8.5, 9.0, None) is LineHelpfulness.UNKNOWN


# ---------------------------------------------------------------------------
# §4 -- composite CLV classifier, R1-R6
# ---------------------------------------------------------------------------


def test_r1_missing_or_unusable_closing_odds():
    for closing in (None, "", "NOT CAPTURED", 0, 50):
        result = classify_clv("moneyline", -135, closing)
        assert result.classification is CLVClass.NOT_COMPARABLE
        assert result.rule == "R1"


def test_r2_spread_with_missing_closing_line():
    result = classify_clv("spread", -110, -110, executed_line=-1.5, closing_line=None)
    assert result.classification is CLVClass.NOT_COMPARABLE
    assert result.rule == "R2"


def test_r3_moneyline_bands():
    # §8's worked case: -135 executed, -142 close -> +1.23 pp -> POSITIVE.
    assert classify_clv("moneyline", -135, -142).classification is CLVClass.POSITIVE
    assert classify_clv("moneyline", -142, -135).classification is CLVClass.NEGATIVE
    # Inside the 0.5 pp neutral band.
    neutral = classify_clv("moneyline", -135, -136)
    assert abs(neutral.price_clv_pp) < NEUTRAL_CLV_BAND_PP
    assert neutral.classification is CLVClass.NEUTRAL


def test_r4_same_line_spread_classifies_by_price():
    # Same line, price shortened after we bet (-110 -> -125): we beat the close.
    beat = classify_clv("spread", -110, -125, executed_line=-1.5, closing_line=-1.5)
    assert beat.rule == "R4"
    assert beat.classification is CLVClass.POSITIVE
    # Same line, price lengthened after we bet: the close beat us.
    lost = classify_clv("spread", -125, -110, executed_line=-1.5, closing_line=-1.5)
    assert lost.rule == "R4"
    assert lost.classification is CLVClass.NEGATIVE


def test_r5_changed_line_defaults_not_comparable():
    # Line helpful but price inside the neutral band -> NOT COMPARABLE.
    result = classify_clv("spread", -110, -111, executed_line=-1.5, closing_line=-2.5)
    assert result.rule == "R5"
    assert result.classification is CLVClass.NOT_COMPARABLE


def test_r5_changed_line_both_dimensions_decisive():
    positive = classify_clv("spread", 100, -120, executed_line=-1.5, closing_line=-2.5)
    assert positive.classification is CLVClass.POSITIVE
    negative = classify_clv("spread", -120, 100, executed_line=-2.5, closing_line=-1.5)
    assert negative.classification is CLVClass.NEGATIVE


def test_r6_missing_executed_price_raises_not_classifies():
    # §4: missing closing data is an answer; missing EXECUTED data is a
    # record-keeping failure and the module raises.
    with pytest.raises(OddsError):
        classify_clv("moneyline", None, -142)
    with pytest.raises(OddsError):
        classify_clv("moneyline", 0, -142)


# ---------------------------------------------------------------------------
# §5 -- two-sided market gate
# ---------------------------------------------------------------------------


def test_pair_gate_accepts_a_clean_pair():
    check = check_two_sided_pair(-135, 112, 0.0, 3.0, age_minutes=5.0)
    assert check.ok and not check.fatal_errors


def test_pair_gate_rejects_non_simultaneous_snapshot():
    check = check_two_sided_pair(-135, 112, 0.0, MAX_PAIR_GAP_MINUTES + 1)
    assert not check.ok
    assert any("apart" in e for e in check.fatal_errors)


def test_pair_gate_rejects_stale_price():
    check = check_two_sided_pair(-135, 112, 0.0, 1.0, age_minutes=61)
    assert not check.ok
    assert any("stale" in e for e in check.fatal_errors)


def test_pair_gate_flags_but_does_not_fail_heavy_vig():
    check = check_two_sided_pair(-300, -300, 0.0, 1.0, age_minutes=1.0)
    assert check.ok
    assert any("suspicious" in f for f in check.flags)
    assert is_suspicious_overround(overround(-300, -300))
    assert not is_suspicious_overround(SUSPICIOUS_OVERROUND)


def test_pair_gate_flags_missing_timestamps_without_failing():
    check = check_two_sided_pair(-135, 112)
    assert check.ok
    assert any("timestamp" in f for f in check.flags)
