"""Venue adapter interface and exchange payload models.

Design: pydantic models validate every REST payload at the boundary (symbol
metadata, tickers). WebSocket book messages are the hot path and deliberately
bypass pydantic — they are parsed into ``BookSnapshot``, a ``__slots__`` class
holding plain float tuples, to keep per-message allocation low.

Failure modes:
- REST payloads that fail validation raise at the boundary; nothing malformed
  reaches the graph or pricing layers.
- WebSocket streams can stall or drop silently; adapters MUST yield
  ``StreamStatus`` events on connect/disconnect/error so staleness and (in
  later phases) kill-switch logic never depend on message flow alone.
- Symbol metadata drifts (precision changes, delistings). Callers refresh via
  ``fetch_symbols`` on a timer; adapters must not cache it internally forever.

Phase 1 note: this interface contains NO order, balance, withdrawal, or
transfer methods. Trading methods arrive in Phase 2 behind the paper/live
gate, and withdrawal/transfer code is banned from this repository entirely.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


class SymbolInfo(BaseModel):
    """Normalized spot symbol metadata.

    ``price_precision`` / ``quantity_precision`` are decimal places (step =
    10^-precision). ``min_notional`` is denominated in USDT on Bitget
    (``minTradeUSDT``); other venues may differ — adapters normalize.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    base: str
    quote: str
    price_precision: int = Field(ge=0, le=18)
    quantity_precision: int = Field(ge=0, le=18)
    min_base_amount: Decimal = Decimal("0")
    min_notional: Decimal = Decimal("0")
    status: str = "online"


class Ticker(BaseModel):
    """24h ticker snapshot, used only for startup liquidity/spread filtering."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    usdt_volume_24h: float = 0.0
    ts_ms: int = 0

    @property
    def spread_bps(self) -> float:
        """Quoted spread in bps; +inf when either side is missing."""
        if self.bid <= 0.0 or self.ask <= 0.0 or self.ask < self.bid:
            return float("inf")
        mid = (self.ask + self.bid) / 2.0
        return (self.ask - self.bid) / mid * 10_000.0


class BookSnapshot:
    """One order-book update. Hot path: __slots__, plain floats, no pydantic.

    ``bids`` descending by price, ``asks`` ascending — exactly as the venue
    publishes them; levels are never averaged, smoothed, or reordered.
    """

    __slots__ = ("symbol", "bids", "asks", "ts_exchange_ms", "recv_ns", "recv_wall_ms")

    def __init__(
        self,
        symbol: str,
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
        ts_exchange_ms: int,
        recv_ns: int,
        recv_wall_ms: int,
    ) -> None:
        self.symbol = symbol
        self.bids = bids
        self.asks = asks
        self.ts_exchange_ms = ts_exchange_ms
        self.recv_ns = recv_ns
        self.recv_wall_ms = recv_wall_ms

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"BookSnapshot({self.symbol}, bids={len(self.bids)}, asks={len(self.asks)}, "
            f"ts={self.ts_exchange_ms})"
        )


class StreamStatus:
    """Control event from a market-data stream (connect/disconnect/error)."""

    __slots__ = ("kind", "detail", "recv_ns")

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"

    def __init__(self, kind: str, detail: str, recv_ns: int) -> None:
        self.kind = kind
        self.detail = detail
        self.recv_ns = recv_ns

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"StreamStatus({self.kind}, {self.detail!r})"


class ExchangeAdapter(abc.ABC):
    """Interface every venue implements. Switching venues touches only the
    concrete adapter module plus one line in the registry."""

    name: ClassVar[str]

    @abc.abstractmethod
    async def fetch_symbols(self) -> list[SymbolInfo]:
        """All online spot symbols with precision/min-size metadata."""

    @abc.abstractmethod
    async def fetch_tickers(self) -> list[Ticker]:
        """24h tickers for liquidity and spread filtering."""

    @abc.abstractmethod
    def stream_books(
        self, symbols: Sequence[str], channel: str
    ) -> AsyncIterator[BookSnapshot | StreamStatus]:
        """Yield book snapshots and stream-status events forever.

        Must reconnect internally with backoff and resubscribe, emitting
        ``StreamStatus`` around every gap so consumers can mark books stale.
        """

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Release network resources. Idempotent."""
