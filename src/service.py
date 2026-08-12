"""FastAPI service exposing the deterministic half of the pipeline.

The split this service enforces:

  * **Qualitative work stays with the model.** Evidence tiering, publication
    times, the priced-in judgment, the skeptical pass, thesis labelling,
    checklist confirmation -- all of that is judgment, done by the agent
    against ``docs/spec.md``, and arrives here as *inputs*.
  * **Every number is computed here, once, in tested code.** Devig, band
    width, edge, EV, G1-G6, C1-C3, GME0-GME6, IC1-IC5, stake sizing. None of
    it is ever re-derived in prose again.

Shadow mode: this service classifies research quality and suggests an
informational flat stake. It cannot place, size, or execute a wager, and no
sportsbook write path exists anywhere in this codebase.

Endpoints:
  POST /odds/{sport}   fetch + normalize current odds (multi-book, merged)
  POST /evaluate       the full deterministic pipeline in one call
  POST /stake          the flat suggested-stake formula on its own
  GET  /healthz
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src import __version__
from src.fair_probability import estimator as fp
from src.fetch.owls_insight import (
    DEFAULT_BOOKMAKER_PREFERENCE,
    DEFAULT_KEEP_BOOKS,
    OwlsInsightClient,
    OwlsInsightError,
)
from src.gates.exposure_control import (
    Candidate,
    OpenEntry,
    apply_exposure_control,
    open_exposure_note,
)
from src.gates.invalidation import build_invalidation_conditions
from src.gates.market_efficiency_gate import (
    BookQuote,
    evaluate_market_efficiency_gate,
)
from src.gates.selection_gate import (
    ChecklistStatus,
    GateInputs,
    evaluate_selection_gate,
)
from src.gates.stake_sizing import suggest_for_candidate
from src.math.novig import (
    check_two_sided_pair,
    implied_probability,
    no_vig_probability,
    overround,
)
from src.store.sheets_client import (
    CREDENTIALS_ENV_VAR,
    FINAL_DECISION_TYPES,
    OAUTH_CLIENT_ID_ENV_VAR,
    OAUTH_CLIENT_SECRET_ENV_VAR,
    OAUTH_REFRESH_TOKEN_ENV_VAR,
    SPREADSHEET_ID_ENV_VAR,
    DecisionLogRow,
    DecisionLogSheetsClient,
    SheetsClientError,
    generate_decision_id,
)

app = FastAPI(
    title="Hard Rock Bet research pipeline",
    version=__version__,
    description=(
        "Shadow-mode research pipeline. Produces research candidates and logs "
        "them; never places or authorizes a wager."
    ),
)

SHADOW_MODE_BANNER = (
    "SHADOW MODE — this service produces research classifications only. "
    "No wager is authorized, and no execution capability exists."
)


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    has_service_account = bool(os.environ.get(CREDENTIALS_ENV_VAR))
    has_oauth = bool(
        os.environ.get(OAUTH_CLIENT_ID_ENV_VAR)
        and os.environ.get(OAUTH_CLIENT_SECRET_ENV_VAR)
        and os.environ.get(OAUTH_REFRESH_TOKEN_ENV_VAR)
    )
    return {
        "status": "ok",
        "version": __version__,
        "shadow_mode": True,
        "odds_api_key_configured": bool(os.environ.get("OWLS_INSIGHT_API_KEY")),
        # True only once BOTH a spreadsheet id AND a working credential path
        # (service-account key OR OAuth refresh token) are present. Checking
        # the spreadsheet id alone, as this used to, would silently claim
        # "configured" with no way to actually authenticate.
        "sheets_configured": bool(os.environ.get(SPREADSHEET_ID_ENV_VAR))
        and (has_service_account or has_oauth),
        "sheets_credential_mode": (
            "service_account" if has_service_account else "oauth" if has_oauth else None
        ),
    }


# ---------------------------------------------------------------------------
# /odds/{sport}
# ---------------------------------------------------------------------------


class OddsRequest(BaseModel):
    keep_books: List[str] = Field(default_factory=lambda: list(DEFAULT_KEEP_BOOKS))
    markets: List[str] = Field(default_factory=lambda: ["h2h"])
    bookmaker_preference: List[str] = Field(
        default_factory=lambda: list(DEFAULT_BOOKMAKER_PREFERENCE)
    )


@app.post("/odds/{sport}")
def fetch_odds(sport: str, req: Optional[OddsRequest] = None) -> Dict[str, Any]:
    """Fetch and normalize one sport's current slate.

    The response is merged ACROSS bookmaker keys per event -- Owls' v1 API
    groups events by bookmaker and each group carries only that book's price,
    so a multi-book view of one game requires the merge this endpoint performs.
    """
    req = req or OddsRequest()
    try:
        client = OwlsInsightClient(keep_books=req.keep_books)
        events = client.fetch_events(sport, markets=req.markets or None)
    except OwlsInsightError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    retrieved_at = datetime.utcnow().isoformat() + "Z"
    payload = []
    for ev in events:
        record = ev.to_dict()
        ref = ev.reference_book(req.bookmaker_preference)
        record["reference_book"] = ref
        record["contributing_books"] = ev.books_for("h2h")
        two_sided = ev.two_sided_h2h(ref) if ref else None
        if two_sided:
            home, away = two_sided
            try:
                record["reference_h2h"] = {
                    "book": ref,
                    "home_odds": home,
                    "away_odds": away,
                    "home_implied": implied_probability(home),
                    "away_implied": implied_probability(away),
                    "overround": overround(home, away),
                    "home_no_vig": no_vig_probability(home, away),
                    "away_no_vig": no_vig_probability(away, home),
                }
            except Exception as exc:  # malformed price at the reference book
                record["reference_h2h"] = {"book": ref, "error": str(exc)}
        payload.append(record)

    return {
        "banner": SHADOW_MODE_BANNER,
        "sport": sport,
        "retrieved_at_utc": retrieved_at,
        "event_count": len(payload),
        "events": payload,
    }


# ---------------------------------------------------------------------------
# /evaluate
# ---------------------------------------------------------------------------


class AdjustmentIn(BaseModel):
    """One named adjustment, carrying the full evidence contract."""

    name: str
    magnitude_pp: float = Field(
        ..., description="Signed RAW magnitude in pp, before weighting."
    )
    tier: int = Field(..., ge=1, le=3)
    priced_in: str = Field(..., description="Y | N | Uncertain")
    freshness: str = Field(
        ..., description="under_6h | 6_to_24h | unknown_or_stale"
    )
    source: str = ""
    publication_time: str = ""
    priced_in_reasoning: str = ""
    explicit_weight_override: Optional[float] = None
    shared_cause_label: Optional[str] = None


class TwoSidedPriceIn(BaseModel):
    """The §1 baseline inputs: BOTH sides, same run, within 10 minutes."""

    side_odds: int
    opposing_odds: int
    #: Minutes on any common monotonic scale, for the 10-minute pair gate.
    side_timestamp_minutes: Optional[float] = None
    opposing_timestamp_minutes: Optional[float] = None
    #: Age of the older quote at evaluation time, for the 60-minute stale gate.
    age_minutes: Optional[float] = None


class OpenEntryIn(BaseModel):
    decision_id: str
    event: str
    teams: List[str]
    thesis: str
    event_datetime: datetime
    run_date: datetime
    final_decision_type: str = "Research Candidate"


class BookQuoteIn(BaseModel):
    book_key: str
    side_odds: int
    opposing_odds: int


class MarketEfficiencyIn(BaseModel):
    hard_rock_odds: Optional[int] = None
    contributing_books: List[BookQuoteIn] = Field(default_factory=list)
    news_cause_fact_within_window: bool = True
    required_checklist_all_confirmed: bool = True
    prior_sightings_today: int = 0
    inconsistencies: List[str] = Field(default_factory=list)


class WeatherIn(BaseModel):
    weather_dependent: bool = False
    am_precip_pct: Optional[float] = None
    am_wind_mph: Optional[float] = None
    am_wind_direction: Optional[str] = None
    am_temp_f: Optional[float] = None


class EvaluateRequest(BaseModel):
    """Everything the deterministic pipeline needs for ONE candidate.

    The qualitative judgments (tier, priced-in, freshness, checklist statuses,
    skeptical-pass verdict, thesis label) are supplied by the agent; every
    number below is computed by this service.
    """

    event: str
    market: str = "h2h"
    side: str
    teams: List[str] = Field(default_factory=list)
    thesis: str = ""
    event_datetime: Optional[datetime] = None
    run_datetime: Optional[datetime] = None

    #: §1 -- the mandatory no-vig baseline. Supply the pair, or the baseline
    #: directly when it was computed upstream.
    two_sided_price: Optional[TwoSidedPriceIn] = None
    baseline_no_vig: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Probability in [0,1] for the side."
    )

    #: The price the candidate is actually evaluated against.
    evaluation_odds: int

    adjustments: List[AdjustmentIn] = Field(default_factory=list)
    skeptical_verdict: Optional[str] = Field(
        None, description="SURVIVES | DEFEATED"
    )

    #: G3 -- {required item key: MET | NOT MET | NOT YET KNOWABLE | N/A}
    required_checklist: Dict[str, str] = Field(default_factory=dict)
    #: G6 -- anything the write-up itself flagged as missing/ambiguous.
    inconsistencies: List[str] = Field(default_factory=list)

    open_entries: List[OpenEntryIn] = Field(default_factory=list)

    #: IC1 / IC2 / IC4 / IC5 inputs.
    starter_name: Optional[str] = None
    thesis_critical_players: List[str] = Field(default_factory=list)
    weather: WeatherIn = Field(default_factory=WeatherIn)
    scheduled_date: Optional[str] = None

    #: Optional parallel Market-Efficiency evaluation for the same game.
    market_efficiency: Optional[MarketEfficiencyIn] = None

    bankroll_usd: Optional[float] = None
    band_from_rounded_point: bool = True


@app.post("/evaluate")
def evaluate(req: EvaluateRequest) -> Dict[str, Any]:
    """Run baseline -> adjustments -> band -> gate -> exposure -> IC -> stake."""
    now = req.run_datetime or datetime.utcnow()
    notes: List[str] = []

    # -- §1 baseline --------------------------------------------------------
    pair_check = None
    baseline = req.baseline_no_vig
    if req.two_sided_price is not None:
        tsp = req.two_sided_price
        check = check_two_sided_pair(
            tsp.side_odds,
            tsp.opposing_odds,
            tsp.side_timestamp_minutes,
            tsp.opposing_timestamp_minutes,
            tsp.age_minutes,
        )
        pair_check = {"ok": check.ok, "fatal_errors": list(check.fatal_errors),
                      "flags": list(check.flags)}
        if not check.ok:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "no valid baseline",
                    "reason": (
                        "fair-probability-spec.md §1: with no valid two-sided, "
                        "same-run, within-10-minute price pair there is no baseline "
                        "and no fair-probability estimate may be produced for this "
                        "candidate this run."
                    ),
                    "pair_check": pair_check,
                },
            )
        baseline = no_vig_probability(tsp.side_odds, tsp.opposing_odds)
    if baseline is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "supply either two_sided_price (preferred) or baseline_no_vig; "
                "an estimate that does not start from the no-vig baseline is out "
                "of contract (fair-probability-spec.md §1)."
            ),
        )

    # -- §2/§3 adjustments and band ----------------------------------------
    try:
        adjustments = [
            fp.Adjustment(
                name=a.name,
                magnitude_pp=a.magnitude_pp,
                tier=fp.Tier(a.tier),
                priced_in=fp.PricedIn(a.priced_in),
                freshness=fp.Freshness(a.freshness),
                source=a.source,
                publication_time=a.publication_time,
                priced_in_reasoning=a.priced_in_reasoning,
                explicit_weight_override=a.explicit_weight_override,
                shared_cause_label=a.shared_cause_label,
            )
            for a in req.adjustments
        ]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    tier3 = [a for a in adjustments if a.tier is fp.Tier.TIER3]
    if tier3:
        raise HTTPException(
            status_code=422,
            detail=(
                "Tier-3 evidence can never support an adjustment at any weight "
                "(research-contract.md §2, fair-probability-spec.md §2a). "
                f"Offending adjustments: {[a.name for a in tier3]}."
            ),
        )

    verdict = (
        fp.SkepticalVerdict(req.skeptical_verdict) if req.skeptical_verdict else None
    )
    est = fp.estimate(
        baseline=baseline,
        adjustments=adjustments,
        skeptical_verdict=verdict,
        band_from_rounded_point=req.band_from_rounded_point,
    )
    notes.extend(est.notes)

    edge_low = est.edge_low_pp(req.evaluation_odds)
    fair_block = {
        "baseline_no_vig_pct": baseline * 100.0,
        "net_adjustment_pp": est.net_adjustment_pp,
        "applied_adjustments_pp": est.applied_adjustments_pp,
        "scaling_factor": est.scaling_factor,
        "point_estimate_raw_pct": est.point_estimate,
        "display": fp.format_estimate(est),
        "display_point_pct": est.display_point_pct,
        "band_pp": est.band_pp,
        "band_low_pct": est.band_low_pct,
        "band_high_pct": est.band_high_pct,
        "band_breakdown": {
            "base_pp": est.band_breakdown.base_pp,
            "freshness_penalty_pp": est.band_breakdown.freshness_penalty_pp,
            "conflict_penalty_pp": est.band_breakdown.conflict_penalty_pp,
            "fragile_dominance_penalty_pp": est.band_breakdown.fragile_dominance_penalty_pp,
            "raw_pp": est.band_breakdown.raw_pp,
            "clamped_pp": est.band_breakdown.clamped_pp,
            "notes": est.band_breakdown.notes,
        },
        "evaluation_odds": req.evaluation_odds,
        "evaluation_implied_pct": implied_probability(req.evaluation_odds) * 100.0,
        "edge_conservative_pp": edge_low,
        "edge_point_pp": est.edge_point_pp(req.evaluation_odds),
        "edge_favorable_pp": est.edge_high_pp(req.evaluation_odds),
        "ev_conservative_per_dollar": est.ev_conservative(req.evaluation_odds),
        "skeptical_verdict": verdict.value if verdict else None,
        "ships": est.ships,
        "governing_note": (
            "Only the conservative (band-low) end may inform a determination "
            "(fair-probability-spec.md §6). Point and favorable ends are context."
        ),
    }

    # §5 -- a DEFEATED estimate does not ship; the pipeline stops at the verdict.
    if verdict is fp.SkepticalVerdict.DEFEATED:
        return {
            "banner": SHADOW_MODE_BANNER,
            "event": req.event,
            "side": req.side,
            "fair_probability": {
                "baseline_no_vig_pct": baseline * 100.0,
                "skeptical_verdict": "DEFEATED",
                "ships": False,
            },
            "selection_gate": None,
            "exposure": None,
            "invalidation_conditions": None,
            "stake": {
                "eligible": False,
                "text": "No suggested stake — estimate DEFEATED at the skeptical pass.",
            },
            "final_decision_type": "Pass",
            "reason_for_pass": (
                "Skeptical pass DEFEATED — no band, edge, or EV is computed "
                "(fair-probability-spec.md §5)."
            ),
            "notes": notes,
        }

    # -- G1-G6 -------------------------------------------------------------
    try:
        checklist = {
            k: ChecklistStatus(v) for k, v in req.required_checklist.items()
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"invalid checklist status: {exc}. Expected one of "
                   f"{[c.value for c in ChecklistStatus]}.",
        ) from exc

    gate = evaluate_selection_gate(
        GateInputs(
            edge_low_pp=edge_low,
            band_width_pp=est.band_pp,
            required_checklist=checklist,
            nonzero_weight_adjustment_tiers=[
                a.tier for a in adjustments if a.weight() != 0.0
            ],
            skeptical_verdict=verdict,
            inconsistencies=list(req.inconsistencies),
        )
    )

    # -- C1-C3 -------------------------------------------------------------
    open_entries = [
        OpenEntry(
            decision_id=e.decision_id,
            event=e.event,
            teams=tuple(e.teams),
            thesis=e.thesis,
            event_datetime=e.event_datetime,
            run_date=e.run_date.date(),
            final_decision_type=e.final_decision_type,
        )
        for e in req.open_entries
    ]
    candidate = Candidate(
        decision_id="CANDIDATE",
        event=req.event,
        teams=tuple(req.teams),
        thesis=req.thesis,
        event_datetime=req.event_datetime or now,
        conservative_edge_pp=edge_low,
        writeup_position=0,
        gate_cleared=gate.cleared,
    )
    exposure = apply_exposure_control([candidate], open_entries, now)[0]

    final_type = gate.final_decision_type
    reason_for_pass = gate.reason_for_pass
    if gate.cleared and exposure.outcome.value == "CAPPED":
        final_type = "Pass"
        reason_for_pass = exposure.reason_for_pass

    # -- IC1-IC5 (only for a candidate that stays a Research Candidate) -----
    ic_block = None
    if final_type == "Research Candidate":
        ic = build_invalidation_conditions(
            starter_name=req.starter_name,
            thesis_critical_players=req.thesis_critical_players,
            conservative_band_low_pct=est.band_low_pct,
            weather_dependent=req.weather.weather_dependent,
            am_precip_pct=req.weather.am_precip_pct,
            am_wind_mph=req.weather.am_wind_mph,
            am_wind_direction=req.weather.am_wind_direction,
            am_temp_f=req.weather.am_temp_f,
            scheduled_date=req.scheduled_date,
        )
        ic_block = {
            "conditions": [
                {"code": c.code, "text": c.text, "unresolved": c.unresolved}
                for c in ic.conditions
            ],
            "minimum_acceptable_odds": ic.minimum_acceptable_odds,
            "has_unresolved": ic.has_unresolved,
            "decision_log_cell": ic.decision_log_cell(),
            "brief_block": ic.brief_block(),
        }

    # -- Market-Efficiency pathway (optional, parallel) ---------------------
    me_block = None
    if req.market_efficiency is not None:
        me = req.market_efficiency
        me_verdict = evaluate_market_efficiency_gate(
            hard_rock_odds=me.hard_rock_odds,
            contributing_books=[
                BookQuote(b.book_key, b.side_odds, b.opposing_odds)
                for b in me.contributing_books
            ],
            news_cause_fact_within_window=me.news_cause_fact_within_window,
            required_checklist_all_confirmed=me.required_checklist_all_confirmed,
            prior_sightings_today=me.prior_sightings_today,
            inconsistencies=me.inconsistencies,
        )
        me_block = me_verdict.as_dict()
        # C1 is shared across pathways: a team already cleared today via the
        # informational pathway cannot also clear via Market-Efficiency.
        if me_verdict.cleared and final_type == "Research Candidate":
            me_block["pathway_note"] = (
                "C1 is read as 'regardless of pathway' "
                "(market-efficiency-candidate-spec.md §6): this team already "
                "cleared today via the informational pathway, so this "
                "Market-Efficiency clearance is capped to Pass."
            )
            me_block["final_decision_type"] = "Pass"
        elif me_verdict.cleared and final_type != "Research Candidate":
            final_type = me_verdict.final_decision_type
            reason_for_pass = None

    # -- stake --------------------------------------------------------------
    bankroll = req.bankroll_usd
    if bankroll is None:
        env_bankroll = os.environ.get("BANKROLL_USD")
        bankroll = float(env_bankroll) if env_bankroll else None
    if bankroll is None:
        stake_block = {
            "eligible": False,
            "amount_usd": None,
            "text": (
                "No suggested stake — current bankroll was not supplied. "
                "stake-sizing-spec.md §3 requires the bankroll read fresh at "
                "STEP 1 of the same run, never a fixed historical number."
            ),
        }
    else:
        suggestion = suggest_for_candidate(final_type, bankroll)
        stake_block = {
            "eligible": suggestion.eligible,
            "amount_usd": suggestion.amount_usd,
            "binding_constraint": suggestion.binding_constraint,
            "text": suggestion.text,
        }

    return {
        "banner": SHADOW_MODE_BANNER,
        "event": req.event,
        "market": req.market,
        "side": req.side,
        "pair_check": pair_check,
        "fair_probability": fair_block,
        "selection_gate": gate.as_dict(),
        "exposure": {
            "outcome": exposure.outcome.value,
            "deciding_rule": exposure.deciding_rule,
            "conflicting_decision_id": exposure.conflicting_decision_id,
            "note": exposure.note,
        },
        "open_exposure_note": open_exposure_note(open_entries, now),
        "market_efficiency": me_block,
        "invalidation_conditions": ic_block,
        "stake": stake_block,
        "final_decision_type": final_type,
        "reason_for_pass": reason_for_pass,
        "decision_log_cells": {
            "T_invalidation_conditions": (
                ic_block["decision_log_cell"] if ic_block
                else f"N/A — not gate-CLEARED ({reason_for_pass or 'see gate outcome'})"
            ),
            "U_correlation_or_exposure_note": exposure.note,
            "V_gate_outcome": gate.gate_outcome_cell,
            "W_final_decision_type": final_type,
            "X_reason_for_pass": reason_for_pass or "",
        },
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# /stake
# ---------------------------------------------------------------------------


class StakeRequest(BaseModel):
    final_decision_type: str
    bankroll_usd: float


@app.post("/stake")
def stake(req: StakeRequest) -> Dict[str, Any]:
    suggestion = suggest_for_candidate(req.final_decision_type, req.bankroll_usd)
    return {
        "banner": SHADOW_MODE_BANNER,
        "eligible": suggestion.eligible,
        "amount_usd": suggestion.amount_usd,
        "binding_constraint": suggestion.binding_constraint,
        "text": suggestion.text,
    }


# ---------------------------------------------------------------------------
# /decision-log
#
# Deliberately separate from /evaluate. /evaluate is a pure computation (per
# the README: "the whole deterministic pipeline in one call ... returns ...
# ready-to-write Decision Log cells") with no side effects -- writing is an
# explicit second step, so a caller can inspect a verdict before deciding
# whether, and with what final narrative fields, to log it. Until this
# existed, GOOGLE_SHEETS_SPREADSHEET_ID/credentials being configured had no
# effect on anything: sheets_client.py was fully built and tested but never
# called from the running service.
# ---------------------------------------------------------------------------


def _decision_log_client() -> DecisionLogSheetsClient:
    try:
        return DecisionLogSheetsClient()
    except SheetsClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class DecisionLogAppendRequest(BaseModel):
    """One Decision Log row. Field names mirror DECISION_LOG_COLUMNS.

    No ``decision_id`` field on purpose: it's generated server-side (a ULID
    via ``generate_decision_id``) so nothing has to read the sheet or guess a
    suffix to avoid collisions -- the entire reason v2 moved off
    "read the sheet, count rows, add one" (sheets_client.py module docstring).
    """

    run_date: str = Field(
        ..., description="YYYY-MM-DD, the agent's own ET business-day date"
    )
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


@app.post("/decision-log/append")
def append_decision_log(req: DecisionLogAppendRequest) -> Dict[str, Any]:
    """Atomically append one row to the live Decision Log."""
    if req.final_decision_type not in FINAL_DECISION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"invalid final_decision_type {req.final_decision_type!r}; "
                f"expected one of {FINAL_DECISION_TYPES}"
            ),
        )
    client = _decision_log_client()
    decision_id = generate_decision_id(req.run_date)
    row = DecisionLogRow(
        decision_id=decision_id,
        run_date=req.run_date,
        event=req.event,
        market=req.market,
        side=req.side,
        current_reference_line=req.current_reference_line,
        current_reference_odds=req.current_reference_odds,
        odds_source=req.odds_source,
        retrieval_timestamp_et=req.retrieval_timestamp_et,
        opposing_side_line=req.opposing_side_line,
        opposing_side_odds=req.opposing_side_odds,
        key_evidence=req.key_evidence,
        evidence_sources=req.evidence_sources,
        evidence_publication_times=req.evidence_publication_times,
        priced_in_assessment=req.priced_in_assessment,
        missing_information=req.missing_information,
        skeptical_case=req.skeptical_case,
        invalidation_conditions=req.invalidation_conditions,
        correlation_or_exposure_note=req.correlation_or_exposure_note,
        gate_outcome=req.gate_outcome,
        final_decision_type=req.final_decision_type,
        reason_for_pass=req.reason_for_pass,
        prompt_version=req.prompt_version,
    )
    try:
        result = client.append_rows([row])
    except SheetsClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "banner": SHADOW_MODE_BANNER,
        "decision_id": decision_id,
        "sheets_response": result,
    }


@app.get("/decision-log")
def read_decision_log(max_rows: int = 5000) -> Dict[str, Any]:
    """Read back the live Decision Log.

    This is the C1-C3 open-exposure source of truth (selection-gate-spec.md
    §6 step 1): exposure control needs current open entries (Final Decision
    Type = Research Candidate / Market-Efficiency Candidate, event not yet
    started) read fresh every firing. Previously every trigger fetched this
    itself via a separate Drive/Sheets connector call outside the service;
    this exposes the same tested read path append_decision_log writes through.
    """
    client = _decision_log_client()
    try:
        rows = client.read_rows(max_rows=max_rows)
    except SheetsClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "banner": SHADOW_MODE_BANNER,
        "row_count": len(rows),
        "rows": rows,
    }
