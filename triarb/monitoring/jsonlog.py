"""Structured JSON-lines logging.

Every log line is one JSON object: ``ts`` (ISO-8601 UTC), ``level``,
``logger``, ``event``, plus arbitrary fields. Grep-able, jq-able, and stable
enough to analyze days of observer output.

Failure modes: a field that orjson cannot serialize is stringified via
``default=str`` rather than dropping the whole line; logging must never
raise into the hot path.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

import orjson


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(fields)
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return orjson.dumps(payload, default=str).decode()


def setup_logging(
    level: int = logging.INFO,
    stream: TextIO | None = None,
    path: str | None = None,
) -> None:
    """Route root logging to JSON lines on ``stream`` and/or append-mode ``path``."""
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    formatter = JsonFormatter()
    if path is not None:
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    if stream is not None or path is None:
        stream_handler = logging.StreamHandler(stream or sys.stdout)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)
    # Third-party debug chatter stays out of the JSON stream.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit a structured event; ``fields`` land as top-level JSON keys."""
    logger.log(level, event, extra={"fields": fields})
