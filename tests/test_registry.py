"""Venue registry: every supported venue resolves to its adapter."""

from __future__ import annotations

import pytest

from triarb.config.models import Venue
from triarb.exchange.base import ExchangeAdapter
from triarb.exchange.binance import BinanceAdapter
from triarb.exchange.bitget import BitgetAdapter
from triarb.exchange.bybit import BybitAdapter
from triarb.exchange.okx import OkxAdapter
from triarb.exchange.registry import create_adapter

EXPECTED = {
    Venue.BITGET: BitgetAdapter,
    Venue.BINANCE: BinanceAdapter,
    Venue.BYBIT: BybitAdapter,
    Venue.OKX: OkxAdapter,
}


@pytest.mark.parametrize("venue", list(Venue))
def test_every_venue_resolves(venue: Venue) -> None:
    adapter = create_adapter(venue)
    assert isinstance(adapter, EXPECTED[venue])
    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.name == venue.value


def test_registry_covers_all_venues() -> None:
    """Adding a Venue member without wiring its adapter must fail this suite."""
    assert set(EXPECTED) == set(Venue)
