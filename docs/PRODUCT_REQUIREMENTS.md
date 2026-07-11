# Product Requirements

Source of truth: the master prompt saved verbatim in `CLAUDE.md` Part B.
This document restates its §1–§4 and §37–§38 as numbered, testable
requirements and records the honest status of each against the code in this
repository as of 2026-07-11.

**Status labels.** Every claim below is labeled either **Implemented** (true
of the code today, with a file citation) or **Planned** (design obligation
for a later phase, with the governing CLAUDE.md Part B section). Requirements
that are partially met are split so each sub-claim carries its own label.

**Current build reality.** This build is a `MARKET_DATA_ONLY` observer. It
contains **no order-placement, balance, withdrawal, or transfer code of any
kind** (`triarb/exchange/base.py` defines only `fetch_symbols`,
`fetch_tickers`, `stream_books`, `aclose`). All four venues ship a public
market-data adapter behind that contract (`triarb/exchange/bitget.py`,
`binance.py`, `bybit.py`, `okx.py`, wired in
`triarb/exchange/registry.py`); Bitget has run live, while the other three
are offline-fixture-verified with a live soak pending (`PROGRESS.md`
iteration 2). Nothing here is profitable or
production-ready, and the README's economics (compounded fee hurdle,
latency reality, rounding losses, adverse selection) apply in full.

## 1. Core product goals (Part B §1)

| ID | Requirement | Acceptance criterion | Status |
|----|-------------|----------------------|--------|
| R-01 | Connects securely to supported exchanges (Bitget, Bybit, OKX Global, Binance Global) as independent venues | Each venue has an adapter behind the common interface; official endpoints only; authenticated connections pass permission validation before use | Partial. **Implemented:** public market data for all four venues over official endpoints, no credentials read anywhere (`triarb/exchange/bitget.py`, `binance.py`, `bybit.py`, `okx.py`, `registry.py`; contract suite `tests/test_adapter_contract.py`); Binance/Bybit/OKX live soak pending (`PROGRESS.md`). **Planned:** all private/authenticated connectivity (Part B §8, §29) |
| R-02 | Downloads all active spot instruments | Startup fetches every online spot symbol with precision and minimum-size metadata; metadata refreshes on a timer | **Implemented** for all four venues: `fetch_symbols` → validated `SymbolInfo` with exact tick/step increments (`triarb/exchange/base.py` and the four adapters), periodic refresh via `_metadata_loop` (`triarb/observer.py`). **Planned:** refresh-on-error / pre-live refresh triggers (Part B §9) |
| R-03 | Builds a directed currency-conversion graph | Every spot market yields buy and sell conversion directions; graph rebuilds from live instrument data | Partial. **Implemented:** adjacency of currencies over online markets (`triarb/graph/builder.py`); leg direction (BUY consumes asks, SELL consumes bids) assigned at compilation (`triarb/graph/triangles.py`, `triarb/pricing/edge.py`). **Planned:** the full per-edge record of Part B §10 (fee model, status, sequence number per edge) |
| R-04 | Discovers every valid three-leg currency cycle | All directed cycles through the configured settlement currency are enumerated, both directions kept as distinct opportunities, deduplicated, filterable | Partial. **Implemented:** `enumerate_cycles` finds every directed Q→A→B→Q cycle for configured quote currency Q, both directions distinct, with liquidity/spread filters and whitelist (`triarb/graph/triangles.py`, tests in `tests/test_triangles.py`). **Planned/TBD:** cycles not anchored at the single configured settlement asset — see TBD-1 |
| R-05 | Maintains real-time local order books | Books track update time and staleness; invalid or stale books are never used for opportunities | Partial. **Implemented:** snapshot-channel `BookStore`, staleness from local monotonic receive time, all books invalidated on WebSocket disconnect (`triarb/pricing/orderbook.py`, `triarb/observer.py`, tests in `tests/test_orderbook.py`). **Planned:** incremental books with sequence numbers, checksums, and snapshot resynchronization (Part B §13) |
| R-06 | Calculates executable returns from market depth | Profitability is computed by walking book levels at the evaluated size — never from last price, mid price, or a single top-of-book level | **Implemented:** depth walks in `triarb/pricing/depth.py`; cycle evaluation in `triarb/pricing/edge.py`; books too shallow reject as `insufficient_depth`, never extrapolated (tests in `tests/test_depth.py`, `tests/test_edge.py`) |
| R-07 | Includes trading fees on every leg | Fees compound per leg; break-even hurdle is `1/(1-f)^n - 1`, not `n·f` | Partial. **Implemented:** compounded fee model and `hurdle_bps` (`triarb/pricing/fees.py`, unit-tested against the README table in `tests/test_fees.py`); rate is a configurable conservative default with optional token discount (`FeesConfig` in `triarb/config/models.py`). **Planned:** fetching actual account fee tiers, per-fill fee assets, expected-vs-actual comparison (Part B §12) |
| R-08 | Includes quantity and price rounding | Actionable results survive an exact simulation of venue price/quantity precision, minimum quantity, and minimum notional, with dust accounted | **Implemented:** exact-`Decimal` feasibility pass simulating the three IOC limit orders, rounding prices toward marketability and quantities down, valuing stranded dust at zero (`triarb/pricing/feasibility.py`, tests in `tests/test_feasibility.py`); float math is a screen only, never authoritative for money (Invariant 21) |
| R-09 | Includes slippage and latency reserves | Configurable `LATENCY_RESERVE_BPS` / `FAILURE_RESERVE_BPS` are subtracted before an opportunity qualifies | **Planned** (Part B §11, §15). Depth walking prices slippage-through-depth implicitly, but no explicit reserve parameters exist in `triarb/config/models.py` — see TBD-3. The observer reports raw net edge; readers must apply their own reserve during analysis |
| R-10 | Identifies the maximum safely executable trade size | An optimizer evaluates candidate sizes against all three books under balance, depth-participation, and increment constraints | **Planned** (Part B §14). The observer evaluates one fixed configured notional (`observer.notional`, `triarb/config/models.py`); it rejects sizes the visible book cannot fill but performs no sizing search |
| R-11 | Executes qualified opportunities | Sequential closed-loop execution: each leg sized from the previous leg's actual fill; every order passes the central risk engine | **Planned** (Part B §16–§19). No order code exists in this build, by design |
| R-12 | Handles partial fills and order failures | A partial fill cancels the remainder, recalculates from actual acquired amounts on fresh books, and continues only with risk approval | **Planned** (Part B §20) |
| R-13 | Unwinds unintended inventory safely | Every failure mode has a documented recovery path; recovery freezes new cycles, reconciles state, and bounds recovery loss | **Planned** (Part B §21) |
| R-14 | Tracks realized net profit | Realized profit is accounted per cycle and never mixed with theoretical, paper, or shadow figures | **Planned** (Part B §27). The observer logs only theoretical edges, always labeled as such (`cycle_detected`/`cycle_ended` events, `triarb/observer.py`, `triarb/monitoring/jsonlog.py`) — Invariant 20 is honored by having no realized-profit figure at all |
| R-15 | Stops automatically when safety rules are triggered | Kill switches (global, per-exchange, per-symbol, automatic loss/staleness/reconciliation) block new cycles and require authorized restart | **Planned** (Part B §24). Today's only automatic stops are data-hygiene measures, not kill switches: stale books are excluded from evaluation and disconnects invalidate all books (`triarb/pricing/orderbook.py`, `triarb/observer.py`) |
| R-16 | The initial production currency is configurable (USDT default) | Settlement currency is a config value, not a constant | **Implemented:** `quote_currency` setting, default `USDT`, validated and uppercased (`triarb/config/models.py`); triangle discovery is driven by it (`triarb/graph/triangles.py`) |

## 2. Clean-room constraints (Part B §2) — hard requirements

These are permanent constraints, not phase-gated features. Violating any of
them is a defect regardless of test results.

| ID | Constraint | Compliance today |
|----|-----------|------------------|
| CR-01 | Original implementation from official exchange documentation, publicly documented market mechanics, independently designed algorithms, and internally created tests | **Implemented as process.** Adapters speak the documented public protocols natively (`triarb/exchange/`); no third-party bot code is vendored; the entire suite (175 tests) is offline and internally authored (`tests/`) |
| CR-02 | Do not copy another arbitrage bot or decompile proprietary software | **Implemented as process** (no such code present) |
| CR-03 | Do not circumvent exchange or geographic restrictions | **Implemented:** only official public endpoints are called (`triarb/exchange/`); no proxy/geo logic exists anywhere |
| CR-04 | Do not scrape private endpoints or use undocumented authentication | **Implemented:** no authentication code exists; no API keys are read in this phase (`.env.example` documents keys as later-phase only) |
| CR-05 | Do not misrepresent performance or claim guaranteed returns | **Implemented as documentation stance:** README states the fee hurdle, latency, and adverse-selection realities and frames the observer as the tool to disprove viability; theoretical edges are never labeled realized |
| CR-06 | Never enable withdrawals; withdrawal/transfer code is banned permanently | **Implemented:** no withdrawal or transfer code exists; the adapter interface has no such methods (`triarb/exchange/base.py`); `.env.example` pins `WITHDRAWAL_PERMISSION_ALLOWED=false`, `AUTOMATIC_EXTERNAL_TRANSFERS=false`. **Planned:** automated rejection of withdrawal-enabled keys (Invariant 16) when key handling ships (Part B §29) |
| CR-07 | Do not store unrestricted exchange keys | **Implemented today** trivially (no keys are read at all). **Planned:** key-permission validation and restricted storage for Phase 2+ (Part B §29) |
| CR-08 | Do not place unauthorized trades | **Implemented:** no trading code exists; the startup gate refuses every mode that would need one (`ensure_supported_mode`, `triarb/config/models.py`) |
| CR-09 | Only official and authorized exchange APIs | **Implemented:** Bitget adapter uses documented public REST and WebSocket endpoints only (`triarb/exchange/bitget.py`) |

## 3. Operating modes and gating (Part B §3)

The four modes, using the master prompt's exact names:

| Mode | Definition (Part B §3) | Status in this build |
|------|------------------------|----------------------|
| `MARKET_DATA_ONLY` | Public data only; builds books, discovers routes, calculates theoretical and executable opportunities; places no simulated or real orders | **Implemented** — the default and only startable mode (`Mode.MARKET_DATA_ONLY`, `triarb/config/models.py`; orchestrator `triarb/observer.py`; sole CLI command `triarb observe`, `triarb/cli.py`) |
| `PAPER_TRADING` | Live books; simulates all three legs, latency, fees, slippage, partial fills, rejections; virtual balances; no real orders | **Planned** (Part B §3, §25). Config value parses but the startup gate refuses it with a "later phase" error |
| `SHADOW_TRADING` | Real balances, live data; records orders that would have been placed and compares expected fills with subsequent market activity; no real orders | **Planned** (Part B §3). Refused at the gate identically |
| `LIVE_TRADING` | Real orders; disabled by default | **Planned and hard-refused.** The gate raises with the authorization-chain message (`ensure_supported_mode`, `triarb/config/models.py`) |

### Mode gate — Implemented

- `mode` is a validated enum defaulting to `market_data_only`; unknown modes
  fail pydantic validation before startup (`triarb/config/models.py`).
- `ensure_supported_mode` runs before any network connection
  (`triarb/cli.py`): `MARKET_DATA_ONLY` passes; `PAPER_TRADING` and
  `SHADOW_TRADING` raise `UnsupportedModeError` naming the later phase;
  `LIVE_TRADING` raises with a distinct message citing the full
  authorization chain.
- Test evidence for Invariant 17: `tests/test_config.py::TestModeGate`.
- `.env.example` pins `LIVE_TRADING_ENABLED=false`,
  `WITHDRAWAL_PERMISSION_ALLOWED=false`,
  `AUTOMATIC_EXTERNAL_TRANSFERS=false`, and documents that flipping the flag
  alone does nothing.

### Live-mode activation chain — Planned (Part B §3)

`LIVE_TRADING` cannot be enabled by configuration in any build until all ten
conditions hold, and a frontend button alone must never enable it:

1. Server-side environment authorization
2. Exchange-account approval
3. API-permission validation
4. Successful paper-trading qualification
5. Successful shadow-trading qualification
6. Successful security review
7. Successful risk review
8. Human authorization
9. Jurisdiction eligibility
10. Immutable activation record

None of these exist yet; the only implemented element is that the gate
refuses the mode outright (which is the correct state for this phase).

## 4. LLM-exclusion design principle (Part B §4)

Claude (or any language model) is responsible for architecture, coding,
testing, documentation, review, debugging, and improvement — and is
**permanently excluded** from the live execution path (Invariant 22):

An LLM must never:

- Decide whether to submit a live order
- Calculate the final live order quantity
- Alter risk limits during execution
- Approve its own live trades
- Generate unvalidated prices
- Participate in the low-latency execution path

| Claim | Status |
|-------|--------|
| No LLM participates in any runtime path of this build | **Implemented:** the runtime dependency set is exchange/network/validation libraries only — no LLM client exists (`pyproject.toml`); the hot path is deterministic Python (`triarb/observer.py`, `triarb/pricing/edge.py`) |
| The live engine is deterministic, versioned, auditable, reproducible, and independently testable | **Planned** (Part B §4) — there is no live engine. The precedent set now: pricing is pure-function and offline-testable (`tests/test_edge.py`, `tests/test_feasibility.py`), and all decisions log machine-readable reasons (`triarb/monitoring/jsonlog.py`) |
| Automated proof that no LLM is in the execution path (Invariant 22 test) | **Planned** (Part B §31) — becomes meaningful when an execution path exists |

## 5. Release conditions (Part B §37)

The system may be declared a release candidate **only** when every condition
below is met. **Current verdict: not a release candidate, and far from one.**
Most conditions cannot even be evaluated yet because the subsystems they
test do not exist.

| ID | Condition | Status today |
|----|-----------|--------------|
| RC-01 | All supported exchange adapters pass contract tests | Not met. One adapter functional (Bitget, market-data only); no shared contract suite yet (Part B §32) |
| RC-02 | All critical invariants pass | Not met. Only invariants applicable to an observer have evidence (e.g. 17: `tests/test_config.py::TestModeGate`; 21: `triarb/pricing/feasibility.py`); execution-side invariants are untestable until Phase 2+ |
| RC-03 | Order-book reconstruction is verified | Not met. Snapshot channels only; incremental reconstruction is Planned (Part B §13) |
| RC-04 | Fee calculations are verified | Partially evidenced: compounding math unit-tested (`tests/test_fees.py`); actual account fee treatment unverified (Part B §12) |
| RC-05 | Precision and rounding are verified | Partially evidenced offline (`tests/test_feasibility.py`); not verified against live venue behavior |
| RC-06 | Paper mode is stable | Not met — paper mode does not exist |
| RC-07 | Shadow mode is stable | Not met — shadow mode does not exist |
| RC-08 | Restart recovery works | Not met — no persisted cycles exist to recover |
| RC-09 | Partial-fill recovery works | Not met — no execution exists |
| RC-10 | Duplicate-order prevention works | Not met — no orders exist |
| RC-11 | Kill switches work | Not met — no kill switches exist (Part B §24) |
| RC-12 | Reconciliation works | Not met — no reconciliation exists (Part B §21) |
| RC-13 | Security review passes | Not met |
| RC-14 | Risk review passes | Not met |
| RC-15 | Chaos testing passes | Not met (Part B §32) |
| RC-16 | Documentation matches implementation | In progress — this document and its siblings label every claim Implemented/Planned to keep it true |
| RC-17 | Live mode remains disabled | True today (`ensure_supported_mode`); must be re-verified at every release |
| RC-18 | No unresolved critical defect remains | Cannot be asserted for unbuilt subsystems |
| RC-19 | An independent release reviewer approves | Not requested — nothing to review for release |

Per Part B (preamble): the bot must never be described as profitable or
production-ready unless objective evidence satisfies **every** condition
above. No such evidence exists.

## 6. Human-authorization checkpoints (Part B §38)

Work must pause for explicit recorded human authorization before any of the
following. **None has been requested or granted; none is applicable to the
current phase.**

| ID | Checkpoint | Relevance now |
|----|-----------|---------------|
| HA-01 | Activating live trading | Blocked additionally by the mode gate and absence of execution code |
| HA-02 | Using real exchange credentials | Not applicable yet — Phase 1 reads no credentials (`.env.example`) |
| HA-03 | Deploying to production | Not applicable — observer runs locally/ad hoc |
| HA-04 | Increasing capital limits | No capital limits exist yet to increase |
| HA-05 | Weakening risk limits | No risk engine exists yet; thresholds must never be lowered automatically to create more trades (Part B §15) |
| HA-06 | Enabling inventory-assisted parallel execution | Mode B of Part B §16 — Planned, disabled until paper/shadow/inventory/recovery tests pass and authorization is recorded |
| HA-07 | Enabling automatic recovery trades | Planned (Part B §21) |
| HA-08 | Changing jurisdiction controls | See TBD-5 |
| HA-09 | Running destructive migrations | No database exists yet |
| HA-10 | Merging into a protected production branch | Standing rule |

Per Part B §38, authorization is **not** requested merely to continue safe
development, testing, or documentation — which is exactly the scope of the
current phase.

## 7. Recorded TBDs (per Part B §6)

Undecided requirements, each with the safest reversible assumption in force:

- **TBD-1 — Cycles not anchored at the settlement currency.** Part B §10
  says to allow configurable settlement assets and not hard-code USDT-only
  triangles. Implemented today: all directed cycles through one configurable
  `quote_currency` (default `USDT`). Whether multiple simultaneous
  settlement assets (or free A→B→C→A cycles) are required is undecided.
  Assumption: single configurable settlement asset, behind config
  (`triarb/config/models.py`) — reversible by extending enumeration.
- **TBD-2 — Actual account fee tiers.** Unavailable without keys (Phase 1
  reads none). Assumption per Part B §12: configurable conservative rate
  (10 bps taker default, optional token-discount multiplier, `FeesConfig`),
  results treated as estimates; live mode stays blocked until fee treatment
  is validated.
- **TBD-3 — Reserve values** (`LATENCY_RESERVE_BPS`, `FAILURE_RESERVE_BPS`,
  slippage limits of Part B §11/§15). No defensible values exist before
  paper-trading data. Assumption: observer reports raw net edge with
  `max_book_age_ms` attached so reserves can be applied during analysis; the
  execution phase must add these as config before any opportunity counts as
  qualified.
- **TBD-4 — Live-qualification thresholds** (Part B §33: minimum durations,
  cycle counts, maximum false-positive/partial-fill/recovery rates).
  Undecidable before paper and shadow data exist. Assumption: `LIVE_TRADING`
  remains refused at the startup gate regardless.
- **TBD-5 — Jurisdiction eligibility criteria** (live-mode requirement 9,
  Part B §3). Assumption: irrelevant while only `MARKET_DATA_ONLY` can
  start; must be resolved with human input before any live-mode work.
