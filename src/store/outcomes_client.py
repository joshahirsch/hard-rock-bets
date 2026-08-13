"""Google Sheets Outcomes log client -- the data the learning engine learns from.

Added 2026-08-13 as part of the v3 season-aware-triggers/learning-engine
rebuild (``claude/v3-learning-engine-proposal-2026-08-13.md``). The Decision
Log records a research *classification*; it has never recorded what actually
happened -- did Josh place the bet, at what stake and price, and did it win,
lose, push, or void. Without that, there is nothing for a calibration job to
learn from.

**Why a separate tab, not new columns on the Decision Log:** the Decision Log
is append-only by explicit design (``DecisionLogSheetsClient`` exposes no
update/delete method on purpose -- selection-gate-spec.md §6). An outcome is
learned *after* the original row was written, sometimes days later once a game
has finished -- recording it requires either mutating the original row
(breaking the append-only guarantee that exists specifically to keep the log
tamper-evident and race-free) or appending a new, separate record linked by
``decision_id``. This module does the latter: one Outcomes-tab row per
placed-or-resolved bet, appended exactly the same atomic, ID-generating way
the Decision Log itself is written -- never a computed row index, never an
in-place edit.

An Outcomes row is created in two stages, both appends, never edits:

1. Josh confirms a bet was placed (``placed=Y``, stake, price) -- appended as
   soon as he confirms it, ``result="PENDING"``.
2. Once the game has concluded, a second, separate row is appended recording
   the resolution (``result`` = WIN/LOSS/PUSH/VOID, ``resolved_at``). The
   calibration job reads the WHOLE Outcomes tab and, for a given
   ``decision_id``, takes the **last** row with a non-PENDING result as that
   bet's true outcome -- this mirrors the Decision Log's own append-only
   philosophy instead of introducing update semantics nowhere else in this
   codebase has.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence

from src.store.sheets_client import (
    CREDENTIALS_ENV_VAR,
    OAUTH_CLIENT_ID_ENV_VAR,
    OAUTH_CLIENT_SECRET_ENV_VAR,
    OAUTH_REFRESH_TOKEN_ENV_VAR,
    SPREADSHEET_ID_ENV_VAR,
    SheetsClientError,
    _a1_range,
    _column_letter,
    _normalize_tab_name,
    _single_line,
    generate_ulid,
)

#: Outcomes tab schema, columns A-J in this exact order.
OUTCOME_COLUMNS: Sequence[str] = (
    "Outcome ID",        # A
    "Decision ID",        # B -- FK into the Decision Log, not enforced by Sheets
    "Placed",              # C -- Y/N
    "Placed Stake USD",   # D
    "Placed Odds",         # E -- the actual slip price, may differ from the
                           #      Decision Log's reference price
    "Placed At ET",        # F
    "Result",               # G -- WIN / LOSS / PUSH / VOID / PENDING
    "Resolved At ET",       # H
    "Closing Line Odds",   # I -- for CLV; optional, filled when known
    "Notes",                # J
)

DEFAULT_TAB_NAME = "Outcomes"
#: Separate env var from the Decision Log's tab-name var so the two tabs can
#: be renamed independently if Josh ever wants to.
TAB_NAME_ENV_VAR = "GOOGLE_SHEETS_OUTCOMES_TAB"

#: Valid ``Result`` values. "PENDING" is the only value valid on a
#: placement-only row (the row created the moment Josh confirms he placed a
#: bet, before the game has finished).
RESULT_VALUES = ("PENDING", "WIN", "LOSS", "PUSH", "VOID")


def generate_outcome_id(prefix: str = "OUT") -> str:
    """``OUT-<ULID>`` -- same collision-free scheme as Decision IDs."""
    return f"{prefix}-{generate_ulid()}"


@dataclass
class OutcomeRow:
    """One Outcomes-tab row. Field order matches ``OUTCOME_COLUMNS``."""

    outcome_id: str
    decision_id: str
    placed: str  # "Y" | "N"
    placed_stake_usd: str = ""
    placed_odds: str = ""
    placed_at_et: str = ""
    result: str = "PENDING"
    resolved_at_et: str = ""
    closing_line_odds: str = ""
    notes: str = ""

    def to_values(self) -> List[str]:
        if self.placed not in ("Y", "N"):
            raise SheetsClientError(
                f"invalid 'placed' value {self.placed!r}; expected 'Y' or 'N'"
            )
        if self.result not in RESULT_VALUES:
            raise SheetsClientError(
                f"invalid result {self.result!r}; expected one of {RESULT_VALUES}"
            )
        values = [
            self.outcome_id,
            self.decision_id,
            self.placed,
            self.placed_stake_usd,
            self.placed_odds,
            self.placed_at_et,
            self.result,
            self.resolved_at_et,
            self.closing_line_odds,
            self.notes,
        ]
        assert len(values) == len(OUTCOME_COLUMNS)
        return [_single_line(v) for v in values]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OutcomesSheetsClient:
    """Append-only Outcomes writer -- same atomic-append discipline as the
    Decision Log, deliberately exposing no update or delete method.

    Shares credentials/spreadsheet wiring with ``DecisionLogSheetsClient`` by
    construction (same env vars, same spreadsheet) but writes to a different
    tab, so the two can be built independently without one requiring the
    other.
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
        import os

        self.spreadsheet_id = spreadsheet_id or os.environ.get(SPREADSHEET_ID_ENV_VAR)
        if not self.spreadsheet_id:
            raise SheetsClientError(
                f"missing spreadsheet id: set {SPREADSHEET_ID_ENV_VAR} in the "
                "environment (never commit the real id -- see .env.example)"
            )
        self.tab_name = _normalize_tab_name(
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

    def _values_api(self):
        if self._service is None:
            try:
                self._service = self._build_service()
            except SheetsClientError:
                raise
            except Exception as exc:
                from src.store.sheets_client import _describe_api_exception

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
        if self.credentials_path:
            from google.oauth2 import service_account

            return service_account.Credentials.from_service_account_file(
                self.credentials_path,
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
        if self._has_oauth_credentials:
            from google.oauth2.credentials import Credentials as OAuthCredentials
            from src.store.sheets_client import OAUTH_TOKEN_URI

            return OAuthCredentials(
                token=None,
                refresh_token=self.oauth_refresh_token,
                token_uri=OAUTH_TOKEN_URI,
                client_id=self.oauth_client_id,
                client_secret=self.oauth_client_secret,
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
        raise SheetsClientError(
            f"missing Google credentials: set either {CREDENTIALS_ENV_VAR} (a "
            f"service-account key file path) or all three of "
            f"{OAUTH_CLIENT_ID_ENV_VAR}/{OAUTH_CLIENT_SECRET_ENV_VAR}/"
            f"{OAUTH_REFRESH_TOKEN_ENV_VAR}"
        )

    # -- writes --------------------------------------------------------------
    def append_rows(self, rows: Sequence[OutcomeRow]) -> Dict[str, Any]:
        if not rows:
            return {"updates": {"updatedRows": 0}}
        body = {"values": [r.to_values() for r in rows]}
        request = self._values_api().append(
            spreadsheetId=self.spreadsheet_id,
            range=_a1_range(self.tab_name, "A1"),
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            includeValuesInResponse=False,
            body=body,
        )
        return self._execute(request)

    def append_row(self, row: OutcomeRow) -> Dict[str, Any]:
        return self.append_rows([row])

    # -- reads -----------------------------------------------------------
    def read_rows(self, max_rows: int = 20000) -> List[Dict[str, str]]:
        last_col = _column_letter(len(OUTCOME_COLUMNS))
        request = self._values_api().get(
            spreadsheetId=self.spreadsheet_id,
            range=_a1_range(self.tab_name, f"A2:{last_col}{max_rows + 1}"),
            valueRenderOption="UNFORMATTED_VALUE",
        )
        values = self._execute(request).get("values", [])
        out: List[Dict[str, str]] = []
        for raw in values:
            padded = list(raw) + [""] * (len(OUTCOME_COLUMNS) - len(raw))
            out.append(dict(zip(OUTCOME_COLUMNS, padded)))
        return out

    def ensure_header(self) -> Dict[str, Any]:
        """Write the A-J header row. Safe to call on a fresh, empty tab only."""
        request = self._values_api().update(
            spreadsheetId=self.spreadsheet_id,
            range=_a1_range(self.tab_name, "A1"),
            valueInputOption="RAW",
            body={"values": [list(OUTCOME_COLUMNS)]},
        )
        return self._execute(request)

    @staticmethod
    def _execute(request) -> Dict[str, Any]:
        from src.store.sheets_client import _describe_api_exception

        try:
            return request.execute()
        except Exception as exc:
            raise SheetsClientError(
                f"Google Sheets API call failed: {_describe_api_exception(exc)}"
            ) from exc
