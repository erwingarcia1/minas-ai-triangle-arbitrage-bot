# PAPER_TRADING.md — Phase 2 design for the PAPER_TRADING mode

> **STATUS: NOT IMPLEMENTED.** This build is MARKET_DATA_ONLY. Configuring
> `mode: paper_trading` is refused at startup by the mode gate
> (`triarb/config/models.py::ensure_supported_mode`) with the message that it
> *"requires the execution engine planned for a later phase"* — test evidence
> in `tests/test_config.py::TestModeGate::test_unimplemented_modes_rejected`.
> Everything below §1 is **Planned** design (master prompt §25, §33, §40
> item 11) unless explicitly labeled Implemented. The repository contains
> **no order, balance, withdrawal, or transfer code**; paper mode will not
> change that — it places no real orders and tracks only virtual balances.

## 1. What exists today vs. what paper mode reuses

The governing rule (§25): paper mode must use the **same** engines as live
mode — *only the exchange execution adapter is simulated*. Anything proven
in paper mode with a forked engine proves nothing.

| §25 shared engine | Status today |
|---|---|
| Currency graph | **Implemented** — `triarb/graph/` (builder, triangle enumeration, filters); reused as-is. |
| Opportunity engine | **Implemented** — float depth-walk screen `triarb/pricing/edge.py` + book store `triarb/pricing/orderbook.py`, orchestrated in `triarb/observer.py`. |
| Fee engine | **Implemented** (configured rates + token discount, `triarb/pricing/fees.py`, `FeesConfig`); fetching account fee rates is Planned (§12). |
| Rounding logic | **Implemented** — exact-Decimal simulation of the three IOC orders, `triarb/pricing/feasibility.py`. |
| Risk engine | **Planned** — §23; does not exist in any form. |
| Execution state machine | **Planned** — §18; does not exist in any form. |
| Recovery state machine | **Planned** — §21; does not exist in any form. |
| Exchange execution adapter | **Planned** — the *only* component paper mode replaces, with a simulated adapter behind the same interface the live adapter will implement. Today's `ExchangeAdapter` (`triarb/exchange/base.py`) has no trading methods at all. |

Consequence: Phase 2 is not "add a simulator" — it is *build the risk
engine, execution state machine (states DETECTED … MANUAL_INTERVENTION per
§18), recovery engine, and accounting*, then run them against a simulated
execution adapter. Market data stays live and public: paper mode uses real
exchange order books (§3 Mode 2) and needs no API keys.

## 2. Simulated execution adapter — required behaviors (Planned, §25)

The simulation must model, at minimum:

- **Latencies**, each separately configurable: WebSocket latency, REST
  latency, decision latency, order-entry latency. Safe default (TBD-PT-6):
  seed from p95 values measured in this deployment's own MARKET_DATA_ONLY
  soak runs, plus margin — never from optimistic assumptions.
- **Queue position** — do not assume that touching a price guarantees a
  fill (§25, verbatim). Safe default (TBD-PT-7): zero queue priority — a
  simulated IOC fills only against liquidity still present *after* the
  adverse update of §3.
- **Price movement and slippage** through the modeled latency window.
- **Partial fills**, which must route through the §20 partial-fill path
  (cancel remainder → recalculate from actual amounts → risk re-approval or
  recovery), never be treated as a normal filled leg.
- **Rejections** — venue error codes, including minimum-order errors,
  mapped through the same error-normalization the live adapter will use.
- **Rate limits** — the simulated venue enforces a request budget and
  returns rate-limit errors when exceeded.
- **Trading fees** per fill, with fee asset modeled (§12), recording
  `estimated_fee` vs `actual_fee` for later comparison.
- **Missing liquidity** — books walked, never extrapolated; exhausting
  visible depth fails the fill (matching today's `insufficient_depth`
  behavior in `triarb/pricing/depth.py` / `feasibility.py`).
- **Disconnects** — mid-cycle WebSocket loss, forcing the reconcile path.
- **Process restarts** — kill and restart mid-cycle; the persisted state
  machine must resume or safely reconcile the incomplete cycle (§18).

## 3. The pessimistic fill model (Planned; committed in README Phase 2)

Naive paper trading is systematically optimistic: it fills you at the book
you saw, at the moment you saw it, ahead of everyone else. The README's
Phase 2 commitment is the opposite bias, and it is binding:

> IOC-only simulated fills with a pessimistic fill model — **the book moves
> one update against you between legs.**

Concretely, for Sequential closed-loop execution (§16 Mode A, the only
execution mode in scope):

1. Leg *N* is planned against the current book (worst-price limit, exact
   rounding — the logic already in `triarb/pricing/feasibility.py`).
2. Before the simulated fill is granted, the book advances by at least one
   real subsequent update, applied **against** the taker's direction; the
   IOC then fills only against what remains inside its limit price. What
   doesn't fill is a partial fill or a zero fill — never re-priced upward.
3. Leg *N+1* is recalculated from the **actual simulated fill output** of
   leg *N* (never the planned quantity — Invariant 4, §20), against the
   then-current book.

Adverse selection rationale (README): the cycles a slow participant gets to
hit are disproportionately the ones faster participants declined. A paper
engine that fills at the observed touch would manufacture profit that live
trading cannot reproduce. If the strategy cannot show a positive net result
*under this pessimistic model*, it must not proceed — and per §15, fill-model
parameters must never be loosened to create more trades or better results.
Loosening any pessimism parameter requires recorded review (§38 spirit).

## 4. FIRST PAPER-TRADING MILESTONE (Planned; defined per §40 item 11)

### Scope

- One venue (default Bitget, matching `Settings.venue`), one or a small
  whitelist of triangles (`triangles.whitelist`), settlement currency USDT.
- Sequential closed-loop execution (§16 Mode A) only. Inventory-assisted
  parallel execution (Mode B) is out of scope and stays disabled (§16).
- Marketable IOC limit orders with price protection only (§17); no market
  orders, no FOK.
- Simulated execution adapter per §2; pessimistic fill model per §3;
  virtual balances only; no API keys; no real orders of any kind.
- Every component behind the §15 profitability gate and §23 risk decision,
  even though fills are simulated — the point is to exercise the real
  engines.

### Acceptance criteria

All numeric values are **TBD** pending Phase 2 review; the safe defaults
below are recorded per `CLAUDE.md` §6, live behind configuration, and may be
tightened without review but never loosened without it.

| # | Criterion | Threshold (TBD → safe default) |
|---:|---|---|
| A1 | Completed simulated cycles with **correct accounting**: per-cycle ledger balances exactly in `Decimal`; every fee, slippage, rounding loss, and stranded-dust amount attributed; `estimated_fee` vs `actual_fee` compared per cycle (§12) | TBD-PT-1: ≥ 1,000 completed simulated cycles with zero unexplained ledger differences |
| A2 | **Restart-resume proof**: process killed mid-cycle at injected points (each state of the §18 machine that can be interrupted), restarted, and every incomplete cycle resumed or safely reconciled — Invariant 15 | TBD-PT-2: ≥ 10 injected mid-cycle restarts, 100% resumed/reconciled, zero duplicate simulated orders |
| A3 | **Kill-switch proof**: each implemented kill switch (§24 — at minimum global, exchange, automatic stale-data, automatic repeated-failure) deliberately triggered and shown to block new cycles while preserving recovery capability — Invariant 18 | Every implemented switch triggered ≥ 1×; zero cycles started while any switch active |
| A4 | Partial-fill handling: injected partial fills always cancel the remainder and recalculate from actual amounts (§20) — Invariant 12 | 100% of injected partials routed through recalculation; zero planned-quantity submissions after a partial |
| A5 | Duplicate-order prevention under injected ack loss / timeout (§19) — Invariant 10 | Zero duplicate simulated submissions across all chaos injections |
| A6 | Reconciliation: simulated fills, virtual balances, and the ledger agree after every cycle and after every injected failure | TBD-PT-3: zero unresolved reconciliation mismatches |
| A7 | Stability window: continuous paper operation including disconnects and restarts | TBD-PT-4: ≥ 14 consecutive days |
| A8 | Result honesty: all outputs labeled PAPER (§28); simulated profit never mixed with THEORETICAL figures (§27) — Invariant 20 | Automated check on the reporting path |

Passing a financial threshold alone is explicitly insufficient (§33): A1–A8
are proofs of *mechanism*, and all must pass regardless of PnL.

### §33 qualification metrics this milestone feeds

The milestone produces the paper-side inputs of the live-trading
qualification configuration (§33). Every value is **TBD** until Phase 2
review fixes the qualification config; safe defaults recorded here:

| §33 metric | Fed by | TBD → safe default |
|---|---|---|
| minimum paper-trading duration | A7 window | 14 days |
| minimum number of simulated cycles | A1 count | 1,000 |
| maximum false-positive rate | detected-executable cycles that simulate to a net loss | TBD-PT-5: ≤ 5% |
| maximum partial-fill rate | A4 statistics | TBD: measured first; provisional ceiling 20% |
| maximum recovery-failure rate | recovery outcomes across injections | 0% — any failed recovery blocks qualification |
| maximum reconciliation-error rate | A6 | 0 unresolved mismatches |
| maximum latency | simulated + measured decision/order latencies vs §15 `LATENCY_RESERVE_BPS` assumptions | TBD: bounded by the latency the reserve was computed from |
| maximum drawdown | virtual-balance equity curve | TBD: ≤ 1% of starting virtual balance |
| minimum positive net result after simulated costs | ledger net over the window, pessimistic model, all costs (fees, slippage, rounding, reserves) | > 0; **required but never sufficient** |

The remaining §33 items (shadow-trading duration and qualified-opportunity
counts) belong to SHADOW_TRADING, out of scope here.

## 5. Honest framing

Paper-mode success is a *precondition*, not a prediction. The README's
economics remain fully in force: three taker legs at 0.1% need ~30.06 bps of
gross dislocation (`triarb.pricing.fees.hurdle_bps`, unit-tested); cycles
vanish in tens of milliseconds while non-colocated round trips run 200 ms+;
rounding strands dust on every leg; and adverse selection skews the
remainder against slow participants. The expected outcome of an honest
pessimistic paper run at default fee tiers from a non-colocated box is
**zero or negative net result** — in which case the correct next step is a
better fee tier or a different strategy, not the next phase. Nothing in this
document describes the system as profitable or production-ready.

## 6. TBD register

| ID | Question | Safest reversible assumption on record |
|---|---|---|
| TBD-PT-1 | Minimum simulated cycles for A1 | 1,000 completed cycles, configurable |
| TBD-PT-2 | Restart-injection count for A2 | ≥ 10 injected restarts covering every interruptible §18 state |
| TBD-PT-3 | Reconciliation tolerance | Zero unresolved mismatches (exact Decimal) |
| TBD-PT-4 | Stability window | 14 consecutive days |
| TBD-PT-5 | Maximum false-positive rate | ≤ 5% |
| TBD-PT-6 | Simulated latency values | Measured p95 from own soak runs + margin, configurable |
| TBD-PT-7 | Queue-position model | Zero queue priority (fills only against post-adverse-update liquidity) |

All TBD values sit behind configuration; tightening them needs no approval,
loosening any of them requires recorded review (§15: thresholds are never
lowered automatically to create more trades).
