# CLAUDE.md — triarb operating guide

> **Read this first, then `PROGRESS.md`, then `docs/FEATURE_MATRIX.md`.**
> This file has two parts: (A) the current repository state and standing
> decisions, and (B) the governing master prompt, saved verbatim. Where the
> two conflict, the master prompt's safety rules always win; the repository
> state section only records how its requirements are being met here.

---

## Part A — Current repository state

### What this repository is

`triarb` — a spot triangular arbitrage platform operating **intra-exchange**
(never transferring funds between exchanges), targeting Bitget, Bybit, OKX
Global, and Binance Global as four independent venues.

**Current phase: Observer / MARKET_DATA_ONLY.** The build streams public
order books, enumerates and prices triangles with fee-aware depth walking and
exact-Decimal rounding feasibility, and logs every detected cycle. **It
contains no order-placement, balance, withdrawal, or transfer code of any
kind.** Live trading is disabled by default and cannot be enabled by config
alone (see the mode gate in `triarb/config/models.py`).

### Standing technology decision (recorded per master prompt §5–§6)

The master prompt recommends Rust **for a greenfield implementation** and
instructs: *"First inspect the existing repository and preserve a sound
existing stack."* This repository arrived with a sound, tested Python stack,
so it is preserved:

- Python 3.11+, asyncio, `websockets` (native WS control incl. app-level
  heartbeats), `aiohttp` (REST), `orjson` (parsing), pydantic v2 (boundary
  validation), PyYAML config, `rich` dashboard.
- **Money rule:** the float hot path is a *screen only*; anything reported as
  actionable is re-simulated in exact `Decimal` with venue rounding
  (`triarb/pricing/feasibility.py`). All future accounting/execution code
  must use `Decimal` exclusively. Binary floats are never authoritative for
  money (Invariant 21).
- Rationale and revisit criteria: `docs/DECISIONS.md`.

### Layout

```
triarb/
├── config/        # pydantic Settings + YAML loader (rejects unknown keys);
│                  # Mode enum + ensure_supported_mode gate
├── exchange/      # ExchangeAdapter contract; per-venue adapters; registry
├── graph/         # currency graph, triangle enumeration, liquidity filters
├── pricing/       # book store, depth walking, fee compounding,
│                  # float edge screen, exact Decimal rounding feasibility
├── monitoring/    # JSON-lines logging, counters/histograms, rich dashboard
├── observer.py    # MARKET_DATA_ONLY orchestrator (hot path lives here)
└── cli.py         # `triarb observe` — the only command in this phase
docs/              # architecture, math, threat model, runbooks (see §6 list)
tests/             # offline tests; no network required
```

### How to work in this repo

1. Start every iteration by reading this file, `PROGRESS.md`, and
   `docs/FEATURE_MATRIX.md`; inspect `git status`/`git diff` and failing
   tests before editing (master prompt §35).
2. Environment: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
3. Gates before any commit: `pytest` (all offline), `ruff check .`,
   `mypy triarb` — all three must be clean.
4. Never add order/balance/withdrawal/transfer code outside the phase plan
   and its review gates. Withdrawal/transfer code is banned permanently.
5. Update `PROGRESS.md` (iteration log) and `docs/FEATURE_MATRIX.md`
   (traceability) with every substantive change.
6. Record irreversible or architecture-level choices in `docs/DECISIONS.md`.

### Safety posture of this build

- `mode: market_data_only` is the default and the only mode this build will
  start in; `paper_trading`, `shadow_trading`, `live_trading` fail the
  startup gate with a precise message.
- `.env.example` pins `LIVE_TRADING_ENABLED=false`,
  `WITHDRAWAL_PERMISSION_ALLOWED=false`, `AUTOMATIC_EXTERNAL_TRANSFERS=false`.
- Phase 1 uses **public endpoints only**; no API keys are read anywhere.

---

## Part B — Master prompt (governing specification, saved verbatim)

# CLAUDE CODE LOOP-ENGINEERING MASTER PROMPT
## Multi-Exchange Spot Triangular Arbitrage Bot
You are the autonomous principal engineer responsible for building a secure, deterministic, production-capable **spot triangular arbitrage platform**.
The bot must support:
* Bitget
* Bybit
* OKX Global
* Binance Global
The system must detect and execute **intra-exchange triangular arbitrage** independently on each supported exchange.
Example:
```text
USDT → BTC → ETH → USDT
```
Another direction could be:
```text
USDT → ETH → BTC → USDT
```
Do not transfer funds between exchanges during an arbitrage cycle.
Each exchange must operate as an independent arbitrage venue with its own:
* Balances
* Fees
* Order books
* Symbol rules
* API limits
* Execution state
* Risk limits
* Profit accounting
The engineering loop is:
```text
Discover
→ Specify
→ Architect
→ Implement
→ Simulate
→ Test
→ Attack
→ Correct
→ Measure
→ Document
→ Re-evaluate
→ Continue
```
Do not describe the bot as profitable or production-ready unless objective evidence satisfies every required release condition.
---
# 1. CORE PRODUCT GOAL
Build a system that:
1. Connects securely to supported exchanges.
2. Downloads all active spot instruments.
3. Builds a directed currency-conversion graph.
4. Discovers every valid three-leg currency cycle.
5. Maintains real-time local order books.
6. Calculates executable returns from market depth.
7. Includes trading fees on every leg.
8. Includes quantity and price rounding.
9. Includes slippage and latency reserves.
10. Identifies the maximum safely executable trade size.
11. Executes qualified opportunities.
12. Handles partial fills and order failures.
13. Unwinds unintended inventory safely.
14. Tracks realized net profit.
15. Stops automatically when safety rules are triggered.
The initial production currency should be configurable.
Examples:
```text
USDT
USDC
BTC
ETH
```
The safest default settlement currency is:
```text
USDT
```
---
# 2. CLEAN-ROOM REQUIREMENT
This must be an original implementation based on:
* Official exchange documentation
* Publicly documented market mechanics
* Independently designed algorithms
* Internally created tests and simulations
Do not:
* Copy another arbitrage bot
* Decompile proprietary software
* Circumvent exchange restrictions
* Circumvent geographic restrictions
* Scrape private endpoints
* Use undocumented authentication methods
* Misrepresent performance
* Claim guaranteed returns
* Enable withdrawals
* Store unrestricted exchange keys
* Place unauthorized trades
Only use official and authorized exchange APIs.
---
# 3. OPERATING MODES
The platform must support four modes.
## Mode 1: Market-data mode
```text
MARKET_DATA_ONLY
```
This mode:
* Connects only to public market data
* Builds order books
* Discovers triangular routes
* Calculates theoretical and executable opportunities
* Places no simulated or real orders
## Mode 2: Paper mode
```text
PAPER_TRADING
```
This mode:
* Uses live exchange order books
* Simulates all three legs
* Simulates latency
* Simulates fees
* Simulates slippage
* Simulates partial fills
* Simulates order rejection
* Tracks virtual balances
* Places no real orders
## Mode 3: Shadow mode
```text
SHADOW_TRADING
```
This mode:
* Uses real account balances
* Uses live exchange data
* Generates orders that would have been placed
* Records expected fills
* Compares expected results with subsequent market activity
* Places no real orders
## Mode 4: Live mode
```text
LIVE_TRADING
```
Live mode must remain disabled by default.
```text
LIVE_TRADING_ENABLED=false
WITHDRAWAL_PERMISSION_ALLOWED=false
AUTOMATIC_EXTERNAL_TRANSFERS=false
```
Live mode requires:
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
A frontend button alone must never enable live trading.
---
# 4. IMPORTANT DESIGN PRINCIPLE
Claude is responsible for:
* Architecture
* Coding
* Testing
* Documentation
* Review
* Debugging
* Improvement
Claude or another language model must not:
* Decide whether to submit a live order
* Calculate the final live order quantity
* Alter risk limits during execution
* Approve its own live trades
* Generate unvalidated prices
* Participate in the low-latency execution path
The live engine must be:
* Deterministic
* Versioned
* Auditable
* Reproducible
* Independently testable
---
# 5. DEFAULT TECHNOLOGY DECISION
First inspect the existing repository and preserve a sound existing stack.
For a greenfield implementation, use:
## Core trading engine
```text
Rust
Tokio asynchronous runtime
Strong decimal arithmetic
Native WebSocket clients
Native REST clients
```
Do not use binary floating-point types for:
* Money
* Prices
* Quantities
* Fees
* Profit calculations
* Percentage thresholds
Use fixed-precision decimal arithmetic.
## Supporting services
Recommended:
```text
PostgreSQL
Redis only where justified
Prometheus-compatible metrics
Structured JSON logs
Docker
```
## Management API
Recommended:
```text
Rust Axum or an equivalent strongly typed API
```
## Dashboard
Recommended:
```text
TypeScript
React or Next.js
```
Do not introduce unnecessary microservices during the first milestone.
Begin with a modular monolith unless scaling evidence requires separation.
---
# 6. INITIAL DISCOVERY PHASE
Before implementing production code:
1. Inspect the complete repository.
2. Identify the current stack.
3. Inspect authentication and secret management.
4. Inspect database schemas.
5. Inspect existing exchange code.
6. Inspect all tests.
7. Inspect deployment configuration.
8. Inspect logging and monitoring.
9. Inspect environment files.
10. Inspect unfinished work.
Create or update:
```text
CLAUDE.md
PROGRESS.md
docs/PRODUCT_REQUIREMENTS.md
docs/ARCHITECTURE.md
docs/TRIANGULAR_ARBITRAGE_MATH.md
docs/CURRENCY_GRAPH.md
docs/EXCHANGE_CAPABILITY_MATRIX.md
docs/EXCHANGE_ADAPTER_CONTRACT.md
docs/ORDER_BOOK_ENGINE.md
docs/OPPORTUNITY_ENGINE.md
docs/EXECUTION_ENGINE.md
docs/RECOVERY_ENGINE.md
docs/RISK_ENGINE.md
docs/SECURITY_MODEL.md
docs/THREAT_MODEL.md
docs/TEST_STRATEGY.md
docs/PAPER_TRADING.md
docs/OBSERVABILITY.md
docs/OPERATIONS_RUNBOOK.md
docs/INCIDENT_RESPONSE.md
docs/FEATURE_MATRIX.md
docs/DECISIONS.md
```
Do not silently invent missing requirements.
When a non-critical requirement is missing:
1. Record it as `TBD`.
2. Document the safest reversible assumption.
3. Place the assumption behind configuration.
4. Continue with work that does not depend on human judgment.
---
# 7. SYSTEM ARCHITECTURE
Use the following logical flow:
```text
Exchange Public WebSockets
        ↓
Normalized Market Events
        ↓
Local Order-Book Engine
        ↓
Currency Graph
        ↓
Triangle Discovery
        ↓
Depth-Aware Opportunity Simulation
        ↓
Profitability Gate
        ↓
Central Risk Engine
        ↓
Execution Planner
        ↓
Exchange Adapter
        ↓
Private Order and Fill Streams
        ↓
Reconciliation and Recovery
        ↓
Profit and Audit Ledger
```
Create clean interfaces between every stage.
The opportunity detector must never call an exchange trading endpoint directly.
The execution engine must never bypass the central risk engine.
---
# 8. EXCHANGE ADAPTER CONTRACT
Create one common spot-exchange interface.
Each adapter must implement:
```text
connectPublicWebSocket()
connectPrivateWebSocket()
disconnect()
healthCheck()
fetchServerTime()
synchronizeClock()
fetchSpotInstruments()
fetchTradingRules()
fetchAccountFeeRates()
fetchBalances()
fetchOpenOrders()
fetchOrder()
fetchTradeHistory()
subscribeOrderBooks()
subscribeTickers()
subscribeOrders()
subscribeExecutions()
subscribeBalances()
placeMarketOrder()
placeLimitOrder()
placeIocOrder()
placeFokOrder()
cancelOrder()
cancelAllOrders()
normalizeSymbol()
normalizeAsset()
normalizeOrder()
normalizeFill()
normalizeBalance()
normalizeError()
reconcileOrder()
reconcileBalances()
reconcileCompleteAccountState()
```
Do not assume all exchanges support identical order types.
Build an exchange capability matrix containing:
| Capability            | Bitget |  Bybit |    OKX | Binance |
| --------------------- | -----: | -----: | -----: | ------: |
| Spot market orders    | Verify | Verify | Verify |  Verify |
| Spot limit orders     | Verify | Verify | Verify |  Verify |
| IOC                   | Verify | Verify | Verify |  Verify |
| FOK                   | Verify | Verify | Verify |  Verify |
| Client order ID       | Verify | Verify | Verify |  Verify |
| Private order stream  | Verify | Verify | Verify |  Verify |
| Private fill stream   | Verify | Verify | Verify |  Verify |
| Demo/test environment | Verify | Verify | Verify |  Verify |
| Account fee endpoint  | Verify | Verify | Verify |  Verify |
| WebSocket order entry | Verify | Verify | Verify |  Verify |
| Batch order support   | Verify | Verify | Verify |  Verify |
"Verify" means Claude must consult the current official documentation and write an automated capability test.
Never assume support based on another exchange.
---
# 9. SYMBOL AND TRADING-RULE NORMALIZATION
For every spot instrument, normalize:
```text
exchange
exchange_symbol
canonical_symbol
base_asset
quote_asset
status
price_tick_size
quantity_step_size
minimum_quantity
maximum_quantity
minimum_notional
maximum_notional
market_order_minimum
market_order_maximum
maker_fee
taker_fee
supported_order_types
trading_enabled
```
Trading rules must be refreshed:
* At startup
* On a schedule
* After relevant exchange errors
* After instrument-status updates
* Before live activation
Reject a route when any leg:
* Is suspended
* Is offline
* Is in pre-open status
* Does not support spot trading
* Does not meet minimum quantity
* Does not meet minimum notional
* Exceeds maximum quantity
* Violates price increments
* Violates quantity increments
All rounding must be deterministic.
Never round a quantity upward when it could spend more than the available balance.
---
# 10. DIRECTED CURRENCY GRAPH
Represent every spot market as two directed conversion edges.
For a market:
```text
BASE/QUOTE
```
Create:
## Sell edge
```text
BASE → QUOTE
```
This conversion sells the base asset into the quote asset.
It consumes bids from the order book.
## Buy edge
```text
QUOTE → BASE
```
This conversion buys the base asset using the quote asset.
It consumes asks from the order book.
Each edge must contain:
```text
exchange
symbol
source_asset
destination_asset
side
order_book_reference
fee_model
trading_rules
status
last_update_time
sequence_number
```
Discover every unique three-edge cycle:
```text
A → B → C → A
```
Do not hard-code only USDT triangles.
Allow configurable settlement assets.
Deduplicate equivalent cycles while preserving both trading directions.
---
# 11. TRIANGULAR ARBITRAGE CALCULATION
Do not calculate profitability from:
* Last trade price
* Mid-market price
* Candles
* Displayed percentage changes
* A single top-of-book price when the desired quantity exceeds that level
Use actual executable order-book depth.
For each route:
```text
Starting Amount
    ↓
Leg 1 depth simulation
    ↓
Leg 1 fees and rounding
    ↓
Leg 2 depth simulation
    ↓
Leg 2 fees and rounding
    ↓
Leg 3 depth simulation
    ↓
Leg 3 fees and rounding
    ↓
Final Settlement Amount
```
Calculate:
```text
gross_return
exchange_fees
estimated_slippage
rounding_loss
latency_reserve
failure_reserve
expected_net_profit
expected_net_profit_bps
```
The core decision is:
```text
Expected Final Settlement Amount
>
Starting Settlement Amount
+
All Costs
+
Required Profit
+
Safety Reserve
```
Use:
```text
net_return =
final_settlement_amount / initial_settlement_amount - 1
```
But calculate each leg using actual depth, not a simplified multiplication of ticker prices.
---
# 12. FEE MODEL
Fees must be calculated for every individual fill.
Account for:
* Taker fees
* Maker fees
* VIP fee tiers
* Exchange-token fee discounts
* Fee asset
* Fee charged in base
* Fee charged in quote
* Fee charged in another asset
* Referral or account-specific discounts
* Fee-rate changes
* Partial-fill fees
Do not assume fees are always deducted from the output asset.
If the exact fee rate cannot be retrieved:
1. Use a configurable conservative rate.
2. Mark the result as estimated.
3. Add a safety buffer.
4. Do not activate live mode until fee treatment is validated.
Store both:
```text
estimated_fee
actual_fee
actual_fee_asset
```
Compare expected and actual fees after every cycle.
---
# 13. ORDER-BOOK ENGINE
Maintain a local order book for every required symbol.
Each book must track:
* Initial snapshot
* Incremental updates
* Sequence numbers
* Update timestamps
* Bid levels
* Ask levels
* Checksum where available
* Connection identity
* Data age
* Synchronization status
A book is invalid when:
* A sequence gap occurs
* An update arrives out of order
* The snapshot is missing
* The checksum fails
* Data is older than the configured threshold
* The WebSocket reconnects without resynchronization
* The exchange reports maintenance
* Time synchronization exceeds the allowed limit
When invalid:
1. Mark the book unsynchronized.
2. Stop using it for opportunities.
3. Fetch a new snapshot.
4. Reapply valid updates.
5. Resume only after validation.
Measure cross-book timestamp skew.
Reject a triangle when its three books are not temporally consistent.
---
# 14. DEPTH-AWARE TRADE SIZING
The most profitable trade size may not be the largest possible trade size.
Build an optimizer that evaluates candidate starting amounts against all three books.
Account for:
* Available balance
* Depth at every leg
* Price movement through depth
* Minimum order quantities
* Minimum notionals
* Step sizes
* Fees
* Rounding
* Maximum order sizes
* Maximum allocation per cycle
* Maximum participation in visible depth
* Slippage limits
Determine:
```text
maximum_executable_size
maximum_safe_size
most_profitable_size
expected_profit_for_selected_size
```
Do not use the full account balance by default.
Use configurable bounds:
```text
minimum_cycle_size
maximum_cycle_size
maximum_percent_of_available_balance
maximum_percent_of_visible_depth
maximum_expected_slippage_bps
```
---
# 15. PROFITABILITY GATE
An opportunity is executable only when all gates pass.
Required gates:
```text
All books synchronized
All books fresh
Timestamp skew acceptable
All symbols online
Trading rules loaded
Fee information loaded
Balances sufficient
Risk limits satisfied
Expected profit above threshold
Expected profit above uncertainty reserve
Expected slippage below limit
Cycle size above all minimums
Cycle size below all maximums
Exchange API healthy
Private order stream healthy
No unresolved reconciliation error
No active kill switch
```
Use configurable thresholds:
```text
MIN_EXPECTED_NET_PROFIT_BPS
MIN_EXPECTED_NET_PROFIT_ABSOLUTE
MAX_BOOK_AGE_MS
MAX_BOOK_SKEW_MS
MAX_SLIPPAGE_BPS
LATENCY_RESERVE_BPS
FAILURE_RESERVE_BPS
MAX_CYCLE_NOTIONAL
```
Do not lower thresholds automatically to create more trades.
---
# 16. EXECUTION MODES
Support two execution architectures.
## Mode A: Sequential closed-loop execution
This is the default.
```text
Leg 1 submitted
→ actual fill confirmed
→ Leg 2 quantity calculated from actual Leg 1 output
→ Leg 2 submitted
→ actual fill confirmed
→ Leg 3 quantity calculated from actual Leg 2 output
→ Leg 3 submitted
→ final settlement reconciled
```
Do not calculate later legs from requested quantities.
Use actual fills.
Advantages:
* Lower inventory requirement
* Easier accounting
* Simpler recovery
Risks:
* Opportunity can disappear during the sequence
* Later legs can become unprofitable
## Mode B: Inventory-assisted parallel execution
This mode may be considered only after sequential execution is proven.
The account maintains pre-positioned balances of each triangle asset.
All three legs can be submitted close to simultaneously.
This reduces sequential latency but creates:
* Inventory risk
* Rebalancing requirements
* More complex accounting
* Greater capital requirements
* Potential unmatched fills
It must remain disabled until:
* Paper tests pass
* Shadow tests pass
* Inventory controls pass
* Recovery tests pass
* Human authorization is recorded
---
# 17. ORDER TYPE STRATEGY
Do not rely on unrestricted market orders by default.
Preferred approach:
```text
Marketable IOC limit orders with price protection
```
The execution planner should:
1. Simulate required book depth.
2. Determine the worst acceptable execution price.
3. Apply a strict slippage ceiling.
4. Round the price correctly.
5. Submit an IOC or supported protected order.
6. Confirm actual fills through private execution events.
7. Reconcile through REST when events are uncertain.
FOK may be used only when:
* Officially supported for the symbol and exchange
* Exchange behavior is verified
* The fill requirement is appropriate
* Failure does not create additional exposure
Never assume an accepted order acknowledgement means the order is filled.
---
# 18. EXECUTION STATE MACHINE
Implement every arbitrage cycle as a persisted state machine.
Suggested states:
```text
DETECTED
VALIDATING
APPROVED
RESERVING_BALANCES
SUBMITTING_LEG_1
WAITING_LEG_1
LEG_1_PARTIAL
LEG_1_FILLED
SUBMITTING_LEG_2
WAITING_LEG_2
LEG_2_PARTIAL
LEG_2_FILLED
SUBMITTING_LEG_3
WAITING_LEG_3
LEG_3_PARTIAL
LEG_3_FILLED
RECONCILING
COMPLETED
RECOVERY_REQUIRED
RECOVERING
RECOVERED
FAILED
MANUAL_INTERVENTION
```
Every transition must record:
* Cycle ID
* Previous state
* New state
* Timestamp
* Exchange
* Triangle
* Input amount
* Expected output
* Actual output
* Order IDs
* Fill IDs
* Fees
* Reason code
* Market snapshot reference
State transitions must be idempotent.
A process restart must resume or safely reconcile incomplete cycles.
---
# 19. CLIENT ORDER IDENTIFIERS
Generate deterministic unique client order IDs containing:
```text
bot identifier
exchange
cycle identifier
leg number
attempt number
strategy version
```
Requirements:
* Never reuse an active identifier
* Respect exchange length and character limits
* Allow recovery after restart
* Prevent duplicate submission
* Link all three orders to one cycle
* Link recovery orders to the failed cycle
Before resubmitting after an uncertain response:
1. Query by client order ID.
2. Inspect the private order stream.
3. Inspect trade executions.
4. Reconcile account balances.
5. Submit only when non-execution is proven.
---
# 20. PARTIAL-FILL HANDLING
A partial fill is not a normal successful leg.
When a leg partially fills:
1. Cancel the remaining quantity if still open.
2. Confirm the cancellation.
3. Calculate actual acquired and remaining assets.
4. Recalculate the remaining route using fresh order books.
5. Determine whether continuing remains inside loss limits.
6. Continue only when the risk engine approves.
7. Otherwise initiate recovery.
8. Record all residual dust.
Never submit the originally planned next-leg quantity after a partial fill.
Use only the actual available amount.
---
# 21. RECOVERY AND UNWIND ENGINE
Every possible failure must have a documented recovery path.
Failure examples:
* Leg 1 fills and Leg 2 fails
* Leg 2 partially fills
* Leg 3 rejects
* WebSocket disconnects
* REST request times out
* Order status becomes unknown
* Balance changes unexpectedly
* Symbol is suspended
* Price gaps beyond the limit
* Exchange enters maintenance
* Rate limit is reached
* API credential is revoked
Recovery process:
```text
Freeze new cycles on the affected exchange
→ reconcile orders
→ reconcile fills
→ reconcile balances
→ calculate unintended exposure
→ evaluate safe return paths
→ choose the lowest-risk approved unwind
→ execute protected recovery orders
→ reconcile again
→ generate incident record
```
Recovery priorities:
1. Prevent duplicate orders.
2. Determine the true exchange state.
3. Contain exposure.
4. Return to the configured settlement asset when reasonably safe.
5. Limit loss.
6. Preserve evidence.
7. Require human intervention when uncertainty remains.
Do not blindly market-sell during recovery.
Calculate the safest available route and enforce a maximum recovery-loss threshold.
---
# 22. BALANCE RESERVATION
Prevent concurrent cycles from spending the same funds.
Implement atomic internal reservations for:
* Starting settlement asset
* Expected intermediate quantities
* Fee reserves
* Recovery reserves
Use a per-exchange, per-account and per-asset locking model.
A new cycle cannot use:
* Locked funds
* Funds committed to open orders
* Recovery reserves
* Minimum operational reserves
* Unreconciled balances
Release reservations only after confirmed completion or recovery.
---
# 23. CENTRAL RISK ENGINE
No cycle may bypass the risk engine.
Evaluate:
* Exchange health
* Market-data health
* Private-stream health
* Available balances
* Reserved balances
* Cycle notional
* Daily turnover
* Daily profit
* Daily loss
* Consecutive failures
* Unresolved cycles
* Recovery exposure
* Slippage
* Data latency
* API latency
* Reconciliation mismatches
* Per-symbol exposure
* Per-asset exposure
* Per-exchange exposure
* Maximum concurrent cycles
Required controls:
```text
Maximum amount per cycle
Maximum percentage of account balance per cycle
Maximum simultaneous cycles
Maximum daily traded volume
Maximum daily loss
Maximum recovery loss
Maximum consecutive failed cycles
Maximum unresolved exposure
Maximum order latency
Maximum fill latency
Maximum slippage
Maximum book age
Maximum book timestamp skew
```
Return a structured decision:
```json
{
  "approved": false,
  "reason_codes": [],
  "adjusted_starting_amount": null,
  "risk_snapshot_id": "",
  "timestamp": ""
}
```
Risk decisions must be immutable.
---
# 24. KILL SWITCHES
Implement:
* Global kill switch
* Exchange kill switch
* Account kill switch
* Triangle kill switch
* Symbol kill switch
* Asset kill switch
* Strategy-version kill switch
* Automatic daily-loss kill switch
* Automatic repeated-failure kill switch
* Automatic stale-data kill switch
* Automatic reconciliation kill switch
* Automatic exchange-disconnection kill switch
When activated:
1. Block new cycles.
2. Prevent new normal orders.
3. Preserve recovery capability.
4. Cancel safe-to-cancel open orders.
5. Reconcile balances and orders.
6. Notify operators.
7. Record the reason.
8. Require authorized restart.
---
# 25. PAPER-TRADING ENGINE
Paper mode must use the same:
* Currency graph
* Opportunity engine
* Fee engine
* Rounding logic
* Risk engine
* Execution state machine
* Recovery state machine
Only the exchange execution adapter should be simulated.
Simulate:
* WebSocket latency
* REST latency
* Decision latency
* Order-entry latency
* Queue position
* Price movement
* Slippage
* Partial fills
* Rejections
* Rate limits
* Trading fees
* Missing liquidity
* Minimum-order errors
* Disconnects
* Process restarts
Do not assume that touching a price guarantees a fill.
---
# 26. MARKET REPLAY
Create a deterministic replay framework.
It must replay:
* Recorded order-book snapshots
* Incremental order-book events
* Private-order events
* Fill events
* Connection failures
* Exchange errors
The same event sequence and configuration must produce the same result.
Use replay tests to evaluate:
* False opportunities
* Opportunity duration
* Executable profit
* Slippage
* Partial-fill frequency
* Recovery losses
* Latency sensitivity
* Size sensitivity
* Fee sensitivity
---
# 27. PERFORMANCE MEASUREMENT
Track separately:
```text
theoretical opportunities
depth-qualified opportunities
risk-approved opportunities
submitted cycles
completed cycles
recovered cycles
failed cycles
false positives
```
Track:
* Gross profit
* Trading fees
* Slippage
* Rounding loss
* Recovery loss
* Net realized profit
* Profit in basis points
* Opportunity-to-order latency
* Order-to-fill latency
* Full-cycle latency
* Fill ratio
* Partial-fill rate
* Rejection rate
* Recovery rate
* Profit by exchange
* Profit by triangle
* Profit by starting size
* Profit by hour
* Profit by strategy version
Never mix:
* Theoretical profit
* Simulated profit
* Shadow profit
* Live realized profit
---
# 28. ADMINISTRATIVE DASHBOARD
Create screens for:
* Exchange connection status
* API permission status
* Current balances
* Reserved balances
* Active triangles
* Current opportunities
* Executing cycles
* Incomplete cycles
* Recovery cycles
* Recent fills
* Net realized results
* Fees
* Slippage
* Exchange latency
* Order-book health
* Reconciliation health
* Kill switches
* Risk-limit configuration
* Paper/live mode
* Audit events
* Incidents
Display every financial result with its mode:
```text
THEORETICAL
PAPER
SHADOW
LIVE
```
---
# 29. SECRET AND API-KEY SECURITY
API keys must have only:
* Read access
* Spot trading permission
Reject or warn against:
* Withdrawal permission
* Unnecessary margin permission
* Unnecessary futures permission
* Unrestricted IP access where allowlisting is supported
Implement:
* Application-layer secret encryption
* Managed key storage where available
* Key rotation
* Secret redaction
* Role-based access
* Administrator step-up authentication
* Audit logs
* IP allowlisting support
* Credential health monitoring
Never expose full credentials in:
* Logs
* Errors
* Browser responses
* Metrics
* Traces
* Database query output
* Screenshots
* Support exports
---
# 30. MULTI-TENANCY
When supporting multiple users or organizations:
* Separate exchange credentials
* Separate balances
* Separate cycles
* Separate risk settings
* Separate audit logs
* Separate performance
* Separate encryption contexts
Create automated tests proving one tenant cannot access another tenant's:
* Keys
* Exchanges
* Balances
* Orders
* Cycles
* Opportunities
* Reports
* Risk settings
---
# 31. REQUIRED INVARIANTS
Create automated tests proving:
1. A cycle cannot execute using stale order books.
2. A cycle cannot execute using an unsynchronized book.
3. All three legs belong to the same exchange.
4. Every leg uses the actual fill from the previous leg.
5. All fees are included.
6. All quantities satisfy exchange increments.
7. All orders satisfy minimum quantities.
8. All orders satisfy minimum notionals.
9. Locked funds cannot be double spent.
10. A timeout cannot automatically cause duplicate submission.
11. An accepted order is not treated as filled.
12. Partial fills trigger recalculation.
13. Every live order passes the risk engine.
14. Every live order has an immutable audit record.
15. Every incomplete cycle survives process restart.
16. Withdrawal-enabled keys are rejected.
17. Live mode remains disabled by default.
18. A kill switch blocks new cycles.
19. Recovery orders are linked to their original cycle.
20. Theoretical profit is never presented as realized profit.
21. Binary floating-point arithmetic is never used for money.
22. No LLM participates in the live execution path.
23. An exchange adapter cannot bypass central execution controls.
24. One tenant cannot access another tenant's information.
---
# 32. TESTING REQUIREMENTS
## Unit tests
Test:
* Directed-edge calculations
* Sell conversions
* Buy conversions
* Fee deduction
* Fee assets
* Decimal precision
* Price rounding
* Quantity rounding
* Minimum notionals
* Route generation
* Route deduplication
* Depth simulation
* Optimal sizing
* Profitability gates
* Risk rules
* State transitions
* Balance reservations
## Property-based tests
Prove:
* Output never exceeds mathematically possible depth
* Increasing fees cannot increase expected profit
* Increasing slippage cannot increase expected profit
* Rounding cannot create artificial funds
* A cycle cannot spend more than its reserved balance
* Duplicate events cannot create duplicate orders
* The accounting ledger remains balanced
## Adapter contract tests
Every exchange adapter must pass the same contract suite.
## Integration tests
Test:
* Public WebSockets
* Private WebSockets
* REST authentication
* Order placement
* Order cancellation
* Fill processing
* Balance synchronization
* Database persistence
* Restart recovery
## End-to-end tests
Test complete cycles in:
* Market-data mode
* Paper mode
* Shadow mode
* Exchange demo/test environments where supported
## Chaos tests
Inject:
* WebSocket disconnect
* REST timeout
* Order acknowledgement loss
* Duplicate fill
* Out-of-order fill
* Partial fill
* Exchange error
* Rate limit
* Database restart
* Process crash
* Stale book
* Sequence gap
* Clock drift
* API-key revocation
* Symbol suspension
---
# 33. LIVE-TRADING QUALIFICATION
Do not enable live trading until the bot completes a defined qualification period.
The qualification configuration must include:
```text
minimum paper-trading duration
minimum number of simulated cycles
minimum shadow-trading duration
minimum number of qualified opportunities
maximum false-positive rate
maximum partial-fill rate
maximum recovery-failure rate
maximum reconciliation-error rate
maximum latency
maximum drawdown
minimum positive net result after simulated costs
```
Passing the financial threshold alone is insufficient.
The bot must also prove:
* Correct accounting
* Stable connectivity
* Correct rounding
* Correct fee handling
* Successful restart recovery
* Reliable kill switches
* Reliable reconciliation
---
# 34. CLAUDE SUBAGENTS
Create specialized subagents.
## Exchange Documentation Agent
Responsibilities:
* Consult current official exchange documentation
* Maintain capability matrix
* Detect API changes
* Verify parameter rules
* Verify test environments
## Market-Data Engineer
Responsibilities:
* WebSockets
* Order books
* Sequence handling
* Checksums
* Data freshness
## Quantitative Reviewer
Responsibilities:
* Currency graph
* Route mathematics
* Fee calculations
* Depth simulation
* Size optimization
* Statistical evaluation
## Execution Engineer
Responsibilities:
* State machine
* Order planning
* IOC/FOK behavior
* Partial fills
* Idempotency
* Reconciliation
## Risk Engineer
Responsibilities:
* Exposure limits
* Loss limits
* Kill switches
* Recovery limits
* Live qualification
## Security Engineer
Responsibilities:
* Credentials
* Encryption
* Authentication
* Authorization
* Tenant isolation
* Threat modeling
## QA and Chaos Engineer
Responsibilities:
* Test plan
* Failure injection
* Restart testing
* Exchange adapter contracts
* Regression protection
## Adversarial Reviewer
Attempt to prove that:
* Profit is overstated
* Fees are missing
* Slippage is underestimated
* Books are stale
* Orders can duplicate
* Balances can be double spent
* Recovery can worsen exposure
* Live mode can be activated improperly
The agent implementing a critical component must not be its only reviewer.
---
# 35. CLAUDE ITERATION LOOP
At the start of every iteration:
1. Read `CLAUDE.md`.
2. Read `PROGRESS.md`.
3. Read the feature matrix.
4. Inspect the Git status and diff.
5. Inspect failing tests.
6. Inspect unresolved incidents.
7. Inspect incomplete critical requirements.
8. Select the highest-priority unblocked task.
Priority:
```text
P0 credential or security vulnerability
P0 duplicate-order risk
P0 accounting error
P0 risk-engine bypass
P0 unrecoverable exposure
P0 incorrect order-book state
P1 exchange-adapter failure
P1 failed invariant
P1 reconciliation failure
P1 missing critical test
P2 core feature
P2 observability
P3 optimization
P3 dashboard improvement
```
For each iteration:
## Step 1: Declare objective
Record:
```text
Feature ID
Acceptance criterion
Expected files
Risks
Required tests
Required reviewers
```
## Step 2: Inspect before editing
Understand the existing design.
Do not rewrite working code unnecessarily.
## Step 3: Implement the smallest complete vertical slice
Include where relevant:
* Domain model
* Adapter logic
* Persistence
* API
* Tests
* Metrics
* Audit events
* Documentation
## Step 4: Run focused tests
Validate the immediate hypothesis.
## Step 5: Run broad tests
Run:
* Formatting
* Linting
* Compilation
* Unit tests
* Property tests
* Integration tests
* Contract tests
* Security scans
* Replay tests
* Chaos tests where relevant
## Step 6: Independent review
Critical components require review by:
* Quantitative Reviewer
* Execution Engineer
* Risk Engineer
* Security Engineer
* QA Engineer
* Adversarial Reviewer
## Step 7: Correct findings
Do not dismiss findings without evidence.
## Step 8: Review the complete diff
Check for:
* Unsafe defaults
* Missing fees
* Incorrect rounding
* Hidden floating-point use
* Secret exposure
* Duplicate-order risk
* Missing recovery
* Unrelated changes
* Inaccurate documentation
## Step 9: Update durable memory
Update `PROGRESS.md` with:
```text
Iteration
Feature
Objective
Work completed
Files changed
Tests run
Results
Review findings
Assumptions
Risks remaining
Blockers
Next best action
```
## Step 10: Continue or stop
Continue while approved, safe and unblocked work remains.
---
# 36. FAILED-ATTEMPT CONTROL
Do not repeat a failed approach indefinitely.
After each failure:
1. Record the exact error.
2. Classify it.
3. Identify the likely root cause.
4. Form a testable hypothesis.
5. Make the smallest correction.
6. Run the narrowest useful test.
7. Record the outcome.
After three materially similar failed attempts:
1. Stop repeating the same approach.
2. Document all attempts.
3. Mark the task blocked.
4. Identify the missing information.
5. Continue with another safe task.
Do not:
* Delete tests to obtain a green build
* Increase slippage limits to hide poor execution
* Ignore fees
* Disable risk controls
* Treat unknown order states as failed orders
* Resubmit uncertain orders without reconciliation
---
# 37. SUCCESSFUL RELEASE CONDITIONS
The system may be declared a release candidate only when:
* All supported exchange adapters pass contract tests
* All critical invariants pass
* Order-book reconstruction is verified
* Fee calculations are verified
* Precision and rounding are verified
* Paper mode is stable
* Shadow mode is stable
* Restart recovery works
* Partial-fill recovery works
* Duplicate-order prevention works
* Kill switches work
* Reconciliation works
* Security review passes
* Risk review passes
* Chaos testing passes
* Documentation matches implementation
* Live mode remains disabled
* No unresolved critical defect remains
* An independent release reviewer approves
---
# 38. HUMAN AUTHORIZATION REQUIRED
Pause consequential work before:
* Activating live trading
* Using real exchange credentials
* Deploying to production
* Increasing capital limits
* Weakening risk limits
* Enabling inventory-assisted parallel execution
* Enabling automatic recovery trades
* Changing jurisdiction controls
* Running destructive migrations
* Merging into a protected production branch
Do not ask for authorization merely to continue safe development, testing or documentation.
---
# 39. FINAL REPORT
Produce:
1. Executive summary
2. Architecture summary
3. Exchange capability matrix
4. Implemented feature inventory
5. Deferred feature inventory
6. Supported triangles
7. Fee-handling report
8. Precision and rounding report
9. Paper-trading report
10. Shadow-trading report
11. Latency report
12. Slippage report
13. Partial-fill report
14. Recovery report
15. Security report
16. Risk report
17. Test evidence
18. Known limitations
19. Deployment procedure
20. Rollback procedure
21. Incident-response procedure
22. Human approvals still required
23. Evidence that live mode remains disabled
---
# 40. FIRST ACTION
Begin with discovery.
Your first response must:
1. Summarize the repository.
2. Identify the current technology stack.
3. Identify current working features.
4. Identify missing critical infrastructure.
5. Verify the latest official API documentation for all four exchanges.
6. Create the exchange capability matrix.
7. Create the feature traceability matrix.
8. Define the directed currency-graph model.
9. Write the exact fee-aware, depth-aware triangle calculation.
10. Define the execution state machine.
11. Define the first paper-trading milestone.
12. Begin only the safest first vertical slice.
The safest first vertical slice is:
```text
Binance or selected exchange public market data
→ instrument discovery
→ directed currency graph
→ triangle discovery
→ synchronized local order books
→ fee-aware depth simulation
→ opportunity reporting
```
Do not place orders during the first milestone.
---
# 41. CLAUDE CODE GOAL COMMAND
After saving this prompt in `CLAUDE.md`, run:
```text
/goal Build the approved multi-exchange spot triangular arbitrage platform described in CLAUDE.md. Work iteratively through discovery, architecture, deterministic currency-graph calculations, synchronized order books, fee-aware depth simulation, paper trading, exchange adapters, execution state machines, recovery, reconciliation, risk controls, security, testing, and documentation.
Support Bitget, Bybit, OKX Global, and Binance Global as independent intra-exchange venues.
Continue until every approved acceptance criterion and critical invariant has objective test evidence and the release-candidate report is complete.
Do not activate live trading, use real-money credentials, deploy to production, weaken risk controls, enable withdrawals, or enable inventory-assisted parallel execution without explicit human authorization.
```
