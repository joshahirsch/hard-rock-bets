"""Google Sheets Decision Log client.

Writes Decision Log rows to the tracker spreadsheet using the real Google
Sheets API v4.

Two deliberate departures from v1, both of which caused real production bugs:

1. **``values.append`` instead of manual next-row computation.** v1 read the
   whole sheet, counted rows, and computed the next row index by hand. Two
   firings overlapping (or a single firing retrying) raced and overwrote each
   other. ``spreadsheets.values.append`` with
   ``insertDataOption=INSERT_ROWS`` is a single atomic server-side append --
   the API picks the row, not us.

2. **ULID Decision IDs instead of "read the sheet, count rows, add one".**
   The old ``HRB-YYYYMMDD-NN`` scheme derived ``NN`` from a row count, which
   collided for exactly the same reason. A ULID is generated locally, is
   globally unique without reading anything, and is lexicographically sortable
   by creation time -- so the log still sorts chronologically. The legacy
   human-readable prefix is kept in front of it for continuity.

Columns A-Y are the Phase 1 Decision Log schema, unchanged since.

Sheet-owned columns: L (Market Implied Probability) and M (No-Vig Market
Probability) are computed by self-expanding array formulas in the sheet and
MUST be written as empty strings. Putting text in them breaks the spill with
``#REF!`` (market-math-spec.md §6).
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

#: Phase 1 Decision Log schema, columns A-Y in this exact order.
DECISION_LOG_COLUMNS: Sequence[str] = (
    "Decision ID",                    # A
    "Run Date",                       # B
    "Event",                          # C
    "Market",                         # D
    "Side",                           # E
    "Current Reference Line",         # F
    "Current Reference Odds",         # G
    "Odds Source",                    # H
    "Retrieval Timestamp ET",         # I
    "Opposing Side Line",             # J
    "Opposing Side Odds",             # K
    "Market Implied Probability",     # L  <- SHEET-OWNED, always blank on write
    "No-Vig Market Probability",      # M  <- SHEET-OWNED, always blank on write
    "Key Evidence",                   # N
    "Evidence Sources",               # O
    "Evidence Publication Times",     # P
    "Priced-In Assessment",           # Q
    "Missing Information",            # R
    "Skeptical Case",                 # S
    "Invalidation Conditions",        # T
    "Correlation or Exposure Note",   # U
    "Gate Outcome",                   # V
    "Final Decision Type",            # W
    "Reason for Pass",                # X
    "Prompt Version",                 # Y
)

#: Zero-based indices of the two formula-owned columns.
FORMULA_OWNED_COLUMN_INDICES = (11, 12)  # L, M

DEFAULT_TAB_NAME = "Decision Log"
SPREADSHEET_ID_ENV_VAR = "GOOGLE_SHEETS_SPREADSHEET_ID"
#: Was documented in .env.example / DEPLOY.md and set as a real Fly secret
#: from the start, but never actually read anywhere until now -- tab_name
#: silently always fell back to DEFAULT_TAB_NAME regardless of this var.
TAB_NAME_ENV_VAR = "GOOGLE_SHEETS_DECISION_LOG_TAB"
CREDENTIALS_ENV_VAR = "GOOGLE_APPLICATION_CREDENTIALS"
#: OAuth alternative to a service-account key -- see
#: scripts/get_oauth_refresh_token.py. Used only if CREDENTIALS_ENV_VAR isn't
#: set. Exists because some Google Cloud orgs enforce
#: `iam.disableServiceAccountKeyCreation` with no project-level override
#: available to a non-org-admin account -- a real failure mode hit standing
#: up this project's own org, not a hypothetical one.
OAUTH_CLIENT_ID_ENV_VAR = "GOOGLE_OAUTH_CLIENT_ID"
OAUTH_CLIENT_SECRET_ENV_VAR = "GOOGLE_OAUTH_CLIENT_SECRET"
OAUTH_REFRESH_TOKEN_ENV_VAR = "GOOGLE_OAUTH_REFRESH_TOKEN"
OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

#: ``Final Decision Type`` enum. "Bet" is never valid in shadow mode.
FINAL_DECISION_TYPES = (
    "Pass",
    "Research Candidate",
    "Revalidation Required",
    "Market-Efficiency Candidate",
    "Market-Efficiency Watch",
)

_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class SheetsClientError(RuntimeError):
    pass


def _describe_api_exception(exc: Exception) -> str:
    """Best-effort human-readable detail for a googleapiclient/google-auth error.

    ``str(exc)`` alone is unreliable here: a ``googleapiclient.errors.HttpError``
    can stringify to almost nothing (e.g. just "HttpError: ") depending on the
    google-api-python-client version and whether the response had a JSON body,
    even though the exception carries a real HTTP status code and Google's own
    error body on ``.resp``/``.content``. Pull those out explicitly when present
    so a caller sees e.g. "HTTP 403: PERMISSION_DENIED ..." instead of nothing.
    """
    parts = [type(exc).__name__]
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is not None:
        parts.append(f"HTTP {status}")
    content = getattr(exc, "content", None)
    if content:
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        parts.append(str(content).strip())
    text = str(exc).strip()
    if text and text not in parts:
        parts.append(text)
    return ": ".join(parts) if len(parts) > 1 else f"{parts[0]} (no further detail available)"


# ---------------------------------------------------------------------------
# ULID
# ---------------------------------------------------------------------------


def generate_ulid(timestamp_ms: Optional[int] = None) -> str:
    """Generate a ULID: 48-bit ms timestamp + 80 bits of randomness, Crockford b32.

    Sortable by creation time, unique without reading anything. This replaces
    v1's read-count-increment Decision ID scheme, which produced real
    collisions when two firings overlapped.
    """
    ts = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    if not (0 <= ts < (1 << 48)):
        raise ValueError("timestamp out of ULID range")
    rand = secrets.randbits(80)
    value = (ts << 80) | rand
    chars = []
    for i in range(25, -1, -1):
        chars.append(_CROCKFORD32[(value >> (i * 5)) & 0x1F])
    return "".join(chars)


def generate_decision_id(run_date: str, prefix: str = "HRB") -> str:
    """``HRB-YYYYMMDD-<ULID>`` -- human-scannable prefix, collision-free suffix.

    ``run_date`` is ``YYYY-MM-DD``.
    """
    return f"{prefix}-{run_date.replace('-', '')}-{generate_ulid()}"


# ---------------------------------------------------------------------------
# Row model
# ---------------------------------------------------------------------------


@dataclass
class DecisionLogRow:
    """One Decision Log row. Field order matches ``DECISION_LOG_COLUMNS``."""

    decision_id: str
    run_date: str
    event: str
    market: str
    side: str
    current_reference_line: str = ""
    current_reference_odds: str = ""
    odds_source: str = ""
    retrieval_timestamp_et: str = ""
    opposing_side_line: str = ""
    opposing_side_odds: str = ""
    key_evidence: str = ""
    evidence_sources: str = ""
    evidence_publication_times: str = ""
    priced_in_assessment: str = ""
    missing_information: str = ""
    skeptical_case: str = ""
    invalidation_conditions: str = ""
    correlation_or_exposure_note: str = ""
    gate_outcome: str = ""
    final_decision_type: str = "Pass"
    reason_for_pass: str = ""
    prompt_version: str = ""

    def to_values(self) -> List[str]:
        """Render as an A-Y value list with L and M left blank for the sheet.

        Every cell is forced single-line: tabs and newlines are replaced with
        "; " because the log's cells are one-line by convention.
        """
        if self.final_decision_type not in FINAL_DECISION_TYPES:
            raise SheetsClientError(
                f"invalid Final Decision Type {self.final_decision_type!r}; "
                f"expected one of {FINAL_DECISION_TYPES}"
            )
        values = [
            self.decision_id,
            self.run_date,
            self.event,
            self.market,
            self.side,
            self.current_reference_line,
            self.current_reference_odds,
            self.odds_source,
            self.retrieval_timestamp_et,
            self.opposing_side_line,
            self.opposing_side_odds,
            "",  # L -- formula-owned
            "",  # M -- formula-owned
            self.key_evidence,
            self.evidence_sources,
            self.evidence_publication_times,
            self.priced_in_assessment,
            self.missing_information,
            self.skeptical_case,
            self.invalidation_conditions,
            self.correlation_or_exposure_note,
            self.gate_outcome,
            self.final_decision_type,
            self.reason_for_pass,
            self.prompt_version,
        ]
        assert len(values) == len(DECISION_LOG_COLUMNS)
        return [_single_line(v) for v in values]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _single_line(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\t", "; ").replace("\r", " ").replace("\n", "; ").strip()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class DecisionLogSheetsClient:
    """Append-only Decision Log writer.

    The Decision Log is append-only by project convention: a prior row is never
    edited retroactively to reflect a later day's conflict
    (selection-gate-spec.md §6). This class exposes no update or delete method
    on purpose.
    """

    def __init__(
        self,
        spreadsheet_id: Optional[str] = None,
        tab_name: Optional[str] = None,
        credentials_path: Optional[str] = None,
        oauth_client_id: Optional[str] = None,
        oauth_client_secret: Optional[str] = None,
        oauth_refresh_token: Optional[str] = None,
        service: Any = None,
    ) -> None:
        self.spreadsheet_id = spreadsheet_id or os.environ.get(SPREADSHEET_ID_ENV_VAR)
        if not self.spreadsheet_id:
            raise SheetsClientError(
                f"missing spreadsheet id: set {SPREADSHEET_ID_ENV_VAR} in the "
                "environment (never commit the real id -- see .env.example)"
            )
        self.tab_name = (
            tab_name or os.environ.get(TAB_NAME_ENV_VAR) or DEFAULT_TAB_NAME
        )
        self.credentials_path = credentials_path or os.environ.get(CREDENTIALS_ENV_VAR)
        self.oauth_client_id = oauth_client_id or os.environ.get(OAUTH_CLIENT_ID_ENV_VAR)
        self.oauth_client_secret = oauth_client_secret or os.environ.get(
            OAUTH_CLIENT_SECRET_ENV_VAR
        )
        self.oauth_refresh_token = oauth_refresh_token or os.environ.get(
            OAUTH_REFRESH_TOKEN_ENV_VAR
        )
        self._service = service

    # -- wiring ------------------------------------------------------------
    def _values_api(self):
        if self._service is None:
            try:
                self._service = self._build_service()
            except SheetsClientError:
                raise
            except Exception as exc:
                # Anything other than our own SheetsClientError -- e.g. a
                # discovery-doc fetch failure -- was previously escaping
                # uncaught and surfacing as a bare 500 with no detail. Wrap it
                # so callers (service.py's 503 handler) see what actually
                # went wrong.
                raise SheetsClientError(
                    f"failed to build Sheets API client: {_describe_api_exception(exc)}"
                ) from exc
        return self._service.spreadsheets().values()

    @property
    def _has_oauth_credentials(self) -> bool:
        return bool(
            self.oauth_client_id and self.oauth_client_secret and self.oauth_refresh_token
        )

    def _build_service(self):  # pragma: no cover - requires real credentials
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise SheetsClientError(
                "google-api-python-client and google-auth are required; "
                "`pip install -r requirements.txt`"
            ) from exc
        creds = self._build_credentials()
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    def _build_credentials(self):  # pragma: no cover - requires real credentials
        # Prefer a service-account key when one is configured: it's the
        # simpler, non-expiring option and was the original design. Fall back
        # to OAuth user credentials (see scripts/get_oauth_refresh_token.py)
        # for environments where Google Cloud's own org policy blocks service
        # account key creation and there's no admin available to override it.
        if self.credentials_path:
            from google.oauth2 import service_account

            return service_account.Credentials.from_service_account_file(
                self.credentials_path, scopes=[SHEETS_SCOPE]
            )
        if self._has_oauth_credentials:
            from google.oauth2.credentials import Credentials as OAuthCredentials

            return OAuthCredentials(
                token=None,
                refresh_token=self.oauth_refresh_token,
                token_uri=OAUTH_TOKEN_URI,
                client_id=self.oauth_client_id,
                client_secret=self.oauth_client_secret,
                scopes=[SHEETS_SCOPE],
            )
        raise SheetsClientError(
            f"missing Google credentials: set either {CREDENTIALS_ENV_VAR} (a "
            f"service-account key file path) or all three of "
            f"{OAUTH_CLIENT_ID_ENV_VAR}/{OAUTH_CLIENT_SECRET_ENV_VAR}/"
            f"{OAUTH_REFRESH_TOKEN_ENV_VAR} (see "
            "scripts/get_oauth_refresh_token.py to generate a refresh token)"
        )

    # -- writes ------------------------------------------------------------
    def append_rows(self, rows: Sequence[DecisionLogRow]) -> Dict[str, Any]:
        """Atomically append rows with ``spreadsheets.values.append``.

        ``insertDataOption=INSERT_ROWS`` makes the server choose the insertion
        point, so concurrent firings cannot collide. We never compute a row
        index ourselves.

        ``valueInputOption=RAW`` keeps evidence text from being reinterpreted
        as a formula or a date by the sheet.
        """
        if not rows:
            return {"updates": {"updatedRows": 0}}
        body = {"values": [r.to_values() for r in rows]}
        request = self._values_api().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.tab_name}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            includeValuesInResponse=False,
            body=body,
        )
        return self._execute(request)

    def append_row(self, row: DecisionLogRow) -> Dict[str, Any]:
        return self.append_rows([row])

    # -- reads -------------------------------------------------------------
    def read_rows(self, max_rows: int = 5000) -> List[Dict[str, str]]:
        """Read back the Decision Log as dicts keyed by column name.

        Used for the C1-C3 open-exposure read-back (selection-gate-spec.md §6
        step 1). Note the spec's own limitation §9 item 5: this read-back is
        the single point of failure for cross-day correlation checks.
        """
        last_col = _column_letter(len(DECISION_LOG_COLUMNS))
        request = self._values_api().get(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.tab_name}!A2:{last_col}{max_rows + 1}",
            valueRenderOption="UNFORMATTED_VALUE",
        )
        values = self._execute(request).get("values", [])
        out: List[Dict[str, str]] = []
        for raw in values:
            padded = list(raw) + [""] * (len(DECISION_LOG_COLUMNS) - len(raw))
            out.append(dict(zip(DECISION_LOG_COLUMNS, padded)))
        return out

    def ensure_header(self) -> Dict[str, Any]:
        """Write the A-Y header row. Safe to call on a fresh, empty tab only."""
        request = self._values_api().update(
            spreadsheetId=self.spreadsheet_id,
            range=f"{self.tab_name}!A1",
            valueInputOption="RAW",
            body={"values": [list(DECISION_LOG_COLUMNS)]},
        )
        return self._execute(request)

    @staticmethod
    def _execute(request) -> Dict[str, Any]:
        """Run a googleapiclient request, translating any failure.

        The Sheets API only raises on ``.execute()`` -- auth refresh failures
        (bad/revoked refresh token, e.g. ``google.auth.exceptions.RefreshError``)
        and API errors (e.g. ``googleapiclient.errors.HttpError`` for a 403/404
        from Google) both surface here. Previously neither was caught anywhere,
        so they escaped as an unhandled exception and the service returned a
        bare 500 with no detail. Wrapping them as ``SheetsClientError`` lets
        service.py's existing 502 handler surface the real reason instead.
        """
        try:
            return request.execute()
        except Exception as exc:
            raise SheetsClientError(
                f"Google Sheets API call failed: {_describe_api_exception(exc)}"
            ) from exc


def _column_letter(index_1_based: int) -> str:
    letters = ""
    n = index_1_based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
