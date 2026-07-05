"""Graph construction, triangle enumeration, filtering, and whitelists."""

from __future__ import annotations

import pytest

from triarb.config.models import Settings
from triarb.exchange.base import Ticker
from triarb.graph.builder import build_adjacency
from triarb.graph.triangles import build_triangles, compile_triangle, enumerate_cycles
from triarb.pricing.edge import Side

from tests.conftest import mk_symbol


def _symbols():
    return [
        mk_symbol("BTCUSDT", "BTC", "USDT"),
        mk_symbol("ETHUSDT", "ETH", "USDT"),
        mk_symbol("ETHBTC", "ETH", "BTC"),
        mk_symbol("SOLUSDT", "SOL", "USDT"),  # no SOL cross pair: not in any triangle
    ]


def _ticker(symbol: str, volume: float = 10_000_000.0, bid: float = 100.0, ask: float = 100.01):
    return Ticker(symbol=symbol, bid=bid, ask=ask, last=bid, usdt_volume_24h=volume, ts_ms=1)


def _tickers(volume: float = 10_000_000.0):
    return [_ticker(s.symbol, volume) for s in _symbols()]


class TestEnumeration:
    def test_finds_both_directions(self) -> None:
        adj = build_adjacency(_symbols())
        cycles = set(enumerate_cycles(adj, "USDT"))
        assert cycles == {("USDT", "BTC", "ETH"), ("USDT", "ETH", "BTC")}

    def test_offline_symbols_excluded(self) -> None:
        symbols = _symbols() + [mk_symbol("XRPBTC", "XRP", "BTC", status="offline")]
        adj = build_adjacency(symbols)
        assert "XRP" not in adj

    def test_compile_sides(self) -> None:
        adj = build_adjacency(_symbols())
        tri = compile_triangle(("USDT", "BTC", "ETH"), adj)
        assert [(leg.symbol, leg.side) for leg in tri.legs] == [
            ("BTCUSDT", Side.BUY),
            ("ETHBTC", Side.BUY),
            ("ETHUSDT", Side.SELL),
        ]
        rev = compile_triangle(("USDT", "ETH", "BTC"), adj)
        assert [(leg.symbol, leg.side) for leg in rev.legs] == [
            ("ETHUSDT", Side.BUY),
            ("ETHBTC", Side.SELL),
            ("BTCUSDT", Side.SELL),
        ]

    def test_compile_missing_market_raises(self) -> None:
        adj = build_adjacency(_symbols())
        with pytest.raises(ValueError, match="no market"):
            compile_triangle(("USDT", "BTC", "SOL"), adj)


class TestDiscovery:
    def _settings(self, **overrides) -> Settings:
        cfg = {
            "quote_currency": "USDT",
            "filters": {"min_24h_quote_volume_usdt": 1_000_000, "max_spread_bps": 15},
        }
        cfg.update(overrides)
        return Settings.model_validate(cfg)

    def test_keeps_liquid_triangles(self) -> None:
        triangles, stats = build_triangles(_symbols(), _tickers(), self._settings())
        assert {t.tri_id for t in triangles} == {"USDT>BTC>ETH", "USDT>ETH>BTC"}
        assert stats.kept == 2
        assert stats.raw_cycles == 2

    def test_volume_filter_drops_everything(self) -> None:
        with pytest.raises(ValueError, match="no triangles survived"):
            build_triangles(_symbols(), _tickers(volume=10.0), self._settings())

    def test_spread_filter(self) -> None:
        tickers = [
            _ticker("BTCUSDT"),
            _ticker("ETHUSDT"),
            # ~198 bps quoted spread on the cross leg kills both directions
            _ticker("ETHBTC", bid=100.0, ask=102.0),
            _ticker("SOLUSDT"),
        ]
        with pytest.raises(ValueError, match="no triangles survived"):
            build_triangles(_symbols(), tickers, self._settings())

    def test_max_triangles_cap(self) -> None:
        settings = self._settings(triangles={"max_triangles": 1})
        triangles, stats = build_triangles(_symbols(), _tickers(), settings)
        assert len(triangles) == 1
        assert stats.dropped_cap == 1

    def test_missing_ticker_dropped(self) -> None:
        tickers = [t for t in _tickers() if t.symbol != "ETHBTC"]
        with pytest.raises(ValueError, match="no triangles survived"):
            build_triangles(_symbols(), tickers, self._settings())


class TestWhitelist:
    def _settings(self, whitelist: list[str]) -> Settings:
        return Settings.model_validate(
            {
                "quote_currency": "USDT",
                "triangles": {"auto_discovery": False, "whitelist": whitelist},
            }
        )

    def test_pinned_triangle(self) -> None:
        triangles, stats = build_triangles(
            _symbols(), _tickers(), self._settings(["USDT-BTC-ETH"])
        )
        assert [t.tri_id for t in triangles] == ["USDT>BTC>ETH"]
        assert stats.kept == 1

    def test_whitelist_bypasses_liquidity_filters(self) -> None:
        # Volume that auto-discovery would reject is fine for a pinned cycle.
        triangles, _ = build_triangles(
            _symbols(), _tickers(volume=10.0), self._settings(["USDT-BTC-ETH"])
        )
        assert len(triangles) == 1

    def test_wrong_start_currency_raises(self) -> None:
        with pytest.raises(ValueError, match="quote currency"):
            build_triangles(_symbols(), _tickers(), self._settings(["BTC-USDT-ETH"]))

    def test_unknown_market_raises(self) -> None:
        with pytest.raises(ValueError, match="no market"):
            build_triangles(_symbols(), _tickers(), self._settings(["USDT-BTC-SOL"]))

    def test_empty_whitelist_raises(self) -> None:
        with pytest.raises(ValueError, match="whitelist is empty"):
            build_triangles(_symbols(), _tickers(), self._settings([]))
