"""Pregame revalidation -- invalidation conditions IC1-IC5.

Faithful port of ``claude/revalidation-spec.md`` §4-§6 (Phase 6,
AUTHORITATIVE; IC3's constant updated 2026-08-11 alongside G1).

These are generated for every candidate that reaches
``Final Decision Type = Research Candidate`` after STEP 3.6, using only
information already gathered earlier in the same run -- no new research step,
no new data source (§4).

They tell the human when NOT to treat a gate-CLEARED candidate as still
defensible research at placement time. They never recommend placing anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from src.gates.selection_gate import G1_EDGE_FLOOR_PP
from src.math.novig import minimum_acceptable_odds

# ---------------------------------------------------------------------------
# Named constants -- revalidation-spec.md §4.
# §7 item 2: IC4's thresholds are reasoned defaults, not calibrated.
# ---------------------------------------------------------------------------

#: IC3 -- required edge for the placement-time price floor, in pp. Explicitly
#: REUSES the gate's own G1 floor rather than introducing a second constant
#: (§4/§7 item 3). Lowered from 3.00 pp to 1.50 pp on 2026-08-11 with G1.
IC3_EDGE_PP = G1_EDGE_FLOOR_PP

#: IC4 -- precipitation probability threshold (crossing ABOVE this, where the
#: AM forecast was below it, voids the candidate).
IC4_PRECIP_THRESHOLD_PCT = 50.0
#: IC4 -- sustained wind speed shift, mph.
IC4_WIND_SHIFT_MPH = 10.0
#: IC4 -- temperature swing, degrees F.
IC4_TEMP_SWING_F = 15.0

#: §4 fail-closed rule: the literal token used in place of a missing fact.
UNRESOLVED = "UNRESOLVED"

IC_RULE_ORDER = ("IC1", "IC2", "IC3", "IC4", "IC5")


@dataclass
class InvalidationCondition:
    code: str
    text: str
    unresolved: bool = False


@dataclass
class InvalidationSet:
    conditions: List[InvalidationCondition]
    #: The IC3 numeric floor, or None when it could not be computed.
    minimum_acceptable_odds: Optional[int]
    has_unresolved: bool

    def decision_log_cell(self) -> str:
        """§4 presentation rule: one semicolon-separated cell, in IC1-IC5 order."""
        return "; ".join(c.text for c in self.conditions)

    def brief_block(self) -> str:
        """The labeled list that goes directly under the candidate's write-up."""
        return "\n".join(f"- {c.text}" for c in self.conditions)


def build_invalidation_conditions(
    *,
    starter_name: Optional[str],
    thesis_critical_players: Optional[List[str]] = None,
    conservative_band_low_pct: Optional[float] = None,
    weather_dependent: bool = False,
    am_precip_pct: Optional[float] = None,
    am_wind_mph: Optional[float] = None,
    am_wind_direction: Optional[str] = None,
    am_temp_f: Optional[float] = None,
    scheduled_date: Optional[str] = None,
    edge_pp: float = IC3_EDGE_PP,
) -> InvalidationSet:
    """Generate IC1-IC5 for one gate-CLEARED candidate (§4).

    Every condition is either a concrete, checkable rule or an explicit
    ``N/A — <why>`` -- never silently omitted. Where a required upstream fact
    is missing or ambiguous, the word ``UNRESOLVED`` is used in place of the
    missing fact rather than dropping the check (§4 fail-closed rule).
    """
    conds: List[InvalidationCondition] = []

    # -- IC1 ----------------------------------------------------------------
    if starter_name:
        conds.append(InvalidationCondition(
            "IC1", f"IC1 — VOID IF {starter_name} does not start."))
    else:
        conds.append(InvalidationCondition(
            "IC1",
            f"IC1 — VOID IF the {UNRESOLVED} probable/confirmed starter this "
            "candidate's thesis depends on does not start.",
            unresolved=True,
        ))

    # -- IC2 ----------------------------------------------------------------
    players = [p for p in (thesis_critical_players or []) if p]
    if players:
        conds.append(InvalidationCondition(
            "IC2",
            "IC2 — VOID IF any of these players whose availability was weighted in a "
            "Step 3.5 named adjustment is ruled out, downgraded, or scratched before "
            f"first pitch: {', '.join(players)}.",
        ))
    else:
        conds.append(InvalidationCondition(
            "IC2",
            f"IC2 — VOID IF the {UNRESOLVED} thesis-critical player(s) behind this "
            "candidate's Step 3.5 adjustments are ruled out, downgraded, or scratched "
            "before first pitch.",
            unresolved=True,
        ))

    # -- IC3 ----------------------------------------------------------------
    min_odds: Optional[int] = None
    if conservative_band_low_pct is None:
        conds.append(InvalidationCondition(
            "IC3",
            f"IC3 — VOID IF the placement price is worse than the minimum acceptable "
            f"odds, which are {UNRESOLVED} (conservative band-low probability missing; "
            f"E = {edge_pp:.2f} pp).",
            unresolved=True,
        ))
    else:
        min_odds = minimum_acceptable_odds(conservative_band_low_pct / 100.0, edge_pp)
        if min_odds is None:
            conds.append(InvalidationCondition(
                "IC3",
                f"IC3 — VOID unconditionally on price: at a conservative "
                f"p={conservative_band_low_pct:.2f}% and E={edge_pp:.2f} pp no American "
                "price qualifies.",
            ))
        else:
            conds.append(InvalidationCondition(
                "IC3",
                f"IC3 — VOID IF the placement price is worse than "
                f"{min_odds:+d} (minimum acceptable odds at conservative "
                f"p={conservative_band_low_pct:.2f}%, E={edge_pp:.2f} pp).",
            ))

    # -- IC4 ----------------------------------------------------------------
    if not weather_dependent:
        conds.append(InvalidationCondition(
            "IC4", "IC4 — N/A — not weather-dependent."))
    else:
        missing = any(
            v is None for v in (am_precip_pct, am_wind_mph, am_wind_direction, am_temp_f)
        )
        precip = f"{am_precip_pct:.0f}%" if am_precip_pct is not None else UNRESOLVED
        wind = (
            f"{am_wind_mph:.0f} mph {am_wind_direction}"
            if am_wind_mph is not None and am_wind_direction
            else UNRESOLVED
        )
        temp = f"{am_temp_f:.0f}F" if am_temp_f is not None else UNRESOLVED
        conds.append(InvalidationCondition(
            "IC4",
            f"IC4 — AM forecast: precip {precip}, wind {wind}, temp {temp}. VOID IF "
            f"precipitation probability crosses above {IC4_PRECIP_THRESHOLD_PCT:.0f}% "
            f"where the AM forecast was below it, the primary wind direction reverses, "
            f"sustained wind shifts by >={IC4_WIND_SHIFT_MPH:.0f} mph, or temperature "
            f"swings by >={IC4_TEMP_SWING_F:.0f}F from the AM figure.",
            unresolved=missing,
        ))

    # -- IC5 ----------------------------------------------------------------
    if scheduled_date:
        conds.append(InvalidationCondition(
            "IC5",
            f"IC5 — VOID IF this game is postponed, suspended pre-start, or moved off "
            f"{scheduled_date}.",
        ))
    else:
        conds.append(InvalidationCondition(
            "IC5",
            f"IC5 — VOID IF this game is postponed, suspended pre-start, or moved off "
            f"its {UNRESOLVED} scheduled date.",
            unresolved=True,
        ))

    return InvalidationSet(
        conditions=conds,
        minimum_acceptable_odds=min_odds,
        has_unresolved=any(c.unresolved for c in conds),
    )


def price_still_acceptable(placement_odds: int, minimum_odds: int) -> bool:
    """IC3 check at placement time: is the actual price at least the floor?

    "Worse than" for the ticket-holder means a higher implied probability.
    """
    from src.math.novig import implied_probability

    return implied_probability(placement_odds) <= implied_probability(minimum_odds) + 1e-12


def not_gate_cleared_cell(why: str) -> str:
    """§5: the Invalidation Conditions cell for a candidate classified Pass."""
    return f"N/A — not gate-CLEARED ({why})"


def revalidation_required_cell(unresolved_required_item: str) -> str:
    """§6: the lighter single-line form for a Revalidation Required candidate."""
    return (
        f"REVALIDATION REQUIRED — {unresolved_required_item} must resolve before this "
        "candidate can be reconsidered; no gate evaluation has occurred."
    )
