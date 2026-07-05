"""Triangle enumeration, liquidity filtering, and compilation into legs.

Enumeration: for the held quote currency Q, every ordered pair (A, B) with
markets Q<->A, A<->B, B<->Q forms the directed cycle Q -> A -> B -> Q. Both
directions of each currency pair are enumerated separately — Q->A->B->Q and
Q->B->A->Q are distinct opportunities that price independently.

Failure modes:
- A whitelist entry that doesn't start at the configured quote currency, or
  references a market the venue doesn't list, fails startup loudly with the
  missing edge named. Whitelisted triangles bypass volume/spread filters (the
  user pinned them deliberately) but never bypass market existence.
- Auto-discovery with filters that eliminate everything raises rather than
  running an observer with zero triangles.
"""

from __future__ import annotations

from dataclasses import dataclass

from triarb.config.models import Settings
from triarb.exchange.base import SymbolInfo, Ticker
from triarb.graph.builder import Adjacency, build_adjacency
from triarb.pricing.edge import Leg, Side, Triangle


@dataclass(frozen=True)
class UniverseStats:
    """What discovery saw and why triangles were dropped — logged at startup."""

    online_symbols: int
    currencies: int
    raw_cycles: int
    dropped_no_ticker: int
    dropped_volume: int
    dropped_spread: int
    dropped_cap: int
    kept: int


def enumerate_cycles(adj: Adjacency, quote: str) -> list[tuple[str, str, str]]:
    """All directed cycles quote -> A -> B -> quote."""
    cycles: list[tuple[str, str, str]] = []
    neighbors_q = adj.get(quote, {})
    for a in neighbors_q:
        if a == quote:
            continue
        for b in adj.get(a, {}):
            if b == quote or b == a:
                continue
            if quote in adj.get(b, {}):
                cycles.append((quote, a, b))
    return cycles


def compile_triangle(cycle: tuple[str, str, str], adj: Adjacency) -> Triangle:
    """Turn a currency cycle into three legs with sides and venue symbols."""
    q, a, b = cycle
    path = (q, a, b, q)
    legs: list[Leg] = []
    for cur_from, cur_to in zip(path, path[1:]):
        market = adj.get(cur_from, {}).get(cur_to)
        if market is None:
            raise ValueError(f"no market for {cur_from}->{cur_to} in cycle {cycle}")
        side = Side.BUY if market.base == cur_to else Side.SELL
        legs.append(Leg(market.symbol, side, cur_from, cur_to))
    return Triangle(f"{q}>{a}>{b}", (legs[0], legs[1], legs[2]))


def _parse_whitelist_entry(entry: str, quote: str) -> tuple[str, str, str]:
    parts = tuple(entry.upper().split("-"))
    if len(parts) != 3:
        raise ValueError(f"whitelist entry {entry!r} must have exactly three currencies")
    if parts[0] != quote:
        raise ValueError(
            f"whitelist entry {entry!r} must start at the configured quote currency {quote}"
        )
    return parts  # type: ignore[return-value]


def build_triangles(
    symbols: list[SymbolInfo],
    tickers: list[Ticker],
    settings: Settings,
) -> tuple[list[Triangle], UniverseStats]:
    """Discovery pipeline: graph -> cycles -> liquidity filter -> rank -> cap.

    With ``auto_discovery: false`` only the whitelist is compiled (market
    existence still enforced, filters bypassed).
    """
    adj = build_adjacency(symbols)
    quote = settings.quote_currency
    by_symbol = {t.symbol: t for t in tickers}

    if not settings.triangles.auto_discovery:
        if not settings.triangles.whitelist:
            raise ValueError("auto_discovery is false but the whitelist is empty")
        triangles = [
            compile_triangle(_parse_whitelist_entry(e, quote), adj)
            for e in settings.triangles.whitelist
        ]
        stats = UniverseStats(
            online_symbols=len(symbols),
            currencies=len(adj),
            raw_cycles=len(triangles),
            dropped_no_ticker=0,
            dropped_volume=0,
            dropped_spread=0,
            dropped_cap=0,
            kept=len(triangles),
        )
        return triangles, stats

    cycles = enumerate_cycles(adj, quote)
    min_volume = settings.filters.min_24h_quote_volume_usdt
    max_spread = settings.filters.max_spread_bps

    scored: list[tuple[float, tuple[str, str, str]]] = []
    dropped_no_ticker = dropped_volume = dropped_spread = 0
    for cycle in cycles:
        triangle = compile_triangle(cycle, adj)
        leg_tickers = [by_symbol.get(leg.symbol) for leg in triangle.legs]
        if any(t is None for t in leg_tickers):
            dropped_no_ticker += 1
            continue
        volumes = [t.usdt_volume_24h for t in leg_tickers]  # type: ignore[union-attr]
        if min(volumes) < min_volume:
            dropped_volume += 1
            continue
        if max(t.spread_bps for t in leg_tickers) > max_spread:  # type: ignore[union-attr]
            dropped_spread += 1
            continue
        scored.append((min(volumes), cycle))

    scored.sort(key=lambda item: item[0], reverse=True)
    cap = settings.triangles.max_triangles
    dropped_cap = max(0, len(scored) - cap)
    kept_cycles = [cycle for _, cycle in scored[:cap]]
    triangles = [compile_triangle(cycle, adj) for cycle in kept_cycles]

    stats = UniverseStats(
        online_symbols=len(symbols),
        currencies=len(adj),
        raw_cycles=len(cycles),
        dropped_no_ticker=dropped_no_ticker,
        dropped_volume=dropped_volume,
        dropped_spread=dropped_spread,
        dropped_cap=dropped_cap,
        kept=len(triangles),
    )
    if not triangles:
        raise ValueError(
            f"no triangles survived discovery (raw={len(cycles)}, "
            f"volume_dropped={dropped_volume}, spread_dropped={dropped_spread}); "
            "loosen filters or pin a whitelist"
        )
    return triangles, stats
