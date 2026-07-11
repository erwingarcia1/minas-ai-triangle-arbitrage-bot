"""OKX Global spot adapter — native WebSocket + REST, public endpoints only.

Protocol notes (OKX API v5):
- REST base ``https://www.okx.com``; success payloads carry ``code == "0"``.
- Instruments: ``GET /api/v5/public/instruments?instType=SPOT`` —
  ``instId`` (``BTC-USDT``), ``baseCcy``/``quoteCcy``, ``state`` (``live``),
  ``tickSz``/``lotSz`` (exact increments as strings — NOT guaranteed to be
  powers of ten), ``minSz`` (minimum order size in base currency).
- Tickers: ``GET /api/v5/market/tickers?instType=SPOT`` — ``bidPx``/
  ``askPx``/``last``; ``volCcy24h`` is 24h volume in the QUOTE currency for
  spot, converted here to USDT via the quote asset's own ``<CCY>-USDT``
  last price from the same batch.
- Public WS ``wss://ws.okx.com:8443/ws/v5/public``; subscribe with
  ``{"op": "subscribe", "args": [{"channel": "books5", "instId":
  "BTC-USDT"}, ...]}``.
- ``books5`` pushes a COMPLETE 5-level snapshot on every message (~100ms
  when changed) — snapshot semantics, no merging. Depth levels are
  ``[price, size, deprecated, order_count]``; only the first two elements
  are read. The incremental ``books``/``books-l2-tbt`` channels (delta +
  checksum) are deliberately not used in this phase.
- Heartbeat: the CLIENT sends the literal text frame ``ping``; the server
  answers ``pong``. The venue drops connections idle for ~30s, so the ping
  task is not optional.
- Symbol format is ``BASE-QUOTE`` on both REST and WS.

Failure modes:
- REST: non-200, venue error code, or schema mismatch → ``RestError``.
  Individually malformed entries are dropped and counted, never coerced.
- WS: any disconnect/timeout yields ``StreamStatus(disconnected)`` and the
  adapter reconnects with exponential backoff and resubscribes. A venue
  ``event: error`` frame yields ``StreamStatus(error)``.
- Subscribe requests are chunked (message-size and rate limits).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp
import orjson
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from triarb.exchange.base import BookSnapshot, ExchangeAdapter, StreamStatus, SymbolInfo, Ticker
from triarb.exchange.errors import RestError

logger = logging.getLogger(__name__)

OKX_REST_URL = "https://www.okx.com"
OKX_WS_PUBLIC_URL = "wss://ws.okx.com:8443/ws/v5/public"

_DEFAULT_CHANNEL = "books5"
_SUBSCRIBE_CHUNK = 20
_SUBSCRIBE_GAP_S = 0.15
_PING_INTERVAL_S = 25.0
_RECV_TIMEOUT_S = 45.0
_BACKOFF_START_S = 1.0
_BACKOFF_CAP_S = 30.0


def _f(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _positive_decimal(raw: Any) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def validate_channel(channel: str) -> str:
    if channel == "auto":
        return _DEFAULT_CHANNEL
    if channel != "books5":
        raise ValueError(
            f"okx channel {channel!r} unsupported in this phase "
            "(snapshot channel only: books5 or 'auto')"
        )
    return channel


def parse_symbol(entry: dict[str, Any]) -> SymbolInfo | None:
    """Parse one ``/public/instruments`` entry; None when not live/roundable."""
    if entry.get("state") != "live":
        return None
    tick = _positive_decimal(entry.get("tickSz"))
    step = _positive_decimal(entry.get("lotSz"))
    if tick is None or step is None:
        return None
    return SymbolInfo(
        symbol=entry["instId"],
        base=entry["baseCcy"].upper(),
        quote=entry["quoteCcy"].upper(),
        price_tick_size=tick,
        quantity_step_size=step,
        min_base_amount=_positive_decimal(entry.get("minSz")) or Decimal(0),
        min_notional=Decimal(0),  # OKX spot publishes no min notional
        status="online",
    )


def parse_ticker(entry: dict[str, Any], usdt_rate: float) -> Ticker:
    """Parse one ``/market/tickers`` entry; ``volCcy24h`` (quote units for
    spot) is converted to USDT via ``usdt_rate`` (0.0 when unknown — the
    symbol is then conservatively excluded by the discovery volume filter)."""
    return Ticker(
        symbol=entry["instId"],
        bid=_f(entry.get("bidPx")),
        ask=_f(entry.get("askPx")),
        last=_f(entry.get("last")),
        usdt_volume_24h=_f(entry.get("volCcy24h")) * usdt_rate,
        ts_ms=int(entry.get("ts") or 0),
    )


def parse_ws_message(
    raw: str | bytes, recv_ns: int, recv_wall_ms: int
) -> list[BookSnapshot] | StreamStatus | None:
    """Parse one WS frame into book snapshots, an error status, or None.

    None means "nothing actionable" (pong, subscribe ack, unknown channel).
    Raises orjson/KeyError/ValueError/IndexError on malformed frames — the
    caller logs and continues; a single bad frame must not kill the stream.
    """
    if raw == "pong" or raw == b"pong":
        return None
    msg = orjson.loads(raw)
    if not isinstance(msg, dict):
        raise ValueError("frame is not a JSON object")
    event = msg.get("event")
    if event == "error":
        return StreamStatus(
            StreamStatus.ERROR, f"code={msg.get('code')} msg={msg.get('msg')}", recv_ns
        )
    if event is not None:  # subscribe/unsubscribe/channel-conn-count acks
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
    snapshots: list[BookSnapshot] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError("depth entry is not an object")
        # levels are [price, size, <deprecated>, order_count]
        bids = [(float(lvl[0]), float(lvl[1])) for lvl in entry.get("bids", ())]
        asks = [(float(lvl[0]), float(lvl[1])) for lvl in entry.get("asks", ())]
        snapshots.append(
            BookSnapshot(
                symbol=inst_id,
                bids=bids,
                asks=asks,
                ts_exchange_ms=int(entry.get("ts") or 0),
                recv_ns=recv_ns,
                recv_wall_ms=recv_wall_ms,
            )
        )
    return snapshots


class OkxAdapter(ExchangeAdapter):
    name = "okx"

    def __init__(
        self,
        rest_url: str = OKX_REST_URL,
        ws_url: str = OKX_WS_PUBLIC_URL,
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
        if payload.get("code") != "0":
            raise RestError(
                self.name, path, f"venue code={payload.get('code')!r} msg={payload.get('msg')!r}"
            )
        return payload.get("data")

    async def fetch_symbols(self) -> list[SymbolInfo]:
        data = await self._get("/api/v5/public/instruments", {"instType": "SPOT"})
        if not isinstance(data, list):
            raise RestError(self.name, "/api/v5/public/instruments", "data is not a list")
        symbols: list[SymbolInfo] = []
        dropped = 0
        for entry in data:
            try:
                info = parse_symbol(entry)
            except (KeyError, ValueError, ArithmeticError) as exc:
                dropped += 1
                logger.warning(
                    "okx symbol entry dropped: %r (%s)", entry.get("instId", "?"), exc
                )
                continue
            if info is not None:
                symbols.append(info)
        if dropped:
            logger.warning("okx: dropped %d malformed symbol entries", dropped)
        return symbols

    async def fetch_tickers(self) -> list[Ticker]:
        data = await self._get("/api/v5/market/tickers", {"instType": "SPOT"})
        if not isinstance(data, list):
            raise RestError(self.name, "/api/v5/market/tickers", "data is not a list")
        by_inst = {e.get("instId"): e for e in data if isinstance(e, dict)}
        quote_to_usdt: dict[str, float] = {"USDT": 1.0}
        tickers: list[Ticker] = []
        for inst_id, entry in by_inst.items():
            if not inst_id or "-" not in inst_id:
                continue
            quote = inst_id.rsplit("-", 1)[1]
            rate = quote_to_usdt.get(quote)
            if rate is None:
                rate = _f((by_inst.get(f"{quote}-USDT") or {}).get("last"))
                quote_to_usdt[quote] = rate
            try:
                tickers.append(parse_ticker(entry, rate))
            except (KeyError, ValueError) as exc:
                logger.warning("okx ticker entry dropped: %r (%s)", inst_id, exc)
        return tickers

    def min_push_interval_ms(self, channel: str) -> float | None:
        try:
            validate_channel(channel)
        except ValueError:
            return None
        return 100.0  # books5: ~100ms when the book changed

    async def stream_books(
        self, symbols: Sequence[str], channel: str = "auto"
    ) -> AsyncIterator[BookSnapshot | StreamStatus]:
        channel = validate_channel(channel)
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
                            logger.error("okx WS frame unparseable (%s): %.300r", exc, raw)
                            continue
                        if parsed is None:
                            continue
                        if isinstance(parsed, StreamStatus):
                            logger.error("okx WS error frame: %s", parsed.detail)
                            yield parsed
                            continue
                        # Book data flowing = healthy connection; only then
                        # is it safe to reset the reconnect backoff.
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
                    "okx WS disconnected (%r); reconnecting in %.1fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, _BACKOFF_CAP_S)
            finally:
                if ping_task is not None:
                    ping_task.cancel()

    @staticmethod
    async def _subscribe(ws: ClientConnection, symbols: Sequence[str], channel: str) -> None:
        args = [{"channel": channel, "instId": symbol} for symbol in symbols]
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
