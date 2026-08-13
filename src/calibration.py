"""Calibration report: the learning engine's read-only analysis step.

Added 2026-08-13 (claude/v3-learning-engine-proposal-2026-08-13.md §4b).

Deliberately NOT an auto-tuner. This module reads Decision Log + Outcomes
history and produces a cited report of what actually happened -- hit rate,
by Final Decision Type and (where the Gate Outcome text makes it parseable)
by deciding rule. It never writes to config.yaml itself. A human (Josh)
reads the report and decides whether a threshold change is warranted, then
that change is a normal, git-diffable config edit -- same discipline as every
other number in this codebase, never a silent runtime adjustment.

**Honest limitation, stated plainly (matches this project's documentation
culture in every other spec file):** the Decision Log schema (columns A-Y)
stores narrative text, not structured numbers -- there is no dedicated
"sport" column, no stored conservative-edge-pp value, no structured
"cleared via Tier-1 anchor vs Tier-2 corroboration" flag. This report computes
what the existing schema actually supports reliably (grouping by Final
Decision Type, and by deciding rule where the Gate Outcome column's
"NOT CLEARED — G1: ..." text is parseable) and says so explicitly rather than
inventing false precision. A future iteration could have /decision-log/append
also write select structured fields to a side table purely for calibration
use, without touching the human-facing sheet's column layout -- not built
yet, flagged here as a known next step rather than assumed done.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Below this many resolved (non-PENDING) placed bets, any hit-rate number is
#: explicitly flagged as too small to act on. Mirrors the sub-50-sample
#: convention already used elsewhere in this project (selection-gate-spec.md
#: §9 item 3) -- kept smaller here (20) because volume will be low for a long
#: time and Josh should still see the number, just clearly caveated.
MIN_SAMPLE_FOR_ANY_CONCLUSION = 20

#: Where a real conclusion (not just "watch this") becomes reasonable.
MIN_SAMPLE_FOR_A_REAL_CONCLUSION = 50

_DECIDING_RULE_RE = re.compile(r"NOT CLEARED\s*[—-]\s*(G[1-6])")
_CLEARED_RE = re.compile(r"^CLEARED", re.IGNORECASE)


@dataclass
class BucketStats:
    label: str
    placed: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    voids: int = 0
    pending: int = 0
    total_staked_usd: float = 0.0
    total_return_usd: float = 0.0
    clv_samples: List[float] = field(default_factory=list)

    @property
    def decided(self) -> int:
        return self.wins + self.losses

    @property
    def hit_rate(self) -> Optional[float]:
        if self.decided == 0:
            return None
        return self.wins / self.decided

    @property
    def roi(self) -> Optional[float]:
        if self.total_staked_usd <= 0:
            return None
        return (self.total_return_usd - self.total_staked_usd) / self.total_staked_usd

    @property
    def sample_note(self) -> str:
        if self.decided < MIN_SAMPLE_FOR_ANY_CONCLUSION:
            return (
                f"N={self.decided} — too small for any conclusion; watch, "
                "don't act."
            )
        if self.decided < MIN_SAMPLE_FOR_A_REAL_CONCLUSION:
            return f"N={self.decided} — an early read, not yet a real conclusion."
        return f"N={self.decided} — large enough for a real read."

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "placed": self.placed,
            "wins": self.wins,
            "losses": self.losses,
            "pushes": self.pushes,
            "voids": self.voids,
            "pending": self.pending,
            "decided": self.decided,
            "hit_rate": self.hit_rate,
            "total_staked_usd": round(self.total_staked_usd, 2),
            "total_return_usd": round(self.total_return_usd, 2),
            "roi": self.roi,
            "avg_clv_pp": (
                round(sum(self.clv_samples) / len(self.clv_samples), 3)
                if self.clv_samples
                else None
            ),
            "sample_note": self.sample_note,
        }


def _american_to_payout_multiplier(odds: Optional[float]) -> Optional[float]:
    """Return (total return per $1 staked) for a WIN, or None if unusable."""
    if odds is None:
        return None
    try:
        odds = float(odds)
    except (TypeError, ValueError):
        return None
    if odds == 0:
        return None
    if odds > 0:
        return 1.0 + odds / 100.0
    return 1.0 + 100.0 / abs(odds)


def _resolve_latest_outcomes(outcome_rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """One resolved (or latest-known) outcome per decision_id.

    Per outcomes_client.py's module docstring: a bet may have a PENDING
    placement row and a later resolution row, both real appends. This keeps,
    per decision_id, the last row with a non-PENDING result if one exists,
    else the last row seen at all (so a still-open placed bet still shows up
    as "placed, pending" rather than silently disappearing).
    """
    latest: Dict[str, Dict[str, str]] = {}
    latest_resolved: Dict[str, Dict[str, str]] = {}
    for row in outcome_rows:
        decision_id = (row.get("Decision ID") or "").strip()
        if not decision_id:
            continue
        latest[decision_id] = row
        if (row.get("Result") or "").strip().upper() not in ("", "PENDING"):
            latest_resolved[decision_id] = row
    # Prefer a resolved row; fall back to the latest row seen (likely PENDING).
    merged = dict(latest)
    merged.update(latest_resolved)
    return merged


def build_calibration_report(
    decision_log_rows: List[Dict[str, str]],
    outcome_rows: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Pure function: no I/O, no sheet access -- easy to unit test.

    ``decision_log_rows`` / ``outcome_rows`` are exactly what
    ``DecisionLogSheetsClient.read_rows`` / ``OutcomesSheetsClient.read_rows``
    return: lists of dicts keyed by column name.
    """
    outcomes_by_decision = _resolve_latest_outcomes(outcome_rows)

    overall = BucketStats(label="overall")
    by_final_type: Dict[str, BucketStats] = {}
    by_deciding_rule: Dict[str, BucketStats] = {}
    unresolved_placed = 0
    cleared_not_placed = 0

    for row in decision_log_rows:
        decision_id = (row.get("Decision ID") or "").strip()
        final_type = (row.get("Final Decision Type") or "").strip()
        gate_outcome = (row.get("Gate Outcome") or "").strip()
        outcome = outcomes_by_decision.get(decision_id)

        is_actionable = final_type in ("Research Candidate", "Market-Efficiency Candidate")
        cleared = bool(_CLEARED_RE.match(gate_outcome)) or is_actionable

        if not outcome:
            if cleared:
                cleared_not_placed += 1
            continue
        if (outcome.get("Placed") or "").strip().upper() != "Y":
            if cleared:
                cleared_not_placed += 1
            continue

        result = (outcome.get("Result") or "PENDING").strip().upper()
        stake_raw = outcome.get("Placed Stake USD") or ""
        odds_raw = outcome.get("Placed Odds") or ""
        closing_raw = outcome.get("Closing Line Odds") or ""
        try:
            stake = float(stake_raw) if stake_raw else 0.0
        except ValueError:
            stake = 0.0

        buckets = [overall]
        if final_type:
            buckets.append(by_final_type.setdefault(final_type, BucketStats(label=final_type)))
        m = _DECIDING_RULE_RE.search(gate_outcome)
        if m:
            rule = m.group(1)
            buckets.append(
                by_deciding_rule.setdefault(rule, BucketStats(label=f"deciding rule {rule}"))
            )
        elif cleared:
            buckets.append(
                by_deciding_rule.setdefault(
                    "CLEARED", BucketStats(label="cleared (no failing rule)")
                )
            )

        if result == "PENDING":
            unresolved_placed += 1

        for b in buckets:
            b.placed += 1
            if result == "PENDING":
                b.pending += 1
                continue
            if result == "WIN":
                b.wins += 1
                b.total_staked_usd += stake
                payout = _american_to_payout_multiplier(
                    float(odds_raw) if odds_raw else None
                )
                if payout is not None:
                    b.total_return_usd += stake * payout
            elif result == "LOSS":
                b.losses += 1
                b.total_staked_usd += stake
            elif result == "PUSH":
                b.pushes += 1
                b.total_staked_usd += stake
                b.total_return_usd += stake
            elif result == "VOID":
                b.voids += 1
                # Void: stake never really at risk, excluded from both sides.

            if odds_raw and closing_raw:
                try:
                    placed_implied = _implied_from_american(float(odds_raw))
                    closing_implied = _implied_from_american(float(closing_raw))
                    if placed_implied is not None and closing_implied is not None:
                        b.clv_samples.append((closing_implied - placed_implied) * 100.0)
                except ValueError:
                    pass

    return {
        "overall": overall.as_dict(),
        "by_final_decision_type": {k: v.as_dict() for k, v in by_final_type.items()},
        "by_deciding_rule": {k: v.as_dict() for k, v in by_deciding_rule.items()},
        "unresolved_placed_count": unresolved_placed,
        "cleared_but_not_confirmed_placed_count": cleared_not_placed,
        "known_limitations": [
            "Grouped only by what the existing Decision Log schema stores as "
            "text (Final Decision Type, and deciding rule when the Gate "
            "Outcome column's 'NOT CLEARED — G1: ...' text is parseable) — "
            "no structured sport, edge-pp, or G4-path breakdown exists yet.",
            "'cleared_but_not_confirmed_placed_count' counts cleared "
            "candidates with no matching Outcomes row at all, which may "
            "include ones Josh placed but hasn't confirmed yet, not only "
            "ones he genuinely passed on.",
            "This report never edits config.yaml. Any threshold change this "
            "suggests is a decision for Josh, made as a normal git-diffable "
            "config edit.",
        ],
    }


def _implied_from_american(odds: float) -> Optional[float]:
    if odds == 0:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)
