"""Fee compounding math.

Fees are modeled as a fraction of the *received* currency on every leg
(taker, since all legs cross the spread with IOC): three legs at rate ``f``
multiply proceeds by ``(1 - f)^3``, so the gross cycle multiplier must exceed
``1 / (1 - f)^3`` to break even — NOT simply ``1 + 3f``.

Failure modes: none at runtime (pure math), but note the model assumption:
venues charge buy-leg fees in base currency and sell-leg fees in quote; a
uniform multiplier on the received amount captures both exactly.
"""

from __future__ import annotations


def hurdle_bps(fee_rate: float, legs: int = 3) -> float:
    """Gross edge (bps) a cycle must clear to break even after fees.

    ``fee_rate`` is a fraction per leg (0.001 = 10 bps). At 0.1% taker and
    three legs the hurdle is ~30.06 bps, slightly above the naive 30.
    """
    if not 0.0 <= fee_rate < 1.0:
        raise ValueError(f"fee_rate must be in [0, 1), got {fee_rate}")
    return (1.0 / (1.0 - fee_rate) ** legs - 1.0) * 10_000.0


def net_multiplier(gross_multiplier: float, fee_rate: float, legs: int = 3) -> float:
    """Cycle multiplier after compounding per-leg fees."""
    return gross_multiplier * (1.0 - fee_rate) ** legs
