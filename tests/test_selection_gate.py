"""Tests for src/gates/selection_gate.py.

Primary fixture: ``claude/selection-gate-spec.md`` §10a -- the full worked gate
decision, taking fair-probability-spec.md §9 Demonstration A through G1-G6 end
to end. Known inputs, known per-rule outputs, known verdict:

    G1 FAIL (conservative-end edge -7.33 pp), G2-G6 PASS,
    outcome NOT CLEARED, deciding rule G1.
"""

import pytest

from src.fair_probability.estimator import SkepticalVerdict, Tier
from src.gates.selection_gate import (
    G1_EDGE_FLOOR_PP,
    G1_EDGE_FLOOR_PP_PRE_20260811,
    G2_BAND_CEILING_PP,
    ChecklistStatus,
    GateInputs,
    GateOutcome,
    evaluate_selection_gate,
    not_evaluated_gate_cell,
)

# The MLB REQUIRED checklist §10a assumes were all explicitly MET.
MLB_CHECKLIST_ALL_MET = {
    "probable_pitchers_confirmed": ChecklistStatus.MET,
    "lineup_timing_honesty": ChecklistStatus.MET,
    "bullpen_recent_usage": ChecklistStatus.MET,
    "weather": ChecklistStatus.MET,
}


def demo_10a_inputs(**overrides) -> GateInputs:
    base = dict(
        edge_low_pp=-7.33,          # 51% - 58.33% (Guardians -140)
        band_width_pp=5.0,          # 2 base + 1 freshness + 2 conflict
        required_checklist=dict(MLB_CHECKLIST_ALL_MET),
        nonzero_weight_adjustment_tiers=[Tier.TIER1, Tier.TIER2],  # A1, A2
        skeptical_verdict=SkepticalVerdict.SURVIVES,
        inconsistencies=[],
    )
    base.update(overrides)
    return GateInputs(**base)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_thresholds_match_the_spec_including_the_20260811_change():
    assert G1_EDGE_FLOOR_PP == 1.50            # lowered 2026-08-11
    assert G1_EDGE_FLOOR_PP_PRE_20260811 == 3.00
    assert G2_BAND_CEILING_PP == 6.00          # explicitly left unchanged


# ---------------------------------------------------------------------------
# §10a worked demonstration
# ---------------------------------------------------------------------------


def test_spec_10a_worked_gate_decision():
    verdict = evaluate_selection_gate(demo_10a_inputs())
    assert verdict.cleared is False
    assert verdict.outcome is GateOutcome.NOT_CLEARED
    assert verdict.deciding_rule == "G1"
    assert verdict.other_failing_rules == []
    assert verdict.final_decision_type == "Pass"

    results = {r.rule: r.passed for r in verdict.all_rule_results}
    assert results == {
        "G1": False, "G2": True, "G3": True,
        "G4": True, "G5": True, "G6": True,
    }
    assert verdict.gate_outcome_cell.startswith("NOT CLEARED — G1")
    assert "Selection gate G1" in verdict.reason_for_pass


def test_spec_10a_still_fails_g1_under_the_historical_3pp_floor():
    # §10a: "Because the edge here is negative, this specific illustration's
    # outcome is unaffected by the 2026-08-11 threshold change."
    verdict = evaluate_selection_gate(
        demo_10a_inputs(), edge_floor_pp=G1_EDGE_FLOOR_PP_PRE_20260811
    )
    assert verdict.deciding_rule == "G1"


def test_all_six_rules_are_always_reported_not_just_the_first_failure():
    verdict = evaluate_selection_gate(
        demo_10a_inputs(edge_low_pp=-7.33, band_width_pp=9.0)
    )
    assert [r.rule for r in verdict.all_rule_results] == [
        "G1", "G2", "G3", "G4", "G5", "G6"
    ]
    assert verdict.deciding_rule == "G1"
    assert "G2" in verdict.other_failing_rules


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


def test_g1_boundary_is_inclusive_at_the_lowered_floor():
    assert evaluate_selection_gate(demo_10a_inputs(edge_low_pp=1.50)).cleared is True
    v = evaluate_selection_gate(demo_10a_inputs(edge_low_pp=1.49))
    assert v.cleared is False and v.deciding_rule == "G1"


def test_g1_clears_at_a_value_the_old_floor_would_have_rejected():
    # The whole point of the 2026-08-11 restructuring: +1.60 pp is now a real,
    # gate-evaluable clearance rather than an NM1 near-miss watch item.
    new_floor = evaluate_selection_gate(demo_10a_inputs(edge_low_pp=1.60))
    old_floor = evaluate_selection_gate(
        demo_10a_inputs(edge_low_pp=1.60), edge_floor_pp=G1_EDGE_FLOOR_PP_PRE_20260811
    )
    assert new_floor.cleared is True
    assert old_floor.cleared is False and old_floor.deciding_rule == "G1"


def test_g2_boundary_is_inclusive_at_6pp():
    assert evaluate_selection_gate(
        demo_10a_inputs(edge_low_pp=4.0, band_width_pp=6.0)
    ).cleared is True
    v = evaluate_selection_gate(demo_10a_inputs(edge_low_pp=4.0, band_width_pp=6.01))
    assert v.deciding_rule == "G2"


def test_g1_and_g2_are_independent():
    # A strong edge with a too-wide band fails only G2.
    v = evaluate_selection_gate(demo_10a_inputs(edge_low_pp=9.0, band_width_pp=8.0))
    assert v.deciding_rule == "G2"
    assert {r.rule for r in v.all_rule_results if not r.passed} == {"G2"}


def test_g3_fails_on_any_unmet_required_item_and_trips_g6():
    checklist = dict(MLB_CHECKLIST_ALL_MET)
    checklist["probable_pitchers_confirmed"] = ChecklistStatus.NOT_YET_KNOWABLE
    v = evaluate_selection_gate(
        demo_10a_inputs(edge_low_pp=4.0, required_checklist=checklist)
    )
    assert v.deciding_rule == "G3"
    # §3: "a failure here is itself a signal something upstream is inconsistent".
    assert "G6" in v.other_failing_rules


def test_g3_accepts_an_explicitly_not_applicable_conditional_item():
    checklist = dict(MLB_CHECKLIST_ALL_MET)
    checklist["weather"] = ChecklistStatus.NOT_APPLICABLE  # dome game
    v = evaluate_selection_gate(
        demo_10a_inputs(edge_low_pp=4.0, required_checklist=checklist)
    )
    assert v.cleared is True


def test_g4_requires_a_tier1_anchor_among_nonzero_weight_adjustments():
    v = evaluate_selection_gate(
        demo_10a_inputs(
            edge_low_pp=4.0, nonzero_weight_adjustment_tiers=[Tier.TIER2, Tier.TIER2]
        )
    )
    assert v.deciding_rule == "G4"
    # A strong edge and a narrow band do not rescue a Tier-2-only case.
    passed = {r.rule for r in v.all_rule_results if r.passed}
    assert {"G1", "G2", "G3", "G5"} <= passed


def test_g5_requires_the_literal_survives():
    v = evaluate_selection_gate(
        demo_10a_inputs(edge_low_pp=4.0, skeptical_verdict=SkepticalVerdict.DEFEATED)
    )
    assert v.deciding_rule == "G5"
    v_missing = evaluate_selection_gate(
        demo_10a_inputs(edge_low_pp=4.0, skeptical_verdict=None)
    )
    assert v_missing.deciding_rule == "G5"
    assert "G6" in v_missing.other_failing_rules


def test_g6_fails_closed_on_a_declared_inconsistency():
    v = evaluate_selection_gate(
        demo_10a_inputs(
            edge_low_pp=4.0,
            inconsistencies=["Step 3 wrote 'lineup confirmed' but no lineup exists at 8 AM"],
        )
    )
    assert v.deciding_rule == "G6"
    assert "Fail-closed" in [r for r in v.all_rule_results if r.rule == "G6"][0].detail


def test_g6_fails_closed_on_a_missing_numeric_input():
    v = evaluate_selection_gate(demo_10a_inputs(edge_low_pp=None))
    assert v.deciding_rule == "G1"          # first failure in G1-G6 order
    assert "G6" in v.other_failing_rules    # and the fail-closed default also trips


# ---------------------------------------------------------------------------
# Cleared path
# ---------------------------------------------------------------------------


def test_a_fully_clean_candidate_clears_and_becomes_a_research_candidate():
    v = evaluate_selection_gate(demo_10a_inputs(edge_low_pp=4.10, band_width_pp=3.0))
    assert v.cleared is True
    assert v.outcome is GateOutcome.CLEARED
    assert v.deciding_rule is None
    assert v.reason_for_pass is None
    assert v.final_decision_type == "Research Candidate"
    assert v.gate_outcome_cell.startswith("CLEARED — ")


def test_not_evaluated_cell_format():
    cell = not_evaluated_gate_cell("classified PASS at Step 3")
    assert cell == "N/A — not evaluated (classified PASS at Step 3)"
