# NFL — Monday Night Football (~3:00 PM ET)

Shadow mode. **You never place, size, or authorize a wager**, and you have no
capability to do so.

This is a narrow, single-game trigger, not a full slate sweep. Its job: fresh
research on the Monday night game(s) specifically, ahead of an 8:15 PM ET
kickoff, plus a final invalidation check on anything still open from Sunday.

Service base URL: `https://hard-rock-bets.fly.dev`
Rulebook: `docs/spec.md`, `claude/research-contract.md` §5 NFL checklist.

Sport key `nfl` confirmed live against Owls Insight 2026-08-13 (292 events
returned, real 2026 season slate). Owls' own `sport_key` event field reads
`americanfootball_nfl` — that's just their internal label; the URL path slug
that actually works is the short `nfl`.

NFL is out of scope for the §3a soft-book priced-in recalibration — use the
old "publicly known hours ago is likely priced in" default here, not the
MLB/WNBA/MLS one.

---

## STEP 1 — Is there a Monday night game?

```
POST https://hard-rock-bets.fly.dev/odds/nfl
```

Filter to events with a Monday `commence_time`. Almost always exactly one game
(occasionally two early in the season). If none, write one line and stop.

## STEP 2 — Read the tracker

```
GET https://hard-rock-bets.fly.dev/decision-log
```

Check every still-open Sunday entry's invalidation conditions (IC1–IC5, same
process as any late check — starter confirmed, thesis-critical player status,
price still above the recorded minimum, forecast within thresholds, game still
on) and report HOLDS/VOIDED for each. Read the Current Bankroll figure fresh.

## STEP 3 — Research the Monday night matchup

Same NFL REQUIRED checklist as `nfl-sunday.md` §3 (starting QB confirmed,
final official injury report, inactives-timing honesty, weather for outdoor
stadiums), applied to this one game with a full afternoon's worth of fresher
information than a Sunday-morning research pass could have had. Assign a
literal thesis label. Classify: PASS, REVALIDATION REQUIRED, or RESEARCH
CANDIDATE.

## STEP 4 — Adjustments, skeptical pass, deterministic verdict

```
POST https://hard-rock-bets.fly.dev/evaluate
```

Include `bankroll_usd` from STEP 2. Pass any still-open Sunday entries in
`open_entries` so C1–C3 evaluate correctly against them.

## STEP 5 — Write the brief and log it

Short. Lead with any VOIDED Sunday candidate and the exact condition that
fired. Then the Monday night matchup's own write-up and verdict. Then OPEN
EXPOSURE. Decision Log rows via:

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
- Never edit a prior Decision Log row. The log is append-only.
