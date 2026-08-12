"""Correlation / Exposure Control C1-C3.

Faithful port of ``claude/selection-gate-spec.md`` §5-§7 (Phase 5,
AUTHORITATIVE). Explicitly NOT part of the 2026-08-11 restructuring and
unchanged by it -- a lower G1 floor makes C1-C3 MORE load-bearing, not less.

Shared across both pathways: ``claude/market-efficiency-candidate-spec.md`` §6
reads C1's "regardless of market type" as also meaning regardless of *pathway*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Named constants -- selection-gate-spec.md §5/§6.
# §9 item 3: N = 1 is the only cap value defined; deliberately conservative.
# ---------------------------------------------------------------------------

#: C1 -- at most this many gate-CLEARED candidates per team per day.
C1_SAME_DAY_SAME_TEAM_CAP = 1
#: C2 -- at most this many OPEN entries carrying a given named thesis.
C2_SAME_THESIS_CAP = 1
#: C3 -- at most this many OPEN entries against a given series.
C3_SAME_SERIES_CAP = 1
#: §5 -- "series" is operationalized as a rolling window of this many days.
SERIES_WINDOW_DAYS = 7

EXPOSURE_RULE_ORDER = ("C1", "C2", "C3")


class ExposureOutcome(str, Enum):
    CLEAR = "CLEAR"
    CAPPED = "CAPPED"


@dataclass
class OpenEntry:
    """An OPEN Decision Log entry (§5).

    "A Decision Log row with ``Final Decision Type = Research Candidate`` whose
    event date/time has not yet passed as of the current run." Openness ignores
    win/loss/push entirely. ``Market-Efficiency Candidate`` rows count too, per
    market-efficiency-candidate-spec.md §6's shared-exposure reading.
    """

    decision_id: str
    event: str
    teams: Tuple[str, ...]
    thesis: str
    event_datetime: datetime
    run_date: date
    final_decision_type: str = "Research Candidate"

    def series_key(self) -> Tuple[str, ...]:
        return series_key(self.teams)

    def is_open(self, now: datetime) -> bool:
        return self.event_datetime > now


@dataclass
class Candidate:
    """A candidate that reached a gate evaluation this run (§6 preamble)."""

    decision_id: str
    event: str
    teams: Tuple[str, ...]
    thesis: str
    event_datetime: datetime
    conservative_edge_pp: float
    #: 0-based position in today's write-up; earlier wins C1's second tie-break.
    writeup_position: int
    gate_cleared: bool = True
    pathway: str = "informational"


@dataclass
class ExposureResult:
    decision_id: str
    outcome: ExposureOutcome
    deciding_rule: Optional[str]
    conflicting_decision_id: Optional[str]
    note: str
    reason_for_pass: Optional[str]
    final_decision_type: str


def normalize_team(name: str) -> str:
    """Case/whitespace-insensitive team key. §9 item 2: exact string matching."""
    return " ".join(name.strip().lower().split())


def series_key(teams: Sequence[str]) -> Tuple[str, ...]:
    """Order-independent key for "the same two teams" (§5)."""
    return tuple(sorted(normalize_team(t) for t in teams))


def same_series(a: OpenEntry, b_teams: Sequence[str], a_dt: datetime, b_dt: datetime) -> bool:
    """Same two teams within a rolling 7-calendar-day window (§5)."""
    if a.series_key() != series_key(b_teams):
        return False
    return abs((b_dt.date() - a_dt.date()).days) <= SERIES_WINDOW_DAYS


def apply_exposure_control(
    candidates: Sequence[Candidate],
    open_entries: Sequence[OpenEntry],
    now: datetime,
) -> List[ExposureResult]:
    """Apply C1 -> C2 -> C3, in that order, to today's gate-evaluated candidates.

    Precedence (§6 item 5): when the conflict is against a **prior day's**
    still-open entry, today's new candidate is always the one capped -- the
    Decision Log is append-only and a historical row is never edited. Within a
    single day's batch, C1's tie-break applies: higher conservative-end edge
    wins; if tied, the earlier write-up position wins.
    """
    still_open = [e for e in open_entries if e.is_open(now)]
    prior_open = [e for e in still_open if e.run_date < now.date()]
    today_open = [e for e in still_open if e.run_date >= now.date()]

    results: List[ExposureResult] = []

    # Only gate-CLEARED candidates can occupy exposure. §6 applies to every
    # candidate that reached a gate evaluation, so non-cleared ones are still
    # reported -- they simply cannot conflict with anything.
    cleared = [c for c in candidates if c.gate_cleared]
    # C1 tie-break ordering: higher conservative-end edge, then earlier position.
    ranked = sorted(cleared, key=lambda c: (-c.conservative_edge_pp, c.writeup_position))

    # Teams already occupied today by an entry logged today (e.g. the morning
    # firing) plus teams claimed as we walk this batch.
    claimed_teams: Dict[str, str] = {}
    for e in today_open:
        for t in e.teams:
            claimed_teams.setdefault(normalize_team(t), e.decision_id)
    claimed_theses: Dict[str, str] = {}
    claimed_series: Dict[Tuple[str, ...], str] = {}

    verdicts: Dict[str, ExposureResult] = {}

    for cand in candidates:
        if not cand.gate_cleared:
            verdicts[cand.decision_id] = ExposureResult(
                cand.decision_id,
                ExposureOutcome.CLEAR,
                None,
                None,
                "N/A — did not clear the gate; no exposure claimed.",
                None,
                "Pass",
            )

    for cand in ranked:
        capped: Optional[ExposureResult] = None

        # -- C1: same-day same-team cap ------------------------------------
        for team in cand.teams:
            key = normalize_team(team)
            holder = claimed_teams.get(key)
            if holder is not None:
                capped = ExposureResult(
                    cand.decision_id,
                    ExposureOutcome.CAPPED,
                    "C1",
                    holder,
                    f"C1 — same-team exposure cap vs {holder} (team: {team}).",
                    f"Exposure control C1 — same-day same-team cap "
                    f"(max {C1_SAME_DAY_SAME_TEAM_CAP} per team per day); "
                    f"{holder} already open on {team} today.",
                    "Pass",
                )
                break

        # -- C2: same-thesis cap -------------------------------------------
        if capped is None:
            thesis_key = cand.thesis.strip().lower()
            holder = claimed_theses.get(thesis_key)
            if holder is None:
                for e in still_open:
                    if e.thesis.strip().lower() == thesis_key:
                        holder = e.decision_id
                        break
            if holder is not None:
                capped = ExposureResult(
                    cand.decision_id,
                    ExposureOutcome.CAPPED,
                    "C2",
                    holder,
                    f"C2 — same-thesis cap vs {holder} (thesis: {cand.thesis!r}).",
                    f"Exposure control C2 — same-thesis cap "
                    f"(max {C2_SAME_THESIS_CAP} open entry); {holder} already open "
                    f"with thesis {cand.thesis!r}.",
                    "Pass",
                )

        # -- C3: same-series cap -------------------------------------------
        if capped is None:
            skey = series_key(cand.teams)
            holder = claimed_series.get(skey)
            if holder is None:
                for e in still_open:
                    if same_series(e, cand.teams, e.event_datetime, cand.event_datetime):
                        holder = e.decision_id
                        break
            if holder is not None:
                capped = ExposureResult(
                    cand.decision_id,
                    ExposureOutcome.CAPPED,
                    "C3",
                    holder,
                    f"C3 — same-series cap vs {holder} "
                    f"(series: {' vs '.join(skey)}, rolling {SERIES_WINDOW_DAYS}-day window).",
                    f"Exposure control C3 — same-series cap "
                    f"(max {C3_SAME_SERIES_CAP} open entry within a rolling "
                    f"{SERIES_WINDOW_DAYS}-day window); {holder} already open on this series.",
                    "Pass",
                )

        if capped is not None:
            verdicts[cand.decision_id] = capped
            continue

        for team in cand.teams:
            claimed_teams[normalize_team(team)] = cand.decision_id
        claimed_theses[cand.thesis.strip().lower()] = cand.decision_id
        claimed_series[series_key(cand.teams)] = cand.decision_id
        final_type = (
            "Market-Efficiency Candidate"
            if cand.pathway == "market_efficiency"
            else "Research Candidate"
        )
        verdicts[cand.decision_id] = ExposureResult(
            cand.decision_id,
            ExposureOutcome.CLEAR,
            None,
            None,
            "No conflicting exposure",
            None,
            final_type,
        )

    for cand in candidates:
        results.append(verdicts[cand.decision_id])
    # Silence the unused-name warning while keeping the §6 step-1 split explicit.
    _ = prior_open
    return results


def open_exposure_note(open_entries: Sequence[OpenEntry], now: datetime) -> str:
    """Build the mandatory OPEN EXPOSURE section (§7), even on a thin day."""
    rows = [e for e in open_entries if e.is_open(now)]
    if not rows:
        return "OPEN EXPOSURE\nNo open research-candidate exposure."
    lines = ["OPEN EXPOSURE"]
    for e in sorted(rows, key=lambda x: x.event_datetime):
        lines.append(
            f"- {e.decision_id} | {e.event} | teams: {', '.join(e.teams)} | "
            f"series: {' vs '.join(e.series_key())} | thesis: {e.thesis} | "
            f"logged: {e.run_date.isoformat()}"
        )
    return "\n".join(lines)


def not_evaluated_exposure_note(why: str) -> str:
    """§8: the Correlation or Exposure Note cell for a non-qualifying row."""
    return f"N/A — {why}"
