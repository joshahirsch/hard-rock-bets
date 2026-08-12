"""Tests for the /decision-log endpoints in src/service.py.

These wire the previously-unused DecisionLogSheetsClient into the running
service. Before this, GOOGLE_SHEETS_SPREADSHEET_ID/credentials being
configured had no observable effect -- nothing called the client.

No real Sheets API is exercised here: src.service.DecisionLogSheetsClient is
monkeypatched to a fake, same spirit as tests/test_sheets_client.py's
FakeService/FakeValuesApi.
"""

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from fastapi.testclient import TestClient  # noqa: E402

import src.service as service  # noqa: E402
from src.store.sheets_client import SheetsClientError  # noqa: E402

client = TestClient(service.app)


class FakeDecisionLogClient:
    """Records what it was asked to write; returns a canned read-back."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.appended = []
        FakeDecisionLogClient.instances.append(self)

    def append_rows(self, rows):
        self.appended.extend(rows)
        return {"updates": {"updatedRows": len(rows)}}

    def read_rows(self, max_rows=5000):
        return [{"Decision ID": "HRB-EXISTING", "Final Decision Type": "Pass"}]


class RaisingDecisionLogClient:
    def __init__(self, *args, **kwargs):
        raise SheetsClientError("missing Google credentials: set ...")


@pytest.fixture(autouse=True)
def _reset_fake_instances():
    FakeDecisionLogClient.instances = []
    yield
    FakeDecisionLogClient.instances = []


APPEND_PAYLOAD = {
    "run_date": "2026-08-12",
    "event": "Twins @ Guardians",
    "market": "h2h",
    "side": "Guardians",
    "gate_outcome": "CLEARED",
    "final_decision_type": "Research Candidate",
    "prompt_version": "test-v1",
}


def test_append_writes_a_row_with_a_server_generated_decision_id(monkeypatch):
    monkeypatch.setattr(service, "DecisionLogSheetsClient", FakeDecisionLogClient)
    resp = client.post("/decision-log/append", json=APPEND_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision_id"].startswith("HRB-20260812-")
    fake = FakeDecisionLogClient.instances[0]
    assert len(fake.appended) == 1
    written = fake.appended[0]
    assert written.event == "Twins @ Guardians"
    assert written.final_decision_type == "Research Candidate"
    assert written.decision_id == body["decision_id"]


def test_append_rejects_invalid_final_decision_type(monkeypatch):
    monkeypatch.setattr(service, "DecisionLogSheetsClient", FakeDecisionLogClient)
    bad = dict(APPEND_PAYLOAD, final_decision_type="Bet")
    resp = client.post("/decision-log/append", json=bad)
    assert resp.status_code == 422
    # Never even constructs a client for an invalid enum value.
    assert FakeDecisionLogClient.instances == []


def test_append_surfaces_missing_credentials_as_503(monkeypatch):
    monkeypatch.setattr(service, "DecisionLogSheetsClient", RaisingDecisionLogClient)
    resp = client.post("/decision-log/append", json=APPEND_PAYLOAD)
    assert resp.status_code == 503
    assert "credentials" in resp.json()["detail"]


def test_read_decision_log_returns_rows(monkeypatch):
    monkeypatch.setattr(service, "DecisionLogSheetsClient", FakeDecisionLogClient)
    resp = client.get("/decision-log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 1
    assert body["rows"][0]["Decision ID"] == "HRB-EXISTING"


def test_read_decision_log_surfaces_missing_credentials_as_503(monkeypatch):
    monkeypatch.setattr(service, "DecisionLogSheetsClient", RaisingDecisionLogClient)
    resp = client.get("/decision-log")
    assert resp.status_code == 503
