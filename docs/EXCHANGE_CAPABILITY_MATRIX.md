# EXCHANGE_CAPABILITY_MATRIX.md

Required by `CLAUDE.md` §8. Every row was researched against **current
official documentation** by a dedicated documentation agent and then
independently re-checked by an adversarial verifier trying to refute the
claims (method recorded in `docs/DECISIONS.md` D-005).

- **Verified:** 2026-07-11.
- **Confidence:** Binance, Bybit — high (verified verbatim against the
  official docs source repositories). Bitget, OKX — medium (official doc
  sites block datacenter fetchers; claims were confirmed via exact-phrase
  extraction of official pages cross-checked against the venues' own SDKs;
  no contradictions found).
- **Scope of use today:** this build consumes ONLY the public market-data
  rows (instruments, tickers, depth channels, heartbeat). All trading-side
  rows are recorded for later phases; **no trading code exists in this
  build**. Automated capability tests against live/demo endpoints land with
  the phase that first uses each capability (§8); until then the offline
  fixture tests in `tests/test_*_parse.py` pin the documented payload
  shapes.

Legend: ✅ yes · ⚠️ partial/caveat · ❌ no.

## 1. Summary matrix (§8)

| Capability            | Bitget | Bybit | OKX | Binance |
| --------------------- | :----: | :---: | :-: | :-----: |
| Spot market orders    | ✅ | ✅ | ✅ | ✅ |
| Spot limit orders     | ✅ | ✅ | ✅ | ✅ |
| IOC                   | ✅ `force=ioc` | ✅ `timeInForce=IOC` | ✅ `ordType=ioc` | ✅ `timeInForce=IOC` |
| FOK                   | ✅ `force=fok` | ✅ `timeInForce=FOK` | ✅ `ordType=fok` | ✅ `timeInForce=FOK` |
| Client order ID       | ✅ `clientOid` (≤50 or ≤64 chars — docs inconsistent, treat ≤50 `[0-9A-Za-z-+_#]`) | ✅ `orderLinkId` (≤36, alnum + `-` `_`) | ✅ `clOrdId` (≤32, case-sensitive alnum) | ✅ `newClientOrderId` |
| Private order stream  | ✅ `orders` | ✅ `order`/`order.spot` | ✅ `orders` (no initial snapshot) | ✅ `executionReport` |
| Private fill stream   | ✅ `fill` | ✅ `execution`/`execution.spot` | ⚠️ `fills` is VIP5+-gated; non-VIP consume fill fields on `orders` pushes | ✅ `executionReport` (`x=TRADE`) |
| Private balance stream| ✅ `account` | ✅ `wallet` (no initial snapshot — bootstrap via REST) | ✅ `account` | ✅ `outboundAccountPosition`/`balanceUpdate` |
| Demo/test environment | ⚠️ demo keys + `paptrading: 1` header on prod host; WS `wspap.bitget.com` | ✅ testnet (`api-testnet.bybit.com`, `stream-testnet…`) AND mainnet demo | ✅ `x-simulated-trading: 1` header on prod host; WS `wspap.okx.com…?brokerId=9999` | ✅ Spot Testnet (`testnet.binance.vision`) |
| Account fee endpoint  | ⚠️ `GET /api/v2/common/trade-rate` (exact spot `business` value unverified) | ✅ `GET /v5/account/fee-rate?category=spot` | ✅ `GET /api/v5/account/trade-fee?instType=SPOT` (negative = you pay) | ✅ `GET /api/v3/account/commission` (one symbol per call) |
| WebSocket order entry | ✅ `place-order` op on private WS | ✅ `wss://…/v5/trade` | ✅ `op: order` on private WS | ✅ WS API `order.place` |
| Batch order support   | ✅ `POST /api/v2/spot/trade/batch-orders` | ✅ ≤10 orders/request (spot) | ✅ ≤20 orders/request, partial success possible | ❌ no independent batch (only OCO/OTO order lists) |
| Exchange-token fee discount | ✅ BGB (20% spot discount; visible in `feeDetail.deduction`) | ❌ none exists in v5 API | ⚠️ OKB holdings lower the fee *tier* (no pay-in-token toggle) | ✅ BNB (`discount` object on commission endpoint) |

## 2. Market-data facts this build is built on (verified, load-bearing)

### Depth-channel semantics — the claim that corrupts books if wrong

| Venue | Channel used | Semantics (verified) | Sequence fields | On gap |
|---|---|---|---|---|
| Bitget | `books15` (also `books1`/`books5`) | **Snapshot every push** — "The books1, books5, and books15 channels each push snapshot data." ~200ms, event-triggered | `seq`/`pseq` (not needed for snapshots) | n/a — replace book each push |
| Binance | `<sym>@depth20@100ms` (5/10/20) | **Snapshot every push** — "Top \<levels\> bids and asks" replaced per push | `lastUpdateId` | n/a |
| OKX | `books5` | **Snapshot every push** — "bbo-tbt and books5 are depth snapshots that are published every 10ms and 100ms" | `seqId` only (no `prevSeqId`, no checksum) | n/a |
| Bybit | `orderbook.50` (or `.200`) | **Snapshot then delta** — first message `type: snapshot`, then `type: delta`; size 0 deletes a level; `u == 1` marks a service-restart snapshot; any `type: snapshot` resets the book | `u` (per-topic, consecutive), `seq` | delta `u ≠ prev_u + 1` → resubscribe topic (implemented: `BybitBookAssembler` emits an empty book and resubscribes) |

Deliberately NOT used: Bitget `books` (snapshot+delta, checksum field
**removed** 2025-08-07, use `seq`/`pseq`); Binance `@depth` diff stream
(delta-only, REST-seeded); OKX `books` 400-level (snapshot+delta; the
`checksum` field is **deprecated since 2026-05-21** — fixed to 0, use
`seqId`/`prevSeqId`) and `books50-l2-tbt`/`books-l2-tbt` (login + VIP4/VIP5
gated); Bybit `orderbook.full` (delta-only; REST seed is
`GET /v5/market/full_orderbook` — note the docs page slug `full-ob` is NOT
the API path).

### Heartbeats (verified)

| Venue | Who pings | Frame | Interval | Idle disconnect |
|---|---|---|---|---|
| Bitget | client | literal text `ping` → `pong` | ~25–30s | 2 min without ping; forced drop every 24h |
| Binance | **server** (protocol-level ping) | client must pong echoing the payload (the `websockets` library does this automatically) | server pings every 20s | forced drop at 24h |
| OKX | client | literal text `ping` → `pong` | timer < 30s | ~30s without data/subscription |
| Bybit | client | JSON `{"op": "ping"}` | ~20s | drops idle connections |

### REST endpoints and field names (verified)

| Venue | Instruments | Tickers (all symbols, one call) | Notes |
|---|---|---|---|
| Bitget | `GET /api/v2/spot/public/symbols` — `pricePrecision`/`quantityPrecision` are DECIMAL PLACES (tick = 10^-p); `minTradeAmount` (base), `minTradeUSDT` (USDT notional); status `online`; `maxTradeAmount` is documented as meaningless — disregard | `GET /api/v2/spot/market/tickers` — `bidPr`/`askPr`/`lastPr`/`usdtVolume` (already USDT-denominated) | envelope `code == "00000"`; all numbers are strings |
| Binance | `GET /api/v3/exchangeInfo` — increments come from `PRICE_FILTER.tickSize` + `LOT_SIZE.stepSize`/`minQty` (filter order not guaranteed); min notional via `NOTIONAL` or legacy `MIN_NOTIONAL`; require `status == TRADING` **and** `isSpotTradingAllowed == true` (margin-only symbols also say TRADING) | `GET /api/v3/ticker/24hr` (weight 80) — `bidPrice`/`askPrice`/`lastPrice`/`quoteVolume` (QUOTE-denominated → convert to USDT) | HTTP status carries errors; stream names lowercase vs payload symbols uppercase |
| OKX | `GET /api/v5/public/instruments?instType=SPOT` — `tickSz`/`lotSz` are STEP STRINGS (not places, not guaranteed powers of ten); `minSz` (base); **no min-notional field exists**; state enum `live/suspend/preopen/test` | `GET /api/v5/market/tickers?instType=SPOT` — `bidPx`/`askPx`/`last`; `volCcy24h` is QUOTE-currency volume for spot → convert to USDT | envelope `code == "0"` (string), errors can arrive with HTTP 200 |
| Bybit | `GET /v5/market/instruments-info?category=spot` — spot is NOT paginated ("limit, cursor are invalid"); `priceFilter.tickSize`, `lotSizeFilter.basePrecision` (base step), `minOrderAmt` (quote notional); `minOrderQty`/`maxOrderQty`/`maxOrderAmt` are deprecated for spot | `GET /v5/market/tickers?category=spot` — `bid1Price`/`ask1Price`/`lastPrice`/`turnover24h` (QUOTE-denominated → convert to USDT) | envelope `retCode == 0`; HTTP can be 200 with a non-zero retCode |

### WebSocket subscription limits (verified)

| Venue | Key limits honored by the adapter |
|---|---|
| Bitget | ≤10 inbound msgs/s per connection; 240 subscribe requests/hour; one subscribe frame ≤ 4096 bytes; ≤100 connections/IP → adapter chunks 20 args/frame at 0.15s gaps |
| Binance | ≤5 inbound msgs/s **including ping/pong**; ≤1024 streams/connection; 24h forced disconnect → adapter batches 50 streams per SUBSCRIBE at 0.5s gaps, client pings disabled |
| OKX | 3 new connections/s per IP; subscribe/unsubscribe/login ≤480/hour per connection → adapter batches 20 args/frame; reconnect storms would exhaust the budget, backoff caps at 30s |
| Bybit | ≤10 args per subscribe request (spot); ≤21,000 chars of args per connection; ≤500 topics → adapter chunks 10 topics/frame at 0.25s gaps |

## 3. Trading-side pitfalls recorded for later phases (not used today)

- **Market-BUY sizing is quote-denominated by default on Bitget, Bybit, and
  OKX spot** (OKX: set `tgtCcy` explicitly; Bybit: `marketUnit`;
  Binance: `quoteOrderQty` is the explicit quote-sizing parameter). Getting
  this wrong mis-sizes a leg by a price factor — the execution engine must
  pin the sizing unit per venue.
- OKX account fee rates are **negative when charged** (`maker=-0.0008` =
  you pay 8 bps) — flip the sign in cost models.
- Bybit private `order` stream can emit documented duplicate
  `orderStatus=Filled` pairs; dedupe on order id + status.
- OKX `orders` and Bybit `wallet` private channels send **no initial
  snapshot** — bootstrap state via REST, then apply events.
- Binance client order IDs: charset violations return error `-1100
  ILLEGAL_CHARS` (not `-1130`, which is `INVALID_PARAMETER`).
- Bitget `clientOid` constraints are inconsistent across official pages
  (≤50 vs ≤64) — use the stricter form: ≤50 chars, `[0-9A-Za-z-+_#]`.
- Demo environments: OKX demo WS requires `?brokerId=9999`; Bitget demo
  REST uses the production host plus `paptrading: 1`; OKX demo REST uses
  the production host plus `x-simulated-trading: 1` (forgetting the header
  targets the live account).

## 4. Maintenance

Any observed behavior contradicting this matrix is a **P1 incident**
(D-005): fix the matrix and the adapter together and add a regression
fixture to the venue's parse tests. Re-verify all rows before any phase
that first uses a trading-side capability, and always before live
activation (§9: "trading rules must be refreshed … before live
activation").
