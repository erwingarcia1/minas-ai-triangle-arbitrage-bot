# Execution Engine — design specification (master prompt §16–§20)

> **STATUS: DESIGN DOCUMENT — NOTHING IN THIS FILE IS IMPLEMENTED.**
> No execution code exists in this build. The repository contains no order,
> balance, withdrawal, or transfer code of any kind, and the mode gate in
> `triarb/config/models.py` (`ensure_supported_mode`) refuses every mode that
> would need an execution path: `PAPER_TRADING`, `SHADOW_TRADING`, and
> `LIVE_TRADING` all fail at startup. Only `MARKET_DATA_ONLY` starts
> (**Implemented** — `triarb/config/models.py`, tests in
> `tests/test_config.py::TestModeGate`). Everything below is the governing
> design for later phases, cited to the master prompt sections in
> `CLAUDE.md` Part B.

## 1. Scope and placement

**Planned** (CLAUDE.md §7): the execution engine sits between the Central
Risk Engine and the Exchange Adapter in the logical flow. Two rules from §7
are absolute:

- The opportunity detector never calls an exchange trading endpoint directly.
- The execution engine never bypasses the central risk engine
  (see `docs/RISK_ENGINE.md`).

**Planned** (CLAUDE.md §4): the live engine must be deterministic, versioned,
auditable, reproducible, and independently testable. Claude or another
language model must not decide whether to submit a live order, calculate the
final live order quantity, alter risk limits during execution, approve its
own live trades, generate unvalidated prices, or participate in the
low-latency execution path (Invariant 22).

**Implemented** today, the only execution-adjacent artifacts are read-only:

- `triarb/pricing/feasibility.py` simulates, in exact `Decimal`, the three
  IOC limit orders a Phase 2 executor *would* place (protected limit price,
  step-rounded quantity, per-leg fee treatment) and rejects unroundable
  cycles. It produces `LegPlan(symbol, side, limit_price, quantity, received)`
  records but sends nothing anywhere.
- `triarb/exchange/base.py` deliberately contains **no** order, balance,
  withdrawal, or transfer methods; the trading methods listed in §8
  (`placeIocOrder`, `cancelOrder`, …) are **Planned** and arrive only behind
  the phase gates.

## 2. Execution modes (§16)

### Mode A: Sequential closed-loop execution — the default

**Planned** (CLAUDE.md §16, Mode A):

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

Later legs are **never** calculated from requested quantities — only from
actual fills (Invariant 4). Between legs the remaining route is re-validated
against fresh order books (see §6 below and `docs/RECOVERY_ENGINE.md`).

Advantages (per §16): lower inventory requirement, easier accounting,
simpler recovery. Risks (per §16): the opportunity can disappear during the
sequence, and later legs can become unprofitable — which is why every
inter-leg step passes back through the risk engine.

### Mode B: Inventory-assisted parallel execution — disabled until authorized

**Planned** (CLAUDE.md §16, Mode B): pre-positioned balances of each triangle
asset allow all three legs to be submitted close to simultaneously, removing
sequential latency at the cost of inventory risk, rebalancing requirements,
more complex accounting, greater capital requirements, and potential
unmatched fills.

Mode B **must remain disabled** until all of the following hold (§16), and
enabling it is one of the actions that always requires recorded human
authorization (§38):

1. Paper tests pass.
2. Shadow tests pass.
3. Inventory controls pass.
4. Recovery tests pass.
5. Human authorization is recorded.

No configuration flag for Mode B will exist before Mode A is proven; when it
does, it defaults to off and cannot be flipped by config alone.

## 3. Order type strategy (§17)

**Planned** (CLAUDE.md §17). The default order type is:

```text
Marketable IOC limit orders with price protection
```

Unrestricted market orders are not the default. The execution planner:

1. Simulates required book depth.
2. Determines the worst acceptable execution price.
3. Applies a strict slippage ceiling.
4. Rounds the price correctly.
5. Submits an IOC or supported protected order.
6. Confirms actual fills through private execution events.
7. Reconciles through REST when events are uncertain.

**Implemented** precursor: steps 1–4 already exist as a read-only simulation
in `triarb/pricing/feasibility.py` — a BUY leg's protected limit price is the
worst walked ask rounded **up** to the venue price tick (stays marketable),
a SELL leg's is the worst walked bid rounded **down**; quantities round
**down** to the venue quantity step (never upward past spendable balance,
per §9). The Phase 2 planner reuses this logic; it does not reimplement it.

FOK may be used only when (§17): it is officially supported for the symbol
and exchange, exchange behavior is verified, the fill requirement is
appropriate, and failure does not create additional exposure. Per-venue
IOC/FOK support is documented in `docs/EXCHANGE_CAPABILITY_MATRIX.md`
(doc-verified for all four venues on 2026-07-11; runtime behavior still
needs the automated capability test that lands with the trading surface) —
never assumed from another exchange (§8).

**Never assume an accepted order acknowledgement means the order is filled**
(§17, Invariant 11). Fill state comes only from private execution events,
reconciled via REST when uncertain.

## 4. Execution state machine (§18)

**Planned** (CLAUDE.md §18): every arbitrage cycle is a persisted state
machine with exactly these 23 states:

| # | State | Meaning |
|---|---|---|
| 1 | `DETECTED` | Opportunity engine emitted a depth-qualified candidate |
| 2 | `VALIDATING` | Books, rules, fees, and freshness re-checked |
| 3 | `APPROVED` | Central risk engine returned `approved: true` |
| 4 | `RESERVING_BALANCES` | Atomic reservations being taken (see `docs/RISK_ENGINE.md` §22) |
| 5 | `SUBMITTING_LEG_1` | Leg 1 order in flight to the venue |
| 6 | `WAITING_LEG_1` | Awaiting private-stream fill events for leg 1 |
| 7 | `LEG_1_PARTIAL` | Leg 1 partially filled — §20 procedure applies |
| 8 | `LEG_1_FILLED` | Leg 1 fully filled and confirmed |
| 9 | `SUBMITTING_LEG_2` | Leg 2 in flight, sized from **actual** leg 1 output |
| 10 | `WAITING_LEG_2` | Awaiting leg 2 fills |
| 11 | `LEG_2_PARTIAL` | Leg 2 partially filled — §20 procedure applies |
| 12 | `LEG_2_FILLED` | Leg 2 fully filled and confirmed |
| 13 | `SUBMITTING_LEG_3` | Leg 3 in flight, sized from **actual** leg 2 output |
| 14 | `WAITING_LEG_3` | Awaiting leg 3 fills |
| 15 | `LEG_3_PARTIAL` | Leg 3 partially filled — §20 procedure applies |
| 16 | `LEG_3_FILLED` | Leg 3 fully filled and confirmed |
| 17 | `RECONCILING` | Final settlement compared against fills, fees, balances |
| 18 | `COMPLETED` | Cycle reconciled clean; reservations released |
| 19 | `RECOVERY_REQUIRED` | A failure left unintended exposure — hand off to recovery |
| 20 | `RECOVERING` | Recovery engine executing the approved unwind |
| 21 | `RECOVERED` | Exposure returned to settlement asset within loss limits |
| 22 | `FAILED` | Cycle ended with a recorded loss or terminal error |
| 23 | `MANUAL_INTERVENTION` | Uncertainty remains; a human must resolve it |

### Transition records

**Planned** (§18): every transition records all of:

```text
Cycle ID
Previous state
New state
Timestamp
Exchange
Triangle
Input amount
Expected output
Actual output
Order IDs
Fill IDs
Fees
Reason code
Market snapshot reference
```

All amounts in transition records are `Decimal` — binary floats are never
authoritative for money (Invariant 21; the money rule is already enforced in
the observer, see `CLAUDE.md` Part A and `triarb/pricing/feasibility.py`).

### Idempotency and restart resume

**Planned** (§18):

- State transitions must be idempotent: replaying a duplicate fill event or
  a duplicate transition command cannot advance the machine twice or emit a
  second order (property test target: "duplicate events cannot create
  duplicate orders", §32).
- A process restart must resume or safely reconcile every incomplete cycle
  (Invariant 15). On startup the engine loads all cycles not in a terminal
  state (`COMPLETED`, `RECOVERED`, `FAILED`), queries the venue by client
  order ID, and either resumes, reconciles, or escalates to
  `RECOVERY_REQUIRED` / `MANUAL_INTERVENTION`. It never re-submits on the
  basis of in-memory state alone.
- **TBD** — persistence backend for the state machine. §5 recommends
  PostgreSQL; this repository has no database yet. Safest reversible
  assumption, recorded per CLAUDE.md §6: an append-only local write-ahead
  journal (JSON lines, `Decimal` serialized as strings) behind a
  configuration key, upgradeable to PostgreSQL without changing transition
  semantics.

## 5. Client order identifiers (§19)

**Planned** (CLAUDE.md §19): deterministic unique client order IDs containing:

```text
bot identifier
exchange
cycle identifier
leg number
attempt number
strategy version
```

Requirements (§19, verbatim intent):

- Never reuse an active identifier.
- Respect exchange length and character limits.
- Allow recovery after restart.
- Prevent duplicate submission.
- Link all three orders to one cycle.
- Link recovery orders to the failed cycle (Invariant 19; see
  `docs/RECOVERY_ENGINE.md`).

**TBD** — per-venue client order ID length and character-set limits for
Bitget, Bybit, OKX, and Binance are "Verify" items pending confirmation in
`docs/EXCHANGE_CAPABILITY_MATRIX.md`; they must come from current official
documentation plus an automated capability test, never assumed (§8). Safest
reversible assumption, recorded per CLAUDE.md §6: encode the six components
into an ID capped at **32 characters** drawn from `[A-Za-z0-9-]` (a
conservative intersection of typical venue rules), with the cap and charset
behind per-venue configuration so verified limits can loosen it without a
scheme change.

### Resubmission after an uncertain response

**Planned** (§19). Before resubmitting after a timeout or ambiguous
acknowledgement:

1. Query by client order ID.
2. Inspect the private order stream.
3. Inspect trade executions.
4. Reconcile account balances.
5. Submit only when non-execution is proven.

A timeout can never automatically cause duplicate submission (Invariant 10);
unknown order states are never treated as failed orders (§36).

## 6. Partial-fill handling (§20)

**Planned** (CLAUDE.md §20): a partial fill is not a normal successful leg.
When a leg partially fills (states `LEG_1_PARTIAL` / `LEG_2_PARTIAL` /
`LEG_3_PARTIAL`):

1. Cancel the remaining quantity if still open.
2. Confirm the cancellation.
3. Calculate actual acquired and remaining assets.
4. Recalculate the remaining route using fresh order books.
5. Determine whether continuing remains inside loss limits.
6. Continue only when the risk engine approves.
7. Otherwise initiate recovery (`RECOVERY_REQUIRED`,
   `docs/RECOVERY_ENGINE.md`).
8. Record all residual dust.

**Never submit the originally planned next-leg quantity after a partial
fill** — only the actual available amount (§20; Invariant 12: partial fills
trigger recalculation). The dust-accounting model already exists read-only:
`triarb/pricing/feasibility.py` tracks stranded dust per currency and values
non-quote dust at zero (pessimistic) — **Implemented** as simulation only.

## 7. What exists in this build (summary)

| Item | Status | Evidence |
|---|---|---|
| Mode gate refusing all execution modes | **Implemented** | `triarb/config/models.py` (`ensure_supported_mode`), `tests/test_config.py::TestModeGate` |
| Decimal simulation of the three protected IOC legs (read-only) | **Implemented** | `triarb/pricing/feasibility.py`, `tests/test_feasibility.py` |
| Adapter interface with zero order/balance/withdrawal/transfer methods | **Implemented** | `triarb/exchange/base.py` |
| Sequential closed-loop executor (Mode A) | **Planned** | CLAUDE.md §16 |
| Inventory-assisted parallel (Mode B), disabled-until-authorized | **Planned** | CLAUDE.md §16, §38 |
| Marketable IOC planner, private-stream fill confirmation | **Planned** | CLAUDE.md §17 |
| 23-state persisted cycle state machine | **Planned** | CLAUDE.md §18 |
| Client order ID scheme + uncertain-response protocol | **Planned** | CLAUDE.md §19 |
| Partial-fill procedure | **Planned** | CLAUDE.md §20 |

## 8. Open TBDs

| TBD | Safest reversible assumption (per CLAUDE.md §6) |
|---|---|
| State-machine persistence backend | Append-only local JSON-lines journal behind config; PostgreSQL (§5) as the upgrade path |
| Per-venue client order ID length/charset limits | Doc-verified limits recorded in `docs/EXCHANGE_CAPABILITY_MATRIX.md` (OKX ≤32 alnum is the strictest); a 32-char `[A-Za-z0-9]` cap satisfies every venue and stays behind per-venue config until runtime-verified |
| Per-venue FOK support and semantics | FOK unused anywhere until verified per §17/§8; IOC-only default |
| `WAITING_LEG_N` timeout values | Conservative configurable timeout; on expiry treat state as **unknown** and run the §19 reconciliation protocol, never assume failure |
