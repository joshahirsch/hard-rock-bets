"""Flat suggested-stake sizing.

Faithful port of ``claude/stake-sizing-spec.md`` (2026-08-11, AUTHORITATIVE).

This is the project's first and only authorization of any stake content, and
it is deliberately narrow:

  * flat, never edge-scaled (§3)
  * no confidence tiers -- Lean/Solid/Strong stay retired (§3)
  * informational only; it does NOT authorize the agent to place, size, or
    execute a real wager, and no execution capability exists anywhere in this
    repository (§5)
  * it lives in the delivered brief text only, NOT as a Decision Log column (§4)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Named constants -- stake-sizing-spec.md §3.
# §6: zero real data exists to validate either figure. Unfitted constants.
# ---------------------------------------------------------------------------

#: The flat suggested stake, in dollars, before the bankroll cap is applied.
FLAT_STAKE_USD = 10.0

#: Hard cap as a fraction of CURRENT bankroll, so the suggestion scales down
#: automatically if the bankroll shrinks below $100.
BANKROLL_CAP_FRACTION = 0.10

#: Absolute floor, in dollars.
STAKE_FLOOR_USD = 1.0

#: §1 -- the only two Final Decision Types that carry a suggested stake.
STAKE_ELIGIBLE_DECISION_TYPES = ("Research Candidate", "Market-Efficiency Candidate")


@dataclass(frozen=True)
class StakeSuggestion:
    amount_usd: Optional[float]
    eligible: bool
    binding_constraint: Optional[str]
    text: str

    def __str__(self) -> str:
        return self.text


def suggested_stake(bankroll_usd: float) -> float:
    """``min($10, round_down(10% x current bankroll))``, floored at $1 (§3).

    ``bankroll_usd`` must be the CURRENT bankroll read fresh at STEP 1 of the
    same brief run -- never a fixed historical number (§3).
    """
    if bankroll_usd < 0:
        raise ValueError("bankroll cannot be negative")
    capped = math.floor(bankroll_usd * BANKROLL_CAP_FRACTION)
    return max(float(min(FLAT_STAKE_USD, capped)), STAKE_FLOOR_USD)


def suggest_for_candidate(
    final_decision_type: str, bankroll_usd: float
) -> StakeSuggestion:
    """Attach a suggested stake to a candidate, if and only if it is eligible.

    §3: "Applies once per cleared candidate, not once per day -- if C1's
    same-day same-team cap or C2/C3 already downgraded a candidate to Pass
    before this point, no stake is suggested (there is nothing to size)."
    """
    if final_decision_type not in STAKE_ELIGIBLE_DECISION_TYPES:
        return StakeSuggestion(
            amount_usd=None,
            eligible=False,
            binding_constraint=None,
            text=(
                f"No suggested stake — Final Decision Type is "
                f"{final_decision_type!r}; sizing applies only to "
                f"{' or '.join(STAKE_ELIGIBLE_DECISION_TYPES)}."
            ),
        )
    amount = suggested_stake(bankroll_usd)
    capped = math.floor(bankroll_usd * BANKROLL_CAP_FRACTION)
    if amount <= STAKE_FLOOR_USD and capped < STAKE_FLOOR_USD:
        constraint = "floor"
    elif capped < FLAT_STAKE_USD:
        constraint = "bankroll cap"
    else:
        constraint = "flat figure"
    return StakeSuggestion(
        amount_usd=amount,
        eligible=True,
        binding_constraint=constraint,
        text=f"Suggested stake (informational, not an instruction): ${amount:,.0f}",
    )
