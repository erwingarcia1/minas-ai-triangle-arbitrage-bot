"""Binance payload parsing against recorded spot API shapes (no network)."""

from __future__ import annotations

from decimal import Decimal

import orjson
import pytest

from triarb.exchange.base import BookSnapshot, StreamStatus
from triarb.exchange.binance import (
    parse_symbol,
    parse_ticker,
    parse_ws_message,
    validate_channel,
)

SYMBOL_ENTRY = {
    "symbol": "BTCUSDT",
    "status": "TRADING",
    "baseAsset": "BTC",
    "baseAssetPrecision": 8,
    "quoteAsset": "USDT",
    "quotePrecision": 8,
    "isSpotTradingAllowed": True,
    "filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000",
         "tickSize": "0.01000000"},
        {"filterType": "LOT_SIZE", "minQty": "0.00001000", "maxQty": "9000",
         "stepSize": "0.00001000"},
        {"filterType": "NOTIONAL", "minNotional": "5.00000000",
         "applyMinToMarket": True, "maxNotional": "9000000"},
    ],
}

TICKER_ENTRY = {
    "symbol": "ETHBTC",
    "bidPrice": "0.05230000",
    "askPrice": "0.05231000",
    "lastPrice": "0.05230500",
    "quoteVolume": "1000.5",  # denominated in BTC
    "closeTime": 1695797408984,
}

DEPTH_FRAME = {
    "stream": "btcusdt@depth20@100ms",
    "data": {
        "lastUpdateId": 160,
        "bids": [["69000.40", "1.2"], ["69000.00", "3.0"]],
        "asks": [["69000.60", "0.8"], ["69001.00", "2.0"]],
    },
}


class TestRestParsing:
    def test_parse_symbol(self) -> None:
        info = parse_symbol(SYMBOL_ENTRY)
        assert info is not None
        assert info.symbol == "BTCUSDT"
        assert (info.base, info.quote) == ("BTC", "USDT")
        assert info.price_tick_size == Decimal("0.01000000")
        assert info.quantity_step_size == Decimal("0.00001000")
        assert info.min_base_amount == Decimal("0.00001000")
        assert info.min_notional == Decimal("5.00000000")
        assert info.min_notional_asset == ""  # NOTIONAL is quote-denominated

    def test_non_trading_symbol_skipped(self) -> None:
        assert parse_symbol({**SYMBOL_ENTRY, "status": "BREAK"}) is None

    def test_spot_disallowed_symbol_skipped(self) -> None:
        assert parse_symbol({**SYMBOL_ENTRY, "isSpotTradingAllowed": False}) is None

    def test_missing_spot_permission_field_drops_symbol(self) -> None:
        # Margin-only symbols also carry status TRADING; spot permission must
        # be explicitly present, never assumed.
        entry = {k: v for k, v in SYMBOL_ENTRY.items() if k != "isSpotTradingAllowed"}
        assert parse_symbol(entry) is None

    def test_legacy_min_notional_filter_name(self) -> None:
        entry = {
            **SYMBOL_ENTRY,
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
            ],
        }
        info = parse_symbol(entry)
        assert info is not None
        assert info.min_notional == Decimal("10")

    def test_missing_rounding_filters_drops_symbol(self) -> None:
        # A symbol we cannot round deterministically must never enter the graph.
        entry = {**SYMBOL_ENTRY, "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001"}]}
        assert parse_symbol(entry) is None

    def test_parse_ticker_converts_quote_volume_to_usdt(self) -> None:
        ticker = parse_ticker(TICKER_ENTRY, usdt_rate=69000.0)
        assert ticker.symbol == "ETHBTC"
        assert ticker.bid == 0.0523
        assert ticker.ask == 0.05231
        assert ticker.usdt_volume_24h == pytest.approx(1000.5 * 69000.0)
        assert ticker.ts_ms == 1695797408984

    def test_parse_ticker_unknown_quote_gets_zero_volume(self) -> None:
        ticker = parse_ticker(TICKER_ENTRY, usdt_rate=0.0)
        assert ticker.usdt_volume_24h == 0.0  # excluded by volume filter, never guessed


class TestWsParsing:
    def test_depth_frame_is_full_snapshot(self) -> None:
        parsed = parse_ws_message(orjson.dumps(DEPTH_FRAME), 123, 456)
        assert isinstance(parsed, BookSnapshot)
        assert parsed.symbol == "BTCUSDT"  # uppercased from the stream name
        assert parsed.bids == [(69000.40, 1.2), (69000.00, 3.0)]
        assert parsed.asks == [(69000.60, 0.8), (69001.00, 2.0)]
        assert parsed.recv_ns == 123

    def test_subscribe_ack_ignored(self) -> None:
        assert parse_ws_message(b'{"result": null, "id": 1}', 1, 1) is None

    def test_error_frame_reported(self) -> None:
        raw = b'{"error": {"code": 2, "msg": "Invalid request"}, "id": 1}'
        parsed = parse_ws_message(raw, 1, 1)
        assert isinstance(parsed, StreamStatus)
        assert parsed.kind == StreamStatus.ERROR
        assert "Invalid request" in parsed.detail

    def test_unknown_stream_ignored(self) -> None:
        raw = orjson.dumps({"stream": "btcusdt@trade", "data": {"p": "1"}})
        assert parse_ws_message(raw, 1, 1) is None

    def test_malformed_frame_raises_for_caller_to_log(self) -> None:
        with pytest.raises(Exception):
            parse_ws_message(b"not json", 1, 1)

    def test_non_object_json_frame_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            parse_ws_message(b"[1, 2, 3]", 1, 1)


class TestChannelValidation:
    def test_auto_resolves_to_default(self) -> None:
        assert validate_channel("auto") == "depth20@100ms"

    @pytest.mark.parametrize(
        "channel", ["depth5", "depth10@1000ms", "depth20@100ms", "depth20"]
    )
    def test_supported_channels_pass(self, channel: str) -> None:
        assert validate_channel(channel) == channel

    @pytest.mark.parametrize("channel", ["depth", "depth50", "books15", "depth20@50ms"])
    def test_unsupported_channels_rejected(self, channel: str) -> None:
        with pytest.raises(ValueError, match="binance channel"):
            validate_channel(channel)
