"""CLI entry point.

Phase 1 exposes exactly one command: ``observe``. There is no ``--live``
flag because there is no execution path to enable — the observer cannot
place orders, and ``ensure_supported_mode`` refuses every mode except
``market_data_only``. A non-live mode remains the default forever; when
execution ships (Phase 2+), live trading will additionally require a
``--live`` flag, ``CONFIRM_LIVE=yes`` in the environment, AND the full
CLAUDE.md §3 authorization chain.

Failure modes: config/venue/mode errors abort before any network
connection; SIGINT/SIGTERM trigger a clean shutdown with a final stats
snapshot.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys

from triarb.config import ensure_supported_mode, load_settings
from triarb.exchange import create_adapter
from triarb.monitoring.dashboard import Dashboard
from triarb.monitoring.jsonlog import log_event, setup_logging
from triarb.observer import Observer

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="triarb",
        description="Single-exchange triangular arbitrage system (Phase 1: observer only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    observe = sub.add_parser(
        "observe",
        help="Stream books, detect cycles, log edges. Places no orders.",
    )
    observe.add_argument("--config", required=True, help="Path to YAML config")
    observe.add_argument(
        "--dashboard",
        action="store_true",
        help="Render a live terminal dashboard (JSON logs go to --log-file)",
    )
    observe.add_argument(
        "--log-file",
        default=None,
        help="Append JSON logs to this file (default with --dashboard: triarb-observer.jsonl)",
    )
    observe.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Stop after this many seconds (default: run until interrupted)",
    )
    observe.add_argument("--verbose", action="store_true", help="Debug-level logging")
    return parser


async def _observe(args: argparse.Namespace) -> None:
    settings = load_settings(args.config)
    ensure_supported_mode(settings)

    log_path = args.log_file
    if args.dashboard and log_path is None:
        log_path = "triarb-observer.jsonl"
    setup_logging(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=None if args.dashboard else sys.stdout,
        path=log_path,
    )

    adapter = create_adapter(settings.venue)
    observer = Observer(settings, adapter)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    log_event(
        logger,
        logging.INFO,
        "startup",
        mode="observer",
        venue=settings.venue.value,
        config=str(args.config),
        note="Phase 1: no order code exists in this build",
    )

    try:
        await observer.prepare()
        with contextlib.ExitStack() as stack:
            if args.dashboard:
                observer.dashboard = stack.enter_context(Dashboard(observer))
            run_task = asyncio.create_task(observer.run(), name="observer")
            stop_task = asyncio.create_task(stop.wait(), name="stop-signal")
            done, pending = await asyncio.wait(
                {run_task, stop_task},
                timeout=args.duration,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if run_task in done:
                run_task.result()  # propagate crashes loudly
    finally:
        await adapter.aclose()
        log_event(logger, logging.INFO, "shutdown", **observer.metrics.snapshot())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "observe":
        asyncio.run(_observe(args))
        return 0
    return 2  # unreachable: subparsers are required


if __name__ == "__main__":
    sys.exit(main())
