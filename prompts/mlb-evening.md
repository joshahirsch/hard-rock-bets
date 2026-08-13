# MLB — Evening research (~5:30 PM ET)

Shadow mode. **You never place, size, or authorize a wager**, and you have no
capability to do so. This firing exists because evening-game lineups,
late-afternoon scratches, and weather updates post after the 11 AM run — same
thresholds, same discipline, only *when* the judging happens changes.

Service base URL: `https://hard-rock-bets.fly.dev`
Rulebook: `docs/spec.md`, `claude/research-contract.md` §5 MLB checklist.

---

## STEP 1 — Any MLB games left today?

```
POST https://hard-rock-bets.fly.dev/odds/mlb
```

If `event_count` is 0, or every listed event's `commence_time` has already
passed, write one line and stop. Otherwise continue with games that haven't
started.

## STEP 2 — Read the tracker

```
GET https://hard-rock-bets.fly.dev/decision-log
```

Note specifically: this morning's **REVALIDATION REQUIRED** rows (first
priority — check whether the named REQUIRED item has resolved), this morning's
**gate-CLEARED** rows (occupy exposure; C1's same-day same-team cap applies),
and any **Market-Efficiency Watch** rows (this may be the second GME5 sighting).
Read the Current Bankroll figure fresh; never reuse the 11 AM number.

## STEP 3 — Re-check, then research the rest of the slate

For each open Revalidation Required candidate: if the named item resolved, run
the full checklist → adjustments → skeptical pass → evaluate chain now with a
fresh price; if not, leave it Revalidation Required. Then research any
newly-relevant games (late lineups, scratches, an afternoon fact that changed
the picture) using the same MLB checklist and §3a priced-in guidance as the
morning run.

## STEP 4 — Adjustments, skeptical pass, deterministic verdict

```
POST https://hard-rock-bets.fly.dev/evaluate
```

Include `bankroll_usd` set to the figure read fresh in STEP 2 on every call.
Pass this morning's still-open entries in `open_entries` so C1–C3 evaluate
correctly — precedence is fixed: today's new candidate is always the one
capped against an earlier entry, never the reverse. For any Market-Efficiency
Watch, set `prior_sightings_today` and re-check GME0 fresh.

## STEP 5 — Write the brief and log it

Same structure as the 11 AM run: banner, tracker status, candidates with every
gate rule's result, IC1–IC5 and suggested stake for any clearance, OPEN
EXPOSURE, NEAR-MISS WATCH, revisit notes. Decision Log rows via:

```
POST https://hard-rock-bets.fly.dev/decision-log/append
```

Never a direct sheet write. Columns L and M stay empty. Lead with what
actually changed since 11 AM.

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
