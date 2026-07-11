"""Venue registry — the single place a new adapter is wired in.

All four venues now ship a public market-data adapter behind the same
``ExchangeAdapter`` contract; each venue runs as an independent observer
process (see docs/DECISIONS.md D-004). Trading-side adapter methods do not
exist in any of them — this build is MARKET_DATA_ONLY.
"""

from __future__ import annotations

from triarb.config.models import Venue
from triarb.exchange.base import ExchangeAdapter
from triarb.exchange.binance import BinanceAdapter
from triarb.exchange.bitget import BitgetAdapter
from triarb.exchange.bybit import BybitAdapter
from triarb.exchange.okx import OkxAdapter


def create_adapter(venue: Venue) -> ExchangeAdapter:
    if venue is Venue.BITGET:
        return BitgetAdapter()
    if venue is Venue.BINANCE:
        return BinanceAdapter()
    if venue is Venue.BYBIT:
        return BybitAdapter()
    if venue is Venue.OKX:
        return OkxAdapter()
    raise AssertionError(f"unhandled venue {venue!r}")  # pragma: no cover
