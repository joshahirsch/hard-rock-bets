"""Selection Gate G1-G6.

Faithful port of ``claude/selection-gate-spec.md`` §2-§4 (Phase 5,
AUTHORITATIVE, as amended by the 2026-08-11 restructuring).

This is a research-quality classification only. It is never a bet
authorization and this module never executes anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from src.fair_probability.estimator import SkepticalVerdict, Tier

# ---------------------------------------------------------------------------
# Named constants -- selection-gate-spec.md §3.
# §4/§9 item 1: reasoned defaults, NOT calibrated to any track record.
# ---------------------------------------------------------------------------

#: G1 -- edge floor at the conservative (band-low) end, in pp.
#: LOWERED from +3.00 pp to +1.50 pp on 2026-08-11 at the project owner's explicit direction
#: (agent-restructure-2026-08-11.md) after 131 candidates produced zero
#: clearances. Honest tension, stated in the spec: this now sits BELOW the
#: fair-probability framework's own +-3 pp minimum band width.
G1_EDGE_FLOOR_PP = 1.50

#: G2 -- band ceiling in pp. Explicitly LEFT UNCHANGED on 2026-08-11.
#: 60% of fair-probability-spec.md §3's +-10 pp absolute maximum.
G2_BAND_CEILING_PP = 6.00

#: The pre-2026-08-11 G1 value, retained for provenance and for tests that
#: exercise the spec's own (historical) worked demonstration in §10a.
G1_EDGE_FLOOR_PP_PRE_20260811 = 3.00

GATE_RULE_ORDER = ("G1", "G2", "G3", "G4", "G5", "G6")


class ChecklistStatus(str, Enum):
    """research-contract.md §5 REQUIRED-item states, as documented in Step 3."""

    MET = "MET"
    NOT_MET = "NOT MET"
    NOT_YET_KNOWABLE = "NOT YET KNOWABLE"
    #: For a conditionally-REQUIRED item the write-up explicitly excluded
    #: (e.g. "weather not a factor in this write-up", dome game).
    NOT_APPLICABLE = "N/A"


class GateOutcome(str, Enum):
    CLEARED = "CLEARED"
    NOT_CLEARED = "NOT CLEARED"


@dataclass
class RuleResult:
    rule: str
    name: str
    passed: bool
    detail: str


@dataclass
class GateVerdict:
    """Structured, fully auditable gate result.

    §3: "Evaluate **all six** rules for every qualifying candidate ... and
    report every rule's individual result (not just the first failure)."
    """

    cleared: bool
    outcome: GateOutcome
    deciding_rule: Optional[str]
    all_rule_results: List[RuleResult]
    other_failing_rules: List[str]
    reasoning: str
    gate_outcome_cell: str
    reason_for_pass: Optional[str]
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
            "other_failing_rules": self.other_failing_rules,
            "reasoning": self.reasoning,
            "gate_outcome_cell": self.gate_outcome_cell,
            "reason_for_pass": self.reason_for_pass,
            "final_decision_type": self.final_decision_type,
        }


@dataclass
class GateInputs:
    """The six inputs §2 says the gate consumes -- and nothing else.

    "No other input may influence the gate outcome -- in particular, Tier 3
    'agreement', a third-party model's number, or any pick-site consensus
    remains fully excluded."
    """

    #: G1 input: edge at the conservative band end, in pp.
    edge_low_pp: Optional[float]
    #: G2 input: the +- band width Step 3.5 computed, in pp.
    band_width_pp: Optional[float]
    #: G3 input: {required item name: ChecklistStatus} for the sport's REQUIRED list.
    required_checklist: Dict[str, ChecklistStatus]
    #: G4 input: tiers of the NONZERO-weight Step 3.5 adjustments.
    nonzero_weight_adjustment_tiers: List[Tier]
    #: G5 input: the literal Step 3.5 skeptical-pass verdict.
    skeptical_verdict: Optional[SkepticalVerdict]
    #: G6 input: any explicitly-noted missing / ambiguous / inconsistent input.
    inconsistencies: List[str] = field(default_factory=list)


def evaluate_selection_gate(
    inputs: GateInputs,
    *,
    edge_floor_pp: float = G1_EDGE_FLOOR_PP,
    band_ceiling_pp: float = G2_BAND_CEILING_PP,
) -> GateVerdict:
    """Evaluate G1-G6 and return a structured verdict.

    Gate outcome is CLEARED only if all six rules pass. Otherwise NOT CLEARED,
    citing the **first failing rule in G1-G6 order** as the deciding rule and
    listing any other failing rules for transparency (§3).
    """
    results: List[RuleResult] = []
    g6_problems: List[str] = list(inputs.inconsistencies)

    # -- G1: edge floor -----------------------------------------------------
    if inputs.edge_low_pp is None:
        results.append(RuleResult("G1", "Edge floor", False,
                                  "Conservative-end edge is missing."))
        g6_problems.append("G1 input missing: conservative-end edge.")
    else:
        ok = inputs.edge_low_pp >= edge_floor_pp
        results.append(RuleResult(
            "G1", "Edge floor", ok,
            f"Conservative-end edge = {inputs.edge_low_pp:+.2f} pp vs. the "
            f"{edge_floor_pp:+.2f} pp floor.",
        ))

    # -- G2: band ceiling ---------------------------------------------------
    if inputs.band_width_pp is None:
        results.append(RuleResult("G2", "Band ceiling", False, "Band width is missing."))
        g6_problems.append("G2 input missing: band width.")
    else:
        ok = inputs.band_width_pp <= band_ceiling_pp
        results.append(RuleResult(
            "G2", "Band ceiling", ok,
            f"Band width = {inputs.band_width_pp:.2f} pp vs. the "
            f"{band_ceiling_pp:.2f} pp ceiling.",
        ))

    # -- G3: checklist reconfirmation ---------------------------------------
    if not inputs.required_checklist:
        results.append(RuleResult(
            "G3", "Checklist reconfirmation", False,
            "No REQUIRED sport-checklist items supplied to reconfirm.",
        ))
        g6_problems.append("G3 input missing: REQUIRED sport checklist.")
    else:
        unmet = [
            f"{k} = {v.value}"
            for k, v in inputs.required_checklist.items()
            if v not in (ChecklistStatus.MET, ChecklistStatus.NOT_APPLICABLE)
        ]
        ok = not unmet
        results.append(RuleResult(
            "G3", "Checklist reconfirmation", ok,
            "Every REQUIRED item explicitly MET."
            if ok else "REQUIRED item(s) not MET: " + "; ".join(unmet),
        ))
        if not ok:
            # §3: "a failure here is itself a signal something upstream is
            # inconsistent (see G6)."
            g6_problems.append(
                "G3 failed, which by construction should not happen for a RESEARCH "
                "CANDIDATE -- upstream Step 3 write-up is inconsistent with its own "
                "checklist compliance."
            )

    # -- G4: Tier anchor ----------------------------------------------------
    tiers = inputs.nonzero_weight_adjustment_tiers
    ok = any(t is Tier.TIER1 for t in tiers)
    results.append(RuleResult(
        "G4", "Tier anchor", ok,
        f"Nonzero-weight adjustment tiers: {[t.value for t in tiers] or 'none'}. "
        + ("At least one Tier 1 anchor present." if ok
           else "No Tier 1 anchor among nonzero-weight adjustments."),
    ))

    # -- G5: skeptical-pass reconfirmation ----------------------------------
    if inputs.skeptical_verdict is None:
        results.append(RuleResult("G5", "Skeptical-pass reconfirmation", False,
                                  "No skeptical-pass verdict recorded."))
        g6_problems.append("G5 input missing: skeptical-pass verdict.")
    else:
        ok = inputs.skeptical_verdict is SkepticalVerdict.SURVIVES
        results.append(RuleResult(
            "G5", "Skeptical-pass reconfirmation", ok,
            f"Step 3.5 verdict recorded as {inputs.skeptical_verdict.value!r}; "
            f"the rule requires the literal 'SURVIVES'.",
        ))

    # -- G6: fail-closed default -------------------------------------------
    ok = not g6_problems
    results.append(RuleResult(
        "G6", "Fail-closed default", ok,
        "No missing/ambiguous/inconsistent inputs found among G1-G5."
        if ok else "Fail-closed: " + "; ".join(g6_problems),
    ))

    failing = [r.rule for r in results if not r.passed]
    ordered_failing = [r for r in GATE_RULE_ORDER if r in failing]
    cleared = not ordered_failing
    deciding = ordered_failing[0] if ordered_failing else None
    by_rule = {r.rule: r for r in results}

    if cleared:
        outcome = GateOutcome.CLEARED
        reasoning = (
            "All six rules passed: conservative-end edge clears the floor, band is "
            "inside the ceiling, every REQUIRED checklist item is MET, a Tier-1 "
            "anchor is present, the skeptical pass SURVIVES, and no input is "
            "missing or ambiguous."
        )
        gate_cell = f"CLEARED — {by_rule['G1'].detail}"
        reason_for_pass = None
        final_type = "Research Candidate"
    else:
        outcome = GateOutcome.NOT_CLEARED
        others = [r for r in ordered_failing[1:]]
        reasoning = (
            f"Deciding rule {deciding}: {by_rule[deciding].detail}"
            + (f" Other failing rules: {', '.join(others)}." if others else "")
        )
        gate_cell = f"NOT CLEARED — {deciding}: {by_rule[deciding].detail}"
        reason_for_pass = (
            f"Selection gate {deciding} — {by_rule[deciding].detail}"
        )
        final_type = "Pass"

    return GateVerdict(
        cleared=cleared,
        outcome=outcome,
        deciding_rule=deciding,
        all_rule_results=results,
        other_failing_rules=ordered_failing[1:],
        reasoning=reasoning,
        gate_outcome_cell=gate_cell,
        reason_for_pass=reason_for_pass,
        final_decision_type=final_type,
    )


def not_evaluated_gate_cell(why: str) -> str:
    """§8: the Gate Outcome cell for a candidate that never reached STEP 3.6."""
    return f"N/A — not evaluated ({why})"
