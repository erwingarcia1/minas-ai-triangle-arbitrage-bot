# EXCHANGE_ADAPTER_CONTRACT.md — venue adapter interface

> Status labels: **Implemented** = true of the code today (file cited).
> **Planned** = required by the master prompt (`CLAUDE.md` Part B, section
> cited) for a later phase. This build is MARKET_DATA_ONLY: the adapter
> surface contains **no order, balance, withdrawal, or transfer methods**;
> withdrawal/transfer code is banned from this repository permanently
> (`CLAUDE.md` Part A).

## 1. Today's contract: `ExchangeAdapter` (Implemented)

Defined in `triarb/exchange/base.py`. One class per venue; adapters are
constructed only via `create_adapter(venue)` in
`triarb/exchange/registry.py` — adding a venue touches one adapter module
plus one registry line.

| Member | Kind | Contract |
|---|---|---|
| `name` | `ClassVar[str]` | Stable venue identifier used in errors and logs (`"bitget"`, …). |
| `fetch_symbols()` | `async → list[SymbolInfo]` | All **online** spot symbols with precision/min-size metadata, fully normalized. |
| `fetch_tickers()` | `async → list[Ticker]` | 24h tickers; used only for startup liquidity/spread filtering. |
| `stream_books(symbols, channel)` | async generator → `BookSnapshot \| StreamStatus` | Yields book snapshots and stream-control events **forever**; owns connect/reconnect/resubscribe internally. |
| `aclose()` | `async → None` | Release network resources. **Idempotent.** |

### Failure semantics per method (Implemented)

`fetch_symbols` / `fetch_tickers` (reference implementation
`triarb/exchange/bitget.py`, exceptions in `triarb/exchange/errors.py`):

- Transport failure, non-200 status, venue-level error code, or a
  response-shape mismatch raises `RestError(venue, endpoint, detail)` —
  never a bare `Exception`, never a silently empty list.
- An *individual* entry that fails validation is dropped, counted, and
  logged (`"dropped %d malformed symbol entries"`); it is never coerced into
  a guessed value. Pydantic validates every REST payload at the boundary
  (`SymbolInfo`, `Ticker` are pydantic models), so nothing malformed reaches
  the graph or pricing layers.
- Callers must not assume the result is cached: symbol metadata drifts
  (precision changes, delistings), so the observer re-fetches on a timer
  (`triarb/observer.py::_metadata_loop`); adapters must not cache it
  internally forever.

`stream_books`:

- The generator never returns under normal operation. On any
  disconnect/timeout it yields
  `StreamStatus(DISCONNECTED, detail)`, sleeps with exponential backoff,
  reconnects, **resubscribes**, and yields `StreamStatus(CONNECTED, url)` —
  consumers must treat every gap between DISCONNECTED and CONNECTED as
  "all books stale" (the observer does:
  `triarb/observer.py::_on_stream_status` → `BookStore.mark_all_stale`).
- A venue error frame yields `StreamStatus(ERROR, detail)` and the stream
  continues; it never kills the loop.
- A single unparseable frame is logged with the raw payload and skipped —
  one bad frame must not kill the stream
  (`triarb/exchange/bitget.py::stream_books`).
- `asyncio.CancelledError` always propagates (clean shutdown).
- An unsupported `channel` raises `ValueError` immediately, before any
  connection (Phase 1 accepts snapshot channels only).

Error taxonomy (`triarb/exchange/errors.py`): `AdapterError` (base) →
`RestError`, `WsProtocolError`, `VenueNotImplementedError`. Every adapter
failure carries venue context.

## 2. Normalized data models (Implemented)

All in `triarb/exchange/base.py`. Boundary rule: pydantic for REST payloads
(validated, frozen); `__slots__` classes with plain floats for the WS hot
path (no pydantic, minimal allocation).

### `SymbolInfo` (pydantic, frozen)

| Field | Normalization rule |
|---|---|
| `symbol` | Venue-native symbol string, used as the key everywhere in-process. |
| `base`, `quote` | Uppercased asset codes (`parse_symbol` in each venue adapter). |
| `price_tick_size`, `quantity_step_size` | **Exact venue increments as Decimals** (> 0), NOT assumed to be powers of ten (§9). Step-publishing venues (Binance/Bybit/OKX) parse the strings directly; precision-publishing venues (Bitget) convert via `Decimal(1).scaleb(-precision)`. |
| `min_base_amount` | `Decimal`, minimum order quantity in base units; `0` = unknown/none. |
| `min_notional` | `Decimal`; on Bitget this is `minTradeUSDT` (USDT-denominated). Other venues may denominate differently — **the adapter normalizes; the consumer must not guess**. |
| `status` | Only `"online"` symbols are emitted by `fetch_symbols` today; non-online entries are filtered in the adapter. |

### `Ticker` (pydantic, frozen)

`bid`/`ask`/`last`/`usdt_volume_24h` as floats (missing/empty venue fields
become `0.0`, never `None`), `ts_ms` venue timestamp. `spread_bps` property
returns `+inf` when either side is missing or crossed — filters then reject
the symbol rather than treating bad data as a tight spread.

### `BookSnapshot` (`__slots__`, floats — hot path)

- `bids` **descending** by price, `asks` **ascending** — exactly as the
  venue publishes; levels are never averaged, smoothed, or reordered.
- `ts_exchange_ms` (venue clock) is carried but **staleness is always
  computed from `recv_ns`**, a local `time.perf_counter_ns()` stamp taken at
  frame receipt, so venue clock skew cannot mask a stalled feed
  (`triarb/pricing/orderbook.py`). `recv_wall_ms` is for log correlation only.
- Phase 1 snapshots replace the whole book verbatim (`BookStore.apply`).

### `StreamStatus` (`__slots__`)

`kind` ∈ `CONNECTED` / `DISCONNECTED` / `ERROR`, plus free-text `detail` and
`recv_ns`. This is the **only** channel by which stream health reaches
consumers — staleness and (in later phases) kill-switch logic must never
depend on message flow alone, because snapshot channels are silent for quiet
symbols.

## 3. Adapter authoring rules (Implemented conventions — binding for new adapters)

Derived from `triarb/exchange/base.py` docstrings and the reference
implementation `triarb/exchange/bitget.py`; new adapters (Binance, Bybit,
OKX) must follow all of them:

1. **`StreamStatus` around every gap.** Yield `DISCONNECTED` before any
   reconnect sleep and `CONNECTED` after the socket is open; never
   reconnect silently.
2. **Reconnect + resubscribe internally.** Exponential backoff (Bitget:
   1 s → 30 s cap, reset on successful connect); the consumer never manages
   connections.
3. **No silent coercion of malformed payloads.** REST: raise `RestError` for
   structural failures, drop-and-count individual bad entries. WS: log the
   raw frame and continue. Never substitute guessed values.
4. **Chunked subscribes.** Respect venue message rate limits (Bitget: 20
   args per subscribe message, 0.15 s gap between chunks); a rejected
   channel surfaces as `StreamStatus(ERROR)`, not an exception.
5. **Application-level heartbeats where the venue requires them** (Bitget:
   literal `ping` text frame every ~25 s; a receive timeout of 45 s treats a
   silent socket as dead).
6. **Snapshot-only book feeds in Phase 1** (decision D-002,
   `docs/DECISIONS.md`): every yielded `BookSnapshot` is a complete book.
   A venue without a snapshot channel (Bybit) must merge deltas *inside the
   adapter* with sequence-gap detection and resubscribe-on-gap, emitting
   only complete snapshots downstream.
7. **Local receive timestamps** (`perf_counter_ns`) on every snapshot;
   never trust the venue clock for freshness.
8. **`CancelledError` propagates; `aclose()` is idempotent** and closes only
   resources the adapter owns.
9. **Public endpoints only in this phase.** No API keys are read anywhere;
   an adapter that needs credentials does not belong in this build.

Venue status (**Implemented**): all four venues ship market-data adapters
behind this contract (`triarb/exchange/bitget.py`, `binance.py`,
`bybit.py`, `okx.py`, wired in `registry.py`), following the D-002
per-venue channel strategy (Binance `<symbol>@depth20@100ms`, OKX
`books5`, Bybit `orderbook.50` merged internally by `BybitBookAssembler`).
Binance/Bybit/OKX are offline-fixture-verified with a live soak pending
(`PROGRESS.md`) — see `docs/EXCHANGE_CAPABILITY_MATRIX.md` for the
verified capability claims each adapter is built on.

## 4. The §8 target contract (Planned surface)

`CLAUDE.md` §8 requires one common spot-exchange interface. The table maps
every §8 method to today's code or the phase where it lands. Phases follow
the README plan (Phase 2 paper execution, Phase 3 live minimum size,
Phase 4 hardening); §8 names are camelCase in the master prompt and will be
snake_case in Python. **No order, balance, or private-stream method exists
today**; per §25, Phase 2 implements the trading surface as a *simulated*
execution adapter first — real venue order entry is Phase 3, gated by §3
qualification.

| §8 method | Status | Mapping / landing phase |
|---|---|---|
| `connectPublicWebSocket()` | **Implemented** (folded in) | Connection management lives inside `stream_books()` — no separate connect call; `StreamStatus(CONNECTED)` is the observable event. |
| `connectPrivateWebSocket()` | **Planned** | First phase touching real accounts (Phase 3; shadow scheduling is TBD-ADPT-1). §29 key rules apply before any credential is read. |
| `disconnect()` | **Implemented** (split) | `aclose()` releases REST resources; the WS closes when the `stream_books` generator is cancelled/garbage-collected. A unified teardown may be added when private connections exist. |
| `healthCheck()` | **Planned** (Phase 2+) | Today health is inferred from `StreamStatus` + book staleness; an active REST health probe lands with the §15 gate "Exchange API healthy". |
| `fetchServerTime()` / `synchronizeClock()` | **Planned** (Phase 3) | Needed for signed requests and §13 time-sync limits. Phase 1 deliberately never compares venue clocks to local clocks for correctness. |
| `fetchSpotInstruments()` | **Implemented** | `fetch_symbols()` (`triarb/exchange/base.py`). |
| `fetchTradingRules()` | **Partial** | Precision, min qty, min notional ship inside `SymbolInfo` today. The rest of the §9 field set (`maximum_quantity`, `maximum_notional`, `market_order_minimum/maximum`, `supported_order_types`, per-symbol `maker_fee`/`taker_fee`) is **Planned** (Phase 2, §9). |
| `fetchAccountFeeRates()` | **Planned** (Phase 3) | Fees are *configured*, not fetched (`FeesConfig`, `triarb/config/models.py`) — a §12-conservative stance: configurable rate, results marked as estimates via the config value. |
| `fetchBalances()` | **Planned** (Phase 3 / shadow, TBD-ADPT-1) | No balance code exists in this build. |
| `fetchOpenOrders()` / `fetchOrder()` / `fetchTradeHistory()` | **Planned** (Phase 3) | Required by §19 (query-before-resubmit) and §21 reconciliation. |
| `subscribeOrderBooks()` | **Implemented** | `stream_books(symbols, channel)`. |
| `subscribeTickers()` | **Partial** | `fetch_tickers()` is a one-shot REST snapshot at startup, not a subscription (TBD-ADPT-2). |
| `subscribeOrders()` / `subscribeExecutions()` / `subscribeBalances()` | **Planned** | Private Order and Fill Streams stage (§7, §8); same phase as `connectPrivateWebSocket()`. |
| `placeMarketOrder()` | **Planned** (Phase 2 simulated / Phase 3 real) | §17: unrestricted market orders are *not* the default — marketable IOC limit orders with price protection are. |
| `placeLimitOrder()` / `placeIocOrder()` | **Planned** (Phase 2 simulated / Phase 3 real) | The Decimal feasibility pass already simulates the exact IOC limit orders this method will place (`triarb/pricing/feasibility.py`). |
| `placeFokOrder()` | **Planned** (Phase 3, per-venue) | §17: only where officially supported and verified per symbol — never assumed from another exchange (§8, D-005). |
| `cancelOrder()` / `cancelAllOrders()` | **Planned** (Phase 2 simulated / Phase 3 real) | Required by §20 partial-fill handling and §24 kill-switch step 4. |
| `normalizeSymbol()` | **Implemented** (folded in) | `parse_symbol` → `SymbolInfo` inside `fetch_symbols()`; a standalone canonical cross-venue symbol scheme is TBD-ADPT-3. |
| `normalizeAsset()` | **Partial** | Uppercasing of base/quote today; a full venue-alias table (if any venue needs one) is **Planned** with the next adapter. |
| `normalizeOrder()` / `normalizeFill()` / `normalizeBalance()` | **Planned** | Land with the order/balance surface; §12 requires fills to carry `estimated_fee`, `actual_fee`, `actual_fee_asset`. |
| `normalizeError()` | **Partial** | Typed hierarchy with venue context exists (`triarb/exchange/errors.py`); the per-venue error-code matrix is **Planned** (Phase 4, README phase plan). |
| `reconcileOrder()` / `reconcileBalances()` / `reconcileCompleteAccountState()` | **Planned** (Phase 3) | Reconciliation and Recovery stage (§7, §21). |

Contract-test requirement (**Partial**, §32): every adapter must pass one
shared contract suite before its venue is enabled.
`tests/test_adapter_contract.py` runs the market-data-scope contract over
all four adapters today (construction without credentials, name/venue
match, channel validation before any connection, idempotent close), and
each venue has a parse suite from documented payload shapes
(`tests/test_*_parse.py`). The trading-scope contract suite lands with the
§8 trading surface.

## 5. TBDs (per CLAUDE.md §6)

| ID | Open question | Safest reversible assumption recorded |
|---|---|---|
| TBD-ADPT-1 | Which phase carries the private-stream and balance surface for `SHADOW_TRADING` (README phases don't schedule shadow; §3 requires it before live). | Private-stream methods are specified with Phase 3 but shadow qualification must complete before any `LIVE_TRADING` activation; the mode gate (`triarb/config/models.py`) refuses both modes today, so no code depends on the answer. |
| TBD-ADPT-2 | Whether `subscribeTickers()` becomes a WS stream or a periodic REST poll. | Startup REST snapshot only, used solely for discovery filtering — tickers are never used for pricing (§11 forbids ticker-price profitability), so staleness there cannot cause a money error. |
| TBD-ADPT-3 | Canonical cross-venue symbol format (§9 `canonical_symbol`). | Venue-native symbols remain the in-process keys while D-004 (one venue per process) holds; a canonical scheme is decided only when a cross-venue view exists, and recorded in `docs/DECISIONS.md`. |
| TBD-ADPT-4 | Fee denomination differences for `min_notional` across venues (Bitget is USDT-denominated; others may use quote currency). | Adapters must normalize into `SymbolInfo.min_notional` and document the denomination; the feasibility check already applies a conservative haircut and treats "unknown" (`0`) as unenforceable rather than passing bad data through (`triarb/pricing/feasibility.py`). |

## 6. Related documents

- `docs/ARCHITECTURE.md` — where the adapter sits in the §7 flow.
- `docs/EXCHANGE_CAPABILITY_MATRIX.md` — per-venue verified capability claims (D-005: official-doc citation + adversarial verification; never assumed from another exchange).
- `docs/DECISIONS.md` — D-002 (snapshot channels per venue), D-004 (venue per process), D-005 (capability verification).
