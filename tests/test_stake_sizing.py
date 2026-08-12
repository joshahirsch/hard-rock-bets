"""Tests for src/gates/stake_sizing.py.

Fixture: ``claude/stake-sizing-spec.md`` §3 -- the rule itself, exercised at
every boundary of ``min($10, round_down(10% x bankroll))`` floored at $1: above
the cap, exactly at the cap, below it, and down at the floor.

Bankroll values below are generic test inputs. No real bankroll figure appears
anywhere in this repository.
"""

import pytest

from src.gates.stake_sizing import (
    BANKROLL_CAP_FRACTION,
    FLAT_STAKE_USD,
    STAKE_ELIGIBLE_DECISION_TYPES,
    STAKE_FLOOR_USD,
    suggest_for_candidate,
    suggested_stake,
)


def test_constants_match_the_spec():
    assert FLAT_STAKE_USD == 10.0
    assert BANKROLL_CAP_FRACTION == 0.10
    assert STAKE_FLOOR_USD == 1.0
    assert STAKE_ELIGIBLE_DECISION_TYPES == (
        "Research Candidate",
        "Market-Efficiency Candidate",
    )


def test_cap_does_not_bind_above_one_hundred_so_the_flat_ten_applies():
    # §3: above a $100 bankroll the 10% cap does not bind and the suggestion is
    # the flat $10.
    assert suggested_stake(150.0) == 10.0


@pytest.mark.parametrize(
    "bankroll, expected",
    [
        (1000.0, 10.0),   # cap does not bind
        (150.0, 10.0),    # cap does not bind
        (100.0, 10.0),    # exactly at the boundary
        (99.0, 9.0),      # cap binds: floor(9.9) = 9
        (95.0, 9.0),      # round_down, not round-to-nearest
        (50.0, 5.0),
        (12.0, 1.0),      # floor(1.2) = 1
        (9.0, 1.0),       # floor(0.9) = 0 -> floored at $1
        (0.0, 1.0),       # floored at $1
    ],
)
def test_formula_min_ten_or_ten_percent_rounded_down_floored_at_one(bankroll, expected):
    assert suggested_stake(bankroll) == expected


def test_stake_is_flat_not_edge_scaled():
    # §3: "A candidate that cleared G1 at +1.6 pp gets the same suggested stake
    # as one that cleared at +9 pp."
    a = suggest_for_candidate("Research Candidate", 150.0)
    b = suggest_for_candidate("Research Candidate", 150.0)
    assert a.amount_usd == b.amount_usd == 10.0
    # There is deliberately no edge parameter in the signature at all.
    import inspect

    assert "edge" not in inspect.signature(suggest_for_candidate).parameters


def test_negative_bankroll_is_rejected():
    with pytest.raises(ValueError):
        suggested_stake(-1.0)


# ---------------------------------------------------------------------------
# §1 / §3 -- which Final Decision Types carry a stake
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "decision_type", ["Research Candidate", "Market-Efficiency Candidate"]
)
def test_eligible_decision_types_get_a_stake(decision_type):
    s = suggest_for_candidate(decision_type, 150.0)
    assert s.eligible is True
    assert s.amount_usd == 10.0
    assert s.text == "Suggested stake (informational, not an instruction): $10"


@pytest.mark.parametrize(
    "decision_type",
    ["Pass", "Revalidation Required", "Market-Efficiency Watch", "Bet"],
)
def test_ineligible_decision_types_get_no_stake(decision_type):
    # §3: if C1/C2/C3 already downgraded a candidate to Pass, there is nothing
    # to size. A Market-Efficiency WATCH is not a clearance either.
    s = suggest_for_candidate(decision_type, 150.0)
    assert s.eligible is False
    assert s.amount_usd is None
    assert "No suggested stake" in s.text


def test_presentation_format_is_the_spec_s_exact_labelled_line():
    s = suggest_for_candidate("Research Candidate", 150.0)
    assert s.text.startswith("Suggested stake (informational, not an instruction):")


def test_binding_constraint_is_reported():
    assert suggest_for_candidate("Research Candidate", 1000.0).binding_constraint == (
        "flat figure"
    )
    assert suggest_for_candidate("Research Candidate", 50.0).binding_constraint == (
        "bankroll cap"
    )
    assert suggest_for_candidate("Research Candidate", 5.0).binding_constraint == "floor"
