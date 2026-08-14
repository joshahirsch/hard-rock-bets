# Afternoon brief — ~4:00 PM ET (second firing)

Shadow mode. You produce research classifications and log them. **You never
place, size, or authorize a wager**, and you have no capability to do so.

This firing exists for one reason: the 8 AM read happens before the day's most
important facts exist. MLB lineups post 3–4 hours pre-game; soccer lineups ~60–75
minutes pre-kickoff; scratches happen all afternoon. **Nothing about how
strictly a candidate is judged changes here** — same thresholds, same priced-in
discipline, same tier hierarchy. Only *when* the judging happens changes.

Service base URL: `https://hard-rock-bets.fly.dev`
Rulebook: `docs/spec.md`. Do not restate its rules from memory.

---

## STEP 1 — Read the tracker

```
GET https://hard-rock-bets.fly.dev/decision-log
```

Use this, not a direct sheet read, for the Decision Log rows — it is the same
atomic store `/decision-log/append` writes to. Separately, read the Current
Bankroll figure fresh; never carry over the morning's number. Note
specifically:

- This morning's **REVALIDATION REQUIRED** rows — these are your first priority.
  Each carries a one-line note naming the exact still-unresolved REQUIRED item.
- This morning's **gate-CLEARED** rows — they now occupy exposure, and C1's
  same-day same-team cap applies against them.
- This morning's **Market-Efficiency Watch** rows — this firing is the second
  sighting GME5 requires.

## STEP 2 — Fetch fresh odds

```
POST https://hard-rock-bets.fly.dev/odds/mlb
```

Prices are same-run only. Never reuse the morning's snapshot for anything.

## STEP 3 — Re-check the morning's Revalidation Required candidates first

For each, check whether the named REQUIRED item has resolved. If it has, run
the full Step 3 → 3.5 → evaluate chain on it now with the freshly-fetched
price. If it hasn't, say so and leave it Revalidation Required.

Then research any newly-relevant games — late-slate games the morning couldn't
reach, and anything where an afternoon fact changed the picture.

Same qualitative work as the morning firing (`docs/spec.md` §2): four
evidence-contract fields per material fact, soft-book priced-in guidance,
Tier 3 discovery-only, sport REQUIRED checklist, literal thesis label.

## STEP 3.5 / 3.6 — Adjustments, skeptical pass, deterministic verdict

```
POST https://hard-rock-bets.fly.dev/evaluate
```

Include `bankroll_usd` set to the figure you read fresh in STEP 1 on **every**
call — omitting it lets the service silently fall back to a static configured
value instead of today's real number.

Pass the morning's still-open entries in `open_entries` so C1–C3 evaluate
correctly against them. Precedence is fixed: when the conflict is against an
earlier entry, **today's new candidate is the one capped** — never edit a prior
row.

For any morning **Market-Efficiency Watch**, set `prior_sightings_today` to the
morning's count so GME5's persistence check can complete. Re-check GME0 fresh —
an afternoon fact may now explain the divergence that the morning couldn't see,
which bars the pathway for the rest of the day.

## STEP 4 — Write the brief

Same structure as the morning: banner, tracker status, candidates with every
gate rule's individual result, IC1–IC5 and the suggested-stake line for any
clearance, **OPEN EXPOSURE**, **NEAR-MISS WATCH**, revisit notes.

Decision Log rows appended via `POST https://hard-rock-bets.fly.dev/decision-log/append`
— one call per row, never a direct sheet write. Columns L and M stay empty.

Lead with what actually changed since this morning — resolved checklist items,
scratches, price moves, and any morning candidate whose invalidation conditions
have now fired.

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
