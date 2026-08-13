# MLB — Afternoon research (~11:00 AM ET)

Shadow mode. You produce research classifications and log them. **You never
place, size, or authorize a wager**, and you have no capability to do so. The
deterministic service holds all the math and gate logic — **never re-derive a
formula, a threshold, or a gate rule in prose.**

Service base URL: `https://hard-rock-bets.fly.dev`
Rulebook: `docs/spec.md` in the pipeline repo, and `claude/research-contract.md`
§5 for the MLB checklist. Read them for anything below that needs detail — do
not restate their rules here from memory.

---

## STEP 1 — Any MLB games today?

```
POST https://hard-rock-bets.fly.dev/odds/mlb
```

If `event_count` is 0, write one line — *"No MLB games today."* — and stop.
This is the normal outcome on an off day, not a gap to fill. Otherwise
continue.

## STEP 2 — Read the tracker

```
GET https://hard-rock-bets.fly.dev/decision-log
```

Use this, not a direct sheet read — it's the same atomic store
`/decision-log/append` writes to. Identify every **open** entry (`Research
Candidate` or `Market-Efficiency Candidate`, event not yet started). Also read
the Current Bankroll figure fresh — never carry over a prior run's number.

## STEP 3 — Research and classify

Per `docs/spec.md` §2 / `research-contract.md` §5 MLB checklist: probable
pitchers confirmed by name and date (Tier 1 — MLB Stats API or MLB.com, never
inferred), lineup-timing honesty (many day-game lineups **will** be posted by
11 AM — check, don't assume unposted the way an 8 AM run would have to),
bullpen recent usage if load-bearing, weather for outdoor parks. Apply the
soft-book priced-in guidance (`research-contract.md` §3a) for any non-marquee
MLB game — a recent Tier 1/2 fact defaults to **Uncertain**, not automatically
Y; **N** is the honest call where the feed is credibly still lagging. Tier 3 is
discovery-only, never support. Assign a literal thesis label. Classify: PASS,
REVALIDATION REQUIRED, or RESEARCH CANDIDATE.

## STEP 4 — Adjustments, skeptical pass, deterministic verdict

Write out named adjustments (direction, raw magnitude pp, tier, freshness
bucket, priced-in call), merging same-fact adjustments into one. Run the
skeptical pass explicitly — strongest argument you're wrong, most fragile
assumption, what the market likely already knows — verdict SURVIVES or
DEFEATED.

```
POST https://hard-rock-bets.fly.dev/evaluate
```

Include `bankroll_usd` set to the figure read fresh in STEP 2 on **every**
call — omitting it lets the service silently fall back to a stale configured
value. Include `market_efficiency` for any MLB h2h game where Hard Rock's
price diverges from the field, after checking GME0. Report the service's
numbers as returned — never round, re-derive, or argue with them.

## STEP 5 — Write the brief and log it

1. Shadow-mode banner. Tracker status (bankroll, or the failed-read note).
2. Each candidate: write-up, evidence with all four fields, checklist results,
   skeptical pass, then the service's gate verdict with **every rule's
   individual result**. For a clearance: IC1–IC5 and the suggested-stake line
   exactly as returned.
3. **OPEN EXPOSURE** — every open entry, or *"No open research-candidate
   exposure."* Mandatory even on a no-candidate day.
4. **NEAR-MISS WATCH** — NM3/NM4 per `docs/spec.md`, or *"No near-misses this
   run."*
5. Decision Log rows via:

   ```
   POST https://hard-rock-bets.fly.dev/decision-log/append
   ```

   One call per row. **Never write a row directly to the sheet.** Columns L
   and M stay empty.
6. Anything worth revisiting this evening.

Close with: *"Shadow mode: no wagers are authorized by this brief. If you place
a bet anyway, it is a Manual bet — log it as such with the exact slip price."*

---

## Hard prohibitions

- Never place, size, or authorize a wager. The suggested stake is
  informational, never an instruction.
- Never present an unobserved price as real.
- Never let a Tier 3 source support a classification or carry weight.
- Never recompute or restate a threshold from memory — call the service.
- Never write anything into Decision Log columns L or M.
- Always log the real result honestly, including a day with nothing to show.
