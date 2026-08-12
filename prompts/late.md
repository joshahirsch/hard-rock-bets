# Late pre-game check — ~5:45 PM ET

Shadow mode. **You never place, size, or authorize a wager**, and you have no
capability to do so.

This is a narrow, final look, not a third full research pass. Its whole purpose
is to catch what has broken since the afternoon firing on candidates that are
still open, close enough to first pitch that lineups and scratches are known.

Service base URL: `{{SERVICE_URL}}`
Rulebook: `docs/spec.md`.

---

## STEP 1 — Read the tracker

List every still-open entry from today's earlier firings: `Research Candidate`
and `Market-Efficiency Candidate` rows whose games have not started, plus any
`Market-Efficiency Watch` still pending reconfirmation.

If there are none, say so plainly in one line and stop after STEP 4. A quiet
late check is the normal outcome, not a gap to fill.

## STEP 2 — Fetch fresh odds

```
POST {{SERVICE_URL}}/odds/mlb
```

Same-run prices only.

## STEP 3 — Walk each open candidate's invalidation conditions

For each open candidate, take the IC1–IC5 list already recorded for it and
check each one against what is now knowable:

- **IC1** — did the named starter actually get confirmed to start?
- **IC2** — has any named thesis-critical player been scratched, downgraded, or
  ruled out?
- **IC3** — is the current price still at least the stated minimum acceptable
  odds? Compare in implied-probability terms; a lower number is worse.
- **IC4** — has the forecast moved past any of the stated thresholds?
- **IC5** — is the game still on, at the same date?

Report each as **HOLDS** or **VOIDED**, naming the specific condition and the
fact that fired it. If a condition was recorded as UNRESOLVED, say what is
still unresolved rather than guessing.

**Do not re-derive the price floor.** It is a number already recorded on the
candidate. If you need it recomputed for any reason, call the service.

## STEP 3.5 — Complete any pending Market-Efficiency persistence check

For a `Market-Efficiency Watch` from an earlier firing, re-check GME0 first,
then call:

```
POST {{SERVICE_URL}}/evaluate
```

with `market_efficiency.prior_sightings_today` set to the number of earlier
sightings today. Note the spec's own caveat: for a game already close to start
there may simply not be time for a valid second look, and staying at Watch is
the correct conservative outcome, not a bug to route around.

## STEP 4 — Write the brief

Short. Lead with any **VOIDED** candidate and the exact condition that fired.
Then the ones that still hold. Then **OPEN EXPOSURE**. Then Decision Log rows
for anything newly evaluated (L and M empty).

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
- Never introduce a new candidate at this hour that hasn't been through the
  full Step 3 → 3.5 → 3.6 chain. This firing checks, it doesn't originate.
