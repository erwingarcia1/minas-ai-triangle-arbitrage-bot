"""Simple terminal dashboard (optional, ``--dashboard``).

Renders counters and live opportunities with ``rich``. When the dashboard
owns the terminal, JSON logs are routed to a file instead of stdout (the CLI
enforces this).

Failure modes: purely cosmetic — any render error is logged and the observer
keeps running; the JSON log stream is always the source of truth.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from rich.console import Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from triarb.observer import Observer

logger = logging.getLogger(__name__)


class Dashboard:
    def __init__(self, observer: "Observer") -> None:
        self._observer = observer
        self._live: Live | None = None
        self._started_at = time.monotonic()

    def __enter__(self) -> "Dashboard":
        self._live = Live(self._render(), refresh_per_second=2, transient=False)
        self._live.__enter__()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._live is not None:
            self._live.__exit__(*exc)

    def refresh(self) -> None:
        if self._live is None:
            return
        try:
            self._live.update(self._render())
        except Exception:  # noqa: BLE001 - cosmetic layer, never kill the observer
            logger.exception("dashboard render failed")

    def _render(self) -> Group:
        obs = self._observer
        counters = obs.metrics.counters
        uptime = time.monotonic() - self._started_at

        header = Text(
            f"triarb observer [{obs.settings.venue.value}] "
            f"uptime={uptime:,.0f}s books={len(obs.store)} "
            f"triangles={len(obs.triangles)} msgs={counters['book_updates']:,} "
            f"evals={counters['evaluations']:,}",
            style="bold",
        )

        stats = Table(title="counters", expand=False)
        stats.add_column("counter")
        stats.add_column("value", justify="right")
        for key in (
            "cycles_detected",
            "cycles_ended",
            "rounding_killed",
            "insufficient_depth",
            "stale_skips",
            "ws_disconnects",
        ):
            stats.add_row(key, f"{counters[key]:,}")

        opps = Table(title="active opportunities (net bps after fees)", expand=False)
        opps.add_column("triangle")
        opps.add_column("net bps", justify="right")
        opps.add_column("peak bps", justify="right")
        opps.add_column("age ms", justify="right")
        opps.add_column("feasible")
        now_ns = time.perf_counter_ns()
        active = sorted(
            obs.active.items(), key=lambda kv: kv[1].last_net_bps, reverse=True
        )
        for tri_id, opp in active[:10]:
            opps.add_row(
                tri_id,
                f"{opp.last_net_bps:.2f}",
                f"{opp.peak_net_bps:.2f}",
                f"{(now_ns - opp.first_ns) / 1e6:,.0f}",
                "yes" if opp.last_feasible else "no",
            )
        return Group(header, stats, opps)
