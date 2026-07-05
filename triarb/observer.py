"""Phase 1 observer: detect, price, and log triangular cycles. NO order code.

Pipeline per book update (the hot path):
  WS snapshot -> BookStore.apply -> for each triangle touching that symbol:
  float edge screen (depth walk + fee compounding) -> if it clears the
  threshold, exact Decimal feasibility (lot size / precision / min notional)
  -> opportunity lifecycle logging.

Opportunity lifecycle: first evaluation at/above threshold logs
``cycle_detected``; the first evaluation below it (or a stale/short book, or
a disconnect) logs ``cycle_ended`` with how long the edge persisted and its
peak. Persistence is measured from the feed's own updates — on a snapshot
channel a silent symbol produces no evaluations, so ``max_age_ms`` is logged
with every detection for honest post-analysis.

Failure modes:
- WS disconnect: all books are marked stale, every active opportunity is
  closed with ``end_reason=ws_disconnect``, and detection resumes only when
  fresh snapshots arrive. Nothing is interpolated across the gap.
- Any unexpected exception in a task cancels the whole TaskGroup and crashes
  the process loudly (logged with context) — no silent degradation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal

from triarb.config.models import Settings
from triarb.exchange.base import BookSnapshot, ExchangeAdapter, StreamStatus, SymbolInfo
from triarb.graph.triangles import build_triangles
from triarb.monitoring.dashboard import Dashboard
from triarb.monitoring.jsonlog import log_event
from triarb.monitoring.metrics import Metrics
from triarb.pricing.edge import Triangle, evaluate_cycle
from triarb.pricing.feasibility import simulate_rounded_cycle
from triarb.pricing.fees import hurdle_bps
from triarb.pricing.orderbook import BookStore

logger = logging.getLogger(__name__)

_SWEEP_INTERVAL_S = 0.25


class ActiveOpportunity:
    """Lifecycle state for one currently-positive cycle."""

    __slots__ = (
        "first_ns",
        "first_wall_ms",
        "last_ns",
        "evals",
        "peak_net_bps",
        "last_net_bps",
        "peak_net_after_rounding_bps",
        "last_feasible",
        "ever_feasible",
    )

    def __init__(self, now_ns: int, wall_ms: int, net_bps: float) -> None:
        self.first_ns = now_ns
        self.first_wall_ms = wall_ms
        self.last_ns = now_ns
        self.evals = 1
        self.peak_net_bps = net_bps
        self.last_net_bps = net_bps
        self.peak_net_after_rounding_bps = float("-inf")
        self.last_feasible = False
        self.ever_feasible = False


class Observer:
    def __init__(self, settings: Settings, adapter: ExchangeAdapter) -> None:
        self.settings = settings
        self.adapter = adapter
        self.store = BookStore()
        self.metrics = Metrics()
        self.triangles: list[Triangle] = []
        self.by_symbol: dict[str, tuple[Triangle, ...]] = {}
        self.meta: dict[str, SymbolInfo] = {}
        self.active: dict[str, ActiveOpportunity] = {}
        self.dashboard: Dashboard | None = None
        self._tri_by_id: dict[str, Triangle] = {}
        self._leg_desc: dict[str, str] = {}
        self._fee_rate = settings.fees.effective_taker_rate
        self._fee_rate_dec = Decimal(str(self._fee_rate))
        self._notional = settings.observer.notional
        self._notional_dec = Decimal(str(settings.observer.notional))
        self._threshold_bps = settings.observer.edge_threshold_bps
        self._staleness_ms = float(settings.observer.staleness_ms)

    # ------------------------------------------------------------- startup

    async def prepare(self) -> None:
        symbols = await self.adapter.fetch_symbols()
        tickers = await self.adapter.fetch_tickers()
        self.meta = {s.symbol: s for s in symbols}
        self.triangles, stats = build_triangles(symbols, tickers, self.settings)
        self._tri_by_id = {t.tri_id: t for t in self.triangles}

        index: dict[str, list[Triangle]] = {}
        for tri in self.triangles:
            self._leg_desc[tri.tri_id] = " / ".join(
                f"{leg.side.name.lower()} {leg.symbol}" for leg in tri.legs
            )
            for symbol in tri.symbols:
                index.setdefault(symbol, []).append(tri)
        self.by_symbol = {symbol: tuple(tris) for symbol, tris in index.items()}

        log_event(
            logger,
            logging.INFO,
            "universe",
            venue=self.settings.venue.value,
            quote=self.settings.quote_currency,
            online_symbols=stats.online_symbols,
            currencies=stats.currencies,
            raw_cycles=stats.raw_cycles,
            dropped_no_ticker=stats.dropped_no_ticker,
            dropped_volume=stats.dropped_volume,
            dropped_spread=stats.dropped_spread,
            dropped_cap=stats.dropped_cap,
            triangles_kept=stats.kept,
            subscribed_symbols=len(self.by_symbol),
            notional=self._notional,
            fee_rate_per_leg=self._fee_rate,
            fee_hurdle_bps=hurdle_bps(self._fee_rate),
            edge_threshold_bps=self._threshold_bps,
            examples=[t.tri_id for t in self.triangles[:10]],
        )

    # ------------------------------------------------------------ run loop

    async def run(self) -> None:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._consume(), name="consume")
            tg.create_task(self._sweep_loop(), name="sweeper")
            tg.create_task(self._stats_loop(), name="stats")
            if self.settings.observer.metadata_refresh_s > 0:
                tg.create_task(self._metadata_loop(), name="metadata")

    async def _consume(self) -> None:
        symbols = sorted(self.by_symbol)
        channel = self.settings.observer.depth_channel
        async for item in self.adapter.stream_books(symbols, channel):
            if isinstance(item, StreamStatus):
                self._on_stream_status(item)
            else:
                self._on_book(item)

    def _on_stream_status(self, status: StreamStatus) -> None:
        log_event(logger, logging.WARNING, "stream_status", kind=status.kind, detail=status.detail)
        if status.kind == StreamStatus.DISCONNECTED:
            self.metrics.inc("ws_disconnects")
            self.store.mark_all_stale()
            for tri_id in list(self.active):
                self._end_opportunity(tri_id, "ws_disconnect", time.perf_counter_ns())
        elif status.kind == StreamStatus.ERROR:
            self.metrics.inc("ws_errors")

    # ------------------------------------------------------------ hot path

    def _on_book(self, snap: BookSnapshot) -> None:
        t0 = time.perf_counter_ns()
        self.store.apply(snap)
        self.metrics.inc("book_updates")
        tris = self.by_symbol.get(snap.symbol)
        if tris:
            for tri in tris:
                self._evaluate(tri, t0, snap.recv_wall_ms)
        self.metrics.reprice_latency_us.observe((time.perf_counter_ns() - t0) / 1_000.0)

    def _evaluate(self, tri: Triangle, now_ns: int, wall_ms: int) -> None:
        self.metrics.inc("evaluations")
        res = evaluate_cycle(
            tri, self.store, self._notional, self._fee_rate, now_ns, self._staleness_ms
        )
        if res.ok:
            self.metrics.net_edge_bps.observe(res.net_bps)
        elif res.reason == "stale_book":
            self.metrics.inc("stale_skips")
        elif res.reason == "insufficient_depth":
            self.metrics.inc("insufficient_depth")

        opp = self.active.get(tri.tri_id)
        if res.ok and res.net_bps >= self._threshold_bps:
            feas = simulate_rounded_cycle(
                tri,
                self.store,
                self.meta,
                self._notional_dec,
                self._fee_rate_dec,
                self.settings.quote_currency,
            )
            feasible = feas.ok and feas.net_bps_after_rounding > 0.0
            if opp is None:
                opp = ActiveOpportunity(now_ns, wall_ms, res.net_bps)
                self.active[tri.tri_id] = opp
                self.metrics.inc("cycles_detected")
                if res.net_bps > 0.0 and not feasible:
                    self.metrics.inc("rounding_killed")
                log_event(
                    logger,
                    logging.INFO,
                    "cycle_detected",
                    tri_id=tri.tri_id,
                    legs=self._leg_desc[tri.tri_id],
                    gross_bps=round(res.gross_bps, 4),
                    net_bps=round(res.net_bps, 4),
                    net_after_rounding_bps=(
                        round(feas.net_bps_after_rounding, 4) if feas.ok else None
                    ),
                    feasible=feasible,
                    reject_reason=feas.reject_reason,
                    stranded_dust={cur: str(v) for cur, v in feas.stranded.items()},
                    notional=self._notional,
                    max_book_age_ms=round(res.max_age_ms, 1),
                    worst_prices=res.worst_prices,
                )
            else:
                opp.last_ns = now_ns
                opp.evals += 1
                opp.last_net_bps = res.net_bps
                if res.net_bps > opp.peak_net_bps:
                    opp.peak_net_bps = res.net_bps
            if feas.ok and feas.net_bps_after_rounding > opp.peak_net_after_rounding_bps:
                opp.peak_net_after_rounding_bps = feas.net_bps_after_rounding
            opp.last_feasible = feasible
            opp.ever_feasible = opp.ever_feasible or feasible
        elif opp is not None:
            self._end_opportunity(tri.tri_id, "edge_gone" if res.ok else res.reason, now_ns)

    def _end_opportunity(self, tri_id: str, reason: str, now_ns: int) -> None:
        opp = self.active.pop(tri_id, None)
        if opp is None:
            return
        self.metrics.inc("cycles_ended")
        log_event(
            logger,
            logging.INFO,
            "cycle_ended",
            tri_id=tri_id,
            end_reason=reason,
            persisted_ms=round((opp.last_ns - opp.first_ns) / 1e6, 3),
            evals_while_active=opp.evals,
            peak_net_bps=round(opp.peak_net_bps, 4),
            peak_net_after_rounding_bps=(
                round(opp.peak_net_after_rounding_bps, 4)
                if opp.peak_net_after_rounding_bps != float("-inf")
                else None
            ),
            ever_feasible=opp.ever_feasible,
        )

    # ------------------------------------------------------- background tasks

    async def _sweep_loop(self) -> None:
        """Close opportunities whose leg books went stale between updates."""
        while True:
            await asyncio.sleep(_SWEEP_INTERVAL_S)
            now_ns = time.perf_counter_ns()
            for tri_id in list(self.active):
                tri = self._tri_by_id[tri_id]
                for leg in tri.legs:
                    book = self.store.get(leg.symbol)
                    if book is None or book.is_stale(now_ns, self._staleness_ms):
                        self._end_opportunity(tri_id, "stale_book", now_ns)
                        break
            if self.dashboard is not None:
                self.dashboard.refresh()

    async def _stats_loop(self) -> None:
        interval = self.settings.observer.stats_interval_s
        while True:
            await asyncio.sleep(interval)
            log_event(logger, logging.INFO, "stats", **self.metrics.snapshot())

    async def _metadata_loop(self) -> None:
        """Refresh symbol metadata; warn if a subscribed symbol degrades.

        Phase 1 does not rebuild the triangle set mid-run — a changed universe
        is logged for the operator; restart to pick it up.
        """
        interval = self.settings.observer.metadata_refresh_s
        while True:
            await asyncio.sleep(interval)
            try:
                symbols = await self.adapter.fetch_symbols()
            except Exception:
                logger.exception("metadata refresh failed; keeping previous metadata")
                self.metrics.inc("metadata_refresh_failures")
                continue
            fresh = {s.symbol: s for s in symbols}
            missing = [s for s in self.by_symbol if s not in fresh]
            if missing:
                log_event(
                    logger,
                    logging.WARNING,
                    "symbols_degraded",
                    symbols=missing,
                    note="subscribed symbols no longer online; restart to rebuild universe",
                )
            self.meta = fresh
            log_event(logger, logging.INFO, "metadata_refreshed", symbols=len(fresh))
