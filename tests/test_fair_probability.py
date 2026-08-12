"""Tests for src/fair_probability/estimator.py.

The primary fixture is ``claude/fair-probability-spec.md`` §9 Demonstration A
(the Twins @ Guardians ML illustration), reproduced number-for-number:
baseline 54.91%, adjustments A1/A2/A3, net +1.25 pp, point 56.16% -> displayed
56%, band 2+1+2 = 5 pp, range 51%-61%, conservative edge vs -140 = -7.33 pp,
conservative EV = -12.6%, minimum acceptable odds at E=3 pp = +109.

Demonstration B (skeptical pass DEFEATED) is covered too.
"""

import pytest

from src.fair_probability.estimator import (
    ADJUSTMENT_CAP_PP,
    BAND_MAX_PP,
    BAND_MIN_PP,
    Adjustment,
    Freshness,
    PricedIn,
    SkepticalVerdict,
    Tier,
    Tier3EvidenceError,
    baseline_from_two_sided,
    estimate,
    format_estimate,
    round_to_nearest,
)

# ---------------------------------------------------------------------------
# §9 Demonstration A fixture
# ---------------------------------------------------------------------------

A1 = Adjustment(
    name="A1 late bullpen-availability note",
    magnitude_pp=+2.0,
    tier=Tier.TIER1,
    priced_in=PricedIn.N,
    freshness=Freshness.UNDER_6H,
    source="Guardians official transactions page (fictional)",
    publication_time="14:10 ET",
)
A2 = Adjustment(
    name="A2 starting-pitcher recent-form divergence",
    magnitude_pp=-1.5,
    tier=Tier.TIER2,
    priced_in=PricedIn.UNCERTAIN,
    freshness=Freshness.SIX_TO_24H,
    source="Named beat-writer article (fictional)",
    publication_time="~20h before estimate",
)
A3 = Adjustment(
    name="A3 7-day head-to-head bullpen ERA trend",
    magnitude_pp=+1.0,
    tier=Tier.TIER1,
    priced_in=PricedIn.Y,
    freshness=Freshness.UNDER_6H,
    source="Team-released aggregate stat (fictional)",
    publication_time="~5h before estimate",
)

DEMO_A_ADJUSTMENTS = [A1, A2, A3]


@pytest.fixture
def demo_a():
    return estimate(
        baseline=baseline_from_two_sided(-135, 112),
        adjustments=DEMO_A_ADJUSTMENTS,
        skeptical_verdict=SkepticalVerdict.SURVIVES,
    )


# ---------------------------------------------------------------------------
# §1 baseline
# ---------------------------------------------------------------------------


def test_baseline_is_the_novig_market_probability():
    assert baseline_from_two_sided(-135, 112) == pytest.approx(0.5491, abs=5e-5)


# ---------------------------------------------------------------------------
# §2a priced-in gate
# ---------------------------------------------------------------------------


def test_priced_in_weights():
    assert A1.weight() == 1.0          # N -> full magnitude
    assert A2.weight() == 0.5          # Uncertain -> default half weight
    assert A3.weight() == 0.0          # Y -> zero weight, excluded


def test_priced_in_y_contributes_nothing_but_is_still_written_up(demo_a):
    assert demo_a.applied_adjustments_pp[2] == 0.0
    assert any("priced-in Y" in n for n in demo_a.notes)


def test_uncertain_default_can_be_overridden_only_explicitly():
    default = Adjustment("x", 2.0, Tier.TIER2, PricedIn.UNCERTAIN, Freshness.UNDER_6H)
    assert default.weight() == 0.5
    argued = Adjustment(
        "x", 2.0, Tier.TIER2, PricedIn.UNCERTAIN, Freshness.UNDER_6H,
        explicit_weight_override=0.25,
    )
    assert argued.weight() == 0.25


def test_tier3_can_never_support_an_adjustment_at_any_weight():
    bad = Adjustment("t3", 2.0, Tier.TIER3, PricedIn.N, Freshness.UNDER_6H)
    with pytest.raises(Tier3EvidenceError):
        bad.weight()


# ---------------------------------------------------------------------------
# §2b combination arithmetic + cap
# ---------------------------------------------------------------------------


def test_demo_a_applied_magnitudes_and_net(demo_a):
    assert demo_a.applied_adjustments_pp[0] == pytest.approx(+2.0)
    assert demo_a.applied_adjustments_pp[1] == pytest.approx(-0.75)
    assert demo_a.applied_adjustments_pp[2] == pytest.approx(0.0)
    assert demo_a.net_adjustment_pp == pytest.approx(+1.25)
    assert demo_a.scaling_factor is None  # 2.75 pp is well under the 8 pp cap


def test_cap_scales_every_applied_adjustment_proportionally():
    big = [
        Adjustment("b1", +6.0, Tier.TIER1, PricedIn.N, Freshness.UNDER_6H),
        Adjustment("b2", +6.0, Tier.TIER1, PricedIn.N, Freshness.UNDER_6H),
    ]
    est = estimate(baseline=0.50, adjustments=big,
                   skeptical_verdict=SkepticalVerdict.SURVIVES)
    assert est.scaling_factor == pytest.approx(ADJUSTMENT_CAP_PP / 12.0)
    # Proportional, not "drop the largest": both survive, both shrink equally.
    assert est.applied_adjustments_pp == pytest.approx([4.0, 4.0])
    assert sum(abs(x) for x in est.applied_adjustments_pp) == pytest.approx(
        ADJUSTMENT_CAP_PP
    )
    assert any("cap applied" in n for n in est.notes)


# ---------------------------------------------------------------------------
# §3 band width
# ---------------------------------------------------------------------------


def test_demo_a_band_breakdown_is_2_plus_1_plus_2(demo_a):
    bd = demo_a.band_breakdown
    assert bd.base_pp == 2.0                  # majority of applied weight is Tier 1
    assert bd.freshness_penalty_pp == 1.0     # A2 sits in the 6-24h bucket
    assert bd.conflict_penalty_pp == 2.0      # A1 and A2 point opposite ways
    assert bd.fragile_dominance_penalty_pp == 0.0  # A1's raw 2.0 pp is not "weak"
    assert demo_a.band_pp == 5.0


def test_band_base_is_tier2_when_tier1_lacks_the_majority():
    est = estimate(
        baseline=0.50,
        adjustments=[
            Adjustment("t2", +2.0, Tier.TIER2, PricedIn.N, Freshness.UNDER_6H),
        ],
        skeptical_verdict=SkepticalVerdict.SURVIVES,
    )
    assert est.band_breakdown.base_pp == 4.0


def test_freshness_penalty_uses_the_least_fresh_applied_fact():
    est = estimate(
        baseline=0.50,
        adjustments=[
            Adjustment("fresh", +2.0, Tier.TIER1, PricedIn.N, Freshness.UNDER_6H),
            Adjustment("stale", +1.0, Tier.TIER1, PricedIn.N,
                       Freshness.UNKNOWN_OR_STALE),
        ],
        skeptical_verdict=SkepticalVerdict.SURVIVES,
    )
    assert est.band_breakdown.freshness_penalty_pp == 3.0


def test_priced_in_y_adjustments_do_not_affect_the_band():
    # A Y-weighted stale/conflicting fact must not widen the band, because it
    # carries zero weight and the band is built from nonzero-weight adjustments.
    est = estimate(
        baseline=0.50,
        adjustments=[
            Adjustment("live", +2.0, Tier.TIER1, PricedIn.N, Freshness.UNDER_6H),
            Adjustment("dead", -9.0, Tier.TIER2, PricedIn.Y,
                       Freshness.UNKNOWN_OR_STALE),
        ],
        skeptical_verdict=SkepticalVerdict.SURVIVES,
    )
    assert est.band_breakdown.base_pp == 2.0
    assert est.band_breakdown.freshness_penalty_pp == 0.0
    assert est.band_breakdown.conflict_penalty_pp == 0.0


def test_fragile_dominance_penalty_fires_for_a_weak_dominant_adjustment():
    est = estimate(
        baseline=0.50,
        adjustments=[
            Adjustment("weak_dominant", +1.0, Tier.TIER1, PricedIn.N,
                       Freshness.UNDER_6H),
        ],
        skeptical_verdict=SkepticalVerdict.SURVIVES,
    )
    # raw magnitude 1.0 pp < 1.5 pp and it supplies the whole net sum.
    assert est.band_breakdown.fragile_dominance_penalty_pp == 1.0


def test_band_is_clamped_to_the_3_to_10_pp_range():
    tight = estimate(
        baseline=0.50,
        adjustments=[
            Adjustment("a", +2.0, Tier.TIER1, PricedIn.N, Freshness.UNDER_6H),
        ],
        skeptical_verdict=SkepticalVerdict.SURVIVES,
    )
    assert tight.band_pp == BAND_MIN_PP  # raw 2 pp clamped up to the 3 pp floor

    wide = estimate(
        baseline=0.50,
        adjustments=[
            Adjustment("a", +2.0, Tier.TIER2, PricedIn.N, Freshness.UNKNOWN_OR_STALE),
            Adjustment("b", -2.0, Tier.TIER2, PricedIn.N, Freshness.UNKNOWN_OR_STALE),
        ],
        skeptical_verdict=SkepticalVerdict.SURVIVES,
    )
    assert wide.band_breakdown.raw_pp == 9.0
    assert wide.band_pp == 9.0
    assert wide.band_pp <= BAND_MAX_PP


def test_the_worst_possible_band_lands_exactly_on_the_10pp_maximum():
    """The §3 penalty components cannot sum past the clamp's own ceiling.

    max base 4 + max freshness 3 + max conflict 2 + fragile 1 = exactly 10 pp.
    So §3's "if the raw formula would exceed +-10 pp, the estimate does not ship
    in that form" branch is arithmetically UNREACHABLE with the published
    constants. Documented in docs/spec.md rather than silently ignored; this
    test pins the worst case so a future constant change makes it visible.
    """
    est = estimate(
        baseline=0.50,
        adjustments=[
            # +1.4 pp dominates the +0.9 pp net sum while being individually weak
            # (raw magnitude under 1.5 pp), which is what fires the fragile penalty.
            Adjustment("a", +1.4, Tier.TIER2, PricedIn.N, Freshness.UNKNOWN_OR_STALE),
            Adjustment("b", -0.5, Tier.TIER2, PricedIn.N, Freshness.UNKNOWN_OR_STALE),
        ],
        skeptical_verdict=SkepticalVerdict.SURVIVES,
    )
    bd = est.band_breakdown
    assert (bd.base_pp, bd.freshness_penalty_pp, bd.conflict_penalty_pp,
            bd.fragile_dominance_penalty_pp) == (4.0, 3.0, 2.0, 1.0)
    assert bd.raw_pp == BAND_MAX_PP == 10.0
    assert est.band_pp == BAND_MAX_PP
    assert bd.exceeds_max is False


# ---------------------------------------------------------------------------
# §4 pseudo-precision
# ---------------------------------------------------------------------------


def test_demo_a_point_estimate_and_display(demo_a):
    assert demo_a.point_estimate == pytest.approx(56.16, abs=0.01)
    assert demo_a.display_point_pct == 56.0
    assert format_estimate(demo_a) == "56% (range 51%-61%)"


def test_round_to_nearest_two_points():
    assert round_to_nearest(56.16) == 56.0
    assert round_to_nearest(57.4) == 58.0
    assert round_to_nearest(43.48) == 44.0


def test_demo_a_band_endpoints(demo_a):
    assert demo_a.band_low_pct == pytest.approx(51.0)
    assert demo_a.band_high_pct == pytest.approx(61.0)


# ---------------------------------------------------------------------------
# §6 edge and EV -- the conservative end governs
# ---------------------------------------------------------------------------


def test_demo_a_edge_table_against_minus_140(demo_a):
    # Spec §9 Demonstration A's own three-row table.
    assert demo_a.edge_low_pp(-140) == pytest.approx(-7.33, abs=0.01)
    assert demo_a.edge_point_pp(-140) == pytest.approx(-2.33, abs=0.01)
    assert demo_a.edge_high_pp(-140) == pytest.approx(+2.67, abs=0.01)


def test_demo_a_conservative_ev_governs(demo_a):
    assert demo_a.ev_conservative(-140) == pytest.approx(-0.126, abs=5e-4)


def test_demo_a_minimum_acceptable_odds_at_3pp(demo_a):
    # §9: "solving for the highest-implied price still <= 0.48 gives +109".
    assert demo_a.minimum_acceptable_odds(3.00) == 109


def test_demo_a_minimum_acceptable_odds_at_the_lowered_1_50pp_floor(demo_a):
    # revalidation-spec.md §8, recomputed 2026-08-11 at E = 1.50 pp -> +103.
    assert demo_a.minimum_acceptable_odds(1.50) == 103


# ---------------------------------------------------------------------------
# §5 skeptical pass
# ---------------------------------------------------------------------------


def test_demo_a_ships(demo_a):
    assert demo_a.ships is True


def test_demo_b_defeated_estimate_does_not_ship():
    # §9 Demonstration B: MLS spread, no-vig baseline 43.48%, a single +4.0 pp
    # Tier-1 adjustment -- defeated at the skeptical pass because the market had
    # already priced the *expectation* via 18-hour-old Tier 2 reporting.
    est = estimate(
        baseline=0.4348,
        adjustments=[
            Adjustment(
                "Confirmed starting XI includes return of the club's top scorer",
                +4.0, Tier.TIER1, PricedIn.N, Freshness.UNDER_6H,
            )
        ],
        skeptical_verdict=SkepticalVerdict.DEFEATED,
    )
    assert est.ships is False
    assert any("DEFEATED" in n for n in est.notes)


def test_missing_skeptical_verdict_is_provisional_and_does_not_ship():
    est = estimate(baseline=0.50, adjustments=[A1], skeptical_verdict=None)
    assert est.ships is False
    assert any("skeptical" in n.lower() for n in est.notes)
