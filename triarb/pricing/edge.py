"""Cycle edge evaluation — the hot-path float screen.

``evaluate_cycle`` walks all three legs' books at the configured notional and
returns net edge after fees in bps. It is float math by design: fast, called
on every book update for every triangle touching that symbol. Candidates that
clear the threshold are re-checked exactly (Decimal, lot sizes, min notional)
by ``triarb.pricing.feasibility`` before being reported as actionable.

Failure modes:
- Any missing/empty/stale leg book, or a book too shallow for the notional,
  yields a result with ``ok=False`` and a machine-readable reason. The
  evaluator never guesses around bad data.
- Float rounding can misjudge edges within ~1e-9 relative error; the Decimal
  confirmation pass is authoritative for anything logged as detected.
"""

from __future__ import annotations

import enum

from triarb.pricing.depth import buy_base_with_quote, sell_base_for_quote
from triarb.pricing.orderbook import BookStore


class Side(enum.IntEnum):
    BUY = 0
    SELL = 1


class Leg:
    """One conversion step: spend ``cur_from``, receive ``cur_to`` on ``symbol``."""

    __slots__ = ("symbol", "side", "cur_from", "cur_to")

    def __init__(self, symbol: str, side: Side, cur_from: str, cur_to: str) -> None:
        self.symbol = symbol
        self.side = side
        self.cur_from = cur_from
        self.cur_to = cur_to

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Leg({self.cur_from}->{self.cur_to} {self.side.name} {self.symbol})"


class Triangle:
    """A compiled three-leg cycle starting and ending in the quote currency."""

    __slots__ = ("tri_id", "legs", "symbols")

    def __init__(self, tri_id: str, legs: tuple[Leg, Leg, Leg]) -> None:
        self.tri_id = tri_id
        self.legs = legs
        self.symbols = frozenset(leg.symbol for leg in legs)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Triangle({self.tri_id})"


class EdgeResult:
    """Outcome of one cycle evaluation."""

    __slots__ = ("ok", "reason", "gross_bps", "net_bps", "max_age_ms", "worst_prices")

    def __init__(
        self,
        ok: bool,
        reason: str,
        gross_bps: float,
        net_bps: float,
        max_age_ms: float,
        worst_prices: tuple[float, float, float],
    ) -> None:
        self.ok = ok
        self.reason = reason
        self.gross_bps = gross_bps
        self.net_bps = net_bps
        self.max_age_ms = max_age_ms
        self.worst_prices = worst_prices


_NO_PRICES = (0.0, 0.0, 0.0)


def evaluate_cycle(
    triangle: Triangle,
    store: BookStore,
    notional: float,
    fee_rate: float,
    now_ns: int,
    staleness_ms: float,
) -> EdgeResult:
    """Depth-walk all three legs at ``notional`` (quote units).

    Returns gross (fee-free) and net (fee-compounded) edge in bps. Both are
    computed from the same books in one pass over each leg; the fee-free and
    fee-adjusted amounts are walked separately because fees shrink the size
    entering later legs.
    """
    gross_amt = notional
    net_amt = notional
    max_age = 0.0
    worst: list[float] = [0.0, 0.0, 0.0]
    keep = 1.0 - fee_rate
    for i, leg in enumerate(triangle.legs):
        book = store.get(leg.symbol)
        if book is None or book.recv_ns == 0:
            return EdgeResult(False, "missing_book", 0.0, 0.0, float("inf"), _NO_PRICES)
        age = book.age_ms(now_ns)
        if age > max_age:
            max_age = age
        if age > staleness_ms:
            return EdgeResult(False, "stale_book", 0.0, 0.0, max_age, _NO_PRICES)
        if leg.side is Side.BUY:
            net_fill = buy_base_with_quote(book.asks, net_amt)
            gross_fill = buy_base_with_quote(book.asks, gross_amt)
        else:
            net_fill = sell_base_for_quote(book.bids, net_amt)
            gross_fill = sell_base_for_quote(book.bids, gross_amt)
        if net_fill is None or gross_fill is None:
            return EdgeResult(False, "insufficient_depth", 0.0, 0.0, max_age, _NO_PRICES)
        net_amt = net_fill[0] * keep
        gross_amt = gross_fill[0]
        worst[i] = net_fill[1]
    gross_bps = (gross_amt / notional - 1.0) * 10_000.0
    net_bps = (net_amt / notional - 1.0) * 10_000.0
    return EdgeResult(True, "ok", gross_bps, net_bps, max_age, (worst[0], worst[1], worst[2]))
