"""Binance Global spot adapter — native WebSocket + REST, public endpoints only.

Protocol notes (Binance spot API):
- REST base ``https://api.binance.com``; errors return non-2xx with
  ``{"code": <negative int>, "msg": ...}``.
- Instruments: ``GET /api/v3/exchangeInfo`` — per symbol: ``status``
  (``TRADING`` = live), ``baseAsset``/``quoteAsset``, and ``filters``:
  ``PRICE_FILTER.tickSize``, ``LOT_SIZE.stepSize``/``minQty``,
  ``NOTIONAL.minNotional`` (``MIN_NOTIONAL.minNotional`` on older payloads).
- Tickers: ``GET /api/v3/ticker/24hr`` — ``bidPrice``/``askPrice``/
  ``lastPrice``/``quoteVolume`` (quote-denominated, NOT USDT; this adapter
  converts via the quote asset's own USDT market from the same batch).
- Public WS: combined-stream endpoint ``wss://stream.binance.com:9443/stream``
  with ``{"method": "SUBSCRIBE", "params": ["btcusdt@depth20@100ms", ...],
  "id": n}``. Combined payloads arrive wrapped:
  ``{"stream": "btcusdt@depth20@100ms", "data": {...}}`` — the wrapper is
  required because partial-depth payloads carry no symbol field.
- Partial book depth streams ``<symbol>@depth<5|10|20>[@100ms]`` push a
  COMPLETE top-N snapshot on every message (``lastUpdateId``, ``bids``,
  ``asks``) — exactly the snapshot semantics the observer's book store
  expects. The incremental ``@depth`` diff stream is deliberately not used.
- Stream names are lowercase; REST symbols are uppercase. The adapter maps
  both directions and emits uppercase symbols downstream.
- Heartbeat: the SERVER sends protocol-level pings; the ``websockets``
  library answers pongs automatically, so no application ping task is
  needed. Connections are dropped by the venue after 24h — the reconnect
  loop handles that like any other disconnect.
- Limits: 5 inbound messages/second per connection, 1024 streams per
  connection; subscriptions are chunked well below both.

Failure modes:
- REST: non-2xx, venue error body, or schema mismatch → ``RestError``.
  Individually malformed entries are dropped and counted, never coerced.
- Symbols whose tick/step filters are missing or non-positive are dropped
  (they cannot be rounded deterministically).
- WS: any disconnect/timeout yields ``StreamStatus(disconnected)`` and the
  adapter reconnects with exponential backoff and resubscribes. Error
  frames (``{"error": ...}``) yield ``StreamStatus(error)``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp
import orjson
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from triarb.exchange.base import BookSnapshot, ExchangeAdapter, StreamStatus, SymbolInfo, Ticker
from triarb.exchange.errors import RestError

logger = logging.getLogger(__name__)

BINANCE_REST_URL = "https://api.binance.com"
BINANCE_WS_URL = "wss://stream.binance.com:9443/stream"

_CHANNELS = ("depth5", "depth10", "depth20")
_SPEEDS = ("@100ms", "@1000ms", "")
_DEFAULT_CHANNEL = "depth20@100ms"

# 5 inbound msgs/s allowed; 2 msgs/s of ~50 streams each stays far inside it.
_SUBSCRIBE_CHUNK = 50
_SUBSCRIBE_GAP_S = 0.5
_RECV_TIMEOUT_S = 45.0
_BACKOFF_START_S = 1.0
_BACKOFF_CAP_S = 30.0


def _f(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _positive_decimal(raw: Any) -> Decimal | None:
    """Parse a venue decimal string; None when missing/zero/malformed."""
    if raw in (None, ""):
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def validate_channel(channel: str) -> str:
    """Resolve 'auto' and reject stream names this phase does not support."""
    if channel == "auto":
        return _DEFAULT_CHANNEL
    for base in _CHANNELS:
        for speed in _SPEEDS:
            if channel == f"{base}{speed}":
                return channel
    raise ValueError(
        f"binance channel {channel!r} unsupported in this phase (partial book "
        "depth snapshots only: depth5|depth10|depth20 with optional "
        "@100ms/@1000ms suffix, or 'auto')"
    )


def parse_symbol(entry: dict[str, Any]) -> SymbolInfo | None:
    """Parse one ``exchangeInfo.symbols`` entry; None when not tradeable or
    when its rounding filters are unusable."""
    # exchangeInfo also lists margin-only symbols with status TRADING; spot
    # permission must be explicitly present (a missing field drops the
    # symbol — loud discovery failure beats silently trading the wrong list).
    if entry.get("status") != "TRADING":
        return None
    if entry.get("isSpotTradingAllowed") is not True:
        return None
    tick = step = min_qty = None
    min_notional = Decimal(0)
    for filt in entry.get("filters", ()):  # filter order is not guaranteed
        ftype = filt.get("filterType")
        if ftype == "PRICE_FILTER":
            tick = _positive_decimal(filt.get("tickSize"))
        elif ftype == "LOT_SIZE":
            step = _positive_decimal(filt.get("stepSize"))
            min_qty = _positive_decimal(filt.get("minQty"))
        elif ftype in ("NOTIONAL", "MIN_NOTIONAL"):
            min_notional = _positive_decimal(filt.get("minNotional")) or Decimal(0)
    if tick is None or step is None:
        return None  # cannot round deterministically without both
    return SymbolInfo(
        symbol=entry["symbol"],
        base=entry["baseAsset"].upper(),
        quote=entry["quoteAsset"].upper(),
        price_tick_size=tick,
        quantity_step_size=step,
        min_base_amount=min_qty or Decimal(0),
        min_notional=min_notional,
        status="online",
    )


def parse_ticker(entry: dict[str, Any], usdt_rate: float) -> Ticker:
    """Parse one ``/ticker/24hr`` entry.

    ``quoteVolume`` is denominated in the symbol's quote asset;
    ``usdt_rate`` is that asset's USDT last price from the same batch
    (0.0 when unknown — the symbol is then conservatively excluded by the
    discovery volume filter, never guessed).
    """
    return Ticker(
        symbol=entry["symbol"],
        bid=_f(entry.get("bidPrice")),
        ask=_f(entry.get("askPrice")),
        last=_f(entry.get("lastPrice")),
        usdt_volume_24h=_f(entry.get("quoteVolume")) * usdt_rate,
        ts_ms=int(entry.get("closeTime") or 0),
    )


def parse_ws_message(
    raw: str | bytes, recv_ns: int, recv_wall_ms: int
) -> BookSnapshot | StreamStatus | None:
    """Parse one combined-stream frame; None for acks and unknown streams.

    Raises orjson/KeyError/ValueError on malformed frames — the caller logs
    and continues; one bad frame must not kill the stream.
    """
    msg = orjson.loads(raw)
    if "error" in msg:
        err = msg["error"]
        return StreamStatus(
            StreamStatus.ERROR, f"code={err.get('code')} msg={err.get('msg')}", recv_ns
        )
    if "result" in msg:  # SUBSCRIBE/UNSUBSCRIBE ack: {"result": null, "id": n}
        return None
    stream = msg.get("stream")
    data = msg.get("data")
    if not stream or not isinstance(data, dict) or "@depth" not in stream:
        return None
    symbol = stream.split("@", 1)[0].upper()
    bids = [(float(p), float(s)) for p, s in data.get("bids", ())]
    asks = [(float(p), float(s)) for p, s in data.get("asks", ())]
    return BookSnapshot(
        symbol=symbol,
        bids=bids,
        asks=asks,
        ts_exchange_ms=0,  # partial-depth payloads carry no event time
        recv_ns=recv_ns,
        recv_wall_ms=recv_wall_ms,
    )


class BinanceAdapter(ExchangeAdapter):
    name = "binance"

    def __init__(
        self,
        rest_url: str = BINANCE_REST_URL,
        ws_url: str = BINANCE_WS_URL,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._rest_url = rest_url.rstrip("/")
        self._ws_url = ws_url
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
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
            return orjson.loads(raw)
        except orjson.JSONDecodeError as exc:
            raise RestError(self.name, path, f"invalid JSON: {exc}") from exc

    async def fetch_symbols(self) -> list[SymbolInfo]:
        payload = await self._get("/api/v3/exchangeInfo")
        entries = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise RestError(self.name, "/api/v3/exchangeInfo", "symbols is not a list")
        symbols: list[SymbolInfo] = []
        dropped = 0
        for entry in entries:
            try:
                info = parse_symbol(entry)
            except (KeyError, ValueError, ArithmeticError) as exc:
                dropped += 1
                logger.warning(
                    "binance symbol entry dropped: %r (%s)", entry.get("symbol", "?"), exc
                )
                continue
            if info is not None:
                symbols.append(info)
        if dropped:
            logger.warning("binance: dropped %d malformed symbol entries", dropped)
        return symbols

    async def fetch_tickers(self) -> list[Ticker]:
        """24h tickers with USDT-normalized volume.

        Startup-only path: refetches ``exchangeInfo`` for the symbol→quote
        mapping rather than caching adapter state — one extra REST call for
        determinism.
        """
        symbols = await self.fetch_symbols()
        entries = await self._get("/api/v3/ticker/24hr")
        if not isinstance(entries, list):
            raise RestError(self.name, "/api/v3/ticker/24hr", "data is not a list")
        by_symbol = {e.get("symbol"): e for e in entries if isinstance(e, dict)}
        quote_to_usdt = _usdt_conversion(
            {s.quote for s in symbols},
            lambda cur: _f((by_symbol.get(f"{cur}USDT") or {}).get("lastPrice")),
        )
        tickers: list[Ticker] = []
        for info in symbols:
            entry = by_symbol.get(info.symbol)
            if entry is None:
                continue
            try:
                tickers.append(parse_ticker(entry, quote_to_usdt.get(info.quote, 0.0)))
            except (KeyError, ValueError) as exc:
                logger.warning("binance ticker entry dropped: %r (%s)", info.symbol, exc)
        return tickers

    async def stream_books(
        self, symbols: Sequence[str], channel: str = "auto"
    ) -> AsyncIterator[BookSnapshot | StreamStatus]:
        channel = validate_channel(channel)
        streams = [f"{s.lower()}@{channel}" for s in symbols]
        backoff = _BACKOFF_START_S
        while True:
            try:
                # ping_interval=None: the SERVER pings every 20s and the
                # library auto-pongs with the echoed payload; client pings
                # would only burn the 5 inbound-messages/s budget.
                async with connect(
                    self._ws_url, ping_interval=None, open_timeout=10, close_timeout=5
                ) as ws:
                    yield StreamStatus(
                        StreamStatus.CONNECTED, self._ws_url, time.perf_counter_ns()
                    )
                    backoff = _BACKOFF_START_S
                    await self._subscribe(ws, streams)
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_S)
                        recv_ns = time.perf_counter_ns()
                        recv_wall_ms = int(time.time() * 1000)
                        try:
                            parsed = parse_ws_message(raw, recv_ns, recv_wall_ms)
                        except (orjson.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                            logger.error("binance WS frame unparseable (%s): %.300r", exc, raw)
                            continue
                        if parsed is None:
                            continue
                        if isinstance(parsed, StreamStatus):
                            logger.error("binance WS error frame: %s", parsed.detail)
                        yield parsed
            except asyncio.CancelledError:
                raise
            except (WebSocketException, asyncio.TimeoutError, OSError) as exc:
                yield StreamStatus(
                    StreamStatus.DISCONNECTED, repr(exc), time.perf_counter_ns()
                )
                logger.warning(
                    "binance WS disconnected (%r); reconnecting in %.1fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, _BACKOFF_CAP_S)

    @staticmethod
    async def _subscribe(ws: ClientConnection, streams: list[str]) -> None:
        for i in range(0, len(streams), _SUBSCRIBE_CHUNK):
            chunk = streams[i : i + _SUBSCRIBE_CHUNK]
            frame = {"method": "SUBSCRIBE", "params": chunk, "id": i // _SUBSCRIBE_CHUNK + 1}
            await ws.send(orjson.dumps(frame).decode())
            if i + _SUBSCRIBE_CHUNK < len(streams):
                await asyncio.sleep(_SUBSCRIBE_GAP_S)

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()


def _usdt_conversion(
    quotes: set[str], last_price: Callable[[str], float]
) -> dict[str, float]:
    """Map each quote asset to its USDT last price (USDT itself = 1.0)."""
    conversion: dict[str, float] = {}
    for quote in quotes:
        if quote == "USDT":
            conversion[quote] = 1.0
        else:
            price = last_price(quote)
            if price > 0.0:
                conversion[quote] = price
    return conversion
