# Opportunity Engine

The MARKET_DATA_ONLY observer pipeline (`triarb/observer.py`): how book
updates become `cycle_detected` / `cycle_ended` log events, and how today's
checks map onto the master prompt's §15 profitability gate (`CLAUDE.md`
Part B). Claims are labeled **Implemented** (file cited) or **Planned**
(section cited).

**This engine's only output is logs and metrics.** It contains no order,
balance, withdrawal, or transfer code; per §7, the opportunity detector
never calls an exchange trading endpoint. The stages downstream of the
Profitability Gate in the §7 flow — Central Risk Engine, Execution Planner,
private streams, Reconciliation and Recovery, Profit and Audit Ledger — do
not exist in this build (**Planned**).

## Pipeline position (§7)

```
Exchange Public WebSockets
        ↓
Normalized Market Events           (adapter → BookSnapshot / StreamStatus)
        ↓
Local Order-Book Engine            (triarb/pricing/orderbook.py)
        ↓
Currency Graph + Triangle Discovery (triarb/graph/, at startup)
        ↓
Depth-Aware Opportunity Simulation (triarb/pricing/edge.py + feasibility.py)
        ↓
Profitability Gate (partial)       → JSON-lines log + metrics — STOP
```

## Startup (`Observer.prepare`)

**Implemented.**

1. `fetch_symbols()` / `fetch_tickers()` through the venue adapter.
2. `build_triangles(...)` (see `docs/CURRENCY_GRAPH.md`) — discovery or
   whitelist, filters, ranking, cap.
3. A symbol → triangles index (`by_symbol`) so a book update re-prices only
   the triangles touching that symbol.
4. A `universe` log event recording what discovery saw and dropped, the
   configured notional, per-leg fee rate, the compounded fee hurdle
   (`hurdle_bps`), and the detection threshold.

## Run loop (`Observer.run`)

**Implemented.** One `asyncio.TaskGroup` with four tasks; any unexpected
exception cancels the group and crashes the process loudly — no silent
degradation:

| Task | Purpose |
|---|---|
| `consume` | drain `adapter.stream_books(...)`; dispatch `BookSnapshot` to the hot path, `StreamStatus` to the disconnect handler |
| `sweeper` | close opportunities whose leg books went stale between updates (every 0.25 s, `_SWEEP_INTERVAL_S`) |
| `stats` | periodic `stats` log event from the metrics registry (`observer.stats_interval_s`, default 60 s) |
| `metadata` | optional symbol-metadata refresh (`observer.metadata_refresh_s`, default 3600 s; 0 disables) |

## Evaluation trigger: per book update

**Implemented** (`Observer._on_book`). Every `BookSnapshot`:

1. replaces the symbol's book verbatim in the `BookStore` (snapshot
   channels — never merged or smoothed);
2. re-evaluates **every triangle touching that symbol** via the
   `by_symbol` index;
3. records the whole update's latency in the `reprice_latency_us`
   histogram (~µs-scale float screen; see
   `docs/TRIANGULAR_ARBITRAGE_MATH.md`).

Evaluation is therefore event-driven: a silent symbol produces no
evaluations. That is why `max_book_age_ms` is attached to every detection —
on a snapshot channel, "no message" is indistinguishable from "book
unchanged", and post-analysis must be able to discard detections priced off
aging legs.

## Threshold and the two-stage check (`Observer._evaluate`)

**Implemented.**

1. **Float screen:** `evaluate_cycle(...)` depth-walks all three legs at
   `observer.notional` with fee compounding at
   `fees.effective_taker_rate`. Failures (`missing_book`, `stale_book`,
   `insufficient_depth`) are counted and end any active opportunity for
   that triangle.
2. **Threshold:** a cycle is a candidate when `res.ok` and
   `net_bps >= observer.edge_threshold_bps` (net after fees, **before**
   rounding; default `0.0` = log every net-positive cycle).
3. **Decimal confirmation:** candidates run `simulate_rounded_cycle(...)`
   — exact `Decimal`, venue tick/step rounding, min-qty/min-notional, dust
   accounting. `feasible = feas.ok and net_bps_after_rounding > 0.0`.
   A cycle whose float edge is positive but infeasible after rounding
   increments the `rounding_killed` counter.

Thresholds are never lowered automatically (§15's prohibition); both knobs
are static config for the run (`triarb/config/models.py::ObserverConfig`).

## ActiveOpportunity lifecycle

**Implemented** (`triarb/observer.py::ActiveOpportunity`, keyed by
`tri_id` in `Observer.active`).

- **Detection** — first evaluation at/above threshold creates the record
  and logs **`cycle_detected`** with: `tri_id`, human-readable legs,
  `gross_bps`, `net_bps`, `net_after_rounding_bps` (null if the Decimal
  sim rejected), `feasible`, `reject_reason`, `stranded_dust`, `notional`,
  `max_book_age_ms`, `worst_prices`.
- **While active** — further at/above-threshold evaluations update the
  record in place (no re-detection): `evals`, `last_net_bps`,
  `peak_net_bps`, `peak_net_after_rounding_bps`, `last_feasible`,
  `ever_feasible`.
- **End** — the first evaluation below threshold, or any failed evaluation,
  or a sweep/disconnect, pops the record and logs **`cycle_ended`** with:
  `end_reason`, `persisted_ms` (monotonic first→last evaluation),
  `evals_while_active`, `peak_net_bps`, `peak_net_after_rounding_bps`
  (null if never feasibly simulated), `ever_feasible`.

`end_reason` values: `edge_gone` (evaluated below threshold),
`stale_book`, `missing_book`, `insufficient_depth`, `ws_disconnect`.

Persistence is measured from the feed's own updates (snapshot-channel
caveat above). Lifecycle behavior — detect, update-in-place, collapse,
below-threshold suppression, rounding-killed counting — is pinned offline
in `tests/test_observer.py::TestLifecycle`.

## Disconnect handling

**Implemented** (`Observer._on_stream_status`). On
`StreamStatus.DISCONNECTED`:

1. every book is invalidated (`BookStore.mark_all_stale` zeroes
   `recv_ns`, making age infinite);
2. every active opportunity is closed with `end_reason=ws_disconnect`;
3. detection resumes only when fresh snapshots arrive — nothing is
   interpolated across the gap.

`stream_status` events are logged for every connect/disconnect/error;
`ws_disconnects` / `ws_errors` counters track them. Tested in
`tests/test_observer.py::test_disconnect_ends_opportunities_and_stales_books`.

## Stale-book sweeper

**Implemented** (`Observer._sweep_loop`). Event-driven evaluation alone
cannot close an opportunity whose books simply stop updating — no update,
no evaluation. Every 0.25 s the sweeper checks each active opportunity's
three leg books against `observer.staleness_ms` (default 300, measured
from local monotonic receive time so venue clock skew cannot mask a
stalled feed) and closes violators with `end_reason=stale_book`. It also
refreshes the terminal dashboard when one is attached.

## Metadata refresh

**Implemented** (`Observer._metadata_loop`). Re-fetches symbol metadata on
a timer; on failure keeps the previous metadata and counts
`metadata_refresh_failures`. If a subscribed symbol is no longer online it
logs `symbols_degraded` — Phase 1 does not rebuild the triangle set
mid-run; the operator restarts to pick up a changed universe.

## §15 profitability gate: today's checks vs planned gates

§15 lists the gates that must **all** pass before an opportunity is
executable. This build has no execution path, so "passing" today yields a
log line at most. Status of each required gate, using §15's exact names:

| §15 gate | Status |
|---|---|
| All books synchronized | **Implemented by construction** for snapshot channels — every update replaces the whole book, so there is no incremental state to desynchronize (`triarb/pricing/orderbook.py`). Sequence/checksum validation for incremental channels is **Planned** (§13) |
| All books fresh | **Implemented** — `staleness_ms` enforced per leg in `evaluate_cycle` plus the sweeper; disconnects stale all books |
| Timestamp skew acceptable | **Planned** (§13, §15 `MAX_BOOK_SKEW_MS`) — cross-book skew is not compared today; see TBD below |
| All symbols online | **Implemented at startup** (graph excludes non-online symbols); mid-run enforcement on status change is **Planned** (§9) — today degradation is logged, not gated |
| Trading rules loaded | **Implemented (subset)** — tick/step/min-qty/min-notional required by the Decimal sim; `missing_metadata` rejects. Full §9 rule set **Planned** |
| Fee information loaded | **Implemented as configured rate** (§12 fallback: conservative configurable rate, results are estimates); fetched account fee rates **Planned** (§8) |
| Balances sufficient | **Planned** — no balance code exists in this build, by design |
| Risk limits satisfied | **Planned** (§23) — no risk engine exists |
| Expected profit above threshold | **Implemented** — `observer.edge_threshold_bps` (the §15 `MIN_EXPECTED_NET_PROFIT_BPS` role); `MIN_EXPECTED_NET_PROFIT_ABSOLUTE` **Planned** |
| Expected profit above uncertainty reserve | **Planned** (§11, §15 `LATENCY_RESERVE_BPS` / `FAILURE_RESERVE_BPS`) |
| Expected slippage below limit | **Planned** (§15 `MAX_SLIPPAGE_BPS`) — depth impact at the fixed notional is priced, but no explicit slippage ceiling gate exists |
| Cycle size above all minimums | **Implemented** — `min_qty` / `min_notional` rejects in `triarb/pricing/feasibility.py` (min-notional via the documented USDT proxy) |
| Cycle size below all maximums | **Planned** (§9 max quantity/notional, §15 `MAX_CYCLE_NOTIONAL`) — maximums are not in `SymbolInfo` yet |
| Exchange API healthy | **Implemented (public WS only)** — `StreamStatus` events gate detection via staleness; REST/private health checks **Planned** (§8 `healthCheck`) |
| Private order stream healthy | **Planned** — no private streams in this build |
| No unresolved reconciliation error | **Planned** (§21) — no reconciliation exists |
| No active kill switch | **Planned** (§24) — no kill switches exist; the only stop today is process exit, which trivially stops a system that never trades |

Of the §15 configurable thresholds, `MIN_EXPECTED_NET_PROFIT_BPS` →
`observer.edge_threshold_bps` and `MAX_BOOK_AGE_MS` →
`observer.staleness_ms` exist today; the rest
(`MIN_EXPECTED_NET_PROFIT_ABSOLUTE`, `MAX_BOOK_SKEW_MS`,
`MAX_SLIPPAGE_BPS`, `LATENCY_RESERVE_BPS`, `FAILURE_RESERVE_BPS`,
`MAX_CYCLE_NOTIONAL`) are **Planned** with their §15 names.

**TBD:** `MAX_BOOK_SKEW_MS` default and the skew-measurement design
(§13: measure cross-book timestamp skew, reject temporally inconsistent
triangles). Safest reversible assumption per `CLAUDE.md` §6: today the
per-leg staleness bound caps pairwise book skew at `staleness_ms` (300 ms
default) indirectly, and every detection logs `max_book_age_ms` so skewed
detections can be discarded in analysis; an explicit configurable skew
gate must land before any mode that acts on detections.

**TBD:** reserve defaults (`LATENCY_RESERVE_BPS`, `FAILURE_RESERVE_BPS`,
`MAX_SLIPPAGE_BPS`) — recorded in `docs/TRIANGULAR_ARBITRAGE_MATH.md`.

## Reading the numbers honestly

A `cycle_detected` line is a **theoretical, mode-labeled observation**
(§27: theoretical profit is never presented as realized profit — there is
no realized profit here at all). The README's economics apply unchanged:
the compounded fee hurdle (~30.06 bps at 0.1% taker), latency (most
detected cycles vanish before a non-colocated order could arrive), rounding
(marginal edges die in the Decimal sim — watch `rounding_killed`), and
adverse selection. The decision rule after a multi-day run is the README's:
if feasible net-positive cycles that outlive your measured round trip are
~0, later phases cannot make money at that tier/latency.

## Test evidence

- `tests/test_observer.py` — detect → persist → end lifecycle, threshold
  suppression, disconnect staling, `rounding_killed`, all offline via a
  fake adapter.
- `tests/test_edge.py`, `tests/test_feasibility.py` — the two pricing
  stages the engine composes.
