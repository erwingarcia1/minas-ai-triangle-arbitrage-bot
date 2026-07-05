"""Book store: updates, staleness, disconnect invalidation."""

from __future__ import annotations

from triarb.exchange.base import BookSnapshot
from triarb.pricing.orderbook import BookStore

from tests.conftest import NOW_NS


def _snap(symbol: str = "BTCUSDT", recv_ns: int = NOW_NS) -> BookSnapshot:
    return BookSnapshot(
        symbol=symbol,
        bids=[(99.0, 1.0)],
        asks=[(100.0, 1.0)],
        ts_exchange_ms=123,
        recv_ns=recv_ns,
        recv_wall_ms=456,
    )


class TestBookStore:
    def test_apply_creates_and_updates(self) -> None:
        store = BookStore()
        book = store.apply(_snap())
        assert store.get("BTCUSDT") is book
        assert book.updates == 1
        assert book.asks == [(100.0, 1.0)]

        again = store.apply(_snap(recv_ns=NOW_NS + 1))
        assert again is book  # updated in place, no churn
        assert book.updates == 2
        assert len(store) == 1

    def test_age_and_staleness(self) -> None:
        store = BookStore()
        book = store.apply(_snap(recv_ns=NOW_NS))
        assert book.age_ms(NOW_NS + int(100e6)) == 100.0
        assert not book.is_stale(NOW_NS + int(300e6), 300.0)
        assert book.is_stale(NOW_NS + int(301e6), 300.0)

    def test_unseen_book_is_infinitely_stale(self) -> None:
        store = BookStore()
        assert store.get("NOPE") is None

    def test_mark_all_stale_on_disconnect(self) -> None:
        store = BookStore()
        book = store.apply(_snap())
        store.mark_all_stale()
        assert book.is_stale(NOW_NS, 300.0)
        assert book.age_ms(NOW_NS) == float("inf")
