"""Adapter contract suite — the same assertions run over every venue (§32).

Offline by construction: nothing here opens a socket. Channel validation in
``stream_books`` happens before any connection attempt, so rejecting a bad
channel must never require the network.
"""

from __future__ import annotations

import os

import orjson
import pytest

import triarb.exchange.binance as binance_mod
import triarb.exchange.bitget as bitget_mod
import triarb.exchange.bybit as bybit_mod
import triarb.exchange.okx as okx_mod
from triarb.config.models import Venue
from triarb.exchange.base import ExchangeAdapter
from triarb.exchange.registry import create_adapter

ALL_VENUES = list(Venue)

# Every exception type the stream loops catch around parse_ws_message.
# Anything else escaping a parser would kill the observer TaskGroup.
CATCHABLE = (orjson.JSONDecodeError, KeyError, ValueError, TypeError, AttributeError, IndexError)

# Hostile frames: valid JSON in shapes no venue should ever send.
HOSTILE_FRAMES = [
    b"[]",
    b"[1, 2, 3]",
    b'"hello"',
    b"123",
    b"null",
    b"true",
    b'{"error": "rate limited"}',
    b'{"error": 42}',
    b'{"arg": "not-a-dict", "data": [{}]}',
    b'{"arg": {"channel": "books5"}, "data": "not-a-list"}',
    b'{"arg": {"channel": "books5", "instId": "X"}, "data": ["not-a-dict"]}',
    b'{"stream": 5, "data": {}}',
    b'{"stream": "x@depth20", "data": {"bids": "oops"}}',
    b'{"topic": 5, "data": {}}',
    b'{"topic": "orderbook.50.X", "type": "snapshot", "data": "oops"}',
    b'{"success": "maybe", "op": []}',
    b'{"event": {"nested": true}}',
    b'{"data": null}',
]


def _parse_for(venue: Venue, raw: bytes) -> object:
    if venue is Venue.BITGET:
        return bitget_mod.parse_ws_message(raw, 1, 1)
    if venue is Venue.BINANCE:
        return binance_mod.parse_ws_message(raw, 1, 1)
    if venue is Venue.OKX:
        return okx_mod.parse_ws_message(raw, 1, 1)
    return bybit_mod.parse_ws_message(raw, bybit_mod.BybitBookAssembler(50), 1, 1)


@pytest.mark.parametrize("venue", ALL_VENUES)
class TestAdapterContract:
    def test_constructor_needs_no_credentials_or_network(
        self, venue: Venue, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # MARKET_DATA_ONLY invariant: building an adapter reads no secrets
        # and performs no I/O — construction must succeed with every
        # credential-looking variable stripped from the environment, and
        # must not have opened an HTTP session.
        for key in list(os.environ):
            upper = key.upper()
            if "KEY" in upper or "SECRET" in upper or "PASSPHRASE" in upper:
                monkeypatch.delenv(key, raising=False)
        adapter = create_adapter(venue)
        assert isinstance(adapter, ExchangeAdapter)
        assert adapter._session is None  # type: ignore[attr-defined]

    def test_name_matches_venue(self, venue: Venue) -> None:
        assert create_adapter(venue).name == venue.value

    def test_hostile_frames_never_escape_the_catchable_set(self, venue: Venue) -> None:
        """No malformed frame may raise anything the stream loop won't
        catch — one bad frame killing the whole observer is the P1 failure
        confirmed by the adversarial review."""
        for raw in HOSTILE_FRAMES:
            try:
                _parse_for(venue, raw)
            except CATCHABLE:
                pass  # loggable skip: exactly what the stream loop does

    def test_auto_channel_has_known_push_cadence(self, venue: Venue) -> None:
        adapter = create_adapter(venue)
        interval = adapter.min_push_interval_ms("auto")
        assert interval is not None and 0 < interval <= 1000

    def test_junk_channel_has_no_cadence(self, venue: Venue) -> None:
        assert create_adapter(venue).min_push_interval_ms("definitely-not-a-channel") is None

    async def test_junk_depth_channel_rejected_before_connecting(
        self, venue: Venue
    ) -> None:
        adapter = create_adapter(venue)
        stream = adapter.stream_books(["BTCUSDT"], "definitely-not-a-channel")
        with pytest.raises(ValueError, match=venue.value):
            await anext(stream)
        await adapter.aclose()

    async def test_aclose_is_idempotent(self, venue: Venue) -> None:
        adapter = create_adapter(venue)
        await adapter.aclose()
        await adapter.aclose()  # second close must not raise
