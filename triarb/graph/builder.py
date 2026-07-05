"""Currency graph: currencies are nodes, spot markets are edges.

Failure modes: if a venue ever listed both orientations of the same pair
(e.g. ETH/BTC and BTC/ETH) the later one would win the adjacency slot; no
known venue does this, and the builder logs if it happens rather than
guessing which book to prefer.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from triarb.exchange.base import SymbolInfo

logger = logging.getLogger(__name__)

Adjacency = dict[str, dict[str, SymbolInfo]]


def build_adjacency(symbols: Iterable[SymbolInfo]) -> Adjacency:
    """Map ``currency -> currency -> market`` for every online spot symbol.

    Both directions of each market are inserted: the same ``SymbolInfo``
    serves X->Y and Y->X; the trade side is derived later from whether the
    destination currency is the market's base.
    """
    adj: Adjacency = {}
    for info in symbols:
        if info.status != "online":
            continue
        for a, b in ((info.base, info.quote), (info.quote, info.base)):
            row = adj.setdefault(a, {})
            existing = row.get(b)
            if existing is not None and existing.symbol != info.symbol:
                logger.warning(
                    "duplicate market edge %s->%s: keeping %s, ignoring %s",
                    a, b, existing.symbol, info.symbol,
                )
                continue
            row[b] = info
    return adj
