# Recovery and Unwind Engine — design specification (master prompt §21)

> **STATUS: DESIGN DOCUMENT — NOTHING IN THIS FILE IS IMPLEMENTED.**
> No execution code exists in this build, and therefore no recovery code
> either: there are no orders to unwind and no balances to reconcile. The
> mode gate in `triarb/config/models.py` (`ensure_supported_mode`) refuses
> every execution mode; only `MARKET_DATA_ONLY` starts (**Implemented** —
> `tests/test_config.py::TestModeGate`). This file records the governing
> design from CLAUDE.md Part B §21 so that recovery is specified *before*
> the first line of execution code is written, not after.

## 1. Governing principle

**Planned** (CLAUDE.md §21): every possible failure must have a documented
recovery path. The execution state machine (`docs/EXECUTION_ENGINE.md`)
enters recovery through the `RECOVERY_REQUIRED` state and moves through
`RECOVERING` to `RECOVERED`, or terminates in `FAILED` or
`MANUAL_INTERVENTION` when uncertainty remains. Recovery is a first-class
engine with its own state machine, shared by paper mode (§25) so it is
exercised long before any real order exists.

Enabling **automatic** recovery trades is one of the actions that always
requires recorded human authorization first (CLAUDE.md §38). Until that
authorization exists, a cycle reaching `RECOVERY_REQUIRED` computes and
records its proposed unwind but escalates to `MANUAL_INTERVENTION` instead
of trading.

## 2. Failure catalogue (§21)

**Planned** — each failure below must have a documented, tested recovery
path before live qualification. Detection signals marked **Implemented**
exist today in the observer; everything in the "recovery path" column is
design.

| Failure (§21) | Detection signal | Recovery path |
|---|---|---|
| Leg 1 fills and Leg 2 fails | Private order stream + state machine (Planned) | Freeze venue, reconcile, unwind leg-1 inventory via safest route |
| Leg 2 partially fills | Private fill events (Planned); §20 procedure | Cancel remainder, recalc route, continue only with risk approval, else unwind |
| Leg 3 rejects | Order-entry error, normalized via adapter `normalizeError` (Planned, §8) | Reconcile, unwind intermediate inventory to settlement asset |
| WebSocket disconnects | **Implemented** for public streams: `StreamStatus` connect/disconnect/error events in `triarb/exchange/base.py`; the observer marks all books stale on a gap (`triarb/observer.py`) | Freeze new cycles; resynchronize books and private streams before resuming |
| REST request times out | HTTP client timeout (Planned for private endpoints) | Treat order state as **unknown**; run the §19 non-execution proof before any resubmit |
| Order status becomes unknown | Missing/contradictory events (Planned) | §19 protocol: query by client order ID, inspect streams and executions, reconcile balances |
| Balance changes unexpectedly | `reconcileBalances` mismatch (Planned, §8) | Freeze venue, full account reconciliation, quantify unintended exposure |
| Symbol is suspended | Instrument-status refresh (§9; metadata refresh timer **Implemented** read-only via `metadata_refresh_s` in `triarb/config/models.py` and `fetch_symbols` in `triarb/exchange/base.py`) | Reject routes on the symbol; unwind via alternate markets if inventory is stranded |
| Price gaps beyond the limit | Slippage ceiling breach in the planner (Planned, §17) | IOC price protection bounds the fill; unfilled remainder goes to recovery sizing |
| Exchange enters maintenance | Venue status endpoint / book invalidation (Planned, §13) | Freeze venue; hold inventory; no orders until books revalidate |
| Rate limit is reached | Normalized rate-limit errors (Planned, §8) | Back off per venue budget; recovery orders get priority over new cycles |
| API credential is revoked | Auth failures on private endpoints (Planned) | Kill switch on the account; alert operator; no automated retry with stale credentials |

## 3. Recovery process pipeline (§21)

**Planned** — the pipeline runs in exactly this order:

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

Notes binding the pipeline to the rest of the design:

- "Freeze new cycles" is a per-exchange gate enforced by the central risk
  engine ("No unresolved reconciliation error" gate, §15) — the recovery
  engine does not need the executor's cooperation to stop it.
- Reconciliation uses the adapter contract's `reconcileOrder`,
  `reconcileBalances`, and `reconcileCompleteAccountState` (Planned, §8).
- "Protected recovery orders" are the same marketable-IOC-with-price-
  protection orders as normal legs (§17) — recovery never gets a laxer
  order type.
- Every recovery order passes through the central risk engine like any
  other order; recovery is not a bypass (§23, Invariant 13).

## 4. Recovery priorities (§21, in order)

**Planned** — when priorities conflict, the earlier one wins:

1. Prevent duplicate orders.
2. Determine the true exchange state.
3. Contain exposure.
4. Return to the configured settlement asset when reasonably safe.
5. Limit loss.
6. Preserve evidence.
7. Require human intervention when uncertainty remains.

Consequence of the ordering: the engine will *hold* an unintended position
(priority 3) rather than risk a duplicate order (priority 1) or trade
against an unknown account state (priority 2). Getting flat is priority 4,
not priority 1.

## 5. No blind market-selling; bounded recovery loss

**Planned** (§21): the recovery engine does **not** blindly market-sell
unintended inventory. It calculates the safest available route back to the
configured settlement asset — which may be multi-hop, may wait for book
revalidation, and may conclude that holding is currently safer — and it
enforces a **maximum recovery-loss threshold** supplied by configuration.

- The threshold is the `Maximum recovery loss` control owned by the central
  risk engine (§23; see `docs/RISK_ENGINE.md`), evaluated against the
  depth-walked, fee-inclusive expected cost of the proposed unwind before
  any recovery order is submitted.
- If no route stays within the threshold, the cycle escalates to
  `MANUAL_INTERVENTION` with the full evaluation recorded. The engine never
  widens the threshold to force an unwind (§36: do not disable risk
  controls; do not increase slippage limits to hide poor execution).
- **TBD** — the default threshold value. Safest reversible assumption
  (per CLAUDE.md §6): no implicit default; if the key is unset, automatic
  recovery execution is disabled and every `RECOVERY_REQUIRED` cycle goes
  to `MANUAL_INTERVENTION`. The key exists in configuration so enabling it
  is explicit and reversible.

Route evaluation reuses the depth-walking and exact-`Decimal` rounding
machinery that already exists read-only (**Implemented** —
`triarb/pricing/depth.py`, `triarb/pricing/feasibility.py`): a recovery
route is priced off actual executable depth with fees and venue rounding,
never off tickers.

## 6. Evidence preservation

**Planned** (§21 priority 6; §18 transition records): every recovery
produces an incident record containing, at minimum:

- The originating cycle's complete transition history (all §18 fields:
  cycle ID, states, timestamps, order IDs, fill IDs, fees, reason codes,
  market snapshot references).
- The reconciliation results from each pipeline pass (orders, fills,
  balances — expected vs. actual).
- The exposure calculation and every candidate unwind route evaluated, with
  the reason the chosen route won and the others were rejected.
- The realized recovery loss vs. the configured maximum.

Records are append-only; nothing is edited after the fact (Invariant 14
requires immutable audit records for live orders — recovery orders are live
orders). **Implemented** precursor: the structured JSON-lines logger
(`triarb/monitoring/jsonlog.py`) already emits one immutable JSON object
per event with ISO-8601 UTC timestamps and never raises into the hot path;
the incident record format builds on it.

## 7. Linkage of recovery orders to the originating cycle

**Planned** (§19, §21; Invariant 19: recovery orders are linked to their
original cycle):

- Recovery orders carry client order IDs from the same deterministic scheme
  as normal legs (`docs/EXECUTION_ENGINE.md` §5), embedding the **failed
  cycle's identifier** plus a recovery attempt number, so venue-side records
  alone are sufficient to associate every recovery fill with the cycle that
  caused it — including after a process restart.
- The accounting ledger books recovery fees and recovery loss against the
  originating cycle, never against a fresh cycle, so per-cycle net results
  include their own cleanup costs (§27 tracks `recovery loss` and
  `recovered cycles` as distinct measures; recovered profit is never mixed
  into normal realized profit).

## 8. Restart behavior

**Planned** (§18, §21): recovery state is persisted with the cycle state
machine. After a process restart, cycles found in `RECOVERY_REQUIRED` or
`RECOVERING` re-enter the pipeline at "reconcile orders" — never at
"execute protected recovery orders" — because the true exchange state must
be re-proven before any order is sent (priority 2 before priority 4).
Duplicate-prevention relies on the §19 client-order-ID query protocol.

## 9. What exists in this build (summary)

| Item | Status | Evidence |
|---|---|---|
| Mode gate refusing all execution modes (hence no recovery surface) | **Implemented** | `triarb/config/models.py`, `tests/test_config.py::TestModeGate` |
| Public-stream failure signals (`StreamStatus` connect/disconnect/error; books marked stale on gaps) | **Implemented** | `triarb/exchange/base.py`, `triarb/observer.py`, `triarb/pricing/orderbook.py` |
| Staleness measured from local monotonic time (venue clock skew cannot hide a stalled feed) | **Implemented** | `triarb/pricing/orderbook.py` |
| Append-only structured JSON event log (incident-record substrate) | **Implemented** | `triarb/monitoring/jsonlog.py` |
| Depth-walked, fee- and rounding-exact route pricing (reused for unwind evaluation) | **Implemented** (read-only) | `triarb/pricing/depth.py`, `triarb/pricing/feasibility.py` |
| Recovery pipeline, priorities, incident records, recovery orders | **Planned** | CLAUDE.md §21 |
| Recovery-order linkage via client order IDs | **Planned** | CLAUDE.md §19, §21; Invariant 19 |

## 10. Open TBDs

| TBD | Safest reversible assumption (per CLAUDE.md §6) |
|---|---|
| Maximum recovery-loss threshold default | No implicit default; unset ⇒ automatic recovery disabled, all recoveries go to `MANUAL_INTERVENTION`; explicit config key to enable |
| Automatic vs. operator-approved recovery execution | Operator-approved until §38 human authorization for automatic recovery trades is recorded |
| Incident-record storage (co-located with state-machine journal vs. separate store) | Same append-only journal as the state machine (see `docs/EXECUTION_ENGINE.md` §8 TBD), separable later without format change |
| Multi-hop unwind route search depth | Direct (single-market) routes only at first, behind config; multi-hop unwinds added only with replay-test evidence (§26) |
