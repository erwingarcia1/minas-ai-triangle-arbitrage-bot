"""Shared test fixtures: synthetic symbols, books, and triangles.

The canonical fixture triangle is USDT -> BTC -> ETH -> USDT priced so the
cycle returns 104 USDT per 100 (gross +400 bps):
  BTC/USDT ask 50000, ETH/BTC ask 0.05, ETH/USDT bid 2600.
"""

from __future__ import annotations

from decimal import Decimal

from triarb.exchange.base import BookSnapshot, SymbolInfo
from triarb.pricing.edge import Leg, Side, Triangle
from triarb.pricing.orderbook import BookStore

NOW_NS = 1_000_000_000_000


def mk_symbol(
    symbol: str,
    base: str,
    quote: str,
    price_precision: int = 2,
    quantity_precision: int = 6,
    min_base_amount: str = "0",
    min_notional: str = "1",
    status: str = "online",
    price_tick_size: str | None = None,
    quantity_step_size: str | None = None,
) -> SymbolInfo:
    """Build a SymbolInfo; precisions are decimal places unless explicit
    tick/step strings are given (for non-power-of-ten increment tests)."""
    return SymbolInfo(
        symbol=symbol,
        base=base,
        quote=quote,
        price_tick_size=(
            Decimal(price_tick_size)
            if price_tick_size is not None
            else Decimal(1).scaleb(-price_precision)
        ),
        quantity_step_size=(
            Decimal(quantity_step_size)
            if quantity_step_size is not None
            else Decimal(1).scaleb(-quantity_precision)
        ),
        min_base_amount=Decimal(min_base_amount),
        min_notional=Decimal(min_notional),
        status=status,
    )


def mk_store(
    books: dict[str, tuple[list[tuple[float, float]], list[tuple[float, float]]]],
    recv_ns: int = NOW_NS,
) -> BookStore:
    """Build a store from ``{symbol: (bids, asks)}``."""
    store = BookStore()
    for sym, (bids, asks) in books.items():
        store.apply(
            BookSnapshot(
                symbol=sym,
                bids=bids,
                asks=asks,
                ts_exchange_ms=1,
                recv_ns=recv_ns,
                recv_wall_ms=1,
            )
        )
    return store


def mk_triangle() -> Triangle:
    """USDT -> BTC -> ETH -> USDT via BTCUSDT / ETHBTC / ETHUSDT."""
    return Triangle(
        "USDT>BTC>ETH",
        (
            Leg("BTCUSDT", Side.BUY, "USDT", "BTC"),
            Leg("ETHBTC", Side.BUY, "BTC", "ETH"),
            Leg("ETHUSDT", Side.SELL, "ETH", "USDT"),
        ),
    )


def profitable_books() -> dict[str, tuple[list[tuple[float, float]], list[tuple[float, float]]]]:
    return {
        "BTCUSDT": ([(49990.0, 5.0)], [(50000.0, 5.0)]),
        "ETHBTC": ([(0.0499, 500.0)], [(0.05, 500.0)]),
        "ETHUSDT": ([(2600.0, 500.0)], [(2601.0, 500.0)]),
    }
