"""Exact feasibility: can this cycle survive lot sizes, precision, and minimums?

This is the cold-path Decimal confirmation run only on triangles whose float
screen cleared the edge threshold. It simulates the three IOC limit orders
the executor (Phase 2) would actually place:

- BUY leg: limit price = worst walked ask rounded UP to the price tick
  (stays marketable); quantity = spendable amount / limit price rounded DOWN
  to the quantity step. Fee charged in the received base currency.
- SELL leg: quantity = holdings rounded DOWN to the quantity step; limit
  price = worst walked bid rounded DOWN to the price tick (stays
  marketable). Fee charged in the received quote currency.

Rounding is step-based (exact multiples of the venue's tick/step Decimals),
not decimal-places-based, so non-power-of-ten increments round correctly.

Rounding down strands dust in intermediate currencies. Stranded non-quote
dust is valued at ZERO in the net-edge-after-rounding figure (pessimistic)
and reported separately; unspent quote from leg 1 never left the account and
is counted back in.

Failure modes / model limits:
- ``min_notional`` (Bitget ``minTradeUSDT``) is checked against the starting
  notional with a 5% haircut as a proxy for each leg's USDT value — exact
  per-leg USDT valuation needs conversion rates that only the executor will
  track. The check is skipped when the cycle's quote currency is not USDT.
- Books too shallow for the size reject with ``insufficient_depth``; the
  simulation never extrapolates past visible levels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from triarb.exchange.base import SymbolInfo
from triarb.pricing.edge import Side, Triangle
from triarb.pricing.orderbook import BookStore

_MIN_NOTIONAL_HAIRCUT = Decimal("0.95")


@dataclass(frozen=True)
class LegPlan:
    symbol: str
    side: str
    limit_price: Decimal
    quantity: Decimal
    received: Decimal


@dataclass
class FeasibilityReport:
    ok: bool
    reject_reason: str | None
    net_bps_after_rounding: float
    final_quote: Decimal
    stranded: dict[str, Decimal] = field(default_factory=dict)
    legs: list[LegPlan] = field(default_factory=list)


def _down(value: Decimal, step: Decimal) -> Decimal:
    """Largest multiple of ``step`` not exceeding ``value``.

    Step-based (not decimal-places) because venue increments are not always
    powers of ten — e.g. a 0.5 price tick. Deterministic: floor division.
    """
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _up(value: Decimal, step: Decimal) -> Decimal:
    """Smallest multiple of ``step`` not below ``value``."""
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


def _walk_buy_worst(
    asks: list[tuple[float, float]], spend: Decimal
) -> Decimal | None:
    """Worst ask price consumed when spending ``spend`` quote units."""
    remaining = spend
    for price_f, size_f in asks:
        if price_f <= 0.0 or size_f <= 0.0:
            continue
        price = Decimal(str(price_f))
        size = Decimal(str(size_f))
        level_cost = price * size
        if remaining <= level_cost:
            return price
        remaining -= level_cost
    return None


def _walk_buy_cost(
    asks: list[tuple[float, float]], qty: Decimal
) -> Decimal | None:
    """Exact cost of buying ``qty`` base units off the asks."""
    remaining = qty
    cost = Decimal(0)
    for price_f, size_f in asks:
        if price_f <= 0.0 or size_f <= 0.0:
            continue
        price = Decimal(str(price_f))
        size = Decimal(str(size_f))
        if remaining <= size:
            cost += remaining * price
            return cost
        cost += size * price
        remaining -= size
    return None


def _walk_sell(
    bids: list[tuple[float, float]], qty: Decimal
) -> tuple[Decimal, Decimal] | None:
    """(proceeds, worst_price) for selling ``qty`` base into the bids."""
    remaining = qty
    proceeds = Decimal(0)
    worst = Decimal(0)
    for price_f, size_f in bids:
        if price_f <= 0.0 or size_f <= 0.0:
            continue
        price = Decimal(str(price_f))
        size = Decimal(str(size_f))
        worst = price
        if remaining <= size:
            proceeds += remaining * price
            return proceeds, worst
        proceeds += size * price
        remaining -= size
    return None


def _reject(reason: str) -> FeasibilityReport:
    return FeasibilityReport(
        ok=False, reject_reason=reason, net_bps_after_rounding=0.0, final_quote=Decimal(0)
    )


def simulate_rounded_cycle(
    triangle: Triangle,
    store: BookStore,
    meta: dict[str, SymbolInfo],
    notional: Decimal,
    fee_rate: Decimal,
    quote_currency: str,
) -> FeasibilityReport:
    """Simulate the cycle with exchange-exact rounding at ``notional`` quote units."""
    keep = Decimal(1) - fee_rate
    holding = notional
    stranded: dict[str, Decimal] = {}
    legs: list[LegPlan] = []
    check_min_notional = quote_currency == "USDT"
    usd_proxy = notional * _MIN_NOTIONAL_HAIRCUT

    for leg in triangle.legs:
        info = meta.get(leg.symbol)
        if info is None:
            return _reject("missing_metadata")
        book = store.get(leg.symbol)
        if book is None or book.recv_ns == 0:
            return _reject("missing_book")

        if leg.side is Side.BUY:
            worst = _walk_buy_worst(book.asks, holding)
            if worst is None:
                return _reject("insufficient_depth")
            limit_price = _up(worst, info.price_tick_size)
            qty = _down(holding / limit_price, info.quantity_step_size)
            if qty <= 0:
                return _reject("qty_rounds_to_zero")
            if qty < info.min_base_amount:
                return _reject("min_qty")
            if check_min_notional and info.min_notional > 0 and usd_proxy < info.min_notional:
                return _reject("min_notional")
            cost = _walk_buy_cost(book.asks, qty)
            if cost is None:
                return _reject("insufficient_depth")
            leftover = holding - cost
            if leftover < 0:
                # limit-price rounding can make qty*limit exceed holdings by a
                # hair; shave one step off the quantity and re-cost.
                qty -= info.quantity_step_size
                if qty <= 0 or qty < info.min_base_amount:
                    return _reject("qty_rounds_to_zero")
                cost = _walk_buy_cost(book.asks, qty)
                if cost is None:
                    return _reject("insufficient_depth")
                leftover = holding - cost
            received = qty * keep
            if leftover > 0:
                stranded[leg.cur_from] = stranded.get(leg.cur_from, Decimal(0)) + leftover
            legs.append(LegPlan(leg.symbol, "buy", limit_price, qty, received))
            holding = received
        else:
            qty = _down(holding, info.quantity_step_size)
            if qty <= 0:
                return _reject("qty_rounds_to_zero")
            if qty < info.min_base_amount:
                return _reject("min_qty")
            if check_min_notional and info.min_notional > 0 and usd_proxy < info.min_notional:
                return _reject("min_notional")
            fill = _walk_sell(book.bids, qty)
            if fill is None:
                return _reject("insufficient_depth")
            proceeds, worst = fill
            limit_price = _down(worst, info.price_tick_size)
            received = proceeds * keep
            dust = holding - qty
            if dust > 0:
                stranded[leg.cur_from] = stranded.get(leg.cur_from, Decimal(0)) + dust
            legs.append(LegPlan(leg.symbol, "sell", limit_price, qty, received))
            holding = received

    # Unspent quote from leg 1 never left the account; count it back in.
    recovered_quote = stranded.pop(quote_currency, Decimal(0))
    total_out = holding + recovered_quote
    net_bps = float((total_out - notional) / notional * 10_000)
    return FeasibilityReport(
        ok=True,
        reject_reason=None,
        net_bps_after_rounding=net_bps,
        final_quote=total_out,
        stranded=stranded,
        legs=legs,
    )
