"""Tests for src/calibration.py.

Pure-function tests: build synthetic Decision Log + Outcomes rows (the same
dict-keyed-by-column-name shape DecisionLogSheetsClient.read_rows /
OutcomesSheetsClient.read_rows return) and assert on the report
build_calibration_report produces. No Sheets I/O anywhere in this file.
"""

from src.calibration import (
    MIN_SAMPLE_FOR_A_REAL_CONCLUSION,
    MIN_SAMPLE_FOR_ANY_CONCLUSION,
    BucketStats,
    _american_to_payout_multiplier,
    _implied_from_american,
    _resolve_latest_outcomes,
    build_calibration_report,
)


def decision_row(**over):
    base = dict(
        **{
            "Decision ID": "HRB-1",
            "Run Date": "2026-08-10",
            "Event": "Twins @ Guardians",
            "Final Decision Type": "Research Candidate",
            "Gate Outcome": "CLEARED — all gates G1-G6 passed.",
        }
    )
    base.update(over)
    return base


def outcome_row(**over):
    base = dict(
        **{
            "Outcome ID": "OUT-1",
            "Decision ID": "HRB-1",
            "Placed": "Y",
            "Placed Stake USD": "10",
            "Placed Odds": "-110",
            "Placed At ET": "2026-08-10 11:00 AM ET",
            "Result": "PENDING",
            "Resolved At ET": "",
            "Closing Line Odds": "",
            "Notes": "",
        }
    )
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# _american_to_payout_multiplier
# ---------------------------------------------------------------------------


def test_payout_multiplier_negative_odds():
    # -110 -> risk 110 to win 100 -> total return per $1 staked = 1 + 100/110
    assert round(_american_to_payout_multiplier(-110), 4) == round(1 + 100 / 110, 4)


def test_payout_multiplier_positive_odds():
    assert _american_to_payout_multiplier(150) == 2.5


def test_payout_multiplier_none_and_zero_and_bad_input():
    assert _american_to_payout_multiplier(None) is None
    assert _american_to_payout_multiplier(0) is None
    assert _american_to_payout_multiplier("garbage") is None


# ---------------------------------------------------------------------------
# _implied_from_american
# ---------------------------------------------------------------------------


def test_implied_probability_negative_and_positive():
    assert round(_implied_from_american(-110), 4) == round(110 / 210, 4)
    assert round(_implied_from_american(150), 4) == round(100 / 250, 4)
    assert _implied_from_american(0) is None


# ---------------------------------------------------------------------------
# _resolve_latest_outcomes
# ---------------------------------------------------------------------------


def test_resolve_latest_prefers_resolved_row_over_pending():
    rows = [
        outcome_row(**{"Result": "PENDING"}),
        outcome_row(**{"Result": "WIN", "Resolved At ET": "2026-08-10 10:00 PM ET"}),
    ]
    resolved = _resolve_latest_outcomes(rows)
    assert resolved["HRB-1"]["Result"] == "WIN"


def test_resolve_latest_falls_back_to_last_row_when_still_pending():
    rows = [outcome_row(**{"Result": "PENDING"})]
    resolved = _resolve_latest_outcomes(rows)
    assert resolved["HRB-1"]["Result"] == "PENDING"


def test_resolve_latest_skips_rows_with_no_decision_id():
    rows = [outcome_row(**{"Decision ID": ""})]
    resolved = _resolve_latest_outcomes(rows)
    assert resolved == {}


# ---------------------------------------------------------------------------
# build_calibration_report -- overall bucket
# ---------------------------------------------------------------------------


def test_overall_bucket_counts_win_and_loss():
    decisions = [
        decision_row(**{"Decision ID": "HRB-1"}),
        decision_row(**{"Decision ID": "HRB-2"}),
    ]
    outcomes = [
        outcome_row(**{"Decision ID": "HRB-1", "Result": "WIN"}),
        outcome_row(**{"Decision ID": "HRB-2", "Outcome ID": "OUT-2", "Result": "LOSS"}),
    ]
    report = build_calibration_report(decisions, outcomes)
    overall = report["overall"]
    assert overall["placed"] == 2
    assert overall["wins"] == 1
    assert overall["losses"] == 1
    assert overall["decided"] == 2
    assert overall["hit_rate"] == 0.5


def test_win_roi_uses_american_odds_payout():
    decisions = [decision_row(**{"Decision ID": "HRB-1"})]
    outcomes = [
        outcome_row(
            **{"Decision ID": "HRB-1", "Result": "WIN", "Placed Stake USD": "100", "Placed Odds": "150"}
        )
    ]
    report = build_calibration_report(decisions, outcomes)
    overall = report["overall"]
    # $100 at +150 wins $150 profit -> total return $250 -> ROI 1.5
    assert overall["total_staked_usd"] == 100.0
    assert overall["total_return_usd"] == 250.0
    assert overall["roi"] == 1.5


def test_push_returns_stake_with_zero_roi_contribution():
    decisions = [decision_row(**{"Decision ID": "HRB-1"})]
    outcomes = [outcome_row(**{"Decision ID": "HRB-1", "Result": "PUSH", "Placed Stake USD": "20"})]
    report = build_calibration_report(decisions, outcomes)
    overall = report["overall"]
    assert overall["pushes"] == 1
    assert overall["total_staked_usd"] == 20.0
    assert overall["total_return_usd"] == 20.0
    # Not counted in decided (wins+losses), so hit_rate is None.
    assert overall["decided"] == 0
    assert overall["hit_rate"] is None


def test_void_excluded_from_stake_and_return():
    decisions = [decision_row(**{"Decision ID": "HRB-1"})]
    outcomes = [outcome_row(**{"Decision ID": "HRB-1", "Result": "VOID", "Placed Stake USD": "20"})]
    report = build_calibration_report(decisions, outcomes)
    overall = report["overall"]
    assert overall["voids"] == 1
    assert overall["total_staked_usd"] == 0.0
    assert overall["total_return_usd"] == 0.0
    assert overall["roi"] is None


def test_pending_result_counts_as_pending_not_decided():
    decisions = [decision_row(**{"Decision ID": "HRB-1"})]
    outcomes = [outcome_row(**{"Decision ID": "HRB-1", "Result": "PENDING"})]
    report = build_calibration_report(decisions, outcomes)
    overall = report["overall"]
    assert overall["pending"] == 1
    assert overall["decided"] == 0
    assert report["unresolved_placed_count"] == 1


# ---------------------------------------------------------------------------
# Not placed / no outcome row -- cleared_but_not_confirmed_placed_count
# ---------------------------------------------------------------------------


def test_cleared_with_no_outcome_row_is_counted_as_cleared_not_placed():
    decisions = [decision_row(**{"Decision ID": "HRB-1", "Gate Outcome": "CLEARED — fine."})]
    report = build_calibration_report(decisions, [])
    assert report["cleared_but_not_confirmed_placed_count"] == 1
    assert report["overall"]["placed"] == 0


def test_placed_n_is_excluded_from_buckets_but_still_counts_as_cleared_not_placed():
    decisions = [decision_row(**{"Decision ID": "HRB-1"})]
    outcomes = [outcome_row(**{"Decision ID": "HRB-1", "Placed": "N"})]
    report = build_calibration_report(decisions, outcomes)
    assert report["overall"]["placed"] == 0
    assert report["cleared_but_not_confirmed_placed_count"] == 1


def test_not_cleared_and_not_placed_is_not_counted_anywhere():
    decisions = [
        decision_row(
            **{
                "Decision ID": "HRB-1",
                "Final Decision Type": "Pass",
                "Gate Outcome": "NOT CLEARED — G1: edge below floor.",
            }
        )
    ]
    report = build_calibration_report(decisions, [])
    assert report["cleared_but_not_confirmed_placed_count"] == 0
    assert report["overall"]["placed"] == 0


# ---------------------------------------------------------------------------
# Grouping -- by Final Decision Type
# ---------------------------------------------------------------------------


def test_grouped_by_final_decision_type():
    decisions = [
        decision_row(**{"Decision ID": "HRB-1", "Final Decision Type": "Research Candidate"}),
        decision_row(**{"Decision ID": "HRB-2", "Final Decision Type": "Market-Efficiency Candidate"}),
    ]
    outcomes = [
        outcome_row(**{"Decision ID": "HRB-1", "Result": "WIN"}),
        outcome_row(**{"Decision ID": "HRB-2", "Outcome ID": "OUT-2", "Result": "LOSS"}),
    ]
    report = build_calibration_report(decisions, outcomes)
    by_type = report["by_final_decision_type"]
    assert by_type["Research Candidate"]["wins"] == 1
    assert by_type["Market-Efficiency Candidate"]["losses"] == 1


# ---------------------------------------------------------------------------
# Grouping -- by deciding rule (parsed from Gate Outcome text)
# ---------------------------------------------------------------------------


def test_grouped_by_deciding_rule_when_not_cleared_text_is_parseable():
    # Placed=Y despite a "NOT CLEARED" gate outcome is an unusual but valid
    # real-world case (e.g. manual override) -- the parser just needs to key
    # off the text, not re-derive gate logic.
    decisions = [
        decision_row(
            **{
                "Decision ID": "HRB-1",
                "Gate Outcome": "NOT CLEARED — G4: no Tier 1 anchor.",
            }
        )
    ]
    outcomes = [outcome_row(**{"Decision ID": "HRB-1", "Result": "LOSS"})]
    report = build_calibration_report(decisions, outcomes)
    assert "G4" in report["by_deciding_rule"]
    assert report["by_deciding_rule"]["G4"]["losses"] == 1


def test_grouped_as_cleared_when_no_failing_rule_is_parseable():
    decisions = [decision_row(**{"Decision ID": "HRB-1", "Gate Outcome": "CLEARED — all gates passed."})]
    outcomes = [outcome_row(**{"Decision ID": "HRB-1", "Result": "WIN"})]
    report = build_calibration_report(decisions, outcomes)
    assert "CLEARED" in report["by_deciding_rule"]
    assert report["by_deciding_rule"]["CLEARED"]["wins"] == 1


# ---------------------------------------------------------------------------
# CLV
# ---------------------------------------------------------------------------


def test_clv_sample_recorded_when_both_placed_and_closing_odds_present():
    decisions = [decision_row(**{"Decision ID": "HRB-1"})]
    outcomes = [
        outcome_row(
            **{
                "Decision ID": "HRB-1",
                "Result": "WIN",
                "Placed Odds": "-110",
                "Closing Line Odds": "-130",
            }
        )
    ]
    report = build_calibration_report(decisions, outcomes)
    assert report["overall"]["avg_clv_pp"] is not None


def test_no_clv_sample_when_closing_odds_missing():
    decisions = [decision_row(**{"Decision ID": "HRB-1"})]
    outcomes = [outcome_row(**{"Decision ID": "HRB-1", "Result": "WIN", "Closing Line Odds": ""})]
    report = build_calibration_report(decisions, outcomes)
    assert report["overall"]["avg_clv_pp"] is None


# ---------------------------------------------------------------------------
# Two appends for the same decision (placement then resolution) -- only the
# resolved state should count, not double-count as two separate bets.
# ---------------------------------------------------------------------------


def test_placement_then_resolution_appends_count_as_one_resolved_bet():
    decisions = [decision_row(**{"Decision ID": "HRB-1"})]
    outcomes = [
        outcome_row(**{"Decision ID": "HRB-1", "Outcome ID": "OUT-1", "Result": "PENDING"}),
        outcome_row(**{"Decision ID": "HRB-1", "Outcome ID": "OUT-2", "Result": "WIN"}),
    ]
    report = build_calibration_report(decisions, outcomes)
    overall = report["overall"]
    assert overall["placed"] == 1
    assert overall["wins"] == 1
    assert overall["pending"] == 0


# ---------------------------------------------------------------------------
# known_limitations is always present and non-empty
# ---------------------------------------------------------------------------


def test_known_limitations_present():
    report = build_calibration_report([], [])
    assert isinstance(report["known_limitations"], list)
    assert len(report["known_limitations"]) >= 1


# ---------------------------------------------------------------------------
# BucketStats sample_note thresholds
# ---------------------------------------------------------------------------


def test_sample_note_below_any_conclusion_threshold():
    b = BucketStats(label="x", wins=5, losses=5)  # decided=10 < 20
    assert "too small" in b.sample_note


def test_sample_note_between_thresholds():
    wins = MIN_SAMPLE_FOR_ANY_CONCLUSION  # decided = 20+1 = 21, still < 50
    b = BucketStats(label="x", wins=wins, losses=1)
    assert "early read" in b.sample_note


def test_sample_note_at_or_above_real_conclusion_threshold():
    b = BucketStats(label="x", wins=MIN_SAMPLE_FOR_A_REAL_CONCLUSION, losses=0)
    assert "real read" in b.sample_note


def test_bucket_as_dict_rounds_and_includes_all_fields():
    b = BucketStats(label="x", wins=1, losses=1, total_staked_usd=10.001, total_return_usd=19.999)
    d = b.as_dict()
    assert d["label"] == "x"
    assert d["total_staked_usd"] == 10.0
    assert d["total_return_usd"] == 20.0
