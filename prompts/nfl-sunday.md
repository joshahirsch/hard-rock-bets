# NFL — Sunday research (~8:00 AM ET)

Shadow mode. **You never place, size, or authorize a wager**, and you have no
capability to do so.

Service base URL: `https://hard-rock-bets.fly.dev`
Rulebook: `docs/spec.md`, `claude/research-contract.md` §5 NFL checklist.

**⚠️ UNCONFIRMED SPORT KEY.** Only `mlb` has ever been live-confirmed against
Owls Insight. The sport key below (`nfl`) is an educated guess — confirm it
with a manual test call before this trigger's first real run (try
`americanfootball_nfl` if `nfl` 404s or returns empty on a Sunday with real
games) — see `claude/v3-learning-engine-proposal-2026-08-13.md`. Do not assume
it's right just because it looks plausible.

Note: the NFL is explicitly **out of scope** for the soft-book/thin-market
priced-in guidance (`research-contract.md` §3a) — it carries enough betting
volume that the old instant-repricing default still applies here; don't import
the "default to Uncertain" recalibration from the MLB/WNBA/MLS prompts.

---

## STEP 1 — Any NFL games today?

```
POST https://hard-rock-bets.fly.dev/odds/nfl
```

If `event_count` is 0, write one line and stop.

## STEP 2 — Read the tracker

```
GET https://hard-rock-bets.fly.dev/decision-log
```

Identify every **open** entry. Read the Current Bankroll figure fresh.

## STEP 3 — Research and classify

Per `research-contract.md` §5 NFL checklist — REQUIRED items:
`starting_qb_confirmed` (named, team announcement / presser / named beat
reporter — this is the single highest-leverage injury fact in the sport, same
discipline MLB gives probable pitchers), `official_injury_report_final` (the
league's official practice-participation report — an early-week report cited
for a Sunday game is **not** the final word; if the brief runs before the
final report, flag status as provisional), `inactives_timing_honesty`
(inactive lists post ~90 minutes pre-kickoff — structurally unknowable at 8
AM, say so explicitly rather than guessing from injury-report trends),
`weather` for outdoor stadiums (NWS point forecast — wind is a first-order
factor for kicking/passing games here in a way it barely is for MLB). Assign a
literal thesis label. Classify: PASS, REVALIDATION REQUIRED, or RESEARCH
CANDIDATE.

## STEP 4 — Adjustments, skeptical pass, deterministic verdict

Write out named adjustments, merge same-fact ones, run the skeptical pass
explicitly (SURVIVES / DEFEATED).

```
POST https://hard-rock-bets.fly.dev/evaluate
```

Include `bankroll_usd` from STEP 2 on every call. Report the service's numbers
as returned.

## STEP 5 — Write the brief and log it

Banner, tracker status, each candidate (write-up, evidence, checklist,
skeptical pass, every gate rule's result, IC1–IC5 + suggested stake for a
clearance), OPEN EXPOSURE (mandatory even with no candidates), NEAR-MISS WATCH,
revisit notes — including anything worth a look again before Monday/Thursday
night. Decision Log rows via:

```
POST https://hard-rock-bets.fly.dev/decision-log/append
```

Never a direct sheet write. Columns L and M stay empty.

Close with: *"Shadow mode: no wagers are authorized by this brief. If you place
a bet anyway, it is a Manual bet — log it as such with the exact slip price."*

---

## Hard prohibitions

- Never place, size, or authorize a wager.
- Never present an unobserved price as real.
- Never let a Tier 3 source support a classification or carry weight.
- Never recompute or restate a threshold from memory — call the service.
- Never write anything into Decision Log columns L or M.
- Always log the real result honestly, including a Sunday with nothing to
  show.
