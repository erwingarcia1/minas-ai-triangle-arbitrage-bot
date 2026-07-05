"""Bitget payload parsing against recorded API v2 shapes (no network)."""

from __future__ import annotations

from decimal import Decimal

import orjson
import pytest

from triarb.exchange.base import BookSnapshot, StreamStatus
from triarb.exchange.bitget import parse_symbol, parse_ticker, parse_ws_message

SYMBOL_ENTRY = {
    "symbol": "BTCUSDT",
    "baseCoin": "BTC",
    "quoteCoin": "USDT",
    "minTradeAmount": "0.0001",
    "maxTradeAmount": "10000000",
    "takerFeeRate": "0.002",
    "makerFeeRate": "0.002",
    "pricePrecision": "2",
    "quantityPrecision": "6",
    "quotePrecision": "8",
    "status": "online",
    "minTradeUSDT": "1",
    "buyLimitPriceRatio": "0.05",
    "sellLimitPriceRatio": "0.05",
}

TICKER_ENTRY = {
    "symbol": "BTCUSDT",
    "high24h": "70000",
    "open": "68000",
    "low24h": "67000",
    "lastPr": "69000.5",
    "quoteVolume": "1000000",
    "baseVolume": "14.5",
    "usdtVolume": "1000500.25",
    "ts": "1695797408984",
    "bidPr": "69000.4",
    "askPr": "69000.6",
    "bidSz": "1.2",
    "askSz": "0.8",
}

BOOK_FRAME = {
    "action": "snapshot",
    "arg": {"instType": "SPOT", "channel": "books15", "instId": "BTCUSDT"},
    "data": [
        {
            "asks": [["69000.6", "0.8"], ["69001.0", "2.0"]],
            "bids": [["69000.4", "1.2"], ["69000.0", "3.0"]],
            "ts": "1695797408984",
        }
    ],
    "ts": 1695797408984,
}


class TestRestParsing:
    def test_parse_symbol(self) -> None:
        info = parse_symbol(SYMBOL_ENTRY)
        assert info is not None
        assert info.symbol == "BTCUSDT"
        assert (info.base, info.quote) == ("BTC", "USDT")
        assert (info.price_precision, info.quantity_precision) == (2, 6)
        assert info.min_base_amount == Decimal("0.0001")
        assert info.min_notional == Decimal("1")

    def test_offline_symbol_skipped(self) -> None:
        assert parse_symbol({**SYMBOL_ENTRY, "status": "offline"}) is None

    def test_parse_ticker(self) -> None:
        ticker = parse_ticker(TICKER_ENTRY)
        assert ticker.bid == 69000.4
        assert ticker.ask == 69000.6
        assert ticker.usdt_volume_24h == 1000500.25
        assert 0 < ticker.spread_bps < 1

    def test_ticker_with_empty_quotes(self) -> None:
        ticker = parse_ticker({**TICKER_ENTRY, "bidPr": "", "askPr": None})
        assert ticker.bid == 0.0
        assert ticker.spread_bps == float("inf")


class TestWsParsing:
    def test_pong_ignored(self) -> None:
        assert parse_ws_message("pong", 1, 2) is None

    def test_subscribe_ack_ignored(self) -> None:
        frame = orjson.dumps(
            {"event": "subscribe", "arg": {"channel": "books15", "instId": "BTCUSDT"}}
        )
        assert parse_ws_message(frame, 1, 2) is None

    def test_error_frame_becomes_status(self) -> None:
        frame = orjson.dumps({"event": "error", "code": 30001, "msg": "channel not exist"})
        status = parse_ws_message(frame, 1, 2)
        assert isinstance(status, StreamStatus)
        assert status.kind == StreamStatus.ERROR
        assert "30001" in status.detail

    def test_book_snapshot(self) -> None:
        parsed = parse_ws_message(orjson.dumps(BOOK_FRAME), recv_ns=7, recv_wall_ms=9)
        assert isinstance(parsed, list) and len(parsed) == 1
        snap = parsed[0]
        assert isinstance(snap, BookSnapshot)
        assert snap.symbol == "BTCUSDT"
        assert snap.asks == [(69000.6, 0.8), (69001.0, 2.0)]
        assert snap.bids == [(69000.4, 1.2), (69000.0, 3.0)]
        assert snap.ts_exchange_ms == 1695797408984
        assert (snap.recv_ns, snap.recv_wall_ms) == (7, 9)

    def test_unknown_channel_ignored(self) -> None:
        frame = orjson.dumps(
            {"arg": {"channel": "ticker", "instId": "BTCUSDT"}, "data": [{}]}
        )
        assert parse_ws_message(frame, 1, 2) is None

    def test_malformed_frame_raises(self) -> None:
        with pytest.raises(orjson.JSONDecodeError):
            parse_ws_message(b"not json", 1, 2)
