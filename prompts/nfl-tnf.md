# NFL — Thursday Night Football (~3:00 PM ET)

Shadow mode. **You never place, size, or authorize a wager**, and you have no
capability to do so.

Same shape as `nfl-mnf.md`, for the Thursday night game. Note the honest
caveat that already exists for a short week: a Thursday game gets meaningfully
less practice-report runway than a Sunday or Monday game — injury statuses
carry more uncertainty than usual and that should be said plainly, not
smoothed over.

Service base URL: `https://hard-rock-bets.fly.dev`
Rulebook: `docs/spec.md`, `claude/research-contract.md` §5 NFL checklist.

Sport key `nfl` confirmed live against Owls Insight 2026-08-13 (292 events
returned, real 2026 season slate). Owls' own `sport_key` event field reads
`americanfootball_nfl` — that's just their internal label; the URL path slug
that actually works is the short `nfl`.

NFL is out of scope for the §3a soft-book priced-in recalibration.

---

## STEP 1 — Is there a Thursday night game?

```
POST https://hard-rock-bets.fly.dev/odds/nfl
```

Filter to events with a Thursday `commence_time`. If none, write one line and
stop.

## STEP 2 — Read the tracker

```
GET https://hard-rock-bets.fly.dev/decision-log
```

Read the Current Bankroll figure fresh. There is normally nothing "open" to
revalidate here (a short week means little carries from the prior Sunday/
Monday into Thursday) — if there is, check it the same way `nfl-mnf.md` does.

## STEP 3 — Research the Thursday night matchup

Same NFL REQUIRED checklist as `nfl-sunday.md` §3. Explicitly flag the
short-week practice-report limitation on any injury-status fact — a
Wednesday-or-earlier report for a Thursday game has had less time to firm up
than the equivalent Sunday-game report would have. Assign a literal thesis
label. Classify: PASS, REVALIDATION REQUIRED, or RESEARCH CANDIDATE.

## STEP 4 — Adjustments, skeptical pass, deterministic verdict

```
POST https://hard-rock-bets.fly.dev/evaluate
```

Include `bankroll_usd` from STEP 2. Pass any still-open entries in
`open_entries`.

## STEP 5 — Write the brief and log it

Short: the matchup's write-up and verdict, OPEN EXPOSURE, and Decision Log
rows via:

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
