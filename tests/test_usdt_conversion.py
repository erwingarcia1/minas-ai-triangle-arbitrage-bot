"""End-to-end tests of the quote→USDT volume normalization in fetch_tickers.

The per-entry parsers take an injected rate; these tests cover the layer
that DERIVES the rate from the same REST batch — previously untested on all
three converting venues (QA review finding). All offline: ``_get`` is
monkeypatched with recorded payload shapes.
"""

from __future__ import annotations

from typing import Any

import pytest

from triarb.exchange.binance import BinanceAdapter, _usdt_conversion
from triarb.exchange.bybit import BybitAdapter
from triarb.exchange.okx import OkxAdapter

from tests.test_binance_parse import SYMBOL_ENTRY as BINANCE_SYMBOL
from tests.test_bybit_parse import SYMBOL_ENTRY as BYBIT_SYMBOL


class TestUsdtConversionHelper:
    def test_usdt_maps_to_identity(self) -> None:
        assert _usdt_conversion({"USDT"}, lambda c: 0.0) == {"USDT": 1.0}

    def test_known_quote_maps_to_its_last_price(self) -> None:
        rates = _usdt_conversion({"BTC", "USDT"}, lambda c: 69000.0 if c == "BTC" else 0.0)
        assert rates == {"BTC": 69000.0, "USDT": 1.0}

    def test_unknown_quote_is_omitted_not_guessed(self) -> None:
        assert "EUR" not in _usdt_conversion({"EUR"}, lambda c: 0.0)


def _patch_get(monkeypatch: pytest.MonkeyPatch, adapter: Any, fixtures: dict[str, Any]) -> None:
    async def fake_get(path: str, params: dict[str, str] | None = None) -> Any:
        return fixtures[path]

    monkeypatch.setattr(adapter, "_get", fake_get)


class TestBinanceFetchTickers:
    async def test_cross_pair_volume_converted_via_btcusdt_last(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ethbtc = {**BINANCE_SYMBOL, "symbol": "ETHBTC", "baseAsset": "ETH", "quoteAsset": "BTC"}
        fixtures = {
            "/api/v3/exchangeInfo": {"symbols": [BINANCE_SYMBOL, ethbtc]},
            "/api/v3/ticker/24hr": [
                {"symbol": "BTCUSDT", "bidPrice": "68999", "askPrice": "69001",
                 "lastPrice": "69000", "quoteVolume": "1000000", "closeTime": 1},
                {"symbol": "ETHBTC", "bidPrice": "0.0523", "askPrice": "0.05231",
                 "lastPrice": "0.052305", "quoteVolume": "1000.5", "closeTime": 1},
            ],
        }
        adapter = BinanceAdapter()
        _patch_get(monkeypatch, adapter, fixtures)
        tickers = {t.symbol: t for t in await adapter.fetch_tickers()}
        assert tickers["BTCUSDT"].usdt_volume_24h == pytest.approx(1_000_000.0)  # USDT × 1.0
        assert tickers["ETHBTC"].usdt_volume_24h == pytest.approx(1000.5 * 69000.0)

    async def test_missing_usdt_market_for_quote_zeroes_volume(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ethbtc = {**BINANCE_SYMBOL, "symbol": "ETHBTC", "baseAsset": "ETH", "quoteAsset": "BTC"}
        fixtures = {
            "/api/v3/exchangeInfo": {"symbols": [ethbtc]},  # no BTCUSDT listed
            "/api/v3/ticker/24hr": [
                {"symbol": "ETHBTC", "bidPrice": "0.0523", "askPrice": "0.05231",
                 "lastPrice": "0.052305", "quoteVolume": "1000.5", "closeTime": 1},
            ],
        }
        adapter = BinanceAdapter()
        _patch_get(monkeypatch, adapter, fixtures)
        (ticker,) = await adapter.fetch_tickers()
        assert ticker.usdt_volume_24h == 0.0  # excluded by the volume filter, never guessed


class TestBybitFetchTickers:
    async def test_cross_pair_turnover_converted_and_ts_from_envelope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ethbtc = {**BYBIT_SYMBOL, "symbol": "ETHBTC", "baseCoin": "ETH", "quoteCoin": "BTC"}
        fixtures = {
            "/v5/market/instruments-info": {
                "retCode": 0,
                "result": {"list": [BYBIT_SYMBOL, ethbtc], "nextPageCursor": ""},
                "time": 111,
            },
            "/v5/market/tickers": {
                "retCode": 0,
                "result": {"list": [
                    {"symbol": "BTCUSDT", "bid1Price": "68999", "ask1Price": "69001",
                     "lastPrice": "69000", "turnover24h": "5000000"},
                    {"symbol": "ETHBTC", "bid1Price": "0.0523", "ask1Price": "0.05231",
                     "lastPrice": "0.052305", "turnover24h": "1000.5"},
                ]},
                "time": 1695797408984,
            },
        }
        adapter = BybitAdapter()
        _patch_get(monkeypatch, adapter, fixtures)
        tickers = {t.symbol: t for t in await adapter.fetch_tickers()}
        assert tickers["BTCUSDT"].usdt_volume_24h == pytest.approx(5_000_000.0)
        assert tickers["ETHBTC"].usdt_volume_24h == pytest.approx(1000.5 * 69000.0)
        assert tickers["ETHBTC"].ts_ms == 1695797408984


class TestOkxFetchTickers:
    async def test_cross_pair_volccy_converted_via_btc_usdt_last(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixtures = {
            "/api/v5/market/tickers": [
                {"instId": "BTC-USDT", "bidPx": "68999", "askPx": "69001",
                 "last": "69000", "volCcy24h": "2000000", "ts": "1"},
                {"instId": "ETH-BTC", "bidPx": "0.0523", "askPx": "0.05231",
                 "last": "0.052305", "volCcy24h": "998.2", "ts": "1"},
            ],
        }
        adapter = OkxAdapter()
        _patch_get(monkeypatch, adapter, fixtures)
        tickers = {t.symbol: t for t in await adapter.fetch_tickers()}
        assert tickers["BTC-USDT"].usdt_volume_24h == pytest.approx(2_000_000.0)
        assert tickers["ETH-BTC"].usdt_volume_24h == pytest.approx(998.2 * 69000.0)

    async def test_quote_without_usdt_market_zeroes_volume(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fixtures = {
            "/api/v5/market/tickers": [
                {"instId": "ETH-EUR", "bidPx": "2300", "askPx": "2301",
                 "last": "2300.5", "volCcy24h": "5000", "ts": "1"},
            ],
        }
        adapter = OkxAdapter()
        _patch_get(monkeypatch, adapter, fixtures)
        (ticker,) = await adapter.fetch_tickers()
        assert ticker.usdt_volume_24h == 0.0
