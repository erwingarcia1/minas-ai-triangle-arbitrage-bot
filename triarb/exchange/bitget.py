"""Bitget spot adapter — native WebSocket + REST, public (market-data) endpoints only.

Protocol notes (Bitget API v2):
- REST base ``https://api.bitget.com``; success payloads carry ``code == "00000"``.
- Symbols:  ``GET /api/v2/spot/public/symbols``
- Tickers:  ``GET /api/v2/spot/market/tickers`` (includes ``usdtVolume``)
- Public WS ``wss://ws.bitget.com/v2/ws/public``; subscribe with
  ``{"op": "subscribe", "args": [{"instType": "SPOT", "channel": "books15",
  "instId": "BTCUSDT"}, ...]}``.
- Heartbeat: the CLIENT sends the literal text frame ``ping`` every ~25s and
  the server answers ``pong``. The server drops idle connections, so the ping
  task is not optional.
- Depth channels ``books1/books5/books15`` push full snapshots on change;
  the full ``books`` channel (incremental + checksum) is deliberately NOT
  supported in Phase 1 — snapshots avoid merge bugs while we only observe.

Failure modes:
- REST: non-200 status, venue error code, or schema mismatch → ``RestError``
  with endpoint context. Entries that fail validation individually are
  dropped and counted, never silently coerced.
- WS: any disconnect/timeout yields ``StreamStatus(disconnected)`` and the
  adapter reconnects with exponential backoff (1s → 30s cap) and
  resubscribes. A venue ``event: error`` frame yields
  ``StreamStatus(error)``; it never kills the stream loop.
- Subscribe requests are chunked (rate limit ~10 messages/s per connection);
  a rejected channel is reported via ``StreamStatus(error)``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any

import aiohttp
import orjson
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from triarb.exchange.base import BookSnapshot, ExchangeAdapter, StreamStatus, SymbolInfo, Ticker
from triarb.exchange.errors import RestError

logger = logging.getLogger(__name__)

BITGET_REST_URL = "https://api.bitget.com"
BITGET_WS_PUBLIC_URL = "wss://ws.bitget.com/v2/ws/public"

_SUBSCRIBE_CHUNK = 20
_SUBSCRIBE_GAP_S = 0.15
_PING_INTERVAL_S = 25.0
_RECV_TIMEOUT_S = 45.0
_BACKOFF_START_S = 1.0
_BACKOFF_CAP_S = 30.0


def _f(value: Any) -> float:
    """Bitget sends numbers as strings and omits/empties illiquid fields."""
    if value in (None, ""):
        return 0.0
    return float(value)


def parse_symbol(entry: dict[str, Any]) -> SymbolInfo | None:
    """Parse one ``/spot/public/symbols`` entry; None for non-online symbols."""
    if entry.get("status") != "online":
        return None
    return SymbolInfo(
        symbol=entry["symbol"],
        base=entry["baseCoin"].upper(),
        quote=entry["quoteCoin"].upper(),
        price_tick_size=Decimal(1).scaleb(-int(entry["pricePrecision"])),
        quantity_step_size=Decimal(1).scaleb(-int(entry["quantityPrecision"])),
        min_base_amount=Decimal(entry.get("minTradeAmount") or "0"),
        min_notional=Decimal(entry.get("minTradeUSDT") or "0"),
        min_notional_asset="USDT",  # minTradeUSDT is USDT for every symbol
        status="online",
    )


def parse_ticker(entry: dict[str, Any]) -> Ticker:
    """Parse one ``/spot/market/tickers`` entry."""
    return Ticker(
        symbol=entry["symbol"],
        bid=_f(entry.get("bidPr")),
        ask=_f(entry.get("askPr")),
        last=_f(entry.get("lastPr")),
        usdt_volume_24h=_f(entry.get("usdtVolume")),
        ts_ms=int(entry.get("ts") or 0),
    )


def parse_ws_message(
    raw: str | bytes, recv_ns: int, recv_wall_ms: int
) -> list[BookSnapshot] | StreamStatus | None:
    """Parse one WS frame into book snapshots, an error status, or None.

    None means "nothing actionable" (pong, subscribe ack, unknown channel).
    Raises orjson/KeyError/ValueError on malformed frames — the caller logs
    the raw frame and continues; a single bad frame must not kill the stream.
    """
    if raw == "pong" or raw == b"pong":
        return None
    msg = orjson.loads(raw)
    if not isinstance(msg, dict):
        raise ValueError("frame is not a JSON object")
    event = msg.get("event")
    if event == "error":
        return StreamStatus(
            StreamStatus.ERROR,
            f"code={msg.get('code')} msg={msg.get('msg')}",
            recv_ns,
        )
    if event is not None:  # subscribe/unsubscribe acks
        return None
    arg = msg.get("arg")
    data = msg.get("data")
    if not arg or not data:
        return None
    if not isinstance(arg, dict) or not isinstance(data, list):
        raise ValueError("unexpected arg/data shape")
    if not str(arg.get("channel", "")).startswith("books"):
        return None
    inst_id = arg["instId"]
    fallback_ts = int(msg.get("ts") or 0)
    snapshots: list[BookSnapshot] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("depth entry is not an object")
        bids = [(float(p), float(s)) for p, s in entry.get("bids", ())]
        asks = [(float(p), float(s)) for p, s in entry.get("asks", ())]
        snapshots.append(
            BookSnapshot(
                symbol=inst_id,
                bids=bids,
                asks=asks,
                ts_exchange_ms=int(entry.get("ts") or fallback_ts),
                recv_ns=recv_ns,
                recv_wall_ms=recv_wall_ms,
            )
        )
    return snapshots


class BitgetAdapter(ExchangeAdapter):
    name = "bitget"

    def __init__(
        self,
        rest_url: str = BITGET_REST_URL,
        ws_url: str = BITGET_WS_PUBLIC_URL,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._rest_url = rest_url.rstrip("/")
        self._ws_url = ws_url
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "triarb/0.1 (observer)"},
            )
            self._owns_session = True
        return self._session

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        session = await self._get_session()
        url = f"{self._rest_url}{path}"
        try:
            async with session.get(url, params=params) as resp:
                raw = await resp.read()
                if resp.status != 200:
                    raise RestError(self.name, path, f"HTTP {resp.status}: {raw[:200]!r}")
        except aiohttp.ClientError as exc:
            raise RestError(self.name, path, f"transport: {exc!r}") from exc
        try:
            payload = orjson.loads(raw)
        except orjson.JSONDecodeError as exc:
            raise RestError(self.name, path, f"invalid JSON: {exc}") from exc
        if payload.get("code") != "00000":
            raise RestError(
                self.name, path, f"venue code={payload.get('code')!r} msg={payload.get('msg')!r}"
            )
        return payload.get("data")

    async def fetch_symbols(self) -> list[SymbolInfo]:
        data = await self._get("/api/v2/spot/public/symbols")
        if not isinstance(data, list):
            raise RestError(self.name, "/api/v2/spot/public/symbols", "data is not a list")
        symbols: list[SymbolInfo] = []
        dropped = 0
        for entry in data:
            try:
                info = parse_symbol(entry)
            except (KeyError, ValueError, ArithmeticError) as exc:
                dropped += 1
                logger.warning(
                    "bitget symbol entry dropped: %r (%s)", entry.get("symbol", "?"), exc
                )
                continue
            if info is not None:
                symbols.append(info)
        if dropped:
            logger.warning("bitget: dropped %d malformed symbol entries", dropped)
        return symbols

    async def fetch_tickers(self) -> list[Ticker]:
        data = await self._get("/api/v2/spot/market/tickers")
        if not isinstance(data, list):
            raise RestError(self.name, "/api/v2/spot/market/tickers", "data is not a list")
        tickers: list[Ticker] = []
        for entry in data:
            try:
                tickers.append(parse_ticker(entry))
            except (KeyError, ValueError) as exc:
                logger.warning(
                    "bitget ticker entry dropped: %r (%s)", entry.get("symbol", "?"), exc
                )
        return tickers

    def min_push_interval_ms(self, channel: str) -> float | None:
        resolved = "books15" if channel == "auto" else channel
        if resolved == "books1":
            return 10.0
        if resolved in ("books5", "books15"):
            return 200.0
        return None

    async def stream_books(
        self, symbols: Sequence[str], channel: str = "auto"
    ) -> AsyncIterator[BookSnapshot | StreamStatus]:
        if channel == "auto":
            channel = "books15"
        if channel not in ("books1", "books5", "books15"):
            raise ValueError(
                f"bitget channel {channel!r} unsupported in this phase "
                "(snapshot channels only: books1/books5/books15 or 'auto')"
            )
        backoff = _BACKOFF_START_S
        while True:
            ping_task: asyncio.Task[None] | None = None
            try:
                async with connect(
                    self._ws_url, ping_interval=None, open_timeout=10, close_timeout=5
                ) as ws:
                    yield StreamStatus(
                        StreamStatus.CONNECTED, self._ws_url, time.perf_counter_ns()
                    )
                    await self._subscribe(ws, symbols, channel)
                    ping_task = asyncio.create_task(self._pinger(ws))
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_S)
                        recv_ns = time.perf_counter_ns()
                        recv_wall_ms = int(time.time() * 1000)
                        try:
                            parsed = parse_ws_message(raw, recv_ns, recv_wall_ms)
                        except (
                            orjson.JSONDecodeError,
                            KeyError,
                            ValueError,
                            TypeError,
                            AttributeError,
                            IndexError,
                        ) as exc:
                            logger.error("bitget WS frame unparseable (%s): %.300r", exc, raw)
                            continue
                        if parsed is None:
                            continue
                        if isinstance(parsed, StreamStatus):
                            logger.error("bitget WS error frame: %s", parsed.detail)
                            yield parsed
                            continue
                        # Book data flowing is the evidence of a healthy
                        # connection; resetting backoff any earlier turns a
                        # handshake-then-drop maintenance loop into a 1s hammer.
                        backoff = _BACKOFF_START_S
                        for snapshot in parsed:
                            yield snapshot
            except asyncio.CancelledError:
                raise
            except (WebSocketException, asyncio.TimeoutError, OSError) as exc:
                yield StreamStatus(
                    StreamStatus.DISCONNECTED, repr(exc), time.perf_counter_ns()
                )
                logger.warning(
                    "bitget WS disconnected (%r); reconnecting in %.1fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, _BACKOFF_CAP_S)
            finally:
                if ping_task is not None:
                    ping_task.cancel()

    @staticmethod
    async def _subscribe(ws: ClientConnection, symbols: Sequence[str], channel: str) -> None:
        args = [
            {"instType": "SPOT", "channel": channel, "instId": symbol} for symbol in symbols
        ]
        for i in range(0, len(args), _SUBSCRIBE_CHUNK):
            chunk = args[i : i + _SUBSCRIBE_CHUNK]
            await ws.send(orjson.dumps({"op": "subscribe", "args": chunk}).decode())
            if i + _SUBSCRIBE_CHUNK < len(args):
                await asyncio.sleep(_SUBSCRIBE_GAP_S)

    @staticmethod
    async def _pinger(ws: ClientConnection) -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL_S)
                await ws.send("ping")
        except asyncio.CancelledError:
            pass
        except WebSocketException:
            pass  # recv loop sees the same closure and handles reconnect

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
