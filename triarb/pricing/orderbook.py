"""Local order-book store.

Books hold the venue's levels verbatim — never averaged, smoothed, or merged
across updates (Phase 1 uses snapshot channels, so each update replaces the
whole book).

Failure modes:
- Staleness is measured from local monotonic receive time, not the exchange
  timestamp, so venue clock skew cannot mask a stalled feed.
- A snapshot-channel caveat: quiet symbols push no updates, so "no message"
  is indistinguishable from "book unchanged". Evaluations therefore carry
  ``max_age_ms`` in every log line, and books older than the configured
  staleness bound are treated as unusable rather than trusted.
"""

from __future__ import annotations

from triarb.exchange.base import BookSnapshot


class Book:
    """Mutable per-symbol book, updated in place to avoid churn."""

    __slots__ = ("symbol", "bids", "asks", "ts_exchange_ms", "recv_ns", "recv_wall_ms", "updates")

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.bids: list[tuple[float, float]] = []
        self.asks: list[tuple[float, float]] = []
        self.ts_exchange_ms = 0
        self.recv_ns = 0
        self.recv_wall_ms = 0
        self.updates = 0

    def age_ms(self, now_ns: int) -> float:
        """Milliseconds since the last update was received locally."""
        if self.recv_ns == 0:
            return float("inf")
        return (now_ns - self.recv_ns) / 1e6

    def is_stale(self, now_ns: int, max_age_ms: float) -> bool:
        return self.age_ms(now_ns) > max_age_ms


class BookStore:
    """All live books, keyed by venue symbol."""

    __slots__ = ("_books",)

    def __init__(self) -> None:
        self._books: dict[str, Book] = {}

    def apply(self, snap: BookSnapshot) -> Book:
        book = self._books.get(snap.symbol)
        if book is None:
            book = Book(snap.symbol)
            self._books[snap.symbol] = book
        book.bids = snap.bids
        book.asks = snap.asks
        book.ts_exchange_ms = snap.ts_exchange_ms
        book.recv_ns = snap.recv_ns
        book.recv_wall_ms = snap.recv_wall_ms
        book.updates += 1
        return book

    def get(self, symbol: str) -> Book | None:
        return self._books.get(symbol)

    def mark_all_stale(self) -> None:
        """Invalidate every book (called on WS disconnect)."""
        for book in self._books.values():
            book.recv_ns = 0

    def __len__(self) -> int:
        return len(self._books)
