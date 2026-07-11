"""Bybit stream-loop behavior over a fake transport (no network).

Covers the fail-safe paths that only exist in ``stream_books``'s network
loop: sequence gap → ERROR status + EMPTY book + one unsubscribe/subscribe
pair; failed ack → forced reconnect (DISCONNECTED marks all books stale).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import orjson
import pytest

import triarb.exchange.bybit as bybit_mod
from triarb.exchange.base import BookSnapshot, StreamStatus
from triarb.exchange.bybit import BybitAdapter


def _frame(obj: dict[str, Any]) -> bytes:
    return orjson.dumps(obj)


SUB_ACK_OK = _frame({"success": True, "ret_msg": "subscribe", "op": "subscribe", "conn_id": "c"})
SUB_ACK_FAIL = _frame(
    {"success": False, "ret_msg": "error:handler not found", "op": "subscribe", "conn_id": "c"}
)


def snapshot(u: int) -> bytes:
    return _frame(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot",
            "ts": 1,
            "data": {"s": "BTCUSDT", "b": [["100", "1"]], "a": [["101", "1"]], "u": u, "seq": 1},
        }
    )


def delta(u: int) -> bytes:
    return _frame(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "delta",
            "ts": 2,
            "data": {"s": "BTCUSDT", "b": [["100", "2"]], "a": [], "u": u, "seq": 2},
        }
    )


class FakeWs:
    """Scripted WebSocket: recv() pops frames, then blocks forever."""

    def __init__(self, frames: list[bytes]) -> None:
        self.frames = list(frames)
        self.sent: list[dict[str, Any]] = []

    async def recv(self) -> bytes:
        if self.frames:
            return self.frames.pop(0)
        await asyncio.Event().wait()  # block until the test closes the stream
        raise AssertionError("unreachable")

    async def send(self, data: str) -> None:
        self.sent.append(orjson.loads(data))


class FakeConnect:
    """Async context manager standing in for websockets.connect."""

    def __init__(self, sessions: list[FakeWs]) -> None:
        self.sessions = list(sessions)
        self.opened = 0

    def __call__(self, *args: Any, **kwargs: Any) -> "FakeConnect":
        return self

    async def __aenter__(self) -> FakeWs:
        self.opened += 1
        return self.sessions.pop(0)

    async def __aexit__(self, *exc: Any) -> None:
        return None


async def _collect(agen: Any, n: int, timeout: float = 5.0) -> list[Any]:
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(anext(agen), timeout))
    return out


class TestSequenceGapFailSafe:
    async def test_gap_yields_error_empty_book_and_one_resubscribe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = FakeWs([SUB_ACK_OK, snapshot(100), delta(103)])  # missed 101-102
        monkeypatch.setattr(bybit_mod, "connect", FakeConnect([ws]))
        adapter = BybitAdapter()
        stream = adapter.stream_books(["BTCUSDT"], "orderbook.50")
        try:
            connected, snap, err, empty = await _collect(stream, 4)
            assert isinstance(connected, StreamStatus)
            assert connected.kind == StreamStatus.CONNECTED
            assert isinstance(snap, BookSnapshot)
            assert snap.bids == [(100.0, 1.0)]
            assert isinstance(err, StreamStatus)
            assert err.kind == StreamStatus.ERROR
            assert "sequence gap" in err.detail
            # The fail-safe empty book makes the symbol unpriceable downstream.
            assert isinstance(empty, BookSnapshot)
            assert empty.symbol == "BTCUSDT"
            assert empty.bids == [] and empty.asks == []
            # The generator is suspended at the empty-book yield; step it
            # once more (recv then blocks forever) so _resubscribe runs.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(anext(stream), 0.2)
            # Exactly one unsubscribe+subscribe pair for the gapped topic.
            ops = [(f.get("op"), tuple(f.get("args", ()))) for f in ws.sent]
            assert ops.count(("unsubscribe", ("orderbook.50.BTCUSDT",))) == 1
            assert ops.count(("subscribe", ("orderbook.50.BTCUSDT",))) == 2  # initial + re-sub
        finally:
            await stream.aclose()
            await adapter.aclose()


class TestFailedAckForcesReconnect:
    async def test_rejected_subscribe_escalates_to_disconnect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # First connection: the subscribe is rejected. The adapter must not
        # leave the topic silently dead — it yields ERROR then DISCONNECTED
        # (so the observer marks all books stale) and reconnects.
        fake = FakeConnect([FakeWs([SUB_ACK_FAIL]), FakeWs([])])
        monkeypatch.setattr(bybit_mod, "connect", fake)
        adapter = BybitAdapter()
        stream = adapter.stream_books(["BTCUSDT"], "orderbook.50")
        try:
            connected, err, disconnected = await _collect(stream, 3)
            assert connected.kind == StreamStatus.CONNECTED
            assert err.kind == StreamStatus.ERROR
            assert "handler not found" in err.detail
            assert disconnected.kind == StreamStatus.DISCONNECTED
            assert "ack failure" in disconnected.detail
        finally:
            await stream.aclose()
            await adapter.aclose()
