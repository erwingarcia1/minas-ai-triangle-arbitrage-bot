"""Depth walking — executable conversion for a given size.

Pure float functions on ``list[tuple[price, size]]``; these are the hot path
and allocate nothing beyond the returned tuple.

Failure modes: if the visible book cannot absorb the requested size the walk
returns ``None`` — callers must treat that as "no opportunity", never
extrapolate beyond the last level.
"""

from __future__ import annotations


def buy_base_with_quote(
    asks: list[tuple[float, float]], quote_amount: float
) -> tuple[float, float] | None:
    """Spend ``quote_amount`` walking the asks. Returns (base_bought, worst_price).

    ``asks`` must be ascending by price. Returns None if the book is empty or
    too shallow for the full amount.
    """
    if quote_amount <= 0.0:
        return None
    remaining = quote_amount
    base_out = 0.0
    worst = 0.0
    for price, size in asks:
        if price <= 0.0 or size <= 0.0:
            continue
        level_cost = price * size
        worst = price
        if remaining <= level_cost:
            base_out += remaining / price
            return base_out, worst
        base_out += size
        remaining -= level_cost
    return None


def sell_base_for_quote(
    bids: list[tuple[float, float]], base_amount: float
) -> tuple[float, float] | None:
    """Sell ``base_amount`` walking the bids. Returns (quote_received, worst_price).

    ``bids`` must be descending by price. Returns None if the book is empty
    or too shallow for the full amount.
    """
    if base_amount <= 0.0:
        return None
    remaining = base_amount
    quote_out = 0.0
    worst = 0.0
    for price, size in bids:
        if price <= 0.0 or size <= 0.0:
            continue
        worst = price
        if remaining <= size:
            quote_out += remaining * price
            return quote_out, worst
        quote_out += size * price
        remaining -= size
    return None
