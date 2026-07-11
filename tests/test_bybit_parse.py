"""Bybit payload parsing and snapshot+delta book assembly (no network)."""

from __future__ import annotations

from decimal import Decimal

import orjson
import pytest

from triarb.exchange.base import BookSnapshot, StreamStatus
from triarb.exchange.bybit import (
    BybitBookAssembler,
    SequenceGap,
    parse_symbol,
    parse_ticker,
    parse_ws_message,
    validate_channel,
)

SYMBOL_ENTRY = {
    "symbol": "BTCUSDT",
    "baseCoin": "BTC",
    "quoteCoin": "USDT",
    "status": "Trading",
    "lotSizeFilter": {
        "basePrecision": "0.000001",
        "quotePrecision": "0.00000001",
        "minOrderQty": "0.000048",
        "maxOrderQty": "71.73",
        "minOrderAmt": "1",
        "maxOrderAmt": "4000000",
    },
    "priceFilter": {"tickSize": "0.01"},
}

TICKER_ENTRY = {
    "symbol": "ETHBTC",
    "bid1Price": "0.052300",
    "ask1Price": "0.052310",
    "lastPrice": "0.052305",
    "turnover24h": "1000.5",  # quote units (BTC)
    "volume24h": "19100",
}


def snapshot_frame(u: int = 100) -> bytes:
    return orjson.dumps(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot",
            "ts": 1695797408984,
            "data": {
                "s": "BTCUSDT",
                "b": [["69000.40", "1.2"], ["69000.00", "3.0"]],
                "a": [["69000.60", "0.8"], ["69001.00", "2.0"]],
                "u": u,
                "seq": 7961638724,
            },
            "cts": 1695797408980,
        }
    )


def delta_frame(u: int, b: list[list[str]] | None = None, a: list[list[str]] | None = None) -> bytes:
    return orjson.dumps(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "delta",
            "ts": 1695797409084,
            "data": {"s": "BTCUSDT", "b": b or [], "a": a or [], "u": u, "seq": 7961638725},
            "cts": 1695797409080,
        }
    )


class TestRestParsing:
    def test_parse_symbol(self) -> None:
        info = parse_symbol(SYMBOL_ENTRY)
        assert info is not None
        assert info.symbol == "BTCUSDT"
        assert (info.base, info.quote) == ("BTC", "USDT")
        assert info.price_tick_size == Decimal("0.01")
        assert info.quantity_step_size == Decimal("0.000001")
        assert info.min_base_amount == Decimal("0.000048")
        assert info.min_notional == Decimal("1")

    def test_non_trading_symbol_skipped(self) -> None:
        assert parse_symbol({**SYMBOL_ENTRY, "status": "Closed"}) is None

    def test_missing_tick_drops_symbol(self) -> None:
        entry = {**SYMBOL_ENTRY, "priceFilter": {}}
        assert parse_symbol(entry) is None

    def test_parse_ticker_converts_quote_volume_to_usdt(self) -> None:
        ticker = parse_ticker(TICKER_ENTRY, usdt_rate=69000.0, ts_ms=1695797408984)
        assert ticker.symbol == "ETHBTC"
        assert ticker.bid == 0.0523
        assert ticker.usdt_volume_24h == pytest.approx(1000.5 * 69000.0)
        assert ticker.ts_ms == 1695797408984

    def test_parse_ticker_unknown_quote_gets_zero_volume(self) -> None:
        assert parse_ticker(TICKER_ENTRY, 0.0, 0).usdt_volume_24h == 0.0


class TestBookAssembly:
    def _assembler(self) -> BybitBookAssembler:
        return BybitBookAssembler(depth_limit=50)

    def test_snapshot_builds_complete_book(self) -> None:
        asm = self._assembler()
        snap = parse_ws_message(snapshot_frame(), asm, 123, 456)
        assert isinstance(snap, BookSnapshot)
        assert snap.symbol == "BTCUSDT"
        assert snap.bids == [(69000.40, 1.2), (69000.00, 3.0)]  # descending
        assert snap.asks == [(69000.60, 0.8), (69001.00, 2.0)]  # ascending
        assert snap.ts_exchange_ms == 1695797408984

    def test_delta_upserts_and_emits_full_book(self) -> None:
        asm = self._assembler()
        parse_ws_message(snapshot_frame(u=100), asm, 1, 1)
        snap = parse_ws_message(
            delta_frame(101, b=[["69000.50", "0.5"]], a=[["69000.60", "1.5"]]), asm, 2, 2
        )
        assert isinstance(snap, BookSnapshot)
        # New bid level inserted in order; ask level size replaced.
        assert snap.bids == [(69000.50, 0.5), (69000.40, 1.2), (69000.00, 3.0)]
        assert snap.asks == [(69000.60, 1.5), (69001.00, 2.0)]

    def test_delta_zero_size_deletes_level(self) -> None:
        asm = self._assembler()
        parse_ws_message(snapshot_frame(u=100), asm, 1, 1)
        snap = parse_ws_message(delta_frame(101, b=[["69000.40", "0"]]), asm, 2, 2)
        assert isinstance(snap, BookSnapshot)
        assert snap.bids == [(69000.00, 3.0)]

    def test_sequence_gap_detected_and_book_dropped(self) -> None:
        asm = self._assembler()
        parse_ws_message(snapshot_frame(u=100), asm, 1, 1)
        gap = parse_ws_message(delta_frame(103), asm, 2, 2)  # missed 101, 102
        assert isinstance(gap, SequenceGap)
        assert gap.symbol == "BTCUSDT"
        assert (gap.expected_u, gap.got_u) == (101, 103)
        # After the gap the internal book is gone: the next delta is also a gap…
        assert isinstance(parse_ws_message(delta_frame(104), asm, 3, 3), SequenceGap)
        # …until a fresh snapshot arrives (resubscribe behavior).
        snap = parse_ws_message(snapshot_frame(u=200), asm, 4, 4)
        assert isinstance(snap, BookSnapshot)

    def test_delta_before_snapshot_is_a_gap(self) -> None:
        asm = self._assembler()
        assert isinstance(parse_ws_message(delta_frame(5), asm, 1, 1), SequenceGap)

    def test_service_restart_snapshot_resets_sequence(self) -> None:
        asm = self._assembler()
        parse_ws_message(snapshot_frame(u=100), asm, 1, 1)
        # u=1 snapshot after service restart replaces the book entirely.
        snap = parse_ws_message(snapshot_frame(u=1), asm, 2, 2)
        assert isinstance(snap, BookSnapshot)
        follow = parse_ws_message(delta_frame(2), asm, 3, 3)
        assert isinstance(follow, BookSnapshot)

    def test_reset_on_reconnect_forgets_all_books(self) -> None:
        asm = self._assembler()
        parse_ws_message(snapshot_frame(u=100), asm, 1, 1)
        asm.reset()
        assert isinstance(parse_ws_message(delta_frame(101), asm, 2, 2), SequenceGap)

    def test_depth_limit_caps_emitted_levels(self) -> None:
        asm = BybitBookAssembler(depth_limit=1)
        snap = parse_ws_message(snapshot_frame(), asm, 1, 1)
        assert isinstance(snap, BookSnapshot)
        assert len(snap.bids) == 1
        assert len(snap.asks) == 1
        assert snap.bids[0] == (69000.40, 1.2)  # best bid survives the cap


class TestWsControlFrames:
    def test_subscribe_ack_ignored(self) -> None:
        raw = b'{"success": true, "ret_msg": "subscribe", "conn_id": "x", "op": "subscribe"}'
        assert parse_ws_message(raw, BybitBookAssembler(50), 1, 1) is None

    def test_failed_subscribe_reported(self) -> None:
        raw = b'{"success": false, "ret_msg": "error:handler not found", "op": "subscribe"}'
        parsed = parse_ws_message(raw, BybitBookAssembler(50), 1, 1)
        assert isinstance(parsed, StreamStatus)
        assert parsed.kind == StreamStatus.ERROR

    def test_pong_ignored(self) -> None:
        raw = b'{"success": true, "ret_msg": "pong", "conn_id": "x", "op": "ping"}'
        assert parse_ws_message(raw, BybitBookAssembler(50), 1, 1) is None

    def test_other_topic_ignored(self) -> None:
        raw = orjson.dumps({"topic": "publicTrade.BTCUSDT", "data": []})
        assert parse_ws_message(raw, BybitBookAssembler(50), 1, 1) is None

    def test_malformed_frame_raises_for_caller_to_log(self) -> None:
        with pytest.raises(Exception):
            parse_ws_message(b"not json", BybitBookAssembler(50), 1, 1)


class TestChannelValidation:
    def test_auto_resolves_to_orderbook_50(self) -> None:
        assert validate_channel("auto") == "orderbook.50"

    @pytest.mark.parametrize("channel", ["orderbook.50", "orderbook.200"])
    def test_supported_channels_pass(self, channel: str) -> None:
        assert validate_channel(channel) == channel

    @pytest.mark.parametrize("channel", ["orderbook.1", "orderbook.500", "books15"])
    def test_unsupported_channels_rejected(self, channel: str) -> None:
        with pytest.raises(ValueError, match="bybit channel"):
            validate_channel(channel)
