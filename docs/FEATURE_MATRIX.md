# Feature traceability matrix

Required by master prompt §40.7; read this third, after `CLAUDE.md` and
`PROGRESS.md`. One table per subsystem. This file is regenerable: every
**Implemented** row cites code or a test that exists in this repository
today; everything else is labeled honestly.

**Repository-wide fact:** this build is a MARKET_DATA_ONLY observer. It
contains **no order, balance, withdrawal, or transfer code of any kind**
(`triarb/exchange/base.py` defines only `fetch_symbols`, `fetch_tickers`,
`stream_books`, `aclose`). Withdrawal/transfer code is banned permanently
(CLAUDE.md Part A). Nothing here is profitable or production-ready; the
README's economics (fee hurdle, latency, adverse selection) stand.

## Legend

| Status | Meaning |
|---|---|
| Implemented | True of the code today; Evidence cites the file/test |
| In progress | Under construction in the current iteration (see `PROGRESS.md`) |
| Planned | Required by the cited CLAUDE.md § for a later phase; no code exists |
| TBD | Requirement genuinely undecided; safest reversible assumption recorded below |

Phases (README phase plan): **1** Observer · **2** Paper execution ·
**3** Live, minimum size · **4** Hardening. SHADOW_TRADING qualification
sits between 2 and 3 (see TBD T-4).

## Market data

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-MD-BITGET | §13, §40 | Bitget public REST (symbols/tickers) + WS `books15` snapshots, client `ping` heartbeat, reconnect w/ backoff | Implemented | `triarb/exchange/bitget.py`; `tests/test_bitget_parse.py` | 1 |
| F-MD-BOOKSTORE | §13 | Local per-symbol books; every snapshot replaces the book verbatim (never averaged/merged) | Implemented | `triarb/pricing/orderbook.py`; `tests/test_orderbook.py` | 1 |
| F-MD-STALENESS | §13 | Staleness from local monotonic receive time; disconnect marks ALL books stale; stale books unusable for pricing | Implemented | `Book.is_stale`, `BookStore.mark_all_stale` (`triarb/pricing/orderbook.py`); `tests/test_orderbook.py::TestBookStore::test_mark_all_stale_on_disconnect` | 1 |
| F-MD-STATUS | §13 | `StreamStatus` connect/disconnect/error events emitted around every gap | Implemented | `triarb/exchange/base.py`; `tests/test_observer.py::TestLifecycle::test_disconnect_ends_opportunities_and_stales_books` | 1 |
| F-MD-HEARTBEAT | §13 | Per-venue heartbeat: Bitget/OKX literal `ping` text, Bybit `{"op":"ping"}`, Binance server-side protocol pings | Implemented (Bitget) / In progress (others, this iteration) | `_pinger` in `triarb/exchange/bitget.py`, `bybit.py`, `okx.py`; `triarb/exchange/binance.py` module docstring | 1 |
| F-MD-SEQ-BYBIT | §13 | Bybit `orderbook.50` snapshot+delta assembly with strict `u`-sequence check; gap → empty book (fail-safe) + `StreamStatus(error)` + per-topic resubscribe; assembler reset on reconnect | In progress (this iteration) | `triarb/exchange/bybit.py`; `tests/test_bybit_parse.py::TestBookAssembly` | 1 |
| F-MD-CHANNEL-GATE | §13 | Channel validation per venue; unsupported (incremental) channels rejected at startup | Implemented (Bitget) / In progress (others) | `validate_channel`/`stream_books` in each adapter; `tests/test_*_parse.py::TestChannelValidation` | 1 |
| F-MD-SKEW | §13, §15 | Cross-book timestamp skew measurement; reject triangles whose books are not temporally consistent (`MAX_BOOK_SKEW_MS`) | Planned | §13 (per-book `max_age_ms` is logged today: `triarb/pricing/edge.py`) | 2 |
| F-MD-INCREMENTAL | §13 | Full incremental books with sequence numbers + checksums | Planned | §13; `docs/DECISIONS.md` D-002 (snapshot-only while observing) | 4 |

## Graph / Discovery

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-GRAPH-ADJ | §10 | Directed currency graph: currencies as nodes, each spot market inserted in both directions (buy edge consumes asks, sell edge consumes bids — side derived at compile time) | Implemented | `triarb/graph/builder.py`; `triarb/graph/triangles.py::compile_triangle`; `tests/test_triangles.py::TestEnumeration::test_compile_sides` | 1 |
| F-GRAPH-ENUM | §10 | Enumerate every directed cycle Q→A→B→Q; both directions kept as distinct opportunities | Implemented | `triarb/graph/triangles.py::enumerate_cycles`; `tests/test_triangles.py::TestEnumeration::test_finds_both_directions` | 1 |
| F-GRAPH-QUOTE-CFG | §10 | Settlement currency configurable (`quote_currency`, default USDT); not hard-coded to USDT | Implemented | `triarb/config/models.py::Settings`; `tests/test_config.py::TestValidation::test_quote_currency_uppercased` | 1 |
| F-GRAPH-FILTER | §10 | Liquidity filtering: per-leg 24h USDT volume + quoted-spread bounds, ranked by weakest-leg volume, capped by `max_triangles` | Implemented | `triarb/graph/triangles.py::build_triangles`; `tests/test_triangles.py::TestDiscovery` | 1 |
| F-GRAPH-WHITELIST | §10 | Whitelist mode: pinned cycles bypass liquidity filters but never market existence; wrong quote start fails loudly | Implemented | `tests/test_triangles.py::TestWhitelist` | 1 |
| F-GRAPH-REFRESH | §9 | Symbol metadata refreshed on a timer; degraded subscribed symbols logged (`symbols_degraded`) | Implemented | `triarb/observer.py::_metadata_loop` | 1 |
| F-GRAPH-REBUILD | §9 | Mid-run universe rebuild on instrument-status change (today: operator restarts to pick up a changed universe) | Planned | §9 (refresh triggers list); note in `triarb/observer.py::_metadata_loop` docstring | 2 |

## Pricing

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-PRICE-DEPTH | §11 | Depth walk of actual executable book levels; never top-of-book multiplication; too-shallow books return `None`, never extrapolated | Implemented | `triarb/pricing/depth.py`; `tests/test_depth.py`; `tests/test_edge.py::TestEvaluateCycle::test_depth_walk_worse_than_top_of_book` | 1 |
| F-PRICE-EDGE | §11 | Float hot-path screen: three-leg depth walk at configured notional, fee compounding per leg, gross/net bps, staleness rejection, machine-readable reject reasons | Implemented | `triarb/pricing/edge.py::evaluate_cycle`; `tests/test_edge.py` | 1 |
| F-PRICE-FEES | §12 | Compounded fee hurdle `1/(1−f)³−1`, per-leg fee on the received amount, configurable taker/maker bps + fee-token discount multiplier | Implemented | `triarb/pricing/fees.py`; `triarb/config/models.py::FeesConfig`; `tests/test_fees.py` (incl. README tier table) | 1 |
| F-PRICE-FEASIBILITY | §9, §11 | Exact-Decimal cold path: simulates the three IOC limit orders with step-based tick/lot rounding (non-power-of-ten steps correct), min qty, dust stranding (non-quote dust valued at zero), unspent quote recovered | Implemented | `triarb/pricing/feasibility.py`; `tests/test_feasibility.py` (incl. `TestStepRounding`, `TestQuoteDustRecovery`) | 1 |
| F-PRICE-MINNOTIONAL | §9 | Per-leg min-notional check. Today: starting notional with 5% haircut as USDT proxy, skipped for non-USDT quotes | TBD (T-1) | `triarb/pricing/feasibility.py` (`_MIN_NOTIONAL_HAIRCUT`); `tests/test_feasibility.py::TestExchangeMinimums` | 1 (proxy) / 2 (exact) |
| F-PRICE-FEE-FETCH | §12 | Account fee rates fetched from venue (`fetchAccountFeeRates`); fees are currently configured, conservative, and marked as configured — not fetched | Planned | §12 (config fallback rule); `PROGRESS.md` iteration 1 limitations | 2 |
| F-PRICE-SIZER | §14 | Depth-aware trade sizing optimizer (`maximum_executable_size`, `maximum_safe_size`, `most_profitable_size`); today a single configured `notional` is priced | Planned | §14 | 2 |
| F-PRICE-RESERVES | §11, §15 | `latency_reserve`, `failure_reserve`, `LATENCY_RESERVE_BPS`, `FAILURE_RESERVE_BPS` in the profit decision | Planned | §11, §15 | 2 |

## Modes & Gating

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-MODE-ENUM | §3 | All four modes parse: MARKET_DATA_ONLY, PAPER_TRADING, SHADOW_TRADING, LIVE_TRADING | Implemented | `triarb/config/models.py::Mode`; `tests/test_config.py::TestModeGate` | 1 |
| F-MODE-GATE | §3 | Startup gate `ensure_supported_mode`: only MARKET_DATA_ONLY starts; LIVE_TRADING refused with the §3 authorization-chain message; enforced in CLI before any network connection | Implemented | `triarb/config/models.py::ensure_supported_mode`; `triarb/cli.py::_observe`; `tests/test_config.py::TestModeGate::test_live_trading_rejected_with_authorization_message` | 1 |
| F-MODE-MDO | §3 | MARKET_DATA_ONLY: public data only, books, discovery, theoretical + executable-depth calculation, zero orders (real or simulated) | Implemented | `triarb/observer.py`; `triarb/cli.py` (only command: `observe`) | 1 |
| F-MODE-PAPER | §3, §25 | PAPER_TRADING engine (live books, simulated legs/latency/fees/slippage/partial fills/rejections, virtual balances) | Planned | §25; `docs/PAPER_TRADING.md` | 2 |
| F-MODE-SHADOW | §3 | SHADOW_TRADING (real balances read, would-be orders recorded, no orders placed) | Planned | §3; phase placement TBD (T-4) | 2–3 |
| F-MODE-LIVE | §3 | LIVE_TRADING behind the full ten-point §3 authorization chain; never enabled by config or frontend alone | Planned | §3; gate refusal is the implemented part (F-MODE-GATE) | 3 |
| F-GATE-PROFIT | §15 | Profitability Gate — full checklist (books synchronized/fresh, skew, rules, fees, balances, risk, `MIN_EXPECTED_NET_PROFIT_BPS`, `MAX_SLIPPAGE_BPS`, `MAX_CYCLE_NOTIONAL`, API/stream health, no kill switch). Today implemented subset: staleness (`MAX_BOOK_AGE_MS` analogue `staleness_ms`), `edge_threshold_bps`, depth sufficiency, Decimal feasibility | Planned (subset Implemented) | Subset: `triarb/pricing/edge.py`, `triarb/observer.py::_evaluate`; full gate: §15 | 2 |

## Adapters

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-ADAPTER-CONTRACT | §8 | Common `ExchangeAdapter` interface — market-data subset only (`fetch_symbols`, `fetch_tickers`, `stream_books`, `aclose`). §8 trading/private/reconcile methods deliberately absent | Implemented (subset) | `triarb/exchange/base.py`; `tests/test_adapter_contract.py` | 1 |
| F-ADAPTER-REGISTRY | §8 | Registry resolves every configured venue to its adapter; exhaustiveness tested | Implemented | `triarb/exchange/registry.py`; `tests/test_registry.py` | 1 |
| F-ADAPTER-NORMALIZE | §9 | Normalized `SymbolInfo` (exact Decimal tick/step, min qty, min notional, status) and `Ticker`; malformed entries dropped and counted, never coerced | Implemented | `triarb/exchange/base.py::SymbolInfo`; per-venue `parse_symbol` tests in `tests/test_*_parse.py` | 1 |
| F-ADAPTER-ERRORS | §8 | Typed adapter errors with venue/endpoint context (`RestError`, `WsProtocolError`) | Implemented | `triarb/exchange/errors.py` | 1 |
| F-ADAPTER-BITGET-MD | §8, §40 | Bitget market-data adapter (see F-MD-BITGET) | Implemented | `triarb/exchange/bitget.py`; `tests/test_bitget_parse.py` | 1 |
| F-ADAPTER-BINANCE-MD | §8, §40 | Binance Global market-data adapter: `exchangeInfo` filters (spot-permission required, tick/step from filters), quote-volume→USDT conversion, combined-stream `depth20@100ms` partial-book snapshots | In progress (this iteration) — code + offline fixture tests done; live soak pending (`PROGRESS.md`) | `triarb/exchange/binance.py`; `tests/test_binance_parse.py` | 1 |
| F-ADAPTER-BYBIT-MD | §8, §40 | Bybit v5 market-data adapter: instruments-info, turnover→USDT conversion, `orderbook.50` snapshot+delta with sequence handling (F-MD-SEQ-BYBIT) | In progress (this iteration) — code + offline fixture tests done; live soak pending | `triarb/exchange/bybit.py`; `tests/test_bybit_parse.py` | 1 |
| F-ADAPTER-OKX-MD | §8, §40 | OKX Global market-data adapter: instruments (exact `tickSz`/`lotSz`), `volCcy24h`→USDT conversion, `books5` snapshots, `ping` heartbeat | In progress (this iteration) — code + offline fixture tests done; live soak pending | `triarb/exchange/okx.py`; `tests/test_okx_parse.py` | 1 |
| F-ADAPTER-TRADING | §8, §17 | Order-entry methods (`placeIocOrder`, `placeFokOrder`, `cancelOrder`, …). **No such code exists anywhere in this build** | Planned | §8; absence: `triarb/exchange/base.py` Phase 1 note | 2 (paper) / 3 (live) |
| F-ADAPTER-PRIVATE | §8 | Private WebSockets (`subscribeOrders`, `subscribeExecutions`, `subscribeBalances`) and reconcile methods | Planned | §8 | 2–3 |
| F-ADAPTER-CLOCK | §8 | `fetchServerTime` / `synchronizeClock` | Planned | §8 | 2 |

## Execution

No execution code exists in this build (deliberate — CLAUDE.md Part A).
Every row below is design-only.

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-EXEC-STATE-MACHINE | §18 | Persisted per-cycle state machine (DETECTED … LEG_1_FILLED … RECONCILING, COMPLETED, RECOVERY_REQUIRED, FAILED, MANUAL_INTERVENTION) with idempotent, fully recorded transitions | Planned | §18; `docs/EXECUTION_ENGINE.md` | 2 |
| F-EXEC-SEQUENTIAL | §16 | Mode A sequential closed-loop execution (default); each leg sized from the previous leg's ACTUAL fill | Planned | §16 | 2 |
| F-EXEC-IOC | §17 | Marketable IOC limit orders with price protection (the Decimal feasibility pass already computes the protected limit prices the executor would use: `triarb/pricing/feasibility.py::LegPlan`) | Planned | §17 | 2 |
| F-EXEC-COID | §19 | Deterministic client order IDs (bot/exchange/cycle/leg/attempt/strategy-version); reconcile-before-resubmit | Planned | §19 | 2 |
| F-EXEC-PARTIAL | §20 | Partial-fill handling: cancel remainder, recalc route from actual holdings, risk approval to continue, else recovery | Planned | §20 | 2 |
| F-EXEC-PARALLEL | §16 | Mode B inventory-assisted parallel execution — disabled until sequential is proven + human authorization recorded | Planned | §16, §38 | post-3 |

## Risk

No risk-engine code exists in this build.

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-RISK-ENGINE | §23 | Central risk engine; no cycle may bypass it; immutable structured decisions | Planned | §23; `docs/RISK_ENGINE.md` | 2 |
| F-RISK-KILL | §24 | Kill switches (global/exchange/triangle/symbol/asset/strategy-version + automatic daily-loss, repeated-failure, stale-data, reconciliation, disconnection) | Planned | §24 | 2 |
| F-RISK-RESERVATION | §22 | Atomic per-exchange/account/asset balance reservations; no double spend | Planned | §22 | 2 |
| F-RISK-LIMITS | §15, §23 | Full configurable threshold set (`MIN_EXPECTED_NET_PROFIT_BPS`, `MAX_BOOK_AGE_MS`, `MAX_SLIPPAGE_BPS`, `MAX_CYCLE_NOTIONAL`, …); never auto-lowered to create trades | Planned (staleness + edge threshold exist today: `triarb/config/models.py::ObserverConfig`) | §15 | 2 |
| F-RISK-QUALIFICATION | §33 | Live-trading qualification config and gate (paper/shadow durations, false-positive/partial-fill/recovery/latency/drawdown bounds) | Planned | §33 | 3 |

## Recovery

No recovery code exists in this build.

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-REC-ENGINE | §21 | Recovery/unwind engine: freeze venue → reconcile orders/fills/balances → lowest-risk approved unwind → incident record; max recovery-loss threshold; never blind market-sells | Planned | §21; `docs/RECOVERY_ENGINE.md` | 2 |
| F-REC-RESTART | §18 | Process restart resumes or safely reconciles incomplete cycles | Planned | §18 | 2 |
| F-REC-RECONCILE | §8, §21 | `reconcileOrder` / `reconcileBalances` / `reconcileCompleteAccountState` | Planned | §8 | 2–3 |

## Accounting

No accounting/ledger code exists in this build.

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-ACCT-FUNNEL | §27 | Opportunity funnel counters. Implemented stages: theoretical detections (`cycles_detected`/`cycles_ended`), depth failures (`insufficient_depth`), rounding kills (`rounding_killed`), stale skips. Execution-side stages (risk-approved, submitted, completed, recovered, failed) have no code | Implemented (observer stages) / Planned (execution stages) | `triarb/observer.py`; `triarb/monitoring/metrics.py`; `tests/test_observer.py::TestLifecycle::test_rounding_killed_counter` | 1 / 2 |
| F-ACCT-LEDGER | §27 | Realized profit/fees/slippage/recovery-loss ledger; Decimal only (Invariant 21). All figures logged today are THEORETICAL — none are realized, and nothing presents them otherwise | Planned | §27 | 2 |
| F-ACCT-MODE-LABEL | §27, §28 | Every financial result labeled THEORETICAL / PAPER / SHADOW / LIVE; never mixed | Planned (only theoretical figures exist today, produced solely by MARKET_DATA_ONLY mode) | §27 | 2 |

## Security

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-SEC-NOKEYS | §29 | Phase 1 reads **no API keys anywhere**; public endpoints only | Implemented | no credential code in `triarb/exchange/*.py`; `tests/test_adapter_contract.py::TestAdapterContract::test_constructor_needs_no_credentials_or_network` | 1 |
| F-SEC-ENV-DEFAULTS | §3, §29 | `LIVE_TRADING_ENABLED=false`, `WITHDRAWAL_PERMISSION_ALLOWED=false`, `AUTOMATIC_EXTERNAL_TRANSFERS=false` pinned as the only supported values | Implemented | `.env.example` | 1 |
| F-SEC-NO-WITHDRAWAL-CODE | §2 | Withdrawal/transfer code permanently banned from the repository | Implemented (structural: no such code exists) | CLAUDE.md Part A rule 4; `triarb/exchange/base.py` Phase 1 note | all |
| F-SEC-KEYPERMS | §29 | Key-permission validation at startup: reject withdrawal-enabled keys, warn on margin/futures/unrestricted-IP | Planned | §29; checklist only: `.env.example` | 2 |
| F-SEC-SECRETS | §29 | Secret encryption, rotation, redaction, credential health monitoring, audit logs | Planned | §29; `docs/SECURITY_MODEL.md` | 2+ |
| F-SEC-TENANCY | §30 | Multi-tenancy isolation | TBD (T-2) | §30 | TBD |

## Observability

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-OBS-JSONLOG | §5 | Structured JSON-lines logging (one object per line; never raises into the hot path) | Implemented | `triarb/monitoring/jsonlog.py` | 1 |
| F-OBS-LIFECYCLE | §27 | Opportunity lifecycle events: `universe`, `cycle_detected` (gross/net/net-after-rounding bps, `feasible`, `reject_reason`, `stranded_dust`, `max_book_age_ms`), `cycle_ended` (`persisted_ms`, peaks, `ever_feasible`, `end_reason`), `stream_status` | Implemented | `triarb/observer.py`; `tests/test_observer.py::TestLifecycle` | 1 |
| F-OBS-METRICS | §5, §27 | In-process counters + fixed-bucket histograms (`reprice_latency_us`, `net_edge_bps`), periodic `stats` snapshot | Implemented | `triarb/monitoring/metrics.py`; `triarb/observer.py::_stats_loop` | 1 |
| F-OBS-DASHBOARD | §28 | Terminal dashboard (`--dashboard`, rich); cosmetic layer — JSON logs remain the source of truth | Implemented | `triarb/monitoring/dashboard.py`; `triarb/cli.py` | 1 |
| F-OBS-PROM | §5 | Prometheus-compatible metrics export | Planned | §5 | 4 |
| F-OBS-ADMIN | §28 | Administrative dashboard screens (balances, cycles, kill switches, incidents, mode labels) | Planned | §28 | post-2 |

## Testing

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-TEST-OFFLINE | §32 | Offline test suite, no network: 180 tests across config, graph, depth, edge, fees, feasibility, order books, observer lifecycle, per-venue payload parsing, adapter contract, and pricing-core properties | Implemented | `tests/` (`pytest`, all offline per `tests/conftest.py`) | 1 |
| F-TEST-GATES | §35 | Commit gates: `pytest` + `ruff check .` + `mypy triarb` (strict) all clean | Implemented | `pyproject.toml` (`[tool.mypy] strict = true`); CLAUDE.md Part A "How to work in this repo" | 1 |
| F-TEST-CONTRACT | §32 | Adapter contract suite — market-data scope today (all venues construct without credentials/network, names match registry); full §8 contract suite grows with trading methods | Implemented (subset) | `tests/test_adapter_contract.py`; `tests/test_registry.py` | 1 / 2 |
| F-TEST-FIXTURES | §8 | Per-venue payload fixture tests derived from official docs (symbols, tickers, WS frames, control frames, malformed frames; Bybit adds snapshot+delta assembly/sequencing) | Implemented (all four venues) | `tests/test_bitget_parse.py`, `tests/test_binance_parse.py`, `tests/test_bybit_parse.py`, `tests/test_okx_parse.py` | 1 |
| F-TEST-PROPERTY | §32 | Property-based tests for the pricing core: depth-walk output bounds, fee monotonicity, exact tick/step increment compliance (Invariant 6), rounding-cannot-create-funds. Ledger/reservation/dedup properties follow with their Phase 2 code | Implemented (pricing core) | `tests/test_properties.py` (Hypothesis, deterministic via `derandomize`) | 1 / 2 |
| F-TEST-INTEGRATION | §32 | Live/network integration tests; next action is a live soak of each adapter in MARKET_DATA_ONLY | Planned | `PROGRESS.md` iteration 2 "Next best action" | 1–2 |
| F-TEST-REPLAY | §26 | Deterministic market-replay framework | Planned | §26 | 2 |
| F-TEST-CHAOS | §32 | Chaos injection (disconnects, timeouts, duplicate/out-of-order fills, crashes, clock drift, …) | Planned | §32 | 4 |

## Docs

| Feature ID | Requirement | Description | Status | Evidence | Phase |
|---|---|---|---|---|---|
| F-DOCS-CORE | §6, §35 | `CLAUDE.md` (Part A + verbatim master prompt), `PROGRESS.md` (append-only log), this feature matrix | Implemented | `CLAUDE.md`; `PROGRESS.md`; `docs/FEATURE_MATRIX.md` | 1 |
| F-DOCS-TREE | §6 | Engineering doc tree: PRODUCT_REQUIREMENTS, ARCHITECTURE, TRIANGULAR_ARBITRAGE_MATH, CURRENCY_GRAPH, EXCHANGE_ADAPTER_CONTRACT, ORDER_BOOK_ENGINE, OPPORTUNITY_ENGINE, EXECUTION_ENGINE, RECOVERY_ENGINE, RISK_ENGINE, SECURITY_MODEL, THREAT_MODEL, TEST_STRATEGY, PAPER_TRADING, OBSERVABILITY, OPERATIONS_RUNBOOK | Implemented (files exist under `docs/`) | `docs/*.md` | 1 |
| F-CAPABILITY-MATRIX | §8 | Exchange capability matrix with official-doc citations + adversarial verification (D-005); "Verify" cells never assumed | In progress (this iteration) | `PROGRESS.md` iteration 2; `docs/DECISIONS.md` D-005 (file `docs/EXCHANGE_CAPABILITY_MATRIX.md` not yet on disk at matrix-generation time) | 1 |
| F-DOCS-INCIDENT | §6 | `docs/INCIDENT_RESPONSE.md` | Implemented | `docs/INCIDENT_RESPONSE.md` | 1 |
| F-DOCS-DECISIONS | §6 | Architecture decision record (D-001 stack, D-002 snapshot channels, D-003 mode gate, D-004 process-per-venue, D-005 doc verification) | Implemented | `docs/DECISIONS.md` | 1 |

## Required invariants (§31)

The 24 invariants require automated tests. Most concern execution, which
does not exist in this build — those are **vacuously true today** (no
cycle can execute at all) but that is not test evidence; they are marked
Planned until real tests exist against real execution code.

| ID | §31 | Invariant | Status | Evidence today | Phase |
|---|---|---|---|---|---|
| I-01 | 1 | No cycle executes on stale order books | Planned (observer analogue Implemented: stale books are never priced or reported) | `triarb/pricing/edge.py` (`stale_book`); `tests/test_edge.py::TestEvaluateCycle::test_stale_book_rejected`; sweep loop `triarb/observer.py::_sweep_loop` | 2 |
| I-02 | 2 | No cycle executes on an unsynchronized book | Planned (analogue Implemented: Bybit sequence gap → empty book + resubscribe; disconnect → all books stale) | `tests/test_bybit_parse.py::TestBookAssembly::test_sequence_gap_detected_and_book_dropped`; `tests/test_orderbook.py::TestBookStore::test_mark_all_stale_on_disconnect` | 2 |
| I-03 | 3 | All three legs belong to the same exchange | Implemented (structural: one venue per process, triangles compiled from a single adapter's symbol list) | `triarb/config/models.py::Settings.venue`; `triarb/graph/triangles.py`; `docs/DECISIONS.md` D-004 | 1 |
| I-04 | 4 | Every leg uses the actual fill from the previous leg | Planned (simulation analogue Implemented: both pricing passes chain each leg from the prior leg's computed output) | `triarb/pricing/edge.py::evaluate_cycle`; `triarb/pricing/feasibility.py::simulate_rounded_cycle` | 2 |
| I-05 | 5 | All fees are included | Implemented (pricing scope: fee compounded on every leg in both passes; fee-asset variants per §12 are Phase 2) | `tests/test_fees.py`; `tests/test_edge.py::TestEvaluateCycle::test_gross_and_net_edge` | 1 / 2 |
| I-06 | 6 | All quantities satisfy exchange increments | Implemented (simulation scope: step-based deterministic rounding, non-power-of-ten steps correct) | `triarb/pricing/feasibility.py::_down/_up`; `tests/test_feasibility.py::TestStepRounding` | 1 / 2 |
| I-07 | 7 | All orders satisfy minimum quantities | Implemented (simulation scope: `min_qty` rejection) | `tests/test_feasibility.py::TestExchangeMinimums::test_min_qty_rejection` | 1 / 2 |
| I-08 | 8 | All orders satisfy minimum notionals | Implemented (simulation scope, proxy check — see TBD T-1) | `tests/test_feasibility.py::TestExchangeMinimums::test_min_notional_rejection` | 1 / 2 |
| I-09 | 9 | Locked funds cannot be double spent | Planned (no balance code exists) | §22 | 2 |
| I-10 | 10 | A timeout cannot cause duplicate submission | Planned (no order code exists) | §19 | 2 |
| I-11 | 11 | An accepted order is not treated as filled | Planned | §17 | 2 |
| I-12 | 12 | Partial fills trigger recalculation | Planned | §20 | 2 |
| I-13 | 13 | Every live order passes the risk engine | Planned | §23 | 2–3 |
| I-14 | 14 | Every live order has an immutable audit record | Planned | §18 | 2–3 |
| I-15 | 15 | Every incomplete cycle survives process restart | Planned | §18 | 2 |
| I-16 | 16 | Withdrawal-enabled keys are rejected | Planned (no keys are read at all today; flags pinned false in `.env.example`) | §29 | 2 |
| I-17 | 17 | Live mode remains disabled by default | Implemented | `tests/test_config.py::TestModeGate::test_default_mode_is_market_data_only` and `::test_live_trading_rejected_with_authorization_message`; `.env.example` | 1 |
| I-18 | 18 | A kill switch blocks new cycles | Planned (no kill switch exists) | §24 | 2 |
| I-19 | 19 | Recovery orders are linked to their original cycle | Planned | §21 | 2 |
| I-20 | 20 | Theoretical profit is never presented as realized | Planned as a test; factually true today: the build produces only theoretical figures and has no realized-profit field anywhere | `triarb/observer.py` log fields (`net_bps`, `net_after_rounding_bps`) | 1 (fact) / 2 (test) |
| I-21 | 21 | Binary floating-point is never used for money | Implemented (standing rule: float hot path is a screen only; anything reported actionable is re-simulated in exact Decimal with venue rounding) | `triarb/pricing/feasibility.py`; `tests/test_feasibility.py::TestStepRounding::test_exact_multiple_is_unchanged_no_float_artifacts`; CLAUDE.md Part A money rule | 1 |
| I-22 | 22 | No LLM participates in the live execution path | Planned (no execution path exists; deterministic-engine requirement §4 binds Phase 2+ design) | §4 | 2–3 |
| I-23 | 23 | An exchange adapter cannot bypass central execution controls | Planned (structurally vacuous today: adapters expose no trading methods at all) | `triarb/exchange/base.py` | 2 |
| I-24 | 24 | One tenant cannot access another tenant's information | TBD (T-2) | §30 | TBD |

## TBD register (per §6)

| ID | Question | Safest reversible assumption recorded | Where |
|---|---|---|---|
| T-1 | Exact per-leg USDT valuation for min-notional checks (needs conversion rates only an executor tracks) | Starting notional with a 5% haircut as the per-leg USDT proxy; check skipped for non-USDT quote cycles (conservative: rejects marginal cycles) | `triarb/pricing/feasibility.py` docstring; F-PRICE-MINNOTIONAL, I-08 |
| T-2 | Whether multi-tenancy (§30) will ever be built | Single-tenant, single-operator, one process per venue; §30 isolation tests become mandatory before any multi-user feature ships | F-SEC-TENANCY, I-24 |
| T-3 | Persistence store (§5 recommends PostgreSQL; nothing is persisted today beyond logs) | JSON-lines logs are the sole durable record until Phase 2 requires persisted cycle state (§18); DB choice deferred to that design | F-EXEC-STATE-MACHINE; `docs/DECISIONS.md` scope |
| T-4 | Phase slot for SHADOW_TRADING (README phase plan names Phases 1–4 without an explicit shadow phase) | Shadow qualification runs after Phase 2 paper qualification and before any Phase 3 live activation, exactly per §3 mode-4 requirements and §33 | F-MODE-SHADOW |

## Regeneration notes

- Statuses are derived from files present at generation time
  (2026-07-11). "In progress (this iteration)" rows finalize when
  `PROGRESS.md` iteration 2 closes: flip to Implemented only after the
  per-venue live soak in MARKET_DATA_ONLY.
- When adding a feature: pick an ID (`F-<SUBSYS>-<NAME>` / `I-nn`), cite
  the CLAUDE.md §, and never mark Implemented without a file or test that
  exists. Update this file with every substantive change (CLAUDE.md
  Part A, rule 5).
