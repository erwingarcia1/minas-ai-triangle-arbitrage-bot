"""Bybit spot adapter — native WebSocket + REST, public endpoints only.

Protocol notes (Bybit v5 API):
- REST base ``https://api.bybit.com``; envelope ``{"retCode": 0, "retMsg":
  "OK", "result": {...}, "time": ...}``; ``retCode != 0`` is an error.
- Instruments: ``GET /v5/market/instruments-info?category=spot`` (spot is
  NOT paginated — one response carries all symbols) — ``symbol``/
  ``baseCoin``/``quoteCoin``/``status`` (``Trading``),
  ``priceFilter.tickSize``, and ``lotSizeFilter``: ``basePrecision`` (the
  base-quantity step), ``minOrderAmt`` (quote min notional), plus
  ``minOrderQty`` (deprecated for spot; used here only as an extra guard
  when present).
- Tickers: ``GET /v5/market/tickers?category=spot`` — ``bid1Price``/
  ``ask1Price``/``lastPrice``/``turnover24h`` (quote-denominated volume,
  converted here to USDT via the quote asset's own ``<CCY>USDT`` last price
  from the same batch).
- Public spot WS ``wss://stream.bybit.com/v5/public/spot``; subscribe with
  ``{"op": "subscribe", "args": ["orderbook.50.BTCUSDT", ...]}`` (args are
  chunked conservatively — spot allows at most 10 args per request).
- ``orderbook.<depth>.<symbol>`` is SNAPSHOT-THEN-DELTA: the first message
  per topic has ``type: "snapshot"`` (full book), subsequent ``type:
  "delta"`` messages carry only changed levels (size ``"0"`` deletes a
  level). ``data.u`` is the per-topic update sequence; a delta whose ``u``
  is not exactly the previous ``u + 1`` means missed messages. ``u == 1``
  marks a service-restart snapshot. Because the rest of the system consumes
  only complete snapshots (see docs/DECISIONS.md D-002), this adapter
  assembles the book internally and emits a full ``BookSnapshot`` per
  update; on a sequence gap it emits an EMPTY snapshot (which no depth walk
  can price — fail safe), yields ``StreamStatus(error)``, and resubscribes
  the topic to obtain a fresh snapshot.
- Heartbeat: the CLIENT sends ``{"op": "ping"}`` every ~20s; the venue
  answers with an ack frame. The server drops idle connections, so the
  ping task is not optional.
- Symbol format is concatenated uppercase (``BTCUSDT``) on REST and WS.

Failure modes:
- REST: non-200, ``retCode != 0``, or schema mismatch → ``RestError``.
  Individually malformed entries are dropped and counted, never coerced.
- WS: any disconnect/timeout yields ``StreamStatus(disconnected)``, the
  internal book assembler is RESET (never carried across connections), and
  the adapter reconnects with exponential backoff and resubscribes.
- A failed subscription ack (``success: false``) yields
  ``StreamStatus(error)``; it never kills the stream loop.
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

BYBIT_REST_URL = "https://api.bybit.com"
BYBIT_WS_PUBLIC_SPOT_URL = "wss://stream.bybit.com/v5/public/spot"

_CHANNELS = ("orderbook.50", "orderbook.200")
_DEFAULT_CHANNEL = "orderbook.50"
_SUBSCRIBE_CHUNK = 10  # spot: max 10 args per subscribe request
_SUBSCRIBE_GAP_S = 0.25
_PING_INTERVAL_S = 20.0
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
    if channel not in _CHANNELS:
        raise ValueError(
            f"bybit channel {channel!r} unsupported in this phase "
            "(orderbook.50|orderbook.200 or 'auto')"
        )
    return channel


def parse_symbol(entry: dict[str, Any]) -> SymbolInfo | None:
    """Parse one ``instruments-info`` entry; None when not trading/roundable."""
    if entry.get("status") != "Trading":
        return None
    lot = entry.get("lotSizeFilter") or {}
    price = entry.get("priceFilter") or {}
    tick = _positive_decimal(price.get("tickSize"))
    step = _positive_decimal(lot.get("basePrecision"))
    if tick is None or step is None:
        return None
    return SymbolInfo(
        symbol=entry["symbol"],
        base=entry["baseCoin"].upper(),
        quote=entry["quoteCoin"].upper(),
        price_tick_size=tick,
        quantity_step_size=step,
        min_base_amount=_positive_decimal(lot.get("minOrderQty")) or Decimal(0),
        min_notional=_positive_decimal(lot.get("minOrderAmt")) or Decimal(0),
        status="online",
    )


def parse_ticker(entry: dict[str, Any], usdt_rate: float, ts_ms: int) -> Ticker:
    """Parse one ``/v5/market/tickers`` entry; ``turnover24h`` (quote units)
    is converted to USDT via ``usdt_rate`` (0.0 when unknown — the symbol is
    then conservatively excluded by the discovery volume filter)."""
    return Ticker(
        symbol=entry["symbol"],
        bid=_f(entry.get("bid1Price")),
        ask=_f(entry.get("ask1Price")),
        last=_f(entry.get("lastPrice")),
        usdt_volume_24h=_f(entry.get("turnover24h")) * usdt_rate,
        ts_ms=ts_ms,
    )


class SequenceGap:
    """A delta arrived out of sequence for ``symbol``; the topic must be
    resubscribed and the emitted empty book keeps pricing fail-safe."""

    __slots__ = ("symbol", "expected_u", "got_u")

    def __init__(self, symbol: str, expected_u: int, got_u: int) -> None:
        self.symbol = symbol
        self.expected_u = expected_u
        self.got_u = got_u

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"SequenceGap({self.symbol}, expected={self.expected_u}, got={self.got_u})"


class _BookState:
    __slots__ = ("bids", "asks", "u")

    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.u = 0


class BybitBookAssembler:
    """Maintains per-symbol books from snapshot+delta messages and produces
    complete ``BookSnapshot``s.

    Sequencing rules (Bybit v5 orderbook):
    - ``type: snapshot`` (including the ``u == 1`` service-restart form)
      replaces the whole book, resets the sequence, and clears the symbol's
      unsynced flag.
    - ``type: delta`` must carry ``u == previous_u + 1``; otherwise messages
      were missed and the local book is untrustworthy → ``SequenceGap`` is
      returned ONCE and the symbol is marked unsynced. Further deltas for
      an unsynced symbol return ``None`` (they are the expected tail of the
      same gap while the resubscribe's fresh snapshot is in flight) — this
      prevents an unsubscribe/subscribe storm at the 20ms push cadence.
    - Delta levels with size 0 delete the price level; others upsert.
    - Atomicity: every level is parsed and validated BEFORE any state is
      touched, so a malformed frame raises without half-applying a delta or
      advancing the sequence — the caller logs it and the next valid delta
      still applies to an uncorrupted book.
    """

    def __init__(self, depth_limit: int) -> None:
        self._depth_limit = depth_limit
        self._books: dict[str, _BookState] = {}
        self._unsynced: set[str] = set()

    def reset(self) -> None:
        """Forget everything (reconnect: all topics resend snapshots)."""
        self._books.clear()
        self._unsynced.clear()

    def apply(
        self, msg_type: str, data: dict[str, Any], recv_ns: int, recv_wall_ms: int
    ) -> BookSnapshot | SequenceGap | None:
        symbol = data["s"]
        u = int(data.get("u") or 0)
        # Parse ALL levels before touching state (atomicity — see class doc).
        bid_levels = [(float(p), float(s)) for p, s in data.get("b", ())]
        ask_levels = [(float(p), float(s)) for p, s in data.get("a", ())]
        if msg_type == "snapshot":
            state = _BookState()
            self._books[symbol] = state
            self._unsynced.discard(symbol)
        else:
            existing = self._books.get(symbol)
            if existing is None:
                if symbol in self._unsynced:
                    return None  # gap already reported; awaiting fresh snapshot
                self._unsynced.add(symbol)
                return SequenceGap(symbol, expected_u=0, got_u=u)
            if u != existing.u + 1:
                self._books.pop(symbol, None)
                self._unsynced.add(symbol)
                return SequenceGap(symbol, expected_u=existing.u + 1, got_u=u)
            state = existing
        state.u = u
        _apply_levels(state.bids, bid_levels)
        _apply_levels(state.asks, ask_levels)
        bids = sorted(state.bids.items(), key=lambda lvl: -lvl[0])[: self._depth_limit]
        asks = sorted(state.asks.items(), key=lambda lvl: lvl[0])[: self._depth_limit]
        return BookSnapshot(
            symbol=symbol,
            bids=bids,
            asks=asks,
            ts_exchange_ms=0,  # stamped by the caller from the frame's ts
            recv_ns=recv_ns,
            recv_wall_ms=recv_wall_ms,
        )


def _apply_levels(side: dict[float, float], levels: list[tuple[float, float]]) -> None:
    for price, size in levels:
        if size <= 0.0:
            side.pop(price, None)
        else:
            side[price] = size


def parse_ws_message(
    raw: str | bytes,
    assembler: BybitBookAssembler,
    recv_ns: int,
    recv_wall_ms: int,
) -> BookSnapshot | StreamStatus | SequenceGap | None:
    """Parse one WS frame; feeds orderbook messages through ``assembler``.

    None means "nothing actionable" (ping ack, subscribe ack, other topic).
    Raises orjson/KeyError/ValueError on malformed frames — the caller logs
    and continues; a single bad frame must not kill the stream.
    """
    msg = orjson.loads(raw)
    if not isinstance(msg, dict):
        raise ValueError("frame is not a JSON object")
    if "success" in msg:  # subscribe/ping acks: {"success": true, "op": ...}
        if msg.get("success") is False:
            return StreamStatus(
                StreamStatus.ERROR,
                f"op={msg.get('op')} ret_msg={msg.get('ret_msg')}",
                recv_ns,
            )
        return None
    if "op" in msg and "topic" not in msg:  # pong or other control frame
        return None
    topic = str(msg.get("topic") or "")
    if not topic.startswith("orderbook."):
        return None
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    result = assembler.apply(str(msg.get("type")), data, recv_ns, recv_wall_ms)
    if isinstance(result, BookSnapshot):
        result.ts_exchange_ms = int(msg.get("ts") or 0)
    return result


class BybitAdapter(ExchangeAdapter):
    name = "bybit"

    def __init__(
        self,
        rest_url: str = BYBIT_REST_URL,
        ws_url: str = BYBIT_WS_PUBLIC_SPOT_URL,
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

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
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
        if not isinstance(payload, dict):
            raise RestError(self.name, path, "payload is not an object")
        if payload.get("retCode") != 0:
            raise RestError(
                self.name,
                path,
                f"venue retCode={payload.get('retCode')} retMsg={payload.get('retMsg')!r}",
            )
        if not isinstance(payload.get("result"), dict):
            raise RestError(self.name, path, "result is not an object")
        return payload

    async def fetch_symbols(self) -> list[SymbolInfo]:
        symbols: list[SymbolInfo] = []
        dropped = 0
        cursor = ""
        # Spot does not paginate ("limit, cursor are invalid" per docs) — one
        # call returns everything; the cursor loop is defensive only, and the
        # cursor is venue-controlled, so it is bounded and repeat-checked
        # rather than trusted (a constant echo must not loop forever).
        for _page in range(10):
            params = {"category": "spot"}
            if cursor:
                params["cursor"] = cursor
            payload = await self._get("/v5/market/instruments-info", params)
            result = payload["result"]
            entries = result.get("list")
            if not isinstance(entries, list):
                raise RestError(
                    self.name, "/v5/market/instruments-info", "result.list is not a list"
                )
            for entry in entries:
                try:
                    info = parse_symbol(entry)
                except (KeyError, ValueError, ArithmeticError) as exc:
                    dropped += 1
                    logger.warning(
                        "bybit symbol entry dropped: %r (%s)", entry.get("symbol", "?"), exc
                    )
                    continue
                if info is not None:
                    symbols.append(info)
            next_cursor = str(result.get("nextPageCursor") or "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        else:
            logger.warning(
                "bybit instruments-info produced a cursor past 10 pages; "
                "stopping with %d symbols (venue-controlled cursor not trusted)",
                len(symbols),
            )
        if dropped:
            logger.warning("bybit: dropped %d malformed symbol entries", dropped)
        return symbols

    async def fetch_tickers(self) -> list[Ticker]:
        """24h tickers with USDT-normalized volume.

        Startup-only path: refetches instruments for the symbol→quote
        mapping rather than caching adapter state (Bybit symbols are
        concatenated, so the quote cannot be recovered from the name alone).
        """
        symbols = await self.fetch_symbols()
        payload = await self._get("/v5/market/tickers", {"category": "spot"})
        entries = payload["result"].get("list")
        if not isinstance(entries, list):
            raise RestError(self.name, "/v5/market/tickers", "result.list is not a list")
        ts_ms = int(payload.get("time") or 0)
        by_symbol = {e.get("symbol"): e for e in entries if isinstance(e, dict)}
        quote_to_usdt = {"USDT": 1.0}
        for info in symbols:
            if info.quote not in quote_to_usdt:
                quote_to_usdt[info.quote] = _f(
                    (by_symbol.get(f"{info.quote}USDT") or {}).get("lastPrice")
                )
        tickers: list[Ticker] = []
        for info in symbols:
            entry = by_symbol.get(info.symbol)
            if entry is None:
                continue
            try:
                tickers.append(
                    parse_ticker(entry, quote_to_usdt.get(info.quote, 0.0), ts_ms)
                )
            except (KeyError, ValueError) as exc:
                logger.warning("bybit ticker entry dropped: %r (%s)", info.symbol, exc)
        return tickers

    async def stream_books(
        self, symbols: Sequence[str], channel: str = "auto"
    ) -> AsyncIterator[BookSnapshot | StreamStatus]:
        channel = validate_channel(channel)
        depth_limit = int(channel.split(".", 1)[1])
        topics = [f"{channel}.{symbol}" for symbol in symbols]
        assembler = BybitBookAssembler(depth_limit)
        backoff = _BACKOFF_START_S
        while True:
            ping_task: asyncio.Task[None] | None = None
            assembler.reset()  # never carry books across connections
            try:
                async with connect(
                    self._ws_url, ping_interval=None, open_timeout=10, close_timeout=5
                ) as ws:
                    yield StreamStatus(
                        StreamStatus.CONNECTED, self._ws_url, time.perf_counter_ns()
                    )
                    await self._subscribe(ws, topics)
                    ping_task = asyncio.create_task(self._pinger(ws))
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_S)
                        recv_ns = time.perf_counter_ns()
                        recv_wall_ms = int(time.time() * 1000)
                        try:
                            parsed = parse_ws_message(raw, assembler, recv_ns, recv_wall_ms)
                        except (orjson.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                            logger.error("bybit WS frame unparseable (%s): %.300r", exc, raw)
                            continue
                        if parsed is None:
                            continue
                        if isinstance(parsed, SequenceGap):
                            logger.warning(
                                "bybit orderbook gap on %s (expected u=%d, got u=%d); "
                                "resubscribing",
                                parsed.symbol,
                                parsed.expected_u,
                                parsed.got_u,
                            )
                            yield StreamStatus(
                                StreamStatus.ERROR, f"sequence gap {parsed.symbol}", recv_ns
                            )
                            # Invalidate downstream book: empty books price nothing.
                            yield BookSnapshot(
                                symbol=parsed.symbol,
                                bids=[],
                                asks=[],
                                ts_exchange_ms=0,
                                recv_ns=recv_ns,
                                recv_wall_ms=recv_wall_ms,
                            )
                            await self._resubscribe(ws, f"{channel}.{parsed.symbol}")
                            continue
                        if isinstance(parsed, StreamStatus):
                            logger.error("bybit WS error frame: %s", parsed.detail)
                            yield parsed
                            continue
                        # Book data flowing = healthy connection; only then
                        # is it safe to reset the reconnect backoff.
                        backoff = _BACKOFF_START_S
                        yield parsed
            except asyncio.CancelledError:
                raise
            except (WebSocketException, asyncio.TimeoutError, OSError) as exc:
                yield StreamStatus(
                    StreamStatus.DISCONNECTED, repr(exc), time.perf_counter_ns()
                )
                logger.warning(
                    "bybit WS disconnected (%r); reconnecting in %.1fs", exc, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, _BACKOFF_CAP_S)
            finally:
                if ping_task is not None:
                    ping_task.cancel()

    @staticmethod
    async def _subscribe(ws: ClientConnection, topics: Sequence[str]) -> None:
        for i in range(0, len(topics), _SUBSCRIBE_CHUNK):
            chunk = list(topics[i : i + _SUBSCRIBE_CHUNK])
            await ws.send(orjson.dumps({"op": "subscribe", "args": chunk}).decode())
            if i + _SUBSCRIBE_CHUNK < len(topics):
                await asyncio.sleep(_SUBSCRIBE_GAP_S)

    @staticmethod
    async def _resubscribe(ws: ClientConnection, topic: str) -> None:
        """Force a fresh snapshot for one topic after a sequence gap."""
        await ws.send(orjson.dumps({"op": "unsubscribe", "args": [topic]}).decode())
        await ws.send(orjson.dumps({"op": "subscribe", "args": [topic]}).decode())

    @staticmethod
    async def _pinger(ws: ClientConnection) -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL_S)
                await ws.send(orjson.dumps({"op": "ping"}).decode())
        except asyncio.CancelledError:
            pass
        except WebSocketException:
            pass  # recv loop sees the same closure and handles reconnect

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()
