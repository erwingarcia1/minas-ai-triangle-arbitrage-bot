"""Adapter contract suite — the same assertions run over every venue (§32).

Offline by construction: nothing here opens a socket. Channel validation in
``stream_books`` happens before any connection attempt, so rejecting a bad
channel must never require the network.
"""

from __future__ import annotations

import pytest

from triarb.config.models import Venue
from triarb.exchange.base import ExchangeAdapter
from triarb.exchange.registry import create_adapter

ALL_VENUES = list(Venue)


@pytest.mark.parametrize("venue", ALL_VENUES)
class TestAdapterContract:
    def test_constructor_needs_no_credentials_or_network(self, venue: Venue) -> None:
        # MARKET_DATA_ONLY invariant: building an adapter reads no secrets
        # and performs no I/O. Construction succeeding in an offline test
        # with no environment variables set IS the evidence.
        adapter = create_adapter(venue)
        assert isinstance(adapter, ExchangeAdapter)

    def test_name_matches_venue(self, venue: Venue) -> None:
        assert create_adapter(venue).name == venue.value

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
