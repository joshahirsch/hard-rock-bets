"""End-to-end tests for the /evaluate pipeline in src/service.py.

The full-pipeline fixture is again the spec's own worked chain:
fair-probability-spec.md §9 Demonstration A -> selection-gate-spec.md §10a.
Feeding the raw evidence judgments into one /evaluate call must reproduce the
same band (5 pp), the same conservative edge (-7.33 pp), the same gate verdict
(NOT CLEARED, deciding rule G1), and no suggested stake.
"""

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from fastapi.testclient import TestClient  # noqa: E402

from src.service import app  # noqa: E402

client = TestClient(app)


DEMO_A_PAYLOAD = {
    "event": "Twins @ Guardians",
    "market": "h2h",
    "side": "Guardians",
    "teams": ["Twins", "Guardians"],
    "thesis": "bullpen availability",
    "event_datetime": "2026-07-23T19:10:00",
    "run_datetime": "2026-07-23T12:00:00",
    "two_sided_price": {
        "side_odds": -135,
        "opposing_odds": 112,
        "side_timestamp_minutes": 0,
        "opposing_timestamp_minutes": 1,
        "age_minutes": 5,
    },
    "evaluation_odds": -140,
    "adjustments": [
        {
            "name": "A1 late bullpen-availability note",
            "magnitude_pp": 2.0,
            "tier": 1,
            "priced_in": "N",
            "freshness": "under_6h",
            "source": "Guardians official transactions page (fictional)",
        },
        {
            "name": "A2 starting-pitcher recent-form divergence",
            "magnitude_pp": -1.5,
            "tier": 2,
            "priced_in": "Uncertain",
            "freshness": "6_to_24h",
            "source": "Named beat-writer article (fictional)",
        },
        {
            "name": "A3 7-day head-to-head bullpen ERA trend",
            "magnitude_pp": 1.0,
            "tier": 1,
            "priced_in": "Y",
            "freshness": "under_6h",
            "source": "Team-released aggregate stat (fictional)",
        },
    ],
    "skeptical_verdict": "SURVIVES",
    "required_checklist": {
        "probable_pitchers_confirmed": "MET",
        "lineup_timing_honesty": "MET",
        "bullpen_recent_usage": "MET",
        "weather": "MET",
    },
    "starter_name": "the Guardians' confirmed starter",
    "thesis_critical_players": ["the player behind adjustment A1"],
    "scheduled_date": "2026-07-23",
    "bankroll_usd": 150.0,
}


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["shadow_mode"] is True


def test_evaluate_reproduces_the_specs_own_worked_chain():
    r = client.post("/evaluate", json=DEMO_A_PAYLOAD)
    assert r.status_code == 200
    body = r.json()

    fp = body["fair_probability"]
    assert fp["baseline_no_vig_pct"] == pytest.approx(54.91, abs=0.01)
    assert fp["net_adjustment_pp"] == pytest.approx(1.25)
    assert fp["display"] == "56% (range 51%-61%)"
    assert fp["band_pp"] == 5.0
    assert fp["edge_conservative_pp"] == pytest.approx(-7.33, abs=0.01)
    assert fp["ev_conservative_per_dollar"] == pytest.approx(-0.126, abs=5e-4)

    gate = body["selection_gate"]
    assert gate["cleared"] is False
    assert gate["deciding_rule"] == "G1"
    assert {r_["rule"]: r_["passed"] for r_ in gate["all_rule_results"]} == {
        "G1": False, "G2": True, "G3": True, "G4": True, "G5": True, "G6": True,
    }

    assert body["final_decision_type"] == "Pass"
    assert body["invalidation_conditions"] is None  # not gate-CLEARED
    assert body["stake"]["eligible"] is False
    assert body["decision_log_cells"]["V_gate_outcome"].startswith("NOT CLEARED — G1")


def test_evaluate_clears_and_produces_ic_and_stake_at_a_better_price():
    # revalidation-spec.md §8's constructed CLEARED variant: Guardians +125
    # (implied 44.44%) -> conservative edge 51 - 44.44 = +6.56 pp.
    payload = dict(DEMO_A_PAYLOAD, evaluation_odds=125)
    body = client.post("/evaluate", json=payload).json()

    assert body["fair_probability"]["edge_conservative_pp"] == pytest.approx(
        6.56, abs=0.01
    )
    assert body["selection_gate"]["cleared"] is True
    assert body["final_decision_type"] == "Research Candidate"

    ic = body["invalidation_conditions"]
    assert [c["code"] for c in ic["conditions"]] == ["IC1", "IC2", "IC3", "IC4", "IC5"]
    assert ic["minimum_acceptable_odds"] == 103   # §8's recomputed E=1.50 pp figure
    assert ic["has_unresolved"] is False

    assert body["stake"] == {
        "eligible": True,
        "amount_usd": 10.0,
        "binding_constraint": "flat figure",
        "text": "Suggested stake (informational, not an instruction): $10",
    }


def test_defeated_estimate_stops_the_pipeline_at_the_verdict():
    payload = dict(DEMO_A_PAYLOAD, evaluation_odds=125, skeptical_verdict="DEFEATED")
    body = client.post("/evaluate", json=payload).json()
    assert body["fair_probability"]["ships"] is False
    assert body["selection_gate"] is None
    assert body["invalidation_conditions"] is None
    assert body["final_decision_type"] == "Pass"
    assert body["stake"]["eligible"] is False


def test_tier3_adjustment_is_rejected_outright():
    payload = dict(DEMO_A_PAYLOAD)
    payload["adjustments"] = [
        {
            "name": "pick-site consensus",
            "magnitude_pp": 3.0,
            "tier": 3,
            "priced_in": "N",
            "freshness": "under_6h",
        }
    ]
    r = client.post("/evaluate", json=payload)
    assert r.status_code == 422
    assert "Tier-3" in r.json()["detail"]


def test_stale_or_non_simultaneous_pair_means_no_baseline_and_no_estimate():
    payload = dict(DEMO_A_PAYLOAD)
    payload["two_sided_price"] = dict(payload["two_sided_price"], age_minutes=90)
    r = client.post("/evaluate", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "no valid baseline"


def test_c1_downgrades_a_cleared_candidate_against_this_mornings_entry():
    # C1 is a same-DAY cap: the conflicting entry was logged by this morning's
    # firing (run_date == today) and its game has not started yet.
    payload = dict(
        DEMO_A_PAYLOAD,
        evaluation_odds=125,
        open_entries=[
            {
                "decision_id": "HRB-20260723-01",
                "event": "Guardians @ White Sox",
                "teams": ["Guardians", "White Sox"],
                "thesis": "unrelated",
                "event_datetime": "2026-07-23T20:10:00",
                "run_date": "2026-07-23T08:00:00",
            }
        ],
    )
    body = client.post("/evaluate", json=payload).json()
    assert body["selection_gate"]["cleared"] is True
    assert body["exposure"]["outcome"] == "CAPPED"
    assert body["exposure"]["deciding_rule"] == "C1"
    assert body["final_decision_type"] == "Pass"
    # §3 of stake-sizing-spec.md: nothing to size once it is downgraded.
    assert body["stake"]["eligible"] is False
    assert "HRB-20260723-01" in body["open_exposure_note"]


def test_c3_downgrades_against_a_prior_days_open_series_entry():
    payload = dict(
        DEMO_A_PAYLOAD,
        evaluation_odds=125,
        open_entries=[
            {
                "decision_id": "HRB-20260722-01",
                "event": "Twins @ Guardians",
                "teams": ["Twins", "Guardians"],
                "thesis": "unrelated",
                "event_datetime": "2026-07-24T19:10:00",
                "run_date": "2026-07-22T08:00:00",
            }
        ],
    )
    body = client.post("/evaluate", json=payload).json()
    assert body["exposure"]["deciding_rule"] == "C3"
    # Precedence: today's candidate is always the one capped.
    assert body["exposure"]["conflicting_decision_id"] == "HRB-20260722-01"
    assert body["final_decision_type"] == "Pass"


def test_market_efficiency_pathway_can_run_alongside():
    payload = dict(
        DEMO_A_PAYLOAD,
        evaluation_odds=-140,   # informational pathway fails G1 here
        market_efficiency={
            "hard_rock_odds": 155,
            "contributing_books": [
                {"book_key": "pinnacle", "side_odds": 148, "opposing_odds": -175},
                {"book_key": "draftkings", "side_odds": 150, "opposing_odds": -180},
                {"book_key": "fanduel", "side_odds": 145, "opposing_odds": -172},
                {"book_key": "betmgm", "side_odds": 152, "opposing_odds": -178},
            ],
            "news_cause_fact_within_window": False,
            "prior_sightings_today": 1,
        },
    )
    body = client.post("/evaluate", json=payload).json()
    me = body["market_efficiency"]
    assert me is not None
    assert [r["rule"] for r in me["all_rule_results"]] == [
        "GME0", "GME1", "GME2", "GME3", "GME4", "GME5", "GME6"
    ]
    assert me["consensus_p_pct"] is not None


def test_stake_endpoint_standalone():
    r = client.post(
        "/stake", json={"final_decision_type": "Research Candidate", "bankroll_usd": 95}
    )
    assert r.json()["amount_usd"] == 9.0
    r2 = client.post("/stake", json={"final_decision_type": "Pass", "bankroll_usd": 95})
    assert r2.json()["eligible"] is False
