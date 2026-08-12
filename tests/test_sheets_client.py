"""Tests for src/store/sheets_client.py.

Focus is on the two v1 bugs this module exists to fix:
  * Decision IDs derived from a row count collided when firings overlapped.
  * Manual next-row computation raced and overwrote rows.

Plus the sheet contract: exactly 25 columns A-Y, with L and M left blank
because they are formula-owned (market-math-spec.md §6).
"""

import pytest

from src.store.sheets_client import (
    DECISION_LOG_COLUMNS,
    FINAL_DECISION_TYPES,
    FORMULA_OWNED_COLUMN_INDICES,
    DecisionLogRow,
    DecisionLogSheetsClient,
    SheetsClientError,
    generate_decision_id,
    generate_ulid,
)


class FakeValuesApi:
    def __init__(self):
        self.calls = []

    def append(self, **kwargs):
        self.calls.append(("append", kwargs))
        return _Executable({"updates": {"updatedRows": len(kwargs["body"]["values"])}})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return _Executable({"values": [["HRB-1", "2026-08-12", "Twins @ Guardians"]]})

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return _Executable({"updatedCells": len(DECISION_LOG_COLUMNS)})


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
    c = DecisionLogSheetsClient(spreadsheet_id="TEST_SHEET_ID", service=service)
    return c, service.values_api


def sample_row(**over) -> DecisionLogRow:
    base = dict(
        decision_id="HRB-20260812-01H",
        run_date="2026-08-12",
        event="Twins @ Guardians",
        market="h2h",
        side="Guardians",
        current_reference_odds="-140",
        odds_source="Owls Insight v1 (hardrock)",
        final_decision_type="Pass",
        reason_for_pass="Selection gate G1",
        prompt_version="research-pipeline-v1",
    )
    base.update(over)
    return DecisionLogRow(**base)


# ---------------------------------------------------------------------------
# ULID Decision IDs
# ---------------------------------------------------------------------------


def test_ulid_is_26_chars_of_crockford_base32():
    ulid = generate_ulid()
    assert len(ulid) == 26
    assert set(ulid) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_ulids_are_unique_without_reading_anything():
    # This is the fix: no sheet read, no row count, no collision.
    ids = {generate_ulid() for _ in range(5000)}
    assert len(ids) == 5000


def test_ulids_are_lexicographically_sortable_by_creation_time():
    earlier = generate_ulid(timestamp_ms=1_700_000_000_000)
    later = generate_ulid(timestamp_ms=1_700_000_001_000)
    assert earlier < later


def test_decision_id_keeps_the_human_readable_prefix():
    did = generate_decision_id("2026-08-12")
    assert did.startswith("HRB-20260812-")
    assert len(did.split("-")[-1]) == 26
    assert generate_decision_id("2026-08-12") != did


# ---------------------------------------------------------------------------
# Row contract
# ---------------------------------------------------------------------------


def test_schema_is_exactly_25_columns_a_through_y():
    assert len(DECISION_LOG_COLUMNS) == 25
    assert DECISION_LOG_COLUMNS[0] == "Decision ID"
    assert DECISION_LOG_COLUMNS[-1] == "Prompt Version"


def test_formula_owned_columns_l_and_m_are_always_blank():
    values = sample_row(key_evidence="something").to_values()
    assert len(values) == 25
    for idx in FORMULA_OWNED_COLUMN_INDICES:
        assert values[idx] == "", (
            f"column {DECISION_LOG_COLUMNS[idx]!r} is formula-owned; writing text "
            "into it breaks the sheet's array-formula spill with #REF!"
        )
    assert DECISION_LOG_COLUMNS[11] == "Market Implied Probability"
    assert DECISION_LOG_COLUMNS[12] == "No-Vig Market Probability"


def test_cells_are_forced_single_line():
    row = sample_row(skeptical_case="line one\nline two\twith a tab")
    values = row.to_values()
    cell = values[DECISION_LOG_COLUMNS.index("Skeptical Case")]
    assert "\n" not in cell and "\t" not in cell
    assert cell == "line one; line two; with a tab"


def test_bet_is_never_a_valid_final_decision_type():
    assert "Bet" not in FINAL_DECISION_TYPES
    with pytest.raises(SheetsClientError):
        sample_row(final_decision_type="Bet").to_values()


@pytest.mark.parametrize("dt", FINAL_DECISION_TYPES)
def test_every_enum_value_is_accepted(dt):
    assert sample_row(final_decision_type=dt).to_values()


# ---------------------------------------------------------------------------
# Append semantics
# ---------------------------------------------------------------------------


def test_append_uses_values_append_with_insert_rows(client):
    c, api = client
    c.append_row(sample_row())
    (method, kwargs), = api.calls
    assert method == "append"
    assert kwargs["insertDataOption"] == "INSERT_ROWS"
    assert kwargs["valueInputOption"] == "RAW"
    assert kwargs["range"] == "Decision Log!A1"
    assert kwargs["spreadsheetId"] == "TEST_SHEET_ID"


def test_append_never_computes_a_row_index(client):
    c, api = client
    c.append_rows([sample_row(), sample_row(decision_id="HRB-2")])
    (_, kwargs), = api.calls
    # The range is a bare anchor -- the server picks the insertion point.
    assert kwargs["range"].endswith("!A1")
    assert len(kwargs["body"]["values"]) == 2


def test_client_exposes_no_update_or_delete_for_existing_rows():
    # The Decision Log is append-only: a prior row is never edited
    # retroactively (selection-gate-spec.md §6).
    public = {m for m in dir(DecisionLogSheetsClient) if not m.startswith("_")}
    assert "delete_row" not in public
    assert "update_row" not in public
    assert public >= {"append_row", "append_rows", "read_rows", "ensure_header"}


def test_read_rows_pads_short_rows_to_the_full_schema(client):
    c, _ = client
    rows = c.read_rows()
    assert len(rows) == 1
    assert set(rows[0]) == set(DECISION_LOG_COLUMNS)
    assert rows[0]["Decision ID"] == "HRB-1"
    assert rows[0]["Prompt Version"] == ""


def test_missing_spreadsheet_id_is_an_error_not_a_default(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_SPREADSHEET_ID", raising=False)
    with pytest.raises(SheetsClientError):
        DecisionLogSheetsClient()


# ---------------------------------------------------------------------------
# Credential selection (service-account key vs. OAuth refresh token)
#
# OAuth exists as a fallback for orgs that block service-account key
# creation outright (iam.disableServiceAccountKeyCreation) with no
# project-level override available to a non-org-admin account.
# ---------------------------------------------------------------------------


def test_missing_all_credentials_names_both_options(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "TEST_SHEET_ID")
    for var in (
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    c = DecisionLogSheetsClient()
    with pytest.raises(SheetsClientError) as exc_info:
        c._build_credentials()
    message = str(exc_info.value)
    assert "GOOGLE_APPLICATION_CREDENTIALS" in message
    assert "GOOGLE_OAUTH_CLIENT_ID" in message
    assert "GOOGLE_OAUTH_REFRESH_TOKEN" in message


def test_partial_oauth_credentials_do_not_count(monkeypatch):
    # All three or none -- two out of three must not silently pass.
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "TEST_SHEET_ID")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.delenv("GOOGLE_OAUTH_REFRESH_TOKEN", raising=False)
    c = DecisionLogSheetsClient()
    assert c._has_oauth_credentials is False
    with pytest.raises(SheetsClientError):
        c._build_credentials()


def test_oauth_credentials_used_when_no_service_account_path(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "TEST_SHEET_ID")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "refresh-token")
    c = DecisionLogSheetsClient()
    assert c._has_oauth_credentials is True
    creds = c._build_credentials()
    assert creds.client_id == "client-id"
    assert creds.client_secret == "client-secret"
    assert creds.refresh_token == "refresh-token"
    assert creds.token_uri == "https://oauth2.googleapis.com/token"


def test_service_account_path_preferred_over_oauth_when_both_set(monkeypatch, tmp_path):
    # Service-account key is the simpler, non-expiring option -- if someone
    # has both configured (e.g. mid-migration), prefer it deterministically
    # rather than picking one arbitrarily.
    import json

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    fake_key = tmp_path / "fake-service-account.json"
    fake_key.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "test",
                "private_key_id": "abc",
                "private_key": pem,
                "client_email": "test@test.iam.gserviceaccount.com",
                "client_id": "1",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
    )
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "TEST_SHEET_ID")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REFRESH_TOKEN", "refresh-token")
    c = DecisionLogSheetsClient(credentials_path=str(fake_key))
    creds = c._build_credentials()
    # google.oauth2.service_account.Credentials, not oauth2.credentials.Credentials
    assert type(creds).__module__.endswith("service_account")
