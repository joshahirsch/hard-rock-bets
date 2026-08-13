"""Tests for src/store/outcomes_client.py.

Same shape as test_sheets_client.py -- this module deliberately mirrors
DecisionLogSheetsClient's atomic-append discipline for a second, separate
tab. Focus here: outcome IDs are collision-free, appends never compute a row
index, invalid placed/result values are rejected before ever reaching the
Sheets API, and -- like the Decision Log -- no update or delete method exists.
"""

import pytest

from src.store.outcomes_client import (
    OUTCOME_COLUMNS,
    RESULT_VALUES,
    OutcomeRow,
    OutcomesSheetsClient,
    generate_outcome_id,
)
from src.store.sheets_client import SheetsClientError, generate_ulid


class FakeValuesApi:
    def __init__(self):
        self.calls = []

    def append(self, **kwargs):
        self.calls.append(("append", kwargs))
        return _Executable({"updates": {"updatedRows": len(kwargs["body"]["values"])}})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Executable({"values": [["OUT-1", "HRB-1", "Y"]]})

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return _Executable({"updatedCells": len(OUTCOME_COLUMNS)})


class _Executable:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeService:
    def __init__(self):
        self.values_api = FakeValuesApi()

    def spreadsheets(self):
        return self

    def values(self):
        return self.values_api


@pytest.fixture
def client():
    service = FakeService()
    c = OutcomesSheetsClient(spreadsheet_id="TEST_SHEET_ID", service=service)
    return c, service.values_api


def sample_row(**over) -> OutcomeRow:
    base = dict(
        outcome_id="OUT-01H0000000000000000000000",
        decision_id="HRB-20260812-01H",
        placed="Y",
        placed_stake_usd="10",
        placed_odds="-110",
        placed_at_et="2026-08-13 12:00 PM ET",
    )
    base.update(over)
    return OutcomeRow(**base)


# ---------------------------------------------------------------------------
# Outcome IDs
# ---------------------------------------------------------------------------


def test_outcome_id_uses_a_ulid_and_the_out_prefix():
    oid = generate_outcome_id()
    assert oid.startswith("OUT-")
    assert len(oid) == len("OUT-") + 26


def test_outcome_ids_are_unique():
    assert generate_outcome_id() != generate_outcome_id()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_is_exactly_10_columns_a_through_j():
    assert len(OUTCOME_COLUMNS) == 10
    assert OUTCOME_COLUMNS[0] == "Outcome ID"
    assert OUTCOME_COLUMNS[1] == "Decision ID"


def test_default_result_is_pending():
    row = sample_row()
    assert row.result == "PENDING"


def test_result_values_include_all_five_states():
    assert set(RESULT_VALUES) == {"PENDING", "WIN", "LOSS", "PUSH", "VOID"}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_placed_must_be_y_or_n():
    row = sample_row(placed="maybe")
    with pytest.raises(SheetsClientError):
        row.to_values()


def test_invalid_result_is_rejected():
    row = sample_row(result="WON")  # not a real value
    with pytest.raises(SheetsClientError):
        row.to_values()


def test_every_result_value_is_accepted():
    for value in RESULT_VALUES:
        row = sample_row(result=value)
        row.to_values()  # must not raise


# ---------------------------------------------------------------------------
# Atomic append -- same discipline as the Decision Log
# ---------------------------------------------------------------------------


def test_append_uses_values_append_with_insert_rows(client):
    c, values_api = client
    c.append_row(sample_row())
    assert len(values_api.calls) == 1
    name, kwargs = values_api.calls[0]
    assert name == "append"
    assert kwargs["insertDataOption"] == "INSERT_ROWS"
    assert kwargs["range"] == "Outcomes!A1"


def test_append_never_computes_a_row_index(client):
    c, values_api = client
    c.append_row(sample_row())
    _, kwargs = values_api.calls[0]
    assert "A2" not in kwargs["range"] and "A3" not in kwargs["range"]


def test_two_appends_for_the_same_decision_id_are_both_kept(client):
    """Placement row, then a later resolution row -- both real, both appended,
    neither edits the other. This is the append-only resolution mechanism the
    module docstring describes."""
    c, values_api = client
    c.append_row(sample_row(result="PENDING"))
    c.append_row(
        sample_row(
            outcome_id="OUT-01H0000000000000000000001",
            result="WIN",
            resolved_at_et="2026-08-13 10:00 PM ET",
        )
    )
    assert len(values_api.calls) == 2
    assert all(name == "append" for name, _ in values_api.calls)


def test_client_exposes_no_update_or_delete_for_existing_rows():
    public_methods = {
        name
        for name in dir(OutcomesSheetsClient)
        if not name.startswith("_") and callable(getattr(OutcomesSheetsClient, name))
    }
    assert not any("update" in m or "delete" in m or "edit" in m for m in public_methods)


def test_read_rows_pads_short_rows_to_the_full_schema(client):
    c, _ = client
    rows = c.read_rows()
    assert len(rows) == 1
    assert set(rows[0].keys()) == set(OUTCOME_COLUMNS)
    assert rows[0]["Outcome ID"] == "OUT-1"
    assert rows[0]["Notes"] == ""


def test_missing_spreadsheet_id_is_an_error(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_SPREADSHEET_ID", raising=False)
    with pytest.raises(SheetsClientError):
        OutcomesSheetsClient()


def test_tab_name_defaults_to_outcomes(client):
    c, _ = client
    assert c.tab_name == "Outcomes"


def test_tab_name_env_var_is_independent_of_decision_log_tab(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "TEST_SHEET_ID")
    monkeypatch.setenv("GOOGLE_SHEETS_OUTCOMES_TAB", "Bet Outcomes")
    monkeypatch.delenv("GOOGLE_SHEETS_DECISION_LOG_TAB", raising=False)
    c = OutcomesSheetsClient(service=FakeService())
    assert c.tab_name == "Bet Outcomes"
