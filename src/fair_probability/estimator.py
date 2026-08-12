"""Fair-probability estimation.

Faithful port of ``claude/fair-probability-spec.md`` (Phase 4, AUTHORITATIVE).
Market math is never re-derived here -- it is imported from ``src.math.novig``
(market-math-spec.md remains the source of truth for every price formula).

Section map:
  §1  baseline = no-vig market probability (mandatory starting point)
  §2  named adjustments, the priced-in gate, combination arithmetic, +-8 pp cap
  §3  uncertainty band width = base + freshness + conflict, clamped 3..10 pp
  §4  pseudo-precision rules (point rounded to nearest 2 pp, never bare)
  §5  skeptical pass (SURVIVES / DEFEATED)
  §6  edge and EV -- always governed by the conservative band end
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from src.math.novig import (
    edge_pp,
    expected_value_per_dollar,
    implied_probability,
    minimum_acceptable_odds,
    no_vig_probability,
)

# ---------------------------------------------------------------------------
# Named constants -- every one of these is quoted from fair-probability-spec.md.
# §8 limitation 1: these are reasoned defaults, NOT fitted to data.
# ---------------------------------------------------------------------------

#: §2a -- weight applied to an adjustment whose priced-in judgment is "Y".
WEIGHT_PRICED_IN_Y = 0.0
#: §2a -- weight applied when the priced-in judgment is "N".
WEIGHT_PRICED_IN_N = 1.0
#: §2a -- default weight when the priced-in judgment is "Uncertain".
WEIGHT_PRICED_IN_UNCERTAIN = 0.5

#: §2b -- cap on the sum of ABSOLUTE values of applied weighted magnitudes (pp).
ADJUSTMENT_CAP_PP = 8.0

#: §3 -- band base when Tier 1 carries the majority of applied weight.
BAND_BASE_TIER1_PP = 2.0
#: §3 -- band base when Tier 2 carries the majority of applied weight.
BAND_BASE_TIER2_PP = 4.0

#: §3 -- freshness penalties, by the LEAST fresh nonzero-weight material fact.
FRESHNESS_PENALTY_UNDER_6H_PP = 0.0
FRESHNESS_PENALTY_6_TO_24H_PP = 1.0
FRESHNESS_PENALTY_UNKNOWN_OR_STALE_PP = 3.0

#: §3 -- conflict penalties.
CONFLICT_PENALTY_AGREE_PP = 0.0
CONFLICT_PENALTY_DISAGREE_PP = 2.0
#: §3 -- additional penalty when one weak adjustment dominates the net sum.
FRAGILE_DOMINANCE_PENALTY_PP = 1.0
#: §3 -- "individually weak" means a RAW magnitude strictly under this (pp).
FRAGILE_RAW_MAGNITUDE_PP = 1.5

#: §3 -- band clamp.
BAND_MIN_PP = 3.0
BAND_MAX_PP = 10.0

#: §4 -- the point estimate is displayed rounded to the nearest N pp.
POINT_ROUNDING_PP = 2.0


class Tier(int, Enum):
    """research-contract.md §2 source tiers."""

    TIER1 = 1
    TIER2 = 2
    TIER3 = 3


class PricedIn(str, Enum):
    """research-contract.md §3 item 4 / fair-probability-spec.md §2a."""

    Y = "Y"
    N = "N"
    UNCERTAIN = "Uncertain"


class Freshness(str, Enum):
    """fair-probability-spec.md §3 freshness buckets."""

    UNDER_6H = "under_6h"
    SIX_TO_24H = "6_to_24h"
    UNKNOWN_OR_STALE = "unknown_or_stale"


class SkepticalVerdict(str, Enum):
    """fair-probability-spec.md §5."""

    SURVIVES = "SURVIVES"
    DEFEATED = "DEFEATED"


class Tier3EvidenceError(ValueError):
    """Tier-3 evidence can never support an adjustment at any weight.

    fair-probability-spec.md §2a, research-contract.md §2 -- "unchanged and
    absolute".
    """


@dataclass
class Adjustment:
    """A discrete, named factor moving the baseline (§2).

    Carries all three mandated fields plus the full evidence contract
    (research-contract.md §3): source, tier, publication time, priced-in call.

    ``magnitude_pp`` is signed: positive raises the probability for the side
    under evaluation, negative lowers it. It is the RAW magnitude, stated
    *before* any weighting or discount is applied (§2).
    """

    name: str
    magnitude_pp: float
    tier: Tier
    priced_in: PricedIn
    freshness: Freshness
    source: str = ""
    publication_time: str = ""
    priced_in_reasoning: str = ""
    #: §2a -- a non-default discount for an "Uncertain" fact. Must be argued in
    #: the write-up; 0.5 is the default and any deviation is not assumed.
    explicit_weight_override: Optional[float] = None
    #: §2b shared-cause rule -- adjustments sharing this label were merged.
    shared_cause_label: Optional[str] = None

    def weight(self) -> float:
        if self.tier is Tier.TIER3:
            raise Tier3EvidenceError(
                f"adjustment {self.name!r} is Tier 3; Tier-3 evidence can never "
                "support an adjustment at any weight (fair-probability-spec.md §2a)"
            )
        if self.priced_in is PricedIn.Y:
            return WEIGHT_PRICED_IN_Y
        if self.priced_in is PricedIn.N:
            return WEIGHT_PRICED_IN_N
        if self.explicit_weight_override is not None:
            return float(self.explicit_weight_override)
        return WEIGHT_PRICED_IN_UNCERTAIN

    def applied_pp(self) -> float:
        """Weighted, signed contribution before the §2b cap is applied."""
        return self.magnitude_pp * self.weight()


@dataclass
class BandBreakdown:
    base_pp: float
    freshness_penalty_pp: float
    conflict_penalty_pp: float
    fragile_dominance_penalty_pp: float
    raw_pp: float
    clamped_pp: float
    clamped: bool
    exceeds_max: bool
    notes: List[str] = field(default_factory=list)


@dataclass
class FairProbabilityEstimate:
    baseline: float
    applied_adjustments_pp: List[float]
    net_adjustment_pp: float
    scaling_factor: Optional[float]
    point_estimate: float
    display_point_pct: float
    band_pp: float
    band_low_pct: float
    band_high_pct: float
    band_breakdown: BandBreakdown
    skeptical_verdict: Optional[SkepticalVerdict]
    ships: bool
    notes: List[str] = field(default_factory=list)

    # -- §6 -----------------------------------------------------------------
    def edge_low_pp(self, odds: int) -> float:
        """Conservative-end edge (band low - implied). THE governing number."""
        return self.band_low_pct - implied_probability(odds) * 100.0

    def edge_point_pp(self, odds: int) -> float:
        """Point-estimate edge -- context only, never a basis for a decision."""
        return self.display_point_pct - implied_probability(odds) * 100.0

    def edge_high_pp(self, odds: int) -> float:
        """Favorable-end edge -- context only, never a basis for a decision."""
        return self.band_high_pct - implied_probability(odds) * 100.0

    def ev_conservative(self, odds: int) -> float:
        """EV per $1 using the conservative-end probability (§6, binding)."""
        return expected_value_per_dollar(self.band_low_pct / 100.0, odds)

    def minimum_acceptable_odds(self, required_edge_pp: float) -> Optional[int]:
        """market-math-spec.md §2's formula fed the conservative-end p_c (§6)."""
        return minimum_acceptable_odds(self.band_low_pct / 100.0, required_edge_pp)


# ---------------------------------------------------------------------------
# §1 -- baseline
# ---------------------------------------------------------------------------


def baseline_from_two_sided(odds_side: int, odds_opposing: int) -> float:
    """The mandatory starting point (§1): the no-vig market probability.

    "An estimate that does not start here is out of contract and must not be
    presented."  If either price is missing/stale or the pair isn't within the
    10-minute simultaneity window, there is no valid baseline -- callers must
    run ``src.math.novig.check_two_sided_pair`` first.
    """
    return no_vig_probability(odds_side, odds_opposing)


# ---------------------------------------------------------------------------
# §2b -- combination arithmetic + cap
# ---------------------------------------------------------------------------


def combine_adjustments(adjustments: List[Adjustment]):
    """Apply the priced-in gate and the +-8 pp cap (§2b).

    Returns ``(applied_pp_list, net_pp, scaling_factor_or_None, notes)``.

    Scaling, when it happens, is proportional across *every* applied
    adjustment (multiply each by ``8 / raw_sum``) -- never by dropping the
    largest or smallest, "which would silently change what the estimate claims
    to be based on".
    """
    notes: List[str] = []
    applied = [a.applied_pp() for a in adjustments]
    raw_abs_sum = sum(abs(x) for x in applied)
    scaling: Optional[float] = None
    if raw_abs_sum > ADJUSTMENT_CAP_PP:
        scaling = ADJUSTMENT_CAP_PP / raw_abs_sum
        applied = [x * scaling for x in applied]
        notes.append(
            f"Adjustment cap applied: raw applied |sum| was {raw_abs_sum:.2f} pp "
            f"(> {ADJUSTMENT_CAP_PP} pp); every applied adjustment scaled by "
            f"{scaling:.4f}."
        )
    for adj in adjustments:
        if adj.priced_in is PricedIn.Y:
            notes.append(
                f"{adj.name}: priced-in Y -> weight 0, excluded from the arithmetic "
                "(written up for completeness only)."
            )
    return applied, sum(applied), scaling, notes


# ---------------------------------------------------------------------------
# §3 -- band width
# ---------------------------------------------------------------------------


def band_width(adjustments: List[Adjustment], applied_pp: List[float]) -> BandBreakdown:
    """``band_pp = base + freshness_penalty + conflict_penalty``, clamped 3..10.

    Only *nonzero-weight* adjustments contribute to any of the three terms.

    Ambiguity resolved here (not stated in the spec): when Tier-1 and Tier-2
    applied weight are exactly equal, neither carries "the majority", so the
    conservative Tier-2 base (4 pp) is used. Likewise, when there are NO
    nonzero-weight adjustments at all (the estimate is the bare baseline), no
    Tier-1 majority exists and the Tier-2 base is used.
    """
    notes: List[str] = []
    live = [
        (adj, applied)
        for adj, applied in zip(adjustments, applied_pp)
        if adj.weight() != 0.0
    ]

    # -- base ---------------------------------------------------------------
    tier1_weight = sum(a.weight() for a, _ in live if a.tier is Tier.TIER1)
    tier2_weight = sum(a.weight() for a, _ in live if a.tier is Tier.TIER2)
    if tier1_weight > tier2_weight:
        base = BAND_BASE_TIER1_PP
        notes.append(
            f"Base {base:.0f} pp: Tier 1 carries the majority of applied weight "
            f"({tier1_weight:.2f} vs {tier2_weight:.2f})."
        )
    else:
        base = BAND_BASE_TIER2_PP
        if not live:
            notes.append(
                f"Base {base:.0f} pp: no nonzero-weight adjustments, so no Tier-1 "
                "majority exists (conservative default)."
            )
        else:
            notes.append(
                f"Base {base:.0f} pp: Tier 1 does not carry the majority of applied "
                f"weight ({tier1_weight:.2f} vs {tier2_weight:.2f})."
            )

    # -- freshness ----------------------------------------------------------
    freshness = FRESHNESS_PENALTY_UNDER_6H_PP
    if any(a.freshness is Freshness.UNKNOWN_OR_STALE for a, _ in live):
        freshness = FRESHNESS_PENALTY_UNKNOWN_OR_STALE_PP
    elif any(a.freshness is Freshness.SIX_TO_24H for a, _ in live):
        freshness = FRESHNESS_PENALTY_6_TO_24H_PP
    notes.append(f"Freshness penalty {freshness:.0f} pp (least-fresh applied fact).")

    # -- conflict -----------------------------------------------------------
    directions = {1 if a.magnitude_pp > 0 else -1 for a, _ in live if a.magnitude_pp != 0}
    conflict = CONFLICT_PENALTY_DISAGREE_PP if len(directions) > 1 else CONFLICT_PENALTY_AGREE_PP
    notes.append(
        f"Conflict penalty {conflict:.0f} pp "
        f"({'adjustments conflict in direction' if conflict else 'all applied adjustments agree'})."
    )

    # -- fragile dominance --------------------------------------------------
    fragile = 0.0
    net = sum(x for _, x in live)
    if net != 0:
        for adj, applied in live:
            supplies_majority = abs(applied) > abs(net) / 2.0
            individually_weak = abs(adj.magnitude_pp) < FRAGILE_RAW_MAGNITUDE_PP
            if supplies_majority and individually_weak:
                fragile = FRAGILE_DOMINANCE_PENALTY_PP
                notes.append(
                    f"Fragile-dominance penalty {fragile:.0f} pp: {adj.name!r} supplies "
                    f"more than half the net signed sum while its raw magnitude "
                    f"({abs(adj.magnitude_pp):.2f} pp) is under {FRAGILE_RAW_MAGNITUDE_PP} pp."
                )
                break

    raw = base + freshness + conflict + fragile
    clamped = min(max(raw, BAND_MIN_PP), BAND_MAX_PP)
    return BandBreakdown(
        base_pp=base,
        freshness_penalty_pp=freshness,
        conflict_penalty_pp=conflict,
        fragile_dominance_penalty_pp=fragile,
        raw_pp=raw,
        clamped_pp=clamped,
        clamped=clamped != raw,
        exceeds_max=raw > BAND_MAX_PP,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# §4 -- pseudo-precision
# ---------------------------------------------------------------------------


def round_to_nearest(value_pct: float, step_pp: float = POINT_ROUNDING_PP) -> float:
    """Round a percentage to the nearest ``step_pp`` points (§4)."""
    return round(value_pct / step_pp) * step_pp


# ---------------------------------------------------------------------------
# Full estimate
# ---------------------------------------------------------------------------


def estimate(
    *,
    baseline: float,
    adjustments: List[Adjustment],
    skeptical_verdict: Optional[SkepticalVerdict] = None,
    band_from_rounded_point: bool = True,
) -> FairProbabilityEstimate:
    """Run the whole §1-§5 pipeline for one candidate.

    ``baseline`` is a probability in [0,1] (the §1 no-vig figure).

    ``band_from_rounded_point`` (default True) resolves a genuine ambiguity in
    the spec: §4 says the point estimate is displayed rounded to the nearest
    2 pp and never without its band, and §9 Demonstration A's own arithmetic
    derives the band endpoints (51% / 61%) and the governing edge (-7.33 pp)
    from the *rounded* 56% point rather than the raw 56.16%. Setting this False
    bands the raw point instead.

    An estimate whose skeptical pass is DEFEATED **does not ship** (§5): no
    band, no edge, no EV -- ``ships`` is False and the caller must log a pass.
    """
    notes: List[str] = []
    applied, net, scaling, combine_notes = combine_adjustments(adjustments)
    notes.extend(combine_notes)

    point = baseline * 100.0 + net
    display_point = round_to_nearest(point)
    breakdown = band_width(adjustments, applied)
    band = breakdown.clamped_pp

    centre = display_point if band_from_rounded_point else point
    band_low = centre - band
    band_high = centre + band

    if breakdown.exceeds_max:
        notes.append(
            f"Raw band {breakdown.raw_pp:.1f} pp exceeds the {BAND_MAX_PP:.0f} pp maximum: "
            "per §3 the estimate does not ship in this form -- take it to the skeptical "
            "pass anyway, where it will very likely (and correctly) be defeated."
        )
    if breakdown.clamped and not breakdown.exceeds_max:
        notes.append(
            f"Band clamped up from {breakdown.raw_pp:.1f} pp to the {BAND_MIN_PP:.0f} pp minimum."
        )

    ships = skeptical_verdict is SkepticalVerdict.SURVIVES
    if skeptical_verdict is SkepticalVerdict.DEFEATED:
        notes.append(
            "Skeptical pass DEFEATED -- estimate does not ship. Log as a pass with the "
            "specific defeating reason; no band, edge, or EV may be quoted (§5)."
        )
    elif skeptical_verdict is None:
        notes.append(
            "No skeptical-pass verdict supplied. Every estimate must survive a skeptical "
            "pass before it ships (§5); this estimate is provisional."
        )

    return FairProbabilityEstimate(
        baseline=baseline,
        applied_adjustments_pp=applied,
        net_adjustment_pp=net,
        scaling_factor=scaling,
        point_estimate=point,
        display_point_pct=display_point,
        band_pp=band,
        band_low_pct=band_low,
        band_high_pct=band_high,
        band_breakdown=breakdown,
        skeptical_verdict=skeptical_verdict,
        ships=ships,
        notes=notes,
    )


def format_estimate(est: FairProbabilityEstimate) -> str:
    """Render per §4: a point value may be shown, but never without its band."""
    return (
        f"{est.display_point_pct:.0f}% "
        f"(range {est.band_low_pct:.0f}%-{est.band_high_pct:.0f}%)"
    )


__all__ = [
    "Adjustment",
    "BandBreakdown",
    "FairProbabilityEstimate",
    "Freshness",
    "PricedIn",
    "SkepticalVerdict",
    "Tier",
    "Tier3EvidenceError",
    "baseline_from_two_sided",
    "band_width",
    "combine_adjustments",
    "edge_pp",
    "estimate",
    "format_estimate",
    "round_to_nearest",
]
