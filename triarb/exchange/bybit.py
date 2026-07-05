"""Bybit spot adapter — placeholder.

Scheduled for a later phase. The module exists so the venue interface's
one-module-per-exchange layout is visible from Phase 1. Constructing it
raises; nothing here talks to the network.
"""

from __future__ import annotations

from triarb.exchange.errors import VenueNotImplementedError


class BybitAdapter:
    name = "bybit"

    def __init__(self) -> None:
        raise VenueNotImplementedError("bybit adapter ships in a later phase")
