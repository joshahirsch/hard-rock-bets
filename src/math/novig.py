"""Deterministic market math.

Faithful port of ``claude/market-math-spec.md`` (Phase 2, AUTHORITATIVE).
Every constant and formula in this module cites the section it came from.
Nothing here estimates a probability -- fair probability is always an INPUT
(market-math-spec.md §2, §7 limitation 7).

Section map:
  §1  odds conventions / validation
  §2  implied probability, decimal odds, proportional devig, fair odds,
      EV, minimum acceptable odds
  §3  CLV sign conventions, line-movement helpfulness
  §4  composite CLV classifier + 0.5 pp neutral band
  §5  validation and data-hygiene tolerances
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Named constants (market-math-spec.md §5: "Tolerances ... are named constants
# in the module -- change them there and here together, nowhere else.")
# ---------------------------------------------------------------------------

#: §4 -- composite CLV neutral tolerance band, in percentage points of implied
#: probability. |delta| < 0.5 pp is treated as price noise / tick granularity.
NEUTRAL_CLV_BAND_PP = 0.5

#: §5 -- two-sided implied sum at or below 1.0 is a data error (rejected).
MIN_OVERROUND = 1.0

#: §5 -- two-sided implied sum above 1.25 is flagged suspicious, not fatal.
SUSPICIOUS_OVERROUND = 1.25

#: §5 -- the two sides of a market must be retrieved within 10 minutes.
MAX_PAIR_GAP_MINUTES = 10

#: §5 -- a price older than 60 minutes at evaluation time is stale.
MAX_PRICE_AGE_MINUTES = 60

#: §1 -- minimum absolute value of a valid American price.
MIN_ABS_AMERICAN_ODDS = 100

#: Floating-point slack used when comparing a computed implied probability
#: against a threshold. Not a spec constant -- an implementation detail so that
#: exact ties (e.g. implied(-100) == 0.5 vs. a threshold of exactly 0.5) are not
#: lost to binary representation error.
_EPS = 1e-9


class OddsError(ValueError):
    """Raised for malformed odds or an out-of-contract two-sided pair.

    market-math-spec.md §1: malformed odds are "rejected, never coerced".
    """


# ---------------------------------------------------------------------------
# §1 -- odds conventions and validation
# ---------------------------------------------------------------------------


def validate_american_odds(odds) -> int:
    """Validate an American price per market-math-spec.md §1.

    Valid: integers with ``|odds| >= 100`` and ``odds != 0``. Positive odds are
    stored *without* a ``+`` sign. ``-100`` and ``+100`` are both valid and are
    the same price (even money).

    Rejected (never coerced): ``0``, ``+-50``, ``99``, non-numeric values, and
    non-integers such as ``130.5``.
    """
    if isinstance(odds, bool):
        raise OddsError(f"malformed odds (bool): {odds!r}")
    if isinstance(odds, float):
        if not float(odds).is_integer():
            raise OddsError(f"malformed odds (non-integer): {odds!r}")
        odds = int(odds)
    if not isinstance(odds, int):
        raise OddsError(f"malformed odds (non-numeric): {odds!r}")
    if odds == 0:
        raise OddsError("malformed odds: 0 is not a valid American price")
    if abs(odds) < MIN_ABS_AMERICAN_ODDS:
        raise OddsError(f"malformed odds (|odds| < 100): {odds!r}")
    return odds


def canonical_american_odds(odds: int) -> int:
    """Return the canonical form of a price.

    market-math-spec.md §1: "-100 and +100 are both valid and are the same
    price (even money); the canonical fair-odds output for p = 0.5 is +100."
    """
    odds = validate_american_odds(odds)
    return 100 if odds == -100 else odds


# ---------------------------------------------------------------------------
# §2 -- core formulas
# ---------------------------------------------------------------------------


def implied_probability(odds: int) -> float:
    """Vig-inclusive implied probability of an American price (§2).

    negative ``o`` -> ``|o| / (|o| + 100)``; positive ``o`` -> ``100 / (o + 100)``.
    """
    odds = validate_american_odds(odds)
    if odds < 0:
        return abs(odds) / (abs(odds) + 100.0)
    return 100.0 / (odds + 100.0)


def decimal_odds(odds: int) -> float:
    """Total return per $1 staked (§2).

    negative ``o`` -> ``1 + 100/|o|``; positive ``o`` -> ``1 + o/100``.
    Identity: ``implied == 1 / decimal``.
    """
    odds = validate_american_odds(odds)
    if odds < 0:
        return 1.0 + 100.0 / abs(odds)
    return 1.0 + odds / 100.0


def no_vig_probability(odds_side: int, odds_opposing: int) -> float:
    """Proportional (multiplicative) devig for two-outcome markets (§2).

    ``p1_novig = p1 / (p1 + p2)``. Requires BOTH sides observed. An implied sum
    at or below 1.0 is a data error and is rejected, not normalized.

    Note the deliberate limitation (§7 item 1): proportional devig only. Power
    and Shin methods are not implemented.
    """
    p1 = implied_probability(odds_side)
    p2 = implied_probability(odds_opposing)
    overround = p1 + p2
    if overround <= MIN_OVERROUND:
        raise OddsError(
            f"implied sum {overround:.6f} <= {MIN_OVERROUND} -- data error, "
            "rejected rather than normalized (market-math-spec.md §2/§5)"
        )
    return p1 / overround


def overround(odds_side: int, odds_opposing: int) -> float:
    """The two-sided implied sum (``p1 + p2``), a.k.a. the book's total (§2)."""
    return implied_probability(odds_side) + implied_probability(odds_opposing)


def is_suspicious_overround(value: float) -> bool:
    """True if the overround exceeds the §5 suspicion threshold of 1.25.

    Surfaced to the user, not auto-fatal -- heavy-vig exotics exist.
    """
    return value > SUSPICIOUS_OVERROUND


def probability_to_fair_odds(p: float) -> float:
    """Probability -> fair American odds (§2), continuous; round only for display.

    ``p > 0.5`` -> ``-100 * p / (1 - p)``; ``p < 0.5`` -> ``+100 * (1 - p) / p``;
    ``p == 0.5`` -> ``+100``.
    """
    if not (0.0 < p < 1.0):
        raise OddsError(f"probability out of range (0,1): {p!r}")
    if math.isclose(p, 0.5, rel_tol=0.0, abs_tol=_EPS):
        return 100.0
    if p > 0.5:
        return -100.0 * p / (1.0 - p)
    return 100.0 * (1.0 - p) / p


def expected_value_per_dollar(p: float, odds: int) -> float:
    """EV per $1 staked (§2): ``EV = p*(d-1) - (1-p)``.

    ``p`` is an INPUT. This module provides no way to produce one -- see
    ``src/fair_probability/estimator.py`` (fair-probability-spec.md §6, which
    binds decisions to the *conservative* end of the band).
    """
    if not (0.0 <= p <= 1.0):
        raise OddsError(f"probability out of range [0,1]: {p!r}")
    d = decimal_odds(odds)
    return p * (d - 1.0) - (1.0 - p)


def minimum_acceptable_odds(p_c: float, edge_pp: float) -> Optional[int]:
    """Minimum acceptable American odds given a conservative fair probability (§2).

    Acceptable iff ``p_c - implied(odds) >= edge_pp/100``. The minimum
    acceptable price is the integer American price with the **highest implied
    probability still <= p_c - edge_pp/100** -- rounding always in the safe
    direction, so the returned price always satisfies the requirement.

    Returns ``None`` if ``p_c - edge_pp/100 <= 0`` (no price qualifies).

    Spec-worked checks (all covered by tests):
      * ``p_c=0.46, edge=3.00`` -> ``+133`` (market-math-spec.md §2)
      * ``p_c=0.53, edge=3.00`` -> ``+100`` (market-math-spec.md §8)
      * ``p_c=0.51, edge=3.00`` -> ``+109`` (fair-probability-spec.md §9)
      * ``p_c=0.51, edge=1.50`` -> ``+103`` (revalidation-spec.md §8, IC3)
    """
    if not (0.0 <= p_c <= 1.0):
        raise OddsError(f"probability out of range [0,1]: {p_c!r}")
    threshold = p_c - edge_pp / 100.0
    if threshold <= 0.0:
        return None

    if threshold >= 0.5:
        # Negative (or even-money) prices: implied(-o) = o/(o+100) <= threshold
        # => o <= 100*threshold/(1-threshold).
        if threshold >= 1.0:
            return None  # every price qualifies; no meaningful minimum
        raw = 100.0 * threshold / (1.0 - threshold)
        candidate = -int(math.floor(raw + _EPS))
        if candidate > -MIN_ABS_AMERICAN_ODDS:
            candidate = -MIN_ABS_AMERICAN_ODDS
    else:
        # Positive prices: implied(+o) = 100/(o+100) <= threshold
        # => o >= 100*(1-threshold)/threshold.
        raw = 100.0 * (1.0 - threshold) / threshold
        candidate = int(math.ceil(raw - _EPS))
        if candidate < MIN_ABS_AMERICAN_ODDS:
            candidate = MIN_ABS_AMERICAN_ODDS

    # Safe-direction correction loop: guarantee the returned price satisfies the
    # requirement, and that it is the *tightest* such price (no wasted edge).
    candidate = _walk_to_safe_side(candidate, threshold)
    return canonical_american_odds(candidate)


def _walk_to_safe_side(candidate: int, threshold: float) -> int:
    """Nudge ``candidate`` so implied(candidate) <= threshold and it is tightest."""

    def implied(o: int) -> float:
        return implied_probability(o)

    def step_worse_for_bettor(o: int) -> int:
        """Next integer price with a HIGHER implied probability."""
        if o > 0:
            return o - 1 if o - 1 >= MIN_ABS_AMERICAN_ODDS else -MIN_ABS_AMERICAN_ODDS
        return o - 1

    def step_better_for_bettor(o: int) -> int:
        """Next integer price with a LOWER implied probability."""
        if o < 0:
            return o + 1 if o + 1 <= -MIN_ABS_AMERICAN_ODDS else MIN_ABS_AMERICAN_ODDS
        return o + 1

    guard = 0
    while implied(candidate) > threshold + _EPS and guard < 10_000:
        candidate = step_better_for_bettor(candidate)
        guard += 1
    while guard < 10_000:
        nxt = step_worse_for_bettor(candidate)
        if implied(nxt) <= threshold + _EPS:
            candidate = nxt
            guard += 1
        else:
            break
    return candidate


def edge_pp(fair_probability: float, odds: int) -> float:
    """Edge in probability percentage points (fair-probability-spec.md §6).

    ``edge = (fair_probability - implied(odds)) * 100``.
    """
    return (fair_probability - implied_probability(odds)) * 100.0


# ---------------------------------------------------------------------------
# §3 -- CLV sign conventions
# ---------------------------------------------------------------------------


def price_clv_pp(executed_odds: int, closing_odds: int) -> float:
    """Price CLV in percentage points (§3).

    ``price_clv_pp = (implied(closing) - implied(executed)) * 100``.
    POSITIVE = the bet beat the close.

    §3 resolution note: the Phase 2 charter sketched "executed implied -
    closing implied" while also mandating "POSITIVE = beat the close"; those are
    algebraically incompatible. The sign convention was binding, so this
    implements **closing - executed**.
    """
    return (implied_probability(closing_odds) - implied_probability(executed_odds)) * 100.0


def line_movement_pts(executed_line: float, closing_line: float) -> float:
    """Raw signed line movement in points (§3): ``closing_line - executed_line``.

    Never combined arithmetically with price movement (§3).
    """
    return closing_line - executed_line


class LineHelpfulness(str, Enum):
    HELPFUL = "HELPFUL"
    HARMFUL = "HARMFUL"
    UNCHANGED = "UNCHANGED"
    UNKNOWN = "UNKNOWN"


def line_move_helpfulness(
    market_type: str,
    executed_line: float,
    closing_line: float,
    total_side: Optional[str] = None,
) -> LineHelpfulness:
    """Classify a line move from the ticket-holder's perspective (§3).

    * Spread (line = the signed handicap on YOUR side): HELPFUL iff
      ``closing < executed`` (a bigger number in hand is always better).
    * Total, Over: HELPFUL iff ``closing > executed`` (you hold the lower total).
    * Total, Under: HELPFUL iff ``closing < executed`` (you hold the higher total).
    """
    mt = market_type.lower()
    if closing_line == executed_line:
        return LineHelpfulness.UNCHANGED
    if mt == "spread":
        return (
            LineHelpfulness.HELPFUL if closing_line < executed_line else LineHelpfulness.HARMFUL
        )
    if mt == "total":
        side = (total_side or "").lower()
        if side == "over":
            return (
                LineHelpfulness.HELPFUL
                if closing_line > executed_line
                else LineHelpfulness.HARMFUL
            )
        if side == "under":
            return (
                LineHelpfulness.HELPFUL
                if closing_line < executed_line
                else LineHelpfulness.HARMFUL
            )
        return LineHelpfulness.UNKNOWN
    return LineHelpfulness.UNKNOWN


# ---------------------------------------------------------------------------
# §4 -- composite CLV classifier
# ---------------------------------------------------------------------------


class CLVClass(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    NOT_COMPARABLE = "NOT COMPARABLE"


@dataclass(frozen=True)
class CLVResult:
    classification: CLVClass
    rule: str
    price_clv_pp: Optional[float]
    line_movement_pts: Optional[float]
    line_helpfulness: Optional[LineHelpfulness]
    reasoning: str


def classify_clv(
    market_type: str,
    executed_odds,
    closing_odds=None,
    executed_line: Optional[float] = None,
    closing_line: Optional[float] = None,
    total_side: Optional[str] = None,
) -> CLVResult:
    """Deterministic, exhaustive composite CLV classifier (§4, rules R1-R6).

    Missing *closing* data is an answer (NOT COMPARABLE), never an error.
    Missing/invalid *executed* data is a record-keeping failure: this raises
    (§4 final bullet).

    The bet's RESULT never affects classification (R6) -- it is not an input.
    """
    executed_odds = validate_american_odds(executed_odds)  # R6 / §4: raises
    mt = market_type.lower()

    if closing_odds in (None, "", "NOT CAPTURED"):
        return CLVResult(
            CLVClass.NOT_COMPARABLE, "R1", None, None, None,
            "Closing odds missing or unusable.",
        )
    try:
        closing_odds = validate_american_odds(closing_odds)
    except OddsError:
        return CLVResult(
            CLVClass.NOT_COMPARABLE, "R1", None, None, None,
            "Closing odds malformed.",
        )

    clv = price_clv_pp(executed_odds, closing_odds)

    if mt == "moneyline":
        return _classify_by_price(clv, "R3", None, None)

    # Spread / total from here.
    if closing_line is None or executed_line is None:
        return CLVResult(
            CLVClass.NOT_COMPARABLE, "R2", clv, None, None,
            "Spread/total with a missing or unusable closing line.",
        )

    move = line_movement_pts(executed_line, closing_line)
    if closing_line == executed_line:
        res = _classify_by_price(clv, "R4", move, LineHelpfulness.UNCHANGED)
        return res

    helpfulness = line_move_helpfulness(mt, executed_line, closing_line, total_side)
    if helpfulness is LineHelpfulness.HELPFUL and clv >= NEUTRAL_CLV_BAND_PP:
        return CLVResult(
            CLVClass.POSITIVE, "R5", clv, move, helpfulness,
            "Changed line: both line and price moved decisively in the bettor's favor.",
        )
    if helpfulness is LineHelpfulness.HARMFUL and clv <= -NEUTRAL_CLV_BAND_PP:
        return CLVResult(
            CLVClass.NEGATIVE, "R5", clv, move, helpfulness,
            "Changed line: both line and price moved decisively against the bettor.",
        )
    return CLVResult(
        CLVClass.NOT_COMPARABLE, "R5", clv, move, helpfulness,
        "Changed line: prices at different lines are not probability-comparable "
        "and the two dimensions did not move decisively the same way.",
    )


def _classify_by_price(
    clv: float, rule: str, move: Optional[float], helpfulness: Optional[LineHelpfulness]
) -> CLVResult:
    if clv >= NEUTRAL_CLV_BAND_PP:
        return CLVResult(CLVClass.POSITIVE, rule, clv, move, helpfulness,
                         f"Price CLV {clv:+.2f} pp >= +{NEUTRAL_CLV_BAND_PP} pp.")
    if clv <= -NEUTRAL_CLV_BAND_PP:
        return CLVResult(CLVClass.NEGATIVE, rule, clv, move, helpfulness,
                         f"Price CLV {clv:+.2f} pp <= -{NEUTRAL_CLV_BAND_PP} pp.")
    return CLVResult(CLVClass.NEUTRAL, rule, clv, move, helpfulness,
                     f"Price CLV {clv:+.2f} pp inside the "
                     f"+-{NEUTRAL_CLV_BAND_PP} pp neutral band.")


# ---------------------------------------------------------------------------
# §5 -- two-sided market gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairCheck:
    ok: bool
    fatal_errors: tuple
    flags: tuple


def check_two_sided_pair(
    odds_side: int,
    odds_opposing: int,
    ts_side_minutes: Optional[float] = None,
    ts_opposing_minutes: Optional[float] = None,
    age_minutes: Optional[float] = None,
) -> PairCheck:
    """Two-sided market gate (§5).

    Timestamps are expressed as minutes on any common monotonic scale;
    ``age_minutes`` is the age of the older quote at evaluation time.

    Fatal: invalid odds, implied sum <= 1.0, pair gap > 10 min, age > 60 min.
    Flagged (not fatal): overround > 1.25, missing timestamps.
    """
    fatal, flags = [], []
    try:
        total = overround(odds_side, odds_opposing)
    except OddsError as exc:
        return PairCheck(False, (str(exc),), ())
    if total <= MIN_OVERROUND:
        fatal.append(f"implied sum {total:.6f} <= {MIN_OVERROUND}")
    if is_suspicious_overround(total):
        flags.append(f"overround {total:.4f} > {SUSPICIOUS_OVERROUND} (suspicious)")
    if ts_side_minutes is None or ts_opposing_minutes is None:
        flags.append("missing retrieval timestamp(s)")
    elif abs(ts_side_minutes - ts_opposing_minutes) > MAX_PAIR_GAP_MINUTES:
        fatal.append(
            f"pair retrieved {abs(ts_side_minutes - ts_opposing_minutes):.1f} min apart "
            f"(> {MAX_PAIR_GAP_MINUTES} min)"
        )
    if age_minutes is not None and age_minutes > MAX_PRICE_AGE_MINUTES:
        fatal.append(f"price age {age_minutes:.1f} min > {MAX_PRICE_AGE_MINUTES} min (stale)")
    return PairCheck(not fatal, tuple(fatal), tuple(flags))
