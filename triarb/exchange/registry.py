"""Venue registry — the single place a new adapter is wired in.

Failure modes: a venue whose adapter has not shipped yet raises
``VenueNotImplementedError`` at startup with the phase it is scheduled for,
instead of failing deep inside the run loop.
"""

from __future__ import annotations

from triarb.config.models import Venue
from triarb.exchange.base import ExchangeAdapter
from triarb.exchange.bitget import BitgetAdapter
from triarb.exchange.errors import VenueNotImplementedError

_PLANNED = {
    Venue.BINANCE: "Phase 2+",
    Venue.BYBIT: "Phase 2+",
    Venue.OKX: "Phase 2+",
}


def create_adapter(venue: Venue) -> ExchangeAdapter:
    if venue is Venue.BITGET:
        return BitgetAdapter()
    raise VenueNotImplementedError(
        f"venue '{venue.value}' is configured but its adapter ships in "
        f"{_PLANNED[venue]}; Phase 1 implements bitget only"
    )
