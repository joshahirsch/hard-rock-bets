# hard-rock-bet-agent

A deterministic **research** pipeline for sports betting. It produces research
candidates, classifies them against a fixed rulebook, and logs them.

> ## Shadow mode
> **This system never places, sizes, or authorizes a wager.** There is no
> sportsbook write path anywhere in this codebase and none is planned. A
> gate-cleared candidate carries a flat, capped, *informational* suggested
> stake — that is a number for a human to consider, not an instruction, and a
> human decides and executes independently, manually.

> ## This is a SANITIZED TEMPLATE
> No real API key, bankroll figure, spreadsheet ID, or personal data appears in
> any git-tracked file. `config/config.example.yaml` and `.env.example` are
> placeholders. `config/config.yaml` and `.env` are git-ignored. Clone it,
> supply your own values, and it runs.

---

## Why this exists

The previous version of this system lived entirely inside scheduled
natural-language agent prompts — 12,000 to 46,000 characters each — that
re-derived every formula, threshold, and gate rule in prose on every single
firing. There were no tests and no version control. Three consequences showed
up in production: arithmetic drifted between runs, thresholds documented in one
of ~15 cross-referencing spec files silently disagreed with another, and
Decision IDs computed by "read the sheet, count the rows, add one" collided
when two firings overlapped.

This repository moves all of that into tested code:

- **Every number is computed once, in one place.** Devig, band width, edge, EV,
  minimum acceptable odds, G1–G6, C1–C3, GME0–GME6, IC1–IC5, stake sizing.
- **Every constant cites its source document**, in code, in config, and in
  `docs/spec.md`.
- **The tests use the source specs' own worked demonstrations as fixtures**, so
  they prove the code matches the documented behavior rather than merely being
  self-consistent.
- **The prompts shrank by roughly 80%** — they now describe the qualitative
  research work and call the service for the verdict.

The judgment work that genuinely requires a model — tiering sources, assessing
what's already priced in, steelmanning the other side — stays in the prompts.
The arithmetic does not.

---

## Layout

```
config/config.example.yaml   every configurable constant, heavily commented,
                             each citing the spec document it came from
.env.example                 API key / spreadsheet ID placeholders
docs/spec.md                 ONE consolidated rulebook (summary; src/ is truth)
prompts/                     morning.md, afternoon.md, late.md — small trigger
                             templates that call the service
src/
  math/novig.py              implied probability, devig, EV, minimum odds, CLV
  fair_probability/estimator.py
                             baseline + adjustments + band + skeptical pass
  gates/selection_gate.py    G1–G6, one call → structured verdict
  gates/exposure_control.py  C1–C3 correlation / exposure caps
  gates/market_efficiency_gate.py
                             GME0–GME6 (the parallel, price-only pathway)
  gates/invalidation.py      IC1–IC5 pregame revalidation conditions
  gates/stake_sizing.py      the flat suggested-stake formula
  fetch/owls_insight.py      direct server-side odds fetch + multi-book merge
  store/sheets_client.py     Google Sheets Decision Log (values.append + ULID)
  service.py                 FastAPI app
tests/                       193 tests, spec-worked-example fixtures
```

---

## Setup

Requires Python 3.10+.

```bash
git clone <this repo> && cd hard-rock-bet-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt          # or: pip install -e ".[dev]"

cp config/config.example.yaml config/config.yaml
cp .env.example .env
$EDITOR .env config/config.yaml              # both are git-ignored
```

### Configure

**`.env`** — secrets and IDs only:

| Variable | What |
|---|---|
| `OWLS_INSIGHT_API_KEY` | Odds vendor key. Auth is `Authorization: Bearer <key>` only; a `?apiKey=` query param returns 403. |
| `GOOGLE_SHEETS_SPREADSHEET_ID` | The long token in your tracker's URL. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Absolute path to a service-account JSON key with Sheets scope. **Share the spreadsheet with that service account's email**, or writes 403. |
| `BANKROLL_USD` | Only feeds the informational stake formula. |

**`config/config.yaml`** — every threshold, checklist, and bookmaker
preference, with a comment on each explaining what it does and which spec
document it came from. The defaults are the spec's own current values; you can
run it unmodified.

The odds vendor's Hard Rock key is `hardrock` (a different vendor used
`hardrockbet` — they are not interchangeable).

### Run

```bash
uvicorn src.service:app --reload --port 8000
curl localhost:8000/healthz
```

Point the prompt templates at it by replacing `{{SERVICE_URL}}`.

### Test

```bash
pytest
```

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | liveness + whether credentials are configured |
| `POST /odds/{sport}` | fetch and normalize the slate; merged across bookmakers per event, with implied / overround / no-vig precomputed for the reference book |
| `POST /evaluate` | **the whole deterministic pipeline in one call** — takes the agent's evidence-tier judgments and fair-probability inputs; returns the estimate + band, the full G1–G6 verdict with every rule's result, the C1–C3 exposure check, IC1–IC5, the suggested stake, and ready-to-write Decision Log cells |
| `POST /stake` | the flat stake formula on its own |

`/evaluate` fails closed. A stale or non-simultaneous price pair returns 422
("no valid baseline") rather than an estimate. A Tier-3 adjustment is rejected
outright. A DEFEATED skeptical pass stops the pipeline at the verdict — no
band, no edge, no EV.

---

## Two deliberate departures from v1

**Odds fetching goes direct, not through a relay.** v1 routed the odds API
through a Zapier webhook→code→storage relay, because the pipeline ran inside a
scheduled agent session that had nowhere safe to hold a Bearer token. That
relay's storage layer had an undocumented 25,000-byte per-value cap, which
forced dropping spreads and totals entirely to fit a slate. This service *is*
the server side: it holds the key and calls the vendor directly, so the cap
doesn't exist and market coverage is a config choice.

The one thing the relay work discovered that *is* preserved — and tested — is
structural: the vendor's `data` object is keyed by **bookmaker**, and each
bookmaker group's event list contains only *that* bookmaker's price nested
inside. Reading a single key gives you a one-book view of every game. Building
a multi-book view requires iterating every kept bookmaker key and merging by
event id.

**Decision IDs are ULIDs, appended atomically.** v1 read the whole sheet,
counted rows, and computed both the next row index and the next `-NN` suffix.
Overlapping firings raced and collided. Rows now go in via
`spreadsheets.values.append` with `insertDataOption=INSERT_ROWS` (the server
picks the insertion point) and IDs are `HRB-YYYYMMDD-<ULID>` — unique without
reading anything, still sortable by creation time. The client deliberately
exposes no update or delete method: the Decision Log is append-only.

---

## Honest limitations

Inherited from the source specifications and carried forward unchanged:

- **Essentially every numeric constant here is a reasoned default, not fitted
  to data.** The 0.5 pp neutral CLV band, the ±8 pp adjustment cap, the 2/4 pp
  band bases, the 3–10 pp clamp, G1's +1.50 pp floor, G2's 6.00 pp ceiling,
  GME3's 4-book minimum, IC4's weather thresholds, the $10 flat stake — none of
  them are calibrated against a track record, because there isn't one yet.
- **G1's floor was lowered from +3.00 pp to +1.50 pp on 2026-08-11**, as a
  deliberate loosening after 131 logged candidates produced zero clearances.
  This creates a real tension that was accepted knowingly: +1.50 pp sits
  *below* the framework's own ±3 pp minimum band width, so a cleared candidate
  is no longer guaranteed to be distinguishable from its own estimate's noise
  floor.
- **The priced-in judgment remains subjective.** The evidence contract forces
  it to be explicit, dated, and gated — it cannot make it objective.
- **Proportional devig and two-outcome markets only.** No three-way markets, no
  multi-outcome props, no parlays, teasers, live bets, or cash-out valuation.
- **Cross-day exposure control depends entirely on an accurate Decision Log
  read-back each run.** A malformed or misread row degrades C2/C3 silently
  rather than erroring loudly.
- **The market-efficiency pathway cannot detect promotional or boosted odds.**
  A boost at any book — including the one under evaluation — looks identical to
  genuine mispricing.
- **Invalidation conditions are self-applied by a human at placement time.**
  Nothing observes whether the check actually happened.

Places where the source specs were ambiguous, silent, or internally
inconsistent — and how each was resolved — are listed in `docs/spec.md` §10.
