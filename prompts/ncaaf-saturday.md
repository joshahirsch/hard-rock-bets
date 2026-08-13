# College Football — Saturday research (~7:00 AM ET)

Shadow mode. **You never place, size, or authorize a wager**, and you have no
capability to do so.

Service base URL: `https://hard-rock-bets.fly.dev`
Rulebook: `docs/spec.md`, `claude/research-contract.md` §5 CFB checklist.

Sport key `ncaaf` confirmed live against Owls Insight 2026-08-13 (186 events
returned, real 2026 season slate starting 2026-08-29). Note Owls Insight's own
`sport_key` field on each event reads `americanfootball_ncaaf` — that's just
Owls' internal label; the URL path slug that actually works is the short
`ncaaf`, not that longer string.

---

## STEP 1 — Any CFB games today?

```
POST https://hard-rock-bets.fly.dev/odds/ncaaf
```

If `event_count` is 0, write one line — *"No CFB games today."* — and stop.
Most days of the week this will be true even in season; CFB is concentrated on
Saturdays (with some Tue–Fri games) — that's expected, not a defect.

## STEP 2 — Read the tracker

```
GET https://hard-rock-bets.fly.dev/decision-log
```

Identify every **open** entry. Read the Current Bankroll figure fresh.

## STEP 3 — Research and classify

Per `research-contract.md` §5 CFB checklist — REQUIRED items:
`starting_qb_confirmed` (named, Tier 1/2 sourced), `injury_report_opacity_acknowledged`
(most FBS programs have no formal injury report; any unsourced injury claim is
Tier 3 by default — DISCOVERY ONLY, say so plainly), `roster_continuity_check`
(bowl/playoff/portal-window games only — transfer portal entries, opt-outs),
`weather` for outdoor stadiums (NWS point forecast, or an explicit "weather not
a factor"). CFB has no sport-wide Tier 1 injury layer the way MLB or the NFL
do — when no Tier 1 source exists for a team, say so rather than treating Tier
3 chatter as confirmation. Watch for the "blue-blood public bias" — a
nationally-followed program's line already absorbs more public money than its
true EV, which is a priced-in consideration, not a handicapping edge. Assign a
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
revisit notes. Decision Log rows via:

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
- Never let a Tier 3 source support a classification or carry weight — CFB has
  the largest Tier 3 pick-content ecosystem of any sport here; be extra
  disciplined about it.
- Never recompute or restate a threshold from memory — call the service.
- Never write anything into Decision Log columns L or M.
- Always log the real result honestly, including a Saturday with nothing to
  show.
