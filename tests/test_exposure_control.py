"""Tests for src/gates/exposure_control.py.

Primary fixture: ``claude/selection-gate-spec.md`` §10b -- the worked C1
example (HRB-DEMO-A Guardians ML at +4.1 pp vs HRB-DEMO-B Guardians -1.5 run
line at +3.6 pp; A keeps Research Candidate, B is capped to Pass).

Also covers the audit's D9 evidence directly: the Braves ML bet made on two
consecutive days of the same Padres series, and the Rays/Blue Jays series bet
twice -- under C1/C3 the second instance in each pair is capped automatically.
"""

from datetime import date, datetime, timedelta

from src.gates.exposure_control import (
    C1_SAME_DAY_SAME_TEAM_CAP,
    C2_SAME_THESIS_CAP,
    C3_SAME_SERIES_CAP,
    SERIES_WINDOW_DAYS,
    Candidate,
    ExposureOutcome,
    OpenEntry,
    apply_exposure_control,
    open_exposure_note,
    series_key,
)

NOW = datetime(2026, 7, 23, 12, 0)
TONIGHT = datetime(2026, 7, 23, 19, 10)


def test_cap_constants_are_all_one_and_the_series_window_is_seven_days():
    assert C1_SAME_DAY_SAME_TEAM_CAP == 1
    assert C2_SAME_THESIS_CAP == 1
    assert C3_SAME_SERIES_CAP == 1
    assert SERIES_WINDOW_DAYS == 7


# ---------------------------------------------------------------------------
# §10b worked demonstration
# ---------------------------------------------------------------------------


def test_spec_10b_c1_same_team_cap_with_edge_tiebreak():
    demo_a = Candidate(
        decision_id="HRB-DEMO-A",
        event="Twins @ Guardians",
        teams=("Twins", "Guardians"),
        thesis="bullpen fatigue",
        event_datetime=TONIGHT,
        conservative_edge_pp=4.1,
        writeup_position=0,
    )
    demo_b = Candidate(
        decision_id="HRB-DEMO-B",
        event="Twins @ Guardians",
        teams=("Twins", "Guardians"),
        thesis="run-line value",
        event_datetime=TONIGHT,
        conservative_edge_pp=3.6,
        writeup_position=1,
    )
    results = {r.decision_id: r for r in apply_exposure_control([demo_a, demo_b], [], NOW)}

    assert results["HRB-DEMO-A"].outcome is ExposureOutcome.CLEAR
    assert results["HRB-DEMO-A"].final_decision_type == "Research Candidate"

    assert results["HRB-DEMO-B"].outcome is ExposureOutcome.CAPPED
    assert results["HRB-DEMO-B"].deciding_rule == "C1"
    assert results["HRB-DEMO-B"].conflicting_decision_id == "HRB-DEMO-A"
    assert results["HRB-DEMO-B"].final_decision_type == "Pass"
    assert "C1" in results["HRB-DEMO-B"].note


def test_c1_tiebreak_falls_back_to_earlier_writeup_position_when_edges_tie():
    first = Candidate("A", "E", ("Guardians",), "t1", TONIGHT, 4.0, writeup_position=0)
    second = Candidate("B", "E", ("Guardians",), "t2", TONIGHT, 4.0, writeup_position=1)
    results = {r.decision_id: r for r in apply_exposure_control([second, first], [], NOW)}
    assert results["A"].outcome is ExposureOutcome.CLEAR
    assert results["B"].deciding_rule == "C1"


def test_c1_winner_is_the_higher_edge_regardless_of_writeup_order():
    weaker_first = Candidate("W", "E", ("Rays",), "t1", TONIGHT, 2.0, writeup_position=0)
    stronger_second = Candidate("S", "E", ("Rays",), "t2", TONIGHT, 5.0, writeup_position=1)
    results = {
        r.decision_id: r
        for r in apply_exposure_control([weaker_first, stronger_second], [], NOW)
    }
    assert results["S"].outcome is ExposureOutcome.CLEAR
    assert results["W"].deciding_rule == "C1"


# ---------------------------------------------------------------------------
# C2 / C3 against prior days -- the audit's D9 failure modes
# ---------------------------------------------------------------------------


def prior_open_entry(**over) -> OpenEntry:
    base = dict(
        decision_id="HRB-20260722-01",
        event="Braves @ Padres",
        teams=("Braves", "Padres"),
        thesis="bullpen fatigue",
        event_datetime=datetime(2026, 7, 24, 22, 10),
        run_date=date(2026, 7, 22),
    )
    base.update(over)
    return OpenEntry(**base)


def test_c3_caps_a_second_bet_on_the_same_series_the_next_day():
    # Audit D9: "the Braves ML bet made twice in the same series".
    today = Candidate(
        "HRB-20260723-01", "Braves @ Padres", ("Braves", "Padres"),
        "different thesis entirely", TONIGHT, 5.0, 0,
    )
    result = apply_exposure_control([today], [prior_open_entry()], NOW)[0]
    assert result.outcome is ExposureOutcome.CAPPED
    assert result.deciding_rule == "C3"
    assert result.conflicting_decision_id == "HRB-20260722-01"


def test_c2_caps_a_second_candidate_carrying_an_already_open_thesis():
    today = Candidate(
        "HRB-20260723-02", "Rays @ Blue Jays", ("Rays", "Blue Jays"),
        "bullpen fatigue", TONIGHT, 5.0, 0,
    )
    result = apply_exposure_control([today], [prior_open_entry()], NOW)[0]
    assert result.outcome is ExposureOutcome.CAPPED
    assert result.deciding_rule == "C2"


def test_precedence_todays_candidate_is_always_the_one_capped():
    # §6 item 5: the historical entry is never retroactively edited.
    prior = prior_open_entry()
    today = Candidate(
        "HRB-20260723-01", "Braves @ Padres", ("Braves", "Padres"),
        "bullpen fatigue", TONIGHT, 99.0, 0,   # far bigger edge, still capped
    )
    result = apply_exposure_control([today], [prior], NOW)[0]
    assert result.outcome is ExposureOutcome.CAPPED
    assert result.conflicting_decision_id == prior.decision_id


def test_a_started_game_is_no_longer_open_exposure():
    # §5: "An entry stops being 'open' once its event starts."
    started = prior_open_entry(event_datetime=NOW - timedelta(hours=1))
    today = Candidate(
        "HRB-20260723-01", "Braves @ Padres", ("Braves", "Padres"),
        "bullpen fatigue", TONIGHT, 5.0, 0,
    )
    result = apply_exposure_control([today], [started], NOW)[0]
    assert result.outcome is ExposureOutcome.CLEAR


def test_series_window_is_a_rolling_seven_calendar_days():
    assert series_key(("Rays", "Blue Jays")) == series_key(("blue jays", "RAYS"))
    inside = prior_open_entry(
        teams=("Rays", "Blue Jays"),
        thesis="unrelated",
        event_datetime=datetime(2026, 7, 31, 19, 0),   # 1 day from the candidate
        run_date=date(2026, 7, 22),
    )
    outside = prior_open_entry(
        teams=("Rays", "Blue Jays"),
        thesis="unrelated",
        event_datetime=datetime(2026, 8, 15, 19, 0),   # 16 days -- outside the window
        run_date=date(2026, 7, 22),
    )
    cand = Candidate(
        "T", "Rays @ Blue Jays", ("Rays", "Blue Jays"), "fresh thesis",
        datetime(2026, 7, 30, 19, 0), 5.0, 0,
    )
    now = datetime(2026, 7, 30, 12, 0)
    assert apply_exposure_control([cand], [inside], now)[0].deciding_rule == "C3"
    assert apply_exposure_control([cand], [outside], now)[0].outcome is ExposureOutcome.CLEAR


def test_a_non_cleared_candidate_claims_no_exposure():
    failed = Candidate(
        "F", "Twins @ Guardians", ("Guardians",), "t", TONIGHT, -7.33, 0,
        gate_cleared=False,
    )
    cleared = Candidate("C", "Twins @ Guardians", ("Guardians",), "t2", TONIGHT, 4.0, 1)
    results = {r.decision_id: r for r in apply_exposure_control([failed, cleared], [], NOW)}
    assert results["F"].final_decision_type == "Pass"
    assert results["C"].outcome is ExposureOutcome.CLEAR


def test_market_efficiency_pathway_shares_the_same_team_cap():
    # market-efficiency-candidate-spec.md §6: C1's "regardless of market type"
    # is read as also meaning regardless of PATHWAY.
    informational = Candidate("I", "E", ("Guardians",), "info", TONIGHT, 4.0, 0)
    me = Candidate(
        "M", "E", ("Guardians",), "price divergence", TONIGHT, 3.0, 1,
        pathway="market_efficiency",
    )
    results = {r.decision_id: r for r in apply_exposure_control([informational, me], [], NOW)}
    assert results["I"].outcome is ExposureOutcome.CLEAR
    assert results["M"].deciding_rule == "C1"


# ---------------------------------------------------------------------------
# §7 portfolio note
# ---------------------------------------------------------------------------


def test_open_exposure_note_is_mandatory_even_when_empty():
    assert open_exposure_note([], NOW).endswith("No open research-candidate exposure.")


def test_open_exposure_note_lists_every_open_entry():
    note = open_exposure_note([prior_open_entry()], NOW)
    assert "HRB-20260722-01" in note
    assert "bullpen fatigue" in note
    assert "2026-07-22" in note
