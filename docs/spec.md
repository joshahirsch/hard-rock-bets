# Hard Rock Bet — Consolidated Rulebook

**This document is a SUMMARY.** `src/` is the source of truth for exact
behavior; where this page and the code disagree, the code (and the tests that
pin it to the original specs' own worked examples) wins. This page exists to
replace the ~15 cross-referencing spec files the v1 prompt system had to
re-derive in prose every firing.

**Shadow mode.** This pipeline produces research candidates and logs them. It
never places or authorizes a wager. There is no sportsbook write path anywhere
in this codebase. A gate-cleared candidate carries a flat, informational
suggested stake — that is a number, not an instruction, and a human decides
and executes independently.

Provenance for every rule below is given as `[source-doc §section]`. Every
numeric constant in this project is a **reasoned default, not fitted to data**
unless explicitly stated otherwise.

---

## 0. Pipeline at a glance

```
STEP 1  read tracker (bankroll, open Decision Log exposure)
STEP 2  fetch odds  ......................  POST /odds/{sport}
STEP 3  qualitative research  ............  AGENT JUDGMENT (this document, §2)
          classify: PASS | REVALIDATION REQUIRED | RESEARCH CANDIDATE
STEP 3.5   fair-probability estimate  ....  deterministic (§3)  + skeptical pass
STEP 3.5-ME market-efficiency edge  ......  deterministic (§6)
STEP 3.6   selection gate G1–G6  .........  deterministic (§4)
           exposure control C1–C3  .......  deterministic (§5)
STEP 3.7   invalidation conditions IC1–IC5  deterministic (§7)
           suggested stake  ..............  deterministic (§8)
STEP 4  write the brief, append Decision Log rows
```

Steps 3.5 through 3.7 plus the stake are ONE call: `POST /evaluate`. Nothing
in that range should ever be recomputed in prose.

**What stays with the agent** (judgment, not arithmetic): assigning a source
tier, recording publication times, making the priced-in call, choosing an
adjustment's direction and raw magnitude, running the skeptical pass, labelling
the thesis, and confirming each REQUIRED checklist item. Everything else is
computed here, once.

---

## 1. Market math `[market-math-spec.md]`

**Odds conventions §1.** American odds are integers with `|odds| ≥ 100` and
`odds ≠ 0`, positive stored without `+`. `−100` and `+100` are the same price;
canonical output for p = 0.5 is `+100`. Anything else (`0`, `±50`, `99`,
`130.5`, non-numeric) is malformed and **rejected, never coerced**.

**Core formulas §2.**

| Quantity | Formula |
|---|---|
| Implied probability | `o < 0 → |o|/(|o|+100)`; `o > 0 → 100/(o+100)` |
| Decimal odds | `o < 0 → 1+100/|o|`; `o > 0 → 1+o/100` (`implied = 1/decimal`) |
| Proportional devig (2-outcome only) | `p1_novig = p1/(p1+p2)`; requires both sides; `p1+p2 ≤ 1.0` is a data error → reject, never normalize |
| Probability → fair American | `p>0.5 → −100p/(1−p)`; `p<0.5 → +100(1−p)/p`; `p=0.5 → +100` |
| EV per $1 | `EV = p(d−1) − (1−p)`; `p` is always an INPUT |
| Minimum acceptable odds | acceptable iff `p_c − implied(odds) ≥ E/100`; the answer is the integer price with the **highest implied probability still ≤ `p_c − E/100`** (rounds to the safe side). `p_c − E/100 ≤ 0` → no price qualifies. |

Worked values pinned by tests: `p_c=0.46, E=3 → +133`; `p_c=0.53, E=3 → +100`;
`p_c=0.51, E=3 → +109`; `p_c=0.51, E=1.50 → +103`.

**CLV sign conventions §3.**
`price_clv_pp = (implied(closing) − implied(executed)) × 100`. **Positive =
the bet beat the close.** (The Phase 2 charter's "executed − closing" sketch
was algebraically incompatible with that mandate; the sign convention was
binding.) `line_movement_pts = closing_line − executed_line`, recorded
separately and **never arithmetically combined** with price movement.

Line-move helpfulness: spread → helpful iff `closing < executed`; total/Over →
helpful iff `closing > executed`; total/Under → helpful iff `closing < executed`.

**Composite CLV classifier §4** — output is exactly one of POSITIVE / NEUTRAL /
NEGATIVE / NOT COMPARABLE. **Neutral band = 0.5 pp** of implied probability.

- R1 closing odds missing/unusable → NOT COMPARABLE
- R2 spread/total with missing closing line → NOT COMPARABLE
- R3 moneyline: `≥ +0.5` POSITIVE, `≤ −0.5` NEGATIVE, else NEUTRAL
- R4 spread/total at an **identical** closing line → classify by R3
- R5 spread/total at a **changed** line → NOT COMPARABLE, except line HELPFUL
  and price `≥ +0.5` → POSITIVE, or line HARMFUL and price `≤ −0.5` → NEGATIVE
- R6 the bet's RESULT never affects classification

Missing **closing** data is an answer. Missing/invalid **executed** data is a
record-keeping failure and raises.

**Tolerances §5** (named constants, changed in one place only):
overround `≤ 1.0` rejected · overround `> 1.25` flagged suspicious (not fatal) ·
two-sided pair retrieval gap `> 10 min` rejected · price age `> 60 min` stale,
rejected · missing timestamps flagged, not fatal.

**Known limits §7:** proportional devig only; two-outcome markets only; no
parlay/teaser/live/cash-out math.

---

## 2. Research contract `[research-contract.md]`

**Source tiers §2.**

| Tier | What | Privilege |
|---|---|---|
| **1** Primary / official | MLB Stats API (`statsapi.mlb.com`, primary since 2026-07-24) or MLB.com probables, official league injury/IL reports, team announcements and coach pressers, NWS point forecasts, team-issued lineup posts | May directly support any classification including RESEARCH CANDIDATE, subject to freshness |
| **2** Established reporting | **Named** beat writers (outlet + byline), major outlets' *news desks* (not their picks verticals), on-record local radio/TV reporters | Usable only when named **with source and publication time**; otherwise treat as Tier 3 |
| **3** Pick / consensus sites | VSiN, Dimers, Covers, Action Network, Pickswise, SportsGrid, etc. | **DISCOVERY ONLY.** May surface a lead. May **never** be cited as support, raise an assessment, or carry any weight. A Tier-3 "edge"/"win probability" is a third-party claim, never adopted or blended. |

Tiering is by **content type, not outlet brand**: an ESPN news-desk injury
report is Tier 2; an ESPN expert-pick column is Tier 3.

**Evidence contract §3** — every material fact carries all four, inline:
1. **Source** (named; "reports say" is not a source)
2. **Tier** (1/2/3)
3. **Publication time** (the fact's timestamp, *not* retrieval time; if truly
   unavailable write literally "time unknown")
4. **Priced-in judgment** (Y / N / Uncertain + one line of why)

A fact missing any field cannot support a classification stronger than PASS.

**§3a soft-book / thin-market priced-in guidance (added 2026-08-11).** "The
fact is publicly known" and "the fact is priced into Hard Rock Bet's specific
line" are **different claims**. For in-scope markets — WNBA (any), MLS (any),
non-marquee MLB games, and any market where Hard Rock Bet's own price is being
evaluated directly — a Tier 1/2 fact published within roughly the last several
hours defaults to **Uncertain**, not automatically Y, unless there is specific
articulable evidence Hard Rock's own line already moved (a visible shift
between two snapshots, or a `last_update` postdating the fact). Where the feed
is credibly still lagging, **N is the honest call, not Uncertain** — do not
default to the middle category out of caution. Out of scope (old default
applies): NFL and nationally-televised marquee MLB games. This is deliberately
a judgment recalibration with **no numeric threshold**.

Motivation, stated plainly: across 131 logged candidates, **zero** facts were
ever judged N. That is itself evidence the old blanket-efficiency default was
too aggressive for a retail book.

**Freshness §4.** Facts older than 24 h must be re-verified this run, else
flagged STALE — NOT RE-VERIFIED and capped at PASS-supporting weight.
"Time unknown" facts may support PASS or REVALIDATION REQUIRED only, never
RESEARCH CANDIDATE alone. **Odds snapshots are same-run only** — never reuse an
earlier run's prices.

**Sport checklists §5** — a candidate missing a REQUIRED item can never be
RESEARCH CANDIDATE; it is capped at PASS or REVALIDATION REQUIRED (agent's
choice, based on whether the item is likely to resolve later today). SUPPORTING
items never gate by themselves.

*MLB REQUIRED:* probable pitchers confirmed by name and date from a Tier 1
source (never inferred from a rotation pattern) · lineup-timing honesty
(state explicitly that lineups are not posted at 8 AM ET; they post 3–4 h
pre-game, so lineup-dependent factors are provisional) · bullpen recent usage
(last 2–3 days of appearances/pitch counts, if bullpen availability is part of
the case; season ERA is SUPPORTING at best) · weather for outdoor parks (NWS
point forecast, or an explicit "weather not a factor in this write-up").
*MLB SUPPORTING:* series/travel context, season splits, park factors, umpire.

Other sports' checklists are in `config/config.example.yaml`
(`research_contract.sport_checklists`): WNBA (rotation news, small-sample
discipline, no expansion-split reasoning), MLS (confirmed lineup — usually
unmet at 8 AM, competition-priority context), NFL (starting QB confirmed,
*final* official injury report, inactives-timing honesty, outdoor weather),
CFB (starting QB confirmed, injury-report opacity acknowledgment, roster
continuity for bowl/playoff/portal games, outdoor weather).

Where a REQUIRED item is structurally unknowable at brief time, **say so** —
never silently omit it or pad around it with Tier 3 speculation.

---

## 3. Fair-probability estimation `[fair-probability-spec.md]`

**§1 Baseline (mandatory).** Every estimate starts from the **no-vig market
probability** for the side in question, computed from a two-sided, same-run,
within-10-minute price pair. If either side is missing, stale (>60 min), or the
pair is not simultaneous, **there is no valid baseline and no estimate may be
produced for that candidate this run.**

**§2 Named adjustments.** Each carries direction, raw magnitude in pp (before
weighting), and full evidence-contract citation.

*§2a the priced-in gate — the framework's central discipline:*

| Priced-in | Weight |
|---|---|
| **Y** | **0.0** — excluded from the arithmetic; still written up as context, with a stated reason for exclusion |
| **N** | **1.0** |
| **Uncertain** | **0.5** default; a different discount must be argued, never assumed |

**Tier 3 can never support an adjustment at any weight.** Absolute.

*§2b combination:* `fair_point = no_vig_baseline + Σ(magnitude_i × weight_i)`.

**Cap: the sum of absolute values of applied weighted magnitudes is capped at
±8 pp.** If exceeded, every applied adjustment is scaled by `8 / raw_sum` —
**never** by dropping the largest or smallest, which would silently change what
the estimate claims to rest on. State when scaling happened and by what factor.

*Shared-cause rule:* adjustments tracing to the same underlying fact must be
merged into one before the sum, and the merge stated explicitly.

**§3 Uncertainty band.** `band_pp = base + freshness_penalty + conflict_penalty`,
computed only from **nonzero-weight** adjustments.

- **base** — 2 pp if Tier 1 carries the majority of applied weight; 4 pp if
  Tier 2 does (Tier 3 never contributes, so it can never be the majority).
- **freshness** — set by the least-fresh applied fact: +0 if all are
  timestamped and under 6 h; +1 if any is 6–24 h; +3 if any is "time unknown"
  or stale (>24 h) and used without this run's re-verification.
- **conflict** — +0 if all applied adjustments agree in direction; +2 if they
  conflict; **+1 additional** if a single adjustment supplies more than half
  the net signed sum while being individually weak (raw magnitude under 1.5 pp).
- **clamp** — minimum ±3 pp, maximum ±10 pp.

**§4 Pseudo-precision.** The adjusted fair probability is **always a range**.
A point value may be shown for convenience, rounded to the **nearest 2 pp**,
but never without its band in the same breath. Never quote to the tenth of a
point, never as a bare single number.

**§5 Skeptical pass (mandatory, every estimate).** Cover explicitly: the
strongest argument the estimate is wrong (steelman, not a token objection); the
single most fragile assumption; what the market likely already knows that the
adjustments claim it doesn't; and a verdict of **SURVIVES or DEFEATED**.
**A DEFEATED estimate does not ship** — no band, no edge, no EV; it is logged
as a pass with the specific defeating reason, and the pipeline stops there.

**§6 Edge and EV — the binding rule.** Edge is a band:
`edge_low = band_low − implied(price)`, `edge_high = band_high − implied(price)`.
**Any accept/reject determination, EV, and minimum-acceptable-odds calculation
must use the conservative (least favorable) end of the band.** The point
estimate and favorable end may be shown for transparency but are never the
basis of a determination.

---

## 4. Selection Gate G1–G6 `[selection-gate-spec.md §2–§4]`

Applies to a RESEARCH CANDIDATE whose §3.5 estimate SURVIVED. **Evaluate all
six every time and report every rule's individual result**, not just the first
failure — so it is auditable how *close* a failing candidate came.

| # | Rule | Threshold |
|---|---|---|
| **G1** | Edge floor | Conservative (band-low) edge **≥ +1.50 pp** |
| **G2** | Band ceiling | Band width **≤ 6.00 pp** |
| **G3** | Checklist reconfirmation | Every REQUIRED sport-checklist item explicitly MET |
| **G4** | Tier anchor | At least one **nonzero-weight** adjustment is **Tier 1** |
| **G5** | Skeptical-pass reconfirmation | Verdict is the literal **SURVIVES** |
| **G6** | Fail-closed default | No required G1–G5 input missing, ambiguous, or inconsistent with the Step 3/3.5 write-up |

**Outcome:** CLEARED only if all six pass. Otherwise NOT CLEARED, citing the
**first failing rule in G1–G6 order** as the deciding rule, listing any other
failures for transparency. CLEARED → `Final Decision Type = Research Candidate`;
NOT CLEARED → downgraded to `Pass` with `Reason for Pass` naming the rule.

**2026-08-11 restructuring, stated honestly.** G1 was lowered from +3.00 pp to
+1.50 pp at the project owner's explicit direction after three-plus weeks and 131 logged
candidates produced **zero** clearances. This is a deliberate loosening, not a
recalibration from data. The tension it creates is real and was accepted
knowingly: **+1.50 pp now sits below the framework's own ±3 pp minimum band
width**, so a G1-cleared candidate is no longer guaranteed to be distinguishable
from the estimate's own irreducible noise floor. **G2 and G4 were explicitly
offered as loosening options and NOT selected** — do not assume the
restructuring touched anything beyond G1.

G1 and G2 are independent: a candidate can clear one and fail the other in
either direction. G4 is distinct from both: a strong edge and a narrow band
built entirely from beat-writer reporting still fails for lack of a primary
anchor. A G3 failure is itself a signal something upstream is inconsistent, so
it also trips G6.

---

## 5. Correlation / exposure control C1–C3 `[selection-gate-spec.md §5–§7]`

**Definitions.** *Team* = either side's franchise name. *Series* = the same two
teams within a **rolling 7-calendar-day window** (simple and auditable, not
exhaustive). *Thesis* = a short literal label the agent assigns in Step 3
("bullpen fatigue", "trending total") specifically so it can be compared across
runs without semantic judgment. *Open entry* = a Decision Log row with
`Final Decision Type = Research Candidate` (or `Market-Efficiency Candidate`)
whose event has **not yet started** — win/loss/push is irrelevant to openness.

Applied in order to every candidate that reached a gate evaluation, after
reading back the Decision Log:

- **C1 — same-day same-team cap: max 1** gate-CLEARED candidate per team per
  day, regardless of market type **and regardless of pathway**.
- **C2 — same-thesis cap: max 1 open** entry per named thesis.
- **C3 — same-series cap: max 1 open** entry per series.

**Tie-break** within one day's batch: higher conservative-end edge wins; if
tied, the earlier write-up position wins. **Precedence:** when the conflict is
against a **prior day's** still-open entry, **today's new candidate is always
the one capped** — the Decision Log is append-only and a historical row is
never edited retroactively.

Capped → `Final Decision Type = Pass`, citing the specific rule and the
Decision ID it lost to.

**Mandatory OPEN EXPOSURE note §7.** Every brief — including a thin or
no-candidate day — lists every open entry (Decision ID, Event, Teams, Series,
Thesis, date logged), or states plainly *"No open research-candidate exposure."*

*Known limits:* exact string matching can miss a cross-market correlation
labelled with different thesis strings, or a same-team correlation spanning a
longer road trip — flag suspected correlations even outside the deterministic
triggers. N = 1 is the only cap value defined, deliberately.

---

## 6. Market-Efficiency pathway GME0–GME6 `[market-efficiency-candidate-spec.md]`

**A different claim, not a loosened gate.** The Selection Gate tests
*informational* edge: "we know something not yet priced in". This pathway tests
*market-efficiency* edge: **"Hard Rock Bet specifically is pricing this worse
for itself than the broader market believes is justified."** It needs no
adjustment, no Tier-1 fact, and no skeptical pass, so it cannot use G1–G6 —
G4 in particular is impossible for a pure price-discrepancy candidate.

**Scope: MLB moneyline (h2h) only.** Do not silently expand.

**Definitions.** *Contributing book* = any US book other than Hard Rock's keys,
in the same response, same event/market, with valid two-sided prices.
`consensus_p` = the **median** (not mean — robustness against one stale quote)
of the contributing books' own no-vig probabilities.
`consensus_spread_pp = (max − min) × 100`.
`me_edge_pp = consensus_p − implied(hardrock_price)`, measured only in the
favorable direction.

| # | Rule | Threshold |
|---|---|---|
| **GME0** | News-cause exclusion | **No** Tier 1/2 fact within **3 hours** plausibly explains the divergence |
| **GME1** | Edge floor | `me_edge_pp ≥ +1.50 pp` (reuses G1's constant) |
| **GME2** | Consensus tightness | `consensus_spread_pp ≤ 6.00 pp` (reuses G2's constant) |
| **GME3** | Minimum book count | **≥ 4** contributing books, each two-sided, retrieved this run, inside the 60-min staleness and 10-min pair gates |
| **GME4** | Leave-one-out robustness | After dropping the single book furthest from the group median, `me_edge_pp` must still clear GME1 |
| **GME5** | Persistence | The same divergence observed on **2 separate firings the same day** |
| **GME6** | Fail-closed | No required input missing, ambiguous, or inconsistent |

**GME0 is the one check this pathway can never loosen.** It exists specifically
to block pathway-shopping — trying the informational route, failing the
priced-in test, then re-labelling the same divergence as market-efficiency
edge. If a REQUIRED checklist item can't be confirmed, treat that as a GME0
trigger too.

**Outcome:** CLEARED only if all pass. A **GME5 first sighting is reported as
`Market-Efficiency Watch — pending second-firing reconfirmation`, not NOT
CLEARED** — it hasn't failed anything, it just hasn't finished the check.
CLEARED → `Final Decision Type = Market-Efficiency Candidate`.

C1–C3 apply identically and are **shared across both pathways**.

*Known limits:* GME4 only guards against a single outlier, not a correlated
error across books sharing an upstream feed. There is **no detection for
promotional/boosted odds** — a boost at any book, including Hard Rock's own,
looks identical to genuine mispricing; suspicion should hold the candidate at
Watch. This risk is elevated by the lower GME1 floor. GME3's 4-book minimum is
flagged in the spec as likely the *more* binding practical constraint than
GME1.

---

## 7. Pregame revalidation IC1–IC5 `[revalidation-spec.md §4]`

Generated for every candidate that ends at `Final Decision Type = Research
Candidate`, using **only information already gathered in the same run** — no
new research step, no new data source. Each is a concrete checkable rule or an
explicit `N/A — <why>`; never silently omitted.

| # | Condition |
|---|---|
| **IC1** | VOID IF the specific named probable pitcher / confirmed starter the thesis depends on does not start |
| **IC2** | VOID IF any player whose availability was weighted in a §3.5 adjustment is ruled out, downgraded, or scratched — named explicitly, cross-referenced to the adjustment |
| **IC3** | VOID IF the actual executable price at placement is worse than the **minimum acceptable American odds**, computed from the candidate's own conservative band-low `p_c` at **E = 1.50 pp**. State the exact price as a number. |
| **IC4** | *(outdoor candidates only)* VOID IF precipitation probability crosses above **50%** where the AM forecast was below it, the primary wind direction **reverses**, sustained wind shifts by **≥10 mph**, or temperature swings by **≥15 °F** from the AM figure. Indoor/non-weather candidates state **"N/A — not weather-dependent"** explicitly. |
| **IC5** | VOID IF the game is postponed, suspended pre-start, or moved off the assumed date |

**Fail-closed rule:** if a required upstream fact is missing or ambiguous, state
the condition with the literal word **UNRESOLVED** in place of that fact — and
treat it as a signal the write-up itself has a completeness gap. Ambiguity is
never resolved by dropping the check.

IC3 deliberately **reuses G1's constant rather than introducing a second one**,
so it tracked G1 down from 3.00 to 1.50 pp on 2026-08-11. A less demanding edge
mechanically means a worse (lower) price is now acceptable — for the running
worked example, the floor moved from +109 to +103.

**Presentation:** all five appear together as a labelled list under the
candidate's write-up in IC1–IC5 order, and are condensed into the Decision Log's
Invalidation Conditions column as a single semicolon-separated cell. A Pass row
gets `"N/A — not gate-CLEARED (<why>)"`. A Revalidation Required row gets the
lighter single-line form naming the still-unresolved REQUIRED item.

*Known limit:* this is entirely self-applied by the human at placement time.
Nothing observes whether the check actually happened.

---

## 8. Stake sizing `[stake-sizing-spec.md]`

**Suggested stake = `min($10, round_down(10% × current bankroll))`, floored at $1.**

- Bankroll is read **fresh at STEP 1 of the same run**, never a fixed
  historical number.
- **Flat, never edge-scaled.** A candidate clearing G1 at +1.6 pp gets the same
  suggestion as one clearing at +9 pp. Deliberate: there are zero settled bets
  under either gate to calibrate an edge-scaled formula against.
- **No confidence tiers.** Lean/Solid/Strong stay retired.
- The 10% cap exists so the suggestion scales down automatically if the
  bankroll shrinks below $100.
- Applies **only** to `Final Decision Type = Research Candidate` or
  `Market-Efficiency Candidate`, once per cleared candidate. If C1/C2/C3 already
  downgraded it to Pass, there is nothing to size.
- Presented in the **delivered brief text only**, never as a Decision Log
  column, in exactly this form:

  > **Suggested stake (informational, not an instruction): $10**

**What this does NOT do:** it does not authorize the agent to place, size, or
execute a wager (no execution capability exists and none is being added); it
does not change how a candidate is classified or capped; it does not revive
edge-scaled or tiered staking.

---

## 9. Decision Log schema

Columns A–Y, unchanged since Phase 1:

`A` Decision ID · `B` Run Date · `C` Event · `D` Market · `E` Side ·
`F` Current Reference Line · `G` Current Reference Odds · `H` Odds Source ·
`I` Retrieval Timestamp ET · `J` Opposing Side Line · `K` Opposing Side Odds ·
**`L` Market Implied Probability** · **`M` No-Vig Market Probability** ·
`N` Key Evidence · `O` Evidence Sources · `P` Evidence Publication Times ·
`Q` Priced-In Assessment · `R` Missing Information · `S` Skeptical Case ·
`T` Invalidation Conditions · `U` Correlation or Exposure Note ·
`V` Gate Outcome · `W` Final Decision Type · `X` Reason for Pass ·
`Y` Prompt Version

**L and M are formula-owned and must be written EMPTY.** Self-expanding array
formulas compute them from G and K; writing text into them breaks the spill
with `#REF!`.

`W` enum: `Pass` / `Research Candidate` / `Revalidation Required` /
`Market-Efficiency Candidate` / `Market-Efficiency Watch`. **Never `Bet`.**

`V` is `"CLEARED — <reason>"`, `"NOT CLEARED — <deciding rule + reason>"`, or
`"N/A — not evaluated (<why>)"`. `U` always states whether an exposure conflict
was checked and what was found.

**Writes are append-only.** Rows go in via `values.append` with
`insertDataOption=INSERT_ROWS` (atomic, server-chosen insertion point) and
Decision IDs are ULID-suffixed (`HRB-YYYYMMDD-<ULID>`) — both replacing v1's
read-count-and-compute pattern, which produced real collisions when firings
overlapped. No row is ever edited retroactively.

---

## 10. Judgment calls made while porting prose to code

These are places the source documents were ambiguous, silent, or internally
inconsistent. Each was resolved explicitly rather than quietly.

1. **Band endpoints are derived from the rounded point estimate.**
   `fair-probability-spec.md` §4 says the point is displayed to the nearest
   2 pp and never without its band, but doesn't say which value the band is
   centered on. §9 Demonstration A's own arithmetic uses the *rounded* 56%
   (band 51%–61%, governing edge −7.33 pp), not the raw 56.16% (which would
   give 51.16% and −7.17 pp). The code reproduces the spec's own numbers;
   `band_from_rounded_point: false` selects the alternative.

2. **Band base on an exact Tier-1/Tier-2 tie, or with no applied adjustments.**
   The spec says "the weighted majority" without defining a tie. Both cases
   resolve to the conservative Tier-2 base (4 pp), because in neither case does
   Tier 1 actually *carry the majority*.

3. **`market-efficiency-candidate-spec.md` §9's leave-one-out prose is
   arithmetically wrong.** It says Book E (40.7%) is "furthest from the median"
   of 41.1%, but Book D (41.6%) is 0.5 pp away versus E's 0.4 pp. GME4 is
   implemented as the **rule** is written in §3/§5 — drop the book furthest
   from the median — which drops D and yields +1.78 pp rather than the doc's
   stated +2.0 pp. **The verdict is unchanged either way** (both clear the
   +1.50 pp floor), so the demonstration's conclusion still holds. Both
   readings are pinned by tests so the discrepancy stays visible.

4. **The §3 "band exceeds ±10 pp, does not ship in this form" branch is
   unreachable.** Max base 4 + max freshness 3 + max conflict 2 + fragile 1 =
   exactly 10 pp, which is the clamp ceiling, not above it. The code computes
   the raw value and exposes `exceeds_max`, and a test pins the worst case at
   exactly 10 so a future constant change surfaces this rather than hiding it.

5. **`Uncertain` weight overrides must be explicit.** The spec permits a
   non-0.5 discount "unless the write-up states and justifies a different
   explicit discount". The code requires an explicit field; there is no
   implicit path to any weight other than 0.0 / 0.5 / 1.0.

6. **Y-weighted adjustments contribute nothing to the band either.** The band
   formula is defined over "nonzero-weight adjustments", so a priced-in-Y fact
   that is stale or points the other way does not widen the band. Pinned by a
   test, since it is easy to get wrong.

7. **Conditionally-REQUIRED checklist items** (MLB weather on a dome game,
   bullpen usage when bullpen availability isn't part of the case) are modelled
   as an explicit `N/A` status that satisfies G3, matching the contract's own
   "or explicitly note 'weather not a factor in this write-up'" language.
   `NOT YET KNOWABLE` does **not** satisfy G3.

8. **IC1–IC5, not IC1–IC4.** `revalidation-spec.md` defines five invalidation
   conditions; all five are implemented.

9. **A gate-CLEARED candidate capped by C1/C2/C3 gets no invalidation
   conditions.** `revalidation-spec.md` §2 scopes IC1–IC5 to candidates that
   *end* at `Final Decision Type = Research Candidate`; an exposure-capped
   candidate is a Pass, so there is nothing left to invalidate (and no stake
   to size).

10. **The Owls Insight relay is not reproduced.** The v1 Zapier relay existed
    only because the pipeline ran inside a scheduled agent session with no
    server. This service *is* the server side, so it holds the key and calls
    the vendor directly. The relay's 25,000-byte Storage-by-Zapier value cap —
    which forced dropping spreads and totals entirely — does not apply here, so
    market coverage is a config choice rather than a hard constraint. The
    load-bearing structural discovery is preserved and tested: `data` is keyed
    by bookmaker and each group carries only that book's price, so a multi-book
    view **requires** iterating every kept key and merging by event id.
