# ORDER_BOOK_ENGINE.md — local order-book engine

> Status labels: **Implemented** = true of the code today, with a file
> reference. **Planned** = required by the master prompt (`CLAUDE.md` Part B,
> section cited) for a later phase. This build is MARKET_DATA_ONLY: books
> feed detection and logging only. **There is no order, balance, withdrawal,
> or transfer code anywhere in this repository**, and nothing in this
> document implies profitability or production readiness.

## 1. Design summary: snapshot channels while observing

**Implemented.** Every venue adapter feeds the engine *complete book
snapshots*; the engine never merges incremental deltas. This is decision
D-002 (`docs/DECISIONS.md`): the master prompt §13 requires full
sequence/checksum handling, but incremental books introduce an entire class
of merge/resync bugs — half-applied deltas, missed gaps, wrong-order
application, checksum drift — and in the observer phase a corrupt book costs
only log quality, not money. Snapshot channels remove that class of bug by
construction:

- Each message fully replaces the book (`BookStore.apply`,
  `triarb/pricing/orderbook.py`); no state is carried between messages, so
  there is nothing to merge and nothing to merge incorrectly.
- A dropped message can only mean a *missed change*, never a corrupted book.
  Missed changes are bounded by the staleness rule (§3 below).
- Reconnects need no resynchronization protocol: the first snapshot after
  resubscribe is complete by definition.

The trade-off: visible depth is capped at the channel's level count
(5–50 levels by venue: OKX `books5`, Bitget `books15`, Binance `depth20`,
Bybit `orderbook.50`). Depth walks that exhaust visible levels
are rejected as `insufficient_depth` and never extrapolated
(`triarb/pricing/edge.py`, `triarb/pricing/feasibility.py`) — conservative
by construction. Full incremental handling is the Planned upgrade (§6–§7).

## 2. Data structures

**Implemented** in `triarb/exchange/base.py` and
`triarb/pricing/orderbook.py`.

`BookSnapshot` (`triarb/exchange/base.py`) — one parsed update, hot path:
`__slots__`, plain float tuples, no pydantic:

| Field | Meaning |
|---|---|
| `symbol` | Venue symbol |
| `bids` | `(price, size)` levels, descending by price, exactly as published |
| `asks` | `(price, size)` levels, ascending by price, exactly as published |
| `ts_exchange_ms` | Venue's own timestamp (retained for logging/analysis; **never** used for staleness) |
| `recv_ns` | Local `time.perf_counter_ns()` at frame receipt in the adapter |
| `recv_wall_ms` | Local wall-clock ms at frame receipt (for log correlation) |

Levels are never averaged, smoothed, or reordered.

`Book` / `BookStore` (`triarb/pricing/orderbook.py`):

- `BookStore.apply(snap)` performs **verbatim replacement**: `bids`, `asks`,
  and all timestamps are overwritten wholesale; a per-book `updates` counter
  increments. Books are mutated in place (`__slots__`) to avoid allocation
  churn on the hot path.
- `BookStore.get(symbol)` returns `None` for a never-seen symbol — treated
  as `missing_book` by the evaluator, never as an empty book.
- `BookStore.mark_all_stale()` invalidates every book (see §4).

Tests: `tests/test_orderbook.py` (replacement, staleness, disconnect
invalidation).

## 3. Staleness: local monotonic clock

**Implemented.** Staleness is measured from `recv_ns` — the local monotonic
clock captured in the adapter at frame receipt
(`triarb/exchange/bitget.py::stream_books`) — never from the venue
timestamp. Venue clock skew therefore cannot mask a stalled feed, and a
venue that back-dates or freezes its `ts` field changes nothing.

- `Book.age_ms(now_ns)` returns milliseconds since the last locally received
  update; a book with `recv_ns == 0` (never updated, or invalidated) reports
  infinite age.
- `Book.is_stale(now_ns, max_age_ms)` applies the configured bound. The
  Phase 1 knob is `observer.staleness_ms` (default **300 ms**,
  `triarb/config/models.py`); it is this build's realization of the §15
  threshold named `MAX_BOOK_AGE_MS`.

Consumers of staleness:

- The float screen (`triarb/pricing/edge.py::evaluate_cycle`) rejects any
  cycle whose leg book exceeds the bound (`reason="stale_book"`) and records
  the *maximum* leg age as `max_age_ms` on every successful evaluation.
- A sweeper task (`triarb/observer.py::_sweep_loop`, every 0.25 s) closes
  active opportunities whose leg books went stale *between* updates, logging
  `cycle_ended` with `end_reason="stale_book"`.
- The Decimal feasibility pass rejects a missing/invalidated book
  (`triarb/pricing/feasibility.py`).

## 4. Disconnect handling: `mark_all_stale`

**Implemented.** Adapters must emit `StreamStatus` control events around
every connection gap (`triarb/exchange/base.py`), so staleness never depends
on message flow alone. On `StreamStatus.DISCONNECTED` the observer
(`triarb/observer.py::_on_stream_status`):

1. Increments the `ws_disconnects` counter.
2. Calls `BookStore.mark_all_stale()` — every book's `recv_ns` is zeroed,
   making its age infinite until a fresh snapshot arrives.
3. Ends every active opportunity with `end_reason="ws_disconnect"`.

Nothing is interpolated across the gap; detection resumes only when fresh
snapshots arrive. The Bitget adapter reconnects internally with exponential
backoff (1 s → 30 s cap) and resubscribes; because the channel is
snapshot-only, the first post-reconnect message is a complete, valid book —
the §13 "reconnect without resynchronization" invalidity condition is
satisfied without a resync protocol. Test evidence:
`tests/test_observer.py::TestLifecycle::test_disconnect_ends_opportunities_and_stales_books`.

## 5. The honest caveat: quiet symbols vs stalled feeds

**Implemented behavior, honestly limited.** Snapshot channels push only on
change. A symbol that trades nothing for two seconds and a feed that
silently stalled for two seconds produce the identical observation: no
message. The engine cannot distinguish them, and does not pretend to.

How `max_age_ms` handles it:

- Books older than `observer.staleness_ms` are treated as **unusable**, not
  trusted (`stale_book` reject; sweeper closes the opportunity). This is
  deliberately pessimistic: it discards some genuinely-unchanged quiet books
  rather than ever pricing off a stalled one.
- Every `cycle_detected` event carries `max_book_age_ms` — the oldest leg's
  age at evaluation time — so post-analysis can discard detections priced
  off aging legs at any stricter threshold the analyst chooses
  (`triarb/observer.py::_evaluate`; README "Reading the output").
- Opportunity persistence (`persisted_ms` in `cycle_ended`) is measured from
  the feed's own updates, and is documented as such — a quiet feed cannot
  inflate persistence beyond the staleness bound because the sweeper closes
  the cycle.

## 6. §13 requirements: today vs the Planned full engine

The master prompt §13 defines the full order-book engine. Status of each
tracked item:

| §13 item | Status | Notes |
|---|---|---|
| Initial snapshot | **Implemented** | Trivially: every message is a full snapshot (`BookStore.apply`) |
| Incremental updates | **Implemented** inside the Bybit adapter only (`BybitBookAssembler` emits complete snapshots downstream) | Engine-level incremental handling for other venues remains **Planned** (§13) |
| Sequence numbers | **Implemented** for Bybit (`u`-continuity in `BybitBookAssembler`; gap → resync) | Snapshot channels on the other venues do not need sequence tracking; engine-level tracking **Planned** (§13) |
| Update timestamps | **Implemented** | `ts_exchange_ms`, `recv_ns`, `recv_wall_ms` on every snapshot |
| Bid levels / Ask levels | **Implemented** | Verbatim venue levels, capped at channel depth |
| Checksum where available | **Planned** (§13) | Required when incremental channels land on venues that publish checksums |
| Connection identity | **Planned** (§13) | `StreamStatus.detail` carries URL/exception text, but books do not record which connection produced them |
| Data age | **Implemented** | `Book.age_ms` from local monotonic clock (§3) |
| Synchronization status | Partial — **Planned** (§13) | Today's status is binary fresh/stale via `recv_ns`; an explicit *unsynchronized* state machine arrives with incremental books |

§13 invalidity conditions:

| A book is invalid when… | Status |
|---|---|
| A sequence gap occurs | **Planned** (§13) — no sequences exist yet; on snapshot channels a gap is a missed change, bounded by staleness |
| An update arrives out of order | **Planned** (§13) — snapshot replacement makes ordering harmless today; explicit detection lands with deltas (see TBD-OB-3) |
| The snapshot is missing | **Implemented** — `missing_book` reject (`triarb/pricing/edge.py`) |
| The checksum fails | **Planned** (§13) |
| Data is older than the configured threshold | **Implemented** — `observer.staleness_ms` (§3) |
| The WebSocket reconnects without resynchronization | **Implemented** — `mark_all_stale` on disconnect; books stay unusable until a fresh snapshot (§4) |
| The exchange reports maintenance | **Planned** (§13) — nearest today: the metadata refresh loop logs `symbols_degraded` when a subscribed symbol leaves the online set (`triarb/observer.py::_metadata_loop`) |
| Time synchronization exceeds the allowed limit | **Planned** (§13) — mitigated today because staleness never uses the venue clock (§3) |

§13 recovery procedure (mark unsynchronized → stop using → fetch new
snapshot → reapply valid updates → resume only after validation): the
snapshot-channel degenerate form is **Implemented** — mark stale, stop
using, wait for the next push (which *is* the new snapshot), resume. The
full five-step form with REST snapshot fetch and delta replay is
**Planned** (§13) and becomes mandatory the moment any incremental channel
is adopted.

**Cross-book timestamp skew** (§13: "Measure cross-book timestamp skew.
Reject a triangle when its three books are not temporally consistent"):
**Planned** (§13, §15 `MAX_BOOK_SKEW_MS`). Today's proxy: every leg must be
within `staleness_ms` of *now* on the same local monotonic clock, which
bounds pairwise receive-time skew across the three legs at
`staleness_ms` (default 300 ms). An explicit, separately configured skew
gate does not exist yet — see TBD-OB-1.

## 7. Planned upgrade path: per-venue channel strategy

Per D-002 (`docs/DECISIONS.md`), every adapter — including future ones —
must emit *complete snapshots* downstream, so `BookStore` and everything
above it stay delta-free even when a venue forces delta handling inside its
adapter. Channel facts below were verified against official documentation
with adversarial re-verification per D-005.

| Venue | Observer channel | Status | Notes |
|---|---|---|---|
| Bitget | `books15` (also accepts `books1`/`books5`; `"auto"` → `books15`) | **Implemented** — `triarb/exchange/bitget.py` | Snapshot channels push the full book on change. The incremental `books` channel (merge + checksum) is explicitly rejected with a `ValueError` in this phase. Later upgrade to `books`: **Planned** (§13; D-002 revisit criteria) |
| Binance | Partial book depth streams `<symbol>@depth20@100ms` | **Implemented** — `triarb/exchange/binance.py` (offline-fixture-verified; live soak pending) | Each push is a full top-N snapshot, so it slots into today's engine unchanged. Later upgrade: diff-depth stream + REST snapshot with `U`/`u` update-ID sequencing (Binance publishes no checksum) — **Planned** (§13) |
| OKX | `books5` | **Implemented** — `triarb/exchange/okx.py` (offline-fixture-verified; live soak pending) | `books5` pushes a full 5-level snapshot on every update. Later upgrade: `books` (400 levels, snapshot + updates with `seqId` and checksum) — **Planned** (§13) |
| Bybit | `orderbook.50` | **Implemented** — `triarb/exchange/bybit.py` (offline-fixture-verified; live soak pending) | Bybit spot has **no multi-level snapshot-only channel**: `orderbook.N` sends one snapshot then deltas. `BybitBookAssembler` merges deltas *internally* with strict `u`-sequence gap detection (gap → empty book emitted + topic resubscribe; tested in `tests/test_bybit_parse.py::TestBookAssembly`), so the only §13 sequence handling in the system stays contained in one adapter module and only complete snapshots flow downstream |

Configuration: `observer.depth_channel` (default `"auto"`,
`triarb/config/models.py`) selects the venue-native channel; adapters
validate the value and reject channels they do not support in this phase
(**Implemented** for Bitget). Adapter defaults for `"auto"` are recorded in
the config model comment: bitget `books15`, binance `depth20@100ms`, okx
`books5`, bybit `orderbook.50`.

## 8. TBD register (per CLAUDE.md §6)

- **TBD-OB-1 — `MAX_BOOK_SKEW_MS` value and timestamp basis.** The explicit
  cross-book skew gate (§13, §15) has no decided threshold or measurement
  basis (venue `ts` vs local receive time). *Safest reversible assumption
  recorded:* skew is implicitly bounded by the per-leg staleness check on
  the local monotonic clock (≤ `staleness_ms`, default 300 ms); when the
  explicit gate lands it will use local receive time (immune to venue clock
  skew, consistent with §3) behind its own config knob.
- **TBD-OB-2 — whether snapshot depth (5–50 levels by venue) suffices for
  execution-phase sizing.** Undecidable until observer data exists. *Safest
  reversible assumption:* walks that exhaust visible depth are rejected as
  `insufficient_depth` and never extrapolated; the incremental upgrade is
  taken only if sizing analysis shows profitable size exceeding visible
  depth (D-002 revisit criteria).
- **TBD-OB-3 — out-of-order snapshot detection.** Today a late-arriving
  older snapshot would overwrite a newer one (last-received-wins); on
  snapshot channels over a single ordered TCP stream this cannot occur
  without a disconnect, which already invalidates all books. *Safest
  reversible assumption:* last-received-wins is acceptable while
  snapshot-only; an explicit `ts_exchange_ms`-regression counter is added
  together with the incremental upgrade, where reordering becomes possible.
