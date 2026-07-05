"""In-process metrics: counters and fixed-bucket histograms.

Deliberately dependency-free and allocation-light — ``Histogram.observe`` is
called on every book update in the hot path.

Failure modes: none at runtime; values reset only on process restart. The
periodic stats log line is the durable record.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from typing import Any

# Handler latency buckets (microseconds).
LATENCY_BUCKETS_US: tuple[float, ...] = (50, 100, 250, 500, 1_000, 2_500, 5_000, 10_000, 50_000)
# Net-edge distribution buckets (bps).
EDGE_BUCKETS_BPS: tuple[float, ...] = (-50, -20, -10, -5, 0, 5, 10, 20, 50)


class Histogram:
    """Fixed-bucket histogram; bucket i counts values <= bounds[i]."""

    __slots__ = ("bounds", "counts", "total", "sum")

    def __init__(self, bounds: tuple[float, ...]) -> None:
        self.bounds = bounds
        self.counts = [0] * (len(bounds) + 1)
        self.total = 0
        self.sum = 0.0

    def observe(self, value: float) -> None:
        self.counts[bisect_right(self.bounds, value)] += 1
        self.total += 1
        self.sum += value

    def snapshot(self) -> dict[str, Any]:
        labels: list[str] = []
        prev = None
        for bound in self.bounds:
            labels.append(f"<={bound:g}" if prev is None else f"({prev:g},{bound:g}]")
            prev = bound
        labels.append(f">{self.bounds[-1]:g}")
        return {
            "total": self.total,
            "mean": (self.sum / self.total) if self.total else 0.0,
            "buckets": dict(zip(labels, self.counts)),
        }


class Metrics:
    """Registry of counters and the two hot-path histograms."""

    __slots__ = ("counters", "reprice_latency_us", "net_edge_bps")

    def __init__(self) -> None:
        self.counters: defaultdict[str, int] = defaultdict(int)
        self.reprice_latency_us = Histogram(LATENCY_BUCKETS_US)
        self.net_edge_bps = Histogram(EDGE_BUCKETS_BPS)

    def inc(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def snapshot(self) -> dict[str, Any]:
        return {
            "counters": dict(self.counters),
            "reprice_latency_us": self.reprice_latency_us.snapshot(),
            "net_edge_bps": self.net_edge_bps.snapshot(),
        }
