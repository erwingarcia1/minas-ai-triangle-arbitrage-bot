"""OKX payload parsing against recorded API v5 shapes (no network)."""

from __future__ import annotations

from decimal import Decimal

import orjson
import pytest

from triarb.exchange.base import BookSnapshot, StreamStatus
from triarb.exchange.okx import (
    parse_symbol,
    parse_ticker,
    parse_ws_message,
    validate_channel,
)

SYMBOL_ENTRY = {
    "instType": "SPOT",
    "instId": "BTC-USDT",
    "baseCcy": "BTC",
    "quoteCcy": "USDT",
    "tickSz": "0.1",
    "lotSz": "0.00000001",
    "minSz": "0.00001",
    "state": "live",
}

TICKER_ENTRY = {
    "instType": "SPOT",
    "instId": "ETH-BTC",
    "last": "0.05231",
    "askPx": "0.05232",
    "bidPx": "0.05230",
    "vol24h": "19100",          # base units
    "volCcy24h": "998.2",       # quote units (BTC) for spot
    "ts": "1695797408984",
}

BOOK_FRAME = {
    "arg": {"channel": "books5", "instId": "BTC-USDT"},
    "data": [
        {
            # levels: [price, size, deprecated, order_count]
            "asks": [["69000.6", "0.8", "0", "3"], ["69001.0", "2.0", "0", "1"]],
            "bids": [["69000.4", "1.2", "0", "2"], ["69000.0", "3.0", "0", "5"]],
            "instId": "BTC-USDT",
            "ts": "1695797408984",
            "seqId": 123456,
        }
    ],
}


class TestRestParsing:
    def test_parse_symbol(self) -> None:
        info = parse_symbol(SYMBOL_ENTRY)
        assert info is not None
        assert info.symbol == "BTC-USDT"
        assert (info.base, info.quote) == ("BTC", "USDT")
        assert info.price_tick_size == Decimal("0.1")
        assert info.quantity_step_size == Decimal("0.00000001")
        assert info.min_base_amount == Decimal("0.00001")
        assert info.min_notional == Decimal("0")

    def test_non_live_symbol_skipped(self) -> None:
        assert parse_symbol({**SYMBOL_ENTRY, "state": "suspend"}) is None

    def test_non_power_of_ten_tick_is_preserved(self) -> None:
        info = parse_symbol({**SYMBOL_ENTRY, "tickSz": "0.5"})
        assert info is not None
        assert info.price_tick_size == Decimal("0.5")

    def test_missing_tick_drops_symbol(self) -> None:
        assert parse_symbol({**SYMBOL_ENTRY, "tickSz": ""}) is None

    def test_parse_ticker_converts_quote_volume_to_usdt(self) -> None:
        ticker = parse_ticker(TICKER_ENTRY, usdt_rate=69000.0)
        assert ticker.symbol == "ETH-BTC"
        assert ticker.bid == 0.0523
        assert ticker.ask == 0.05232
        assert ticker.usdt_volume_24h == pytest.approx(998.2 * 69000.0)
        assert ticker.ts_ms == 1695797408984

    def test_parse_ticker_unknown_quote_gets_zero_volume(self) -> None:
        assert parse_ticker(TICKER_ENTRY, usdt_rate=0.0).usdt_volume_24h == 0.0


class TestWsParsing:
    def test_books5_frame_is_full_snapshot(self) -> None:
        parsed = parse_ws_message(orjson.dumps(BOOK_FRAME), 123, 456)
        assert isinstance(parsed, list)
        (snap,) = parsed
        assert isinstance(snap, BookSnapshot)
        assert snap.symbol == "BTC-USDT"
        # 4-element levels: only price and size are read.
        assert snap.bids == [(69000.4, 1.2), (69000.0, 3.0)]
        assert snap.asks == [(69000.6, 0.8), (69001.0, 2.0)]
        assert snap.ts_exchange_ms == 1695797408984

    def test_pong_ignored(self) -> None:
        assert parse_ws_message(b"pong", 1, 1) is None
        assert parse_ws_message("pong", 1, 1) is None

    def test_subscribe_ack_ignored(self) -> None:
        raw = b'{"event": "subscribe", "arg": {"channel": "books5", "instId": "BTC-USDT"}}'
        assert parse_ws_message(raw, 1, 1) is None

    def test_error_frame_reported(self) -> None:
        raw = b'{"event": "error", "code": "60012", "msg": "Invalid request"}'
        parsed = parse_ws_message(raw, 1, 1)
        assert isinstance(parsed, StreamStatus)
        assert parsed.kind == StreamStatus.ERROR
        assert "60012" in parsed.detail

    def test_unknown_channel_ignored(self) -> None:
        raw = orjson.dumps({"arg": {"channel": "tickers", "instId": "X"}, "data": [{}]})
        assert parse_ws_message(raw, 1, 1) is None

    def test_malformed_frame_raises_for_caller_to_log(self) -> None:
        with pytest.raises(Exception):
            parse_ws_message(b"not json", 1, 1)

    def test_non_object_json_frame_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            parse_ws_message(b"[1, 2, 3]", 1, 1)


class TestChannelValidation:
    def test_auto_resolves_to_books5(self) -> None:
        assert validate_channel("auto") == "books5"

    def test_books5_passes(self) -> None:
        assert validate_channel("books5") == "books5"

    @pytest.mark.parametrize("channel", ["books", "books-l2-tbt", "bbo-tbt", "books15"])
    def test_unsupported_channels_rejected(self, channel: str) -> None:
        with pytest.raises(ValueError, match="okx channel"):
            validate_channel(channel)
