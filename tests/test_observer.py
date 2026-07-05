"""Observer opportunity lifecycle: detect -> persist -> end, offline.

Drives synthetic book snapshots through the observer's hot path with a fake
adapter — no network, no asyncio timing dependence.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence

from triarb.config.models import Settings
from triarb.exchange.base import (
    BookSnapshot,
    ExchangeAdapter,
    StreamStatus,
    SymbolInfo,
    Ticker,
)
from triarb.observer import Observer

from tests.conftest import mk_symbol


class FakeAdapter(ExchangeAdapter):
    name = "fake"

    def __init__(self, symbols: list[SymbolInfo], tickers: list[Ticker]) -> None:
        self._symbols = symbols
        self._tickers = tickers

    async def fetch_symbols(self) -> list[SymbolInfo]:
        return self._symbols

    async def fetch_tickers(self) -> list[Ticker]:
        return self._tickers

    def stream_books(
        self, symbols: Sequence[str], channel: str
    ) -> AsyncIterator[BookSnapshot | StreamStatus]:
        raise NotImplementedError("tests drive _on_book directly")

    async def aclose(self) -> None:
        pass


def _snap(symbol: str, bids, asks) -> BookSnapshot:
    now = time.perf_counter_ns()
    return BookSnapshot(
        symbol=symbol,
        bids=bids,
        asks=asks,
        ts_exchange_ms=1,
        recv_ns=now,
        recv_wall_ms=int(time.time() * 1000),
    )


def _feed_profitable(observer: Observer) -> None:
    observer._on_book(_snap("BTCUSDT", [(49990.0, 5.0)], [(50000.0, 5.0)]))
    observer._on_book(_snap("ETHBTC", [(0.0499, 500.0)], [(0.05, 500.0)]))
    observer._on_book(_snap("ETHUSDT", [(2600.0, 500.0)], [(2601.0, 500.0)]))


async def _make_observer() -> Observer:
    settings = Settings.model_validate(
        {
            "quote_currency": "USDT",
            "triangles": {"auto_discovery": False, "whitelist": ["USDT-BTC-ETH"]},
            "observer": {"notional": 100.0, "edge_threshold_bps": 0.0},
        }
    )
    symbols = [
        mk_symbol("BTCUSDT", "BTC", "USDT"),
        mk_symbol("ETHUSDT", "ETH", "USDT", quantity_precision=4),
        mk_symbol("ETHBTC", "ETH", "BTC", quantity_precision=4),
    ]
    tickers = [
        Ticker(symbol=s.symbol, bid=1.0, ask=1.0001, last=1.0, usdt_volume_24h=1e7, ts_ms=1)
        for s in symbols
    ]
    observer = Observer(settings, FakeAdapter(symbols, tickers))
    await observer.prepare()
    return observer


class TestLifecycle:
    async def test_detection_and_end(self) -> None:
        observer = await _make_observer()
        _feed_profitable(observer)

        assert observer.metrics.counters["cycles_detected"] == 1
        assert "USDT>BTC>ETH" in observer.active
        opp = observer.active["USDT>BTC>ETH"]
        assert opp.last_net_bps > 360
        assert opp.last_feasible

        # Book improves: same opportunity updates in place, no re-detection.
        first_net = opp.last_net_bps
        observer._on_book(_snap("ETHUSDT", [(2610.0, 500.0)], [(2611.0, 500.0)]))
        assert observer.metrics.counters["cycles_detected"] == 1
        assert observer.active["USDT>BTC>ETH"] is opp
        assert opp.peak_net_bps > first_net
        assert opp.evals >= 2

        # Edge collapses: ETH/USDT bid drops, cycle now loses money.
        observer._on_book(_snap("ETHUSDT", [(2400.0, 500.0)], [(2401.0, 500.0)]))
        assert "USDT>BTC>ETH" not in observer.active
        assert observer.metrics.counters["cycles_ended"] == 1

    async def test_no_detection_below_threshold(self) -> None:
        observer = await _make_observer()
        observer._threshold_bps = 500.0  # above the fixture's ~368 bps edge
        _feed_profitable(observer)
        assert observer.metrics.counters["cycles_detected"] == 0
        assert not observer.active

    async def test_disconnect_ends_opportunities_and_stales_books(self) -> None:
        observer = await _make_observer()
        _feed_profitable(observer)
        assert observer.active

        observer._on_stream_status(
            StreamStatus(StreamStatus.DISCONNECTED, "test", time.perf_counter_ns())
        )
        assert not observer.active
        assert observer.metrics.counters["ws_disconnects"] == 1
        assert observer.metrics.counters["cycles_ended"] == 1
        # Books are unusable until fresh snapshots arrive.
        book = observer.store.get("BTCUSDT")
        assert book is not None and book.age_ms(time.perf_counter_ns()) == float("inf")

    async def test_rounding_killed_counter(self) -> None:
        observer = await _make_observer()
        # Whole-ETH steps: 0.04 ETH rounds to zero -> float edge positive,
        # execution impossible.
        observer.meta["ETHBTC"] = mk_symbol(
            "ETHBTC", "ETH", "BTC", quantity_precision=0
        )
        _feed_profitable(observer)
        assert observer.metrics.counters["cycles_detected"] == 1
        assert observer.metrics.counters["rounding_killed"] == 1
        assert not observer.active["USDT>BTC>ETH"].last_feasible
