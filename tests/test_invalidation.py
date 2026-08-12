"""Tests for src/gates/invalidation.py.

Primary fixture: ``claude/revalidation-spec.md`` §8 -- the worked IC1-IC5
demonstration, whose IC3 figures were recomputed on 2026-08-11 when E dropped
from 3.00 pp to 1.50 pp:

    conservative band-low p = 51%
    E = 1.50 pp -> minimum acceptable odds +103   (was +109 at E = 3.00 pp)
"""

import pytest

from src.gates.invalidation import (
    IC3_EDGE_PP,
    IC4_PRECIP_THRESHOLD_PCT,
    IC4_TEMP_SWING_F,
    IC4_WIND_SHIFT_MPH,
    IC_RULE_ORDER,
    UNRESOLVED,
    build_invalidation_conditions,
    not_gate_cleared_cell,
    price_still_acceptable,
    revalidation_required_cell,
)
from src.gates.selection_gate import G1_EDGE_FLOOR_PP


def test_ic3_reuses_g1s_own_floor_rather_than_a_second_constant():
    # §4 / §7 item 3: "no new, independently-justified constant is introduced,
    # this document simply tracks G1's value".
    assert IC3_EDGE_PP == G1_EDGE_FLOOR_PP == 1.50


def test_ic4_thresholds_match_the_spec():
    assert IC4_PRECIP_THRESHOLD_PCT == 50.0
    assert IC4_WIND_SHIFT_MPH == 10.0
    assert IC4_TEMP_SWING_F == 15.0


# ---------------------------------------------------------------------------
# §8 worked demonstration
# ---------------------------------------------------------------------------


@pytest.fixture
def demo_8():
    return build_invalidation_conditions(
        starter_name="the Guardians' confirmed starting pitcher",
        thesis_critical_players=["the player behind adjustment A1"],
        conservative_band_low_pct=51.0,
        weather_dependent=True,
        am_precip_pct=15.0,
        am_wind_mph=6.0,
        am_wind_direction="out to center",
        am_temp_f=74.0,
        scheduled_date="2026-07-23",
    )


def test_all_five_conditions_are_generated_in_order(demo_8):
    assert [c.code for c in demo_8.conditions] == list(IC_RULE_ORDER)


def test_ic3_price_floor_is_plus_103_at_the_lowered_edge(demo_8):
    # §8: "the highest-implied price still <= 0.495 is +103".
    assert demo_8.minimum_acceptable_odds == 103
    ic3 = demo_8.conditions[2]
    assert "+103" in ic3.text
    assert "E=1.50 pp" in ic3.text


def test_ic3_at_the_historical_3pp_edge_would_have_been_plus_109():
    # §8: "Compare to the pre-2026-08-11 figure this same hypothetical would
    # have produced at E = 3.00 pp: +109."
    old = build_invalidation_conditions(
        starter_name="x", conservative_band_low_pct=51.0, edge_pp=3.00
    )
    assert old.minimum_acceptable_odds == 109


def test_lowered_edge_produces_a_less_demanding_price_floor():
    new = build_invalidation_conditions(starter_name="x", conservative_band_low_pct=51.0)
    old = build_invalidation_conditions(
        starter_name="x", conservative_band_low_pct=51.0, edge_pp=3.00
    )
    from src.math.novig import implied_probability

    assert implied_probability(new.minimum_acceptable_odds) > implied_probability(
        old.minimum_acceptable_odds
    )


def test_ic4_states_the_am_forecast_and_all_four_triggers(demo_8):
    ic4 = demo_8.conditions[3]
    assert "precip 15%" in ic4.text
    assert "6 mph out to center" in ic4.text
    assert "above 50%" in ic4.text
    assert "reverses" in ic4.text
    assert ">=10 mph" in ic4.text
    assert ">=15F" in ic4.text


def test_decision_log_cell_is_one_semicolon_separated_line(demo_8):
    cell = demo_8.decision_log_cell()
    assert "\n" not in cell
    assert cell.count("; IC") == 4  # IC1; IC2; IC3; IC4; IC5
    assert cell.startswith("IC1 —")


def test_no_unresolved_fields_in_the_complete_demo(demo_8):
    assert demo_8.has_unresolved is False


# ---------------------------------------------------------------------------
# §4 fail-closed rule
# ---------------------------------------------------------------------------


def test_missing_facts_use_the_literal_unresolved_never_a_dropped_check():
    result = build_invalidation_conditions(
        starter_name=None,
        thesis_critical_players=[],
        conservative_band_low_pct=None,
        weather_dependent=True,
        scheduled_date=None,
    )
    assert [c.code for c in result.conditions] == list(IC_RULE_ORDER)
    assert result.has_unresolved is True
    assert all(UNRESOLVED in c.text for c in result.conditions if c.unresolved)
    assert result.minimum_acceptable_odds is None


def test_non_weather_dependent_candidate_states_na_explicitly_rather_than_omitting():
    result = build_invalidation_conditions(
        starter_name="x", conservative_band_low_pct=51.0, weather_dependent=False
    )
    ic4 = result.conditions[3]
    assert ic4.text == "IC4 — N/A — not weather-dependent."
    assert ic4.unresolved is False


def test_ic3_reports_no_qualifying_price_when_the_edge_cannot_be_met():
    result = build_invalidation_conditions(
        starter_name="x", conservative_band_low_pct=1.0, edge_pp=1.50
    )
    assert result.minimum_acceptable_odds is None
    assert "VOID unconditionally on price" in result.conditions[2].text


# ---------------------------------------------------------------------------
# Placement-time check
# ---------------------------------------------------------------------------


def test_price_still_acceptable_compares_in_implied_probability_space():
    assert price_still_acceptable(103, 103) is True
    assert price_still_acceptable(120, 103) is True    # better price for the bettor
    assert price_still_acceptable(102, 103) is False   # worse (higher implied)
    assert price_still_acceptable(-110, 103) is False


# ---------------------------------------------------------------------------
# §5 / §6 cell conventions
# ---------------------------------------------------------------------------


def test_non_cleared_cell_convention():
    assert not_gate_cleared_cell("G1") == "N/A — not gate-CLEARED (G1)"


def test_revalidation_required_cell_convention():
    cell = revalidation_required_cell("probable pitchers confirmed")
    assert cell.startswith("REVALIDATION REQUIRED — probable pitchers confirmed")
    assert "no gate evaluation has occurred" in cell
