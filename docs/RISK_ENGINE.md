# Central Risk Engine — design specification (master prompt §22–§24, §33)

> **STATUS: DESIGN DOCUMENT — NOTHING IN THIS FILE IS IMPLEMENTED.**
> No execution code exists in this build, so there are no balances to
> reserve, no cycles to approve, and no live kill switches to trip. The
> mode gate in `triarb/config/models.py` (`ensure_supported_mode`) refuses
> every execution mode; only `MARKET_DATA_ONLY` starts (**Implemented** —
> `tests/test_config.py::TestModeGate`). This file specifies the central
> risk engine from CLAUDE.md Part B §22–§24 and its role as the promotion
> gate to live trading (§33).

## 1. Governing principle

**Planned** (CLAUDE.md §23, §7): **no cycle may bypass the risk engine.**
The execution engine must never bypass the central risk engine, and an
exchange adapter cannot bypass central execution controls (Invariants 13
and 23). Every live order — including every recovery order
(`docs/RECOVERY_ENGINE.md`) — passes through it. The risk engine is
deterministic code, versioned and auditable; no LLM participates in its
decisions or the live execution path (§4, Invariant 22).

The risk engine also owns the profitability gate's risk-facing entries
(§15): `Risk limits satisfied`, `Balances sufficient`,
`No unresolved reconciliation error`, and `No active kill switch` are its
verdicts, consumed by the opportunity pipeline.

## 2. Balance reservation model (§22)

**Planned** (CLAUDE.md §22): concurrent cycles must not spend the same
funds. The engine implements **atomic internal reservations** for:

- Starting settlement asset
- Expected intermediate quantities
- Fee reserves
- Recovery reserves

Locking model: **per-exchange, per-account, per-asset**. A reservation is
taken atomically before `SUBMITTING_LEG_1` (state `RESERVING_BALANCES` in
the §18 machine) and covers the whole cycle.

A new cycle cannot use:

- Locked funds
- Funds committed to open orders
- Recovery reserves
- Minimum operational reserves
- Unreconciled balances

Reservations are released **only after confirmed completion or recovery**
(states `COMPLETED` or `RECOVERED`) — never on submission, never on an
unconfirmed acknowledgement, and never while a cycle sits in
`RECOVERY_REQUIRED` / `MANUAL_INTERVENTION`.

Test obligations (§31, §32): locked funds cannot be double spent
(Invariant 9); a cycle cannot spend more than its reserved balance
(property test); rounding cannot create artificial funds (property test).
All reservation arithmetic is `Decimal` (Invariant 21).

**TBD** — the `Minimum operational reserves` amount per asset. Safest
reversible assumption (per CLAUDE.md §6): a configurable per-asset floor
defaulting to **100% of balance reserved** (i.e., nothing spendable) until
the operator explicitly sets floors — an unset config yields zero trading
capacity rather than full-balance exposure.

## 3. Evaluation inputs (§23)

**Planned** — every cycle approval evaluates all of:

- Exchange health
- Market-data health
- Private-stream health
- Available balances
- Reserved balances
- Cycle notional
- Daily turnover
- Daily profit
- Daily loss
- Consecutive failures
- Unresolved cycles
- Recovery exposure
- Slippage
- Data latency
- API latency
- Reconciliation mismatches
- Per-symbol exposure
- Per-asset exposure
- Per-exchange exposure
- Maximum concurrent cycles

**Implemented** precursors feeding the market-data-health inputs already
exist read-only: per-book staleness from local monotonic receive time
(`triarb/pricing/orderbook.py`), stream connect/disconnect/error events
(`triarb/exchange/base.py` `StreamStatus`), and the observer's
`max_book_age_ms` attached to every evaluation (`triarb/observer.py`).
Everything account- or order-related is **Planned** — this build reads no
private endpoint and holds no keys.

## 4. Required controls (§23)

**Planned** — the engine enforces every control below; none may be relaxed
automatically to create more trades (§15, §36):

| Control (§23, exact name) | Notes |
|---|---|
| Maximum amount per cycle | Interacts with §14 `maximum_cycle_size` and §15 `MAX_CYCLE_NOTIONAL` |
| Maximum percentage of account balance per cycle | §14 `maximum_percent_of_available_balance`; full balance is never the default (§14) |
| Maximum simultaneous cycles | Also an evaluation input (`Maximum concurrent cycles`) |
| Maximum daily traded volume | Resets on a configured day boundary (TBD below) |
| Maximum daily loss | Breach trips the Automatic daily-loss kill switch (§24) |
| Maximum recovery loss | Consumed by the recovery engine before any unwind order (`docs/RECOVERY_ENGINE.md`) |
| Maximum consecutive failed cycles | Breach trips the Automatic repeated-failure kill switch (§24) |
| Maximum unresolved exposure | Sum of exposure in non-terminal cycles |
| Maximum order latency | Submission → acknowledgement |
| Maximum fill latency | Acknowledgement → fill events |
| Maximum slippage | §15 `MAX_SLIPPAGE_BPS`; §14 `maximum_expected_slippage_bps` |
| Maximum book age | §15 `MAX_BOOK_AGE_MS`; observer precursor: `observer.staleness_ms` (**Implemented**, `triarb/config/models.py`) |
| Maximum book timestamp skew | §15 `MAX_BOOK_SKEW_MS`; cross-book temporal consistency (§13) |

All thresholds are configuration, all are `Decimal`-safe, and none has a
production default yet — see TBDs (§8 below).

## 5. Structured, immutable risk decisions (§23)

**Planned** — every evaluation returns exactly this structure:

```json
{
  "approved": false,
  "reason_codes": [],
  "adjusted_starting_amount": null,
  "risk_snapshot_id": "",
  "timestamp": ""
}
```

Semantics:

- `approved` — the only field the executor may branch on.
- `reason_codes` — machine-readable list; every rejection names each failed
  control, never a bare "denied".
- `adjusted_starting_amount` — the engine may approve a *smaller* amount
  than requested (as a decimal string); it never adjusts upward.
- `risk_snapshot_id` — references the immutable snapshot of every input in
  §3 as evaluated, so any decision can be reproduced after the fact.
- `timestamp` — evaluation time, UTC.

**Risk decisions are immutable** (§23). They are written append-only before
the decision is acted on, are never edited or deleted, and are part of the
per-order audit record (Invariant 14). A denied decision cannot be re-used
after inputs change — a new evaluation produces a new decision with a new
snapshot ID.

## 6. Kill switches (§24)

**Planned** — all twelve, with their scope and activation trigger:

| Kill switch (§24, exact name) | Scope blocked | Activation |
|---|---|---|
| Global kill switch | All new cycles everywhere | Manual (operator) |
| Exchange kill switch | One venue | Manual |
| Account kill switch | One account on one venue | Manual |
| Triangle kill switch | One triangle | Manual |
| Symbol kill switch | Any cycle touching the symbol | Manual |
| Asset kill switch | Any cycle touching the asset | Manual |
| Strategy-version kill switch | Cycles from one strategy version | Manual |
| Automatic daily-loss kill switch | Affected scope (account/venue) | Automatic: `Maximum daily loss` breached |
| Automatic repeated-failure kill switch | Affected scope | Automatic: `Maximum consecutive failed cycles` breached |
| Automatic stale-data kill switch | Affected venue/symbols | Automatic: book age or skew beyond `Maximum book age` / `Maximum book timestamp skew` |
| Automatic reconciliation kill switch | Affected account | Automatic: reconciliation mismatch detected and unresolved |
| Automatic exchange-disconnection kill switch | Affected venue | Automatic: public or private stream loss without resynchronization |

**Activation semantics** — when any switch activates, in order (§24):

1. Block new cycles.
2. Prevent new normal orders.
3. Preserve recovery capability.
4. Cancel safe-to-cancel open orders.
5. Reconcile balances and orders.
6. Notify operators.
7. Record the reason.
8. Require authorized restart.

Notes:

- Step 3 before step 4: a kill switch must never strand a mid-flight cycle
  — recovery orders remain possible while normal orders are blocked
  (`docs/RECOVERY_ENGINE.md`).
- "A kill switch blocks new cycles" is Invariant 18 and requires an
  automated test before live qualification.
- Switches latch: clearing the triggering condition does not re-enable
  trading; only an authorized restart does (step 8). The restart
  authorization mechanism is TBD (§8 below).
- **Implemented** precursor to the stale-data switch, detection only: the
  observer already refuses to price off stale books and marks all books
  stale on stream gaps (`triarb/observer.py`, `triarb/pricing/orderbook.py`)
  — but there is nothing to switch off in this build.

## 7. Promotion gate: live-trading qualification (§33)

The risk engine owns the promotion gate. **Planned** (CLAUDE.md §33): live
trading stays disabled until a defined qualification period completes, with
the qualification configuration containing every key below:

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

Passing the financial threshold alone is insufficient (§33). The bot must
also prove: correct accounting, stable connectivity, correct rounding,
correct fee handling, successful restart recovery, reliable kill switches,
and reliable reconciliation. Qualification metrics come from PAPER_TRADING
and SHADOW_TRADING runs and are never mixed with theoretical or live
figures (§27).

Even a passed qualification does not enable LIVE_TRADING by itself — the
full §3 chain still applies (server-side environment authorization,
exchange-account approval, API-permission validation, paper and shadow
qualification, security review, risk review, human authorization,
jurisdiction eligibility, immutable activation record). A frontend button
alone must never enable live trading (§3).

**Implemented** today: the gate's floor. `ensure_supported_mode` in
`triarb/config/models.py` refuses `LIVE_TRADING` with the §3
authorization-chain message and refuses `PAPER_TRADING` / `SHADOW_TRADING`
because no execution engine exists; `.env.example` pins
`LIVE_TRADING_ENABLED=false`, `WITHDRAWAL_PERMISSION_ALLOWED=false`,
`AUTOMATIC_EXTERNAL_TRANSFERS=false` (Invariant 17 evidence:
`tests/test_config.py::TestModeGate`). None of this build's code reads an
API key.

## 8. What exists in this build (summary)

| Item | Status | Evidence |
|---|---|---|
| Mode gate: only `MARKET_DATA_ONLY` starts; `LIVE_TRADING` refused with authorization-chain message | **Implemented** | `triarb/config/models.py`, `tests/test_config.py::TestModeGate` |
| Safe env defaults (`LIVE_TRADING_ENABLED=false`, withdrawals/transfers disallowed) | **Implemented** | `.env.example`, CLAUDE.md Part A |
| Stale-book refusal and stream-gap handling (detection only) | **Implemented** | `triarb/pricing/orderbook.py`, `triarb/observer.py` |
| Observer-side thresholds (`staleness_ms`, `edge_threshold_bps`, `notional`) as config precursors | **Implemented** | `triarb/config/models.py` (`ObserverConfig`) |
| Balance reservations, locking model | **Planned** | CLAUDE.md §22 |
| Evaluation inputs, required controls, immutable decision JSON | **Planned** | CLAUDE.md §23 |
| All twelve kill switches and activation semantics | **Planned** | CLAUDE.md §24 |
| Live-qualification configuration and promotion gate | **Planned** | CLAUDE.md §33, §3 |

## 9. Open TBDs

| TBD | Safest reversible assumption (per CLAUDE.md §6) |
|---|---|
| Numeric defaults for every §23 control and §33 qualification key | No implicit defaults; unset controls evaluate as **deny** (`approved: false` with a `missing_limit` reason code), so an incomplete config cannot trade |
| `Minimum operational reserves` per asset | Unset ⇒ 100% of balance reserved (zero spendable) until explicitly configured |
| Daily-boundary definition for daily volume/loss counters | UTC midnight, behind a config key |
| Kill-switch authorized-restart mechanism (who, how recorded) | Manual config-plus-restart with an operator identity recorded in the audit log; step-up authentication (§29) when the management API exists |
| Risk-decision / snapshot storage backend | Same append-only journal assumption as the execution state machine (`docs/EXECUTION_ENGINE.md` §8), PostgreSQL (§5) as upgrade path |
| Exposure valuation currency for per-asset/per-symbol limits | Valued in the configured settlement asset (default `USDT`, §1) using depth-walked prices, pessimistic side |
