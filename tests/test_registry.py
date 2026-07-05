"""Venue registry: bitget resolves, unshipped venues fail loudly at startup."""

from __future__ import annotations

import pytest

from triarb.config.models import Venue
from triarb.exchange.bitget import BitgetAdapter
from triarb.exchange.errors import VenueNotImplementedError
from triarb.exchange.registry import create_adapter


def test_bitget_resolves() -> None:
    adapter = create_adapter(Venue.BITGET)
    assert isinstance(adapter, BitgetAdapter)
    assert adapter.name == "bitget"


@pytest.mark.parametrize("venue", [Venue.BINANCE, Venue.BYBIT, Venue.OKX])
def test_unshipped_venues_raise(venue: Venue) -> None:
    with pytest.raises(VenueNotImplementedError, match=venue.value):
        create_adapter(venue)
