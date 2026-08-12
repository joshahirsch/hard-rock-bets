"""Market-Efficiency Gate GME0-GME6.

Faithful port of ``claude/market-efficiency-candidate-spec.md`` (drafted and
deployed 2026-07-28, GME1 threshold updated 2026-08-11).

This is a *sibling* gate, not a loosened Selection Gate. It measures a
different claim: not "we know something the market doesn't", but "Hard Rock
Bet specifically is pricing this worse for itself than the broader market
believes is justified" (§1).

Scope (§2): MLB moneyline (h2h) only. Do not silently expand it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence

from src.math.novig import implied_probability, no_vig_probability

# ---------------------------------------------------------------------------
# Named constants -- market-efficiency-candidate-spec.md §5.
# §8 item 2: reused constants, not calibrated to this pathway specifically.
# ---------------------------------------------------------------------------

#: GME0 -- a Tier 1/2 fact published within this many hours that plausibly
#: explains the divergence bars the candidate from this pathway for the day.
GME0_NEWS_WINDOW_HOURS = 3

#: GME1 -- market-efficiency edge floor, pp. Reuses G1's exact constant;
#: lowered from +3.00 pp to +1.50 pp on 2026-08-11 as a direct corollary of
#: G1's own change, to preserve the stated reuse relationship.
GME1_EDGE_FLOOR_PP = 1.50
GME1_EDGE_FLOOR_PP_PRE_20260811 = 3.00

#: GME2 -- consensus tightness ceiling, pp. Reuses G2's exact constant.
#: Explicitly UNCHANGED on 2026-08-11.
GME2_CONSENSUS_SPREAD_CEILING_PP = 6.00

#: GME3 -- minimum contributing books (5 total prices including Hard Rock's).
#: Explicitly UNCHANGED on 2026-08-11; the spec flags this as likely the more
#: binding practical constraint.
GME3_MIN_CONTRIBUTING_BOOKS = 4

#: GME5 -- distinct same-day sightings required before GATE-CLEARED status.
GME5_REQUIRED_SIGHTINGS = 2

#: §3 -- the book under evaluation is excluded from its own consensus.
HARD_ROCK_BOOK_KEYS = ("hardrock", "hardrockbet", "hardrockbet_oh")

GME_RULE_ORDER = ("GME0", "GME1", "GME2", "GME3", "GME4", "GME5", "GME6")


class MEOutcome(str, Enum):
    CLEARED = "CLEARED"
    NOT_CLEARED = "NOT CLEARED"
    #: §5 -- a GME5 first-sighting has not FAILED anything, it just hasn't
    #: finished the check. Reported as a Watch, not as NOT CLEARED.
    WATCH = "Market-Efficiency Watch — pending second-firing reconfirmation"


@dataclass
class BookQuote:
    """One contributing book's two-sided quote for the side under evaluation."""

    book_key: str
    side_odds: int
    opposing_odds: int

    def no_vig(self) -> float:
        """That book's own no-vig probability via market-math-spec.md §2."""
        return no_vig_probability(self.side_odds, self.opposing_odds)


@dataclass
class MERuleResult:
    rule: str
    name: str
    passed: bool
    detail: str


@dataclass
class MEVerdict:
    cleared: bool
    outcome: MEOutcome
    deciding_rule: Optional[str]
    all_rule_results: List[MERuleResult]
    other_failing_rules: List[str]
    consensus_p_pct: Optional[float]
    consensus_spread_pp: Optional[float]
    me_edge_pp: Optional[float]
    leave_one_out_edge_pp: Optional[float]
    dropped_book: Optional[str]
    reasoning: str
    gate_outcome_cell: str
    final_decision_type: str

    def as_dict(self) -> dict:
        return {
            "cleared": self.cleared,
            "outcome": self.outcome.value,
            "deciding_rule": self.deciding_rule,
            "all_rule_results": [
                {"rule": r.rule, "name": r.name, "passed": r.passed, "detail": r.detail}
                for r in self.all_rule_results
            ],
            "consensus_p_pct": self.consensus_p_pct,
            "consensus_spread_pp": self.consensus_spread_pp,
            "me_edge_pp": self.me_edge_pp,
            "leave_one_out_edge_pp": self.leave_one_out_edge_pp,
            "dropped_book": self.dropped_book,
            "reasoning": self.reasoning,
            "gate_outcome_cell": self.gate_outcome_cell,
            "final_decision_type": self.final_decision_type,
        }


# ---------------------------------------------------------------------------
# §3 -- definitions
# ---------------------------------------------------------------------------


def consensus_probability(no_vig_probs: Sequence[float]) -> float:
    """Median (NOT mean) of the contributing books' no-vig probabilities (§3).

    Median is chosen deliberately "for robustness against a single stale or
    erroneous quote".
    """
    return statistics.median(no_vig_probs)


def consensus_spread_pp(no_vig_probs: Sequence[float]) -> float:
    """``max - min`` across contributing books, in pp (§3)."""
    return (max(no_vig_probs) - min(no_vig_probs)) * 100.0


def market_efficiency_edge_pp(consensus_p: float, hard_rock_odds: int) -> float:
    """``consensus_p - implied(hardrock_price)``, in pp (§3).

    POSITIVE means Hard Rock Bet is offering a more favorable (lower
    implied-probability) price on this side than the broader market believes is
    fair. Only the favorable direction is measured here -- a worse-than-field
    price at your own book is a caution, not an edge.
    """
    return (consensus_p - implied_probability(hard_rock_odds)) * 100.0


def leave_one_out(no_vig_probs: Sequence[float], book_keys: Sequence[str]):
    """Drop the single book furthest from the group median and recompute (§3/GME4).

    Returns ``(dropped_book_key, recomputed_consensus_p)``.
    """
    med = statistics.median(no_vig_probs)
    idx = max(range(len(no_vig_probs)), key=lambda i: abs(no_vig_probs[i] - med))
    remaining = [p for i, p in enumerate(no_vig_probs) if i != idx]
    return book_keys[idx], statistics.median(remaining)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def evaluate_market_efficiency_gate(
    *,
    hard_rock_odds: Optional[int],
    contributing_books: Sequence[BookQuote],
    news_cause_fact_within_window: bool,
    required_checklist_all_confirmed: bool = True,
    prior_sightings_today: int = 0,
    inconsistencies: Optional[Sequence[str]] = None,
    edge_floor_pp: float = GME1_EDGE_FLOOR_PP,
    consensus_spread_ceiling_pp: float = GME2_CONSENSUS_SPREAD_CEILING_PP,
    min_contributing_books: int = GME3_MIN_CONTRIBUTING_BOOKS,
) -> MEVerdict:
    """Evaluate GME0-GME6 and report every rule's result (§5).

    ``news_cause_fact_within_window``: True if any Tier 1/2 fact published
    within the last ``GME0_NEWS_WINDOW_HOURS`` hours plausibly explains the
    divergence. §7: an unconfirmable REQUIRED checklist item is treated the
    same as a GME0 trigger, which ``required_checklist_all_confirmed=False``
    expresses.

    ``prior_sightings_today``: how many *earlier* firings today already
    observed this same game/side clearing the edge floor. GME5 needs
    ``GME5_REQUIRED_SIGHTINGS`` total sightings including this one.
    """
    results: List[MERuleResult] = []
    problems: List[str] = list(inconsistencies or [])

    books = [b for b in contributing_books if b.book_key.lower() not in HARD_ROCK_BOOK_KEYS]
    dropped_from_input = len(contributing_books) - len(books)
    if dropped_from_input:
        problems.append(
            f"{dropped_from_input} Hard Rock book key(s) were supplied as contributing "
            "books and were excluded (§3 definition)."
        )

    # -- GME0 ---------------------------------------------------------------
    gme0_ok = not news_cause_fact_within_window and required_checklist_all_confirmed
    detail = []
    if news_cause_fact_within_window:
        detail.append(
            f"A Tier 1/2 fact within {GME0_NEWS_WINDOW_HOURS}h plausibly explains the "
            "divergence — barred from Market-Efficiency classification for the rest of "
            "today; route to the informational pathway."
        )
    if not required_checklist_all_confirmed:
        detail.append(
            "A REQUIRED sport-checklist item could not be confirmed — treated as a GME0 "
            "trigger per §7."
        )
    results.append(MERuleResult(
        "GME0", "News-cause exclusion", gme0_ok,
        " ".join(detail) if detail else
        f"No Tier 1/2 fact within {GME0_NEWS_WINDOW_HOURS}h explains the divergence; "
        "REQUIRED checklist items confirmed.",
    ))

    # -- consensus math -----------------------------------------------------
    consensus_p = spread_pp = me_edge = loo_edge = None
    dropped_book = None
    keys = [b.book_key for b in books]
    probs: List[float] = []
    if books and hard_rock_odds is not None:
        try:
            probs = [b.no_vig() for b in books]
            consensus_p = consensus_probability(probs)
            spread_pp = consensus_spread_pp(probs)
            me_edge = market_efficiency_edge_pp(consensus_p, hard_rock_odds)
        except Exception as exc:  # malformed odds anywhere in the set
            problems.append(f"Consensus computation failed: {exc}")
            probs = []
    else:
        problems.append(
            "Missing inputs for the consensus computation "
            f"(hard_rock_odds={hard_rock_odds!r}, contributing_books={len(books)})."
        )

    # -- GME1 ---------------------------------------------------------------
    if me_edge is None:
        results.append(MERuleResult("GME1", "Edge floor", False,
                                    "me_edge_pp could not be computed."))
    else:
        results.append(MERuleResult(
            "GME1", "Edge floor", me_edge >= edge_floor_pp,
            f"me_edge_pp = {me_edge:+.2f} pp vs. the {edge_floor_pp:+.2f} pp floor.",
        ))

    # -- GME2 ---------------------------------------------------------------
    if spread_pp is None:
        results.append(MERuleResult("GME2", "Consensus tightness ceiling", False,
                                    "consensus_spread_pp could not be computed."))
    else:
        results.append(MERuleResult(
            "GME2", "Consensus tightness ceiling", spread_pp <= consensus_spread_ceiling_pp,
            f"consensus_spread_pp = {spread_pp:.2f} pp vs. the "
            f"{consensus_spread_ceiling_pp:.2f} pp ceiling.",
        ))

    # -- GME3 ---------------------------------------------------------------
    results.append(MERuleResult(
        "GME3", "Minimum book count", len(books) >= min_contributing_books,
        f"{len(books)} contributing book(s) "
        f"({', '.join(keys) or 'none'}) vs. the {min_contributing_books} minimum.",
    ))

    # -- GME4 ---------------------------------------------------------------
    if len(probs) >= 2 and hard_rock_odds is not None:
        dropped_book, loo_consensus = leave_one_out(probs, keys)
        loo_edge = market_efficiency_edge_pp(loo_consensus, hard_rock_odds)
        results.append(MERuleResult(
            "GME4", "Leave-one-out robustness", loo_edge >= edge_floor_pp,
            f"Dropping {dropped_book!r} (furthest from the median) gives consensus "
            f"{loo_consensus * 100:.2f}% and me_edge_pp {loo_edge:+.2f} pp vs. the "
            f"{edge_floor_pp:+.2f} pp floor.",
        ))
    else:
        results.append(MERuleResult("GME4", "Leave-one-out robustness", False,
                                    "Not enough contributing books to run the check."))

    # -- GME5 ---------------------------------------------------------------
    sightings = prior_sightings_today + (1 if (me_edge or 0) >= edge_floor_pp else 0)
    gme5_ok = sightings >= GME5_REQUIRED_SIGHTINGS
    results.append(MERuleResult(
        "GME5", "Persistence reconfirmation", gme5_ok,
        f"{sightings} same-day sighting(s) of this divergence vs. the "
        f"{GME5_REQUIRED_SIGHTINGS} required.",
    ))

    # -- GME6 ---------------------------------------------------------------
    gme6_ok = not problems
    results.append(MERuleResult(
        "GME6", "Fail-closed default", gme6_ok,
        "No missing/ambiguous/inconsistent inputs found among GME0-GME5."
        if gme6_ok else "Fail-closed: " + "; ".join(problems),
    ))

    failing = {r.rule for r in results if not r.passed}
    ordered_failing = [r for r in GME_RULE_ORDER if r in failing]
    cleared = not ordered_failing
    by_rule = {r.rule: r for r in results}

    # §5: a first-sighting is a Watch, not a failure -- but only when GME5 is
    # the *only* thing outstanding.
    if not cleared and ordered_failing == ["GME5"]:
        return MEVerdict(
            cleared=False,
            outcome=MEOutcome.WATCH,
            deciding_rule=None,
            all_rule_results=results,
            other_failing_rules=[],
            consensus_p_pct=None if consensus_p is None else consensus_p * 100.0,
            consensus_spread_pp=spread_pp,
            me_edge_pp=me_edge,
            leave_one_out_edge_pp=loo_edge,
            dropped_book=dropped_book,
            reasoning=(
                "First sighting of this divergence today; every other rule passes. "
                "Logged as Market-Efficiency Watch pending a second-firing "
                "reconfirmation — it has not failed anything, it just hasn't "
                "finished the check."
            ),
            gate_outcome_cell=f"{MEOutcome.WATCH.value} — {by_rule['GME5'].detail}",
            final_decision_type="Market-Efficiency Watch",
        )

    if cleared:
        return MEVerdict(
            cleared=True,
            outcome=MEOutcome.CLEARED,
            deciding_rule=None,
            all_rule_results=results,
            other_failing_rules=[],
            consensus_p_pct=consensus_p * 100.0,
            consensus_spread_pp=spread_pp,
            me_edge_pp=me_edge,
            leave_one_out_edge_pp=loo_edge,
            dropped_book=dropped_book,
            reasoning="All of GME0-GME6 passed, including the two-firing persistence check.",
            gate_outcome_cell=f"CLEARED — {by_rule['GME1'].detail}",
            final_decision_type="Market-Efficiency Candidate",
        )

    deciding = ordered_failing[0]
    return MEVerdict(
        cleared=False,
        outcome=MEOutcome.NOT_CLEARED,
        deciding_rule=deciding,
        all_rule_results=results,
        other_failing_rules=ordered_failing[1:],
        consensus_p_pct=None if consensus_p is None else consensus_p * 100.0,
        consensus_spread_pp=spread_pp,
        me_edge_pp=me_edge,
        leave_one_out_edge_pp=loo_edge,
        dropped_book=dropped_book,
        reasoning=(
            f"Deciding rule {deciding}: {by_rule[deciding].detail}"
            + (f" Other failing rules: {', '.join(ordered_failing[1:])}."
               if len(ordered_failing) > 1 else "")
        ),
        gate_outcome_cell=f"NOT CLEARED — {deciding}: {by_rule[deciding].detail}",
        final_decision_type="Pass",
    )
