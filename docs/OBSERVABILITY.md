# OBSERVABILITY.md — logs, metrics, dashboard

> Status labels: **Implemented** = true of the code today, with a file
> reference. **Planned** = required by the master prompt (`CLAUDE.md`
> Part B, section cited) for a later phase. This build is MARKET_DATA_ONLY:
> every number it emits is theoretical/expected — **nothing is realized,
> simulated, or executed, because no order code exists**. Nothing here
> asserts profitability or production readiness; the README's economics
> section (fee hurdle, latency reality, adverse selection) is the honest
> frame for reading any of these numbers.

## 1. JSON-lines log format

**Implemented** in `triarb/monitoring/jsonlog.py`. Every log line is one
JSON object with a fixed envelope plus event-specific fields flattened to
the top level:

| Envelope field | Meaning |
|---|---|
| `ts` | ISO-8601 UTC timestamp, millisecond precision |
| `level` | `INFO` / `WARNING` / `ERROR` |
| `logger` | Python logger name (module path) |
| `event` | Event name (the log message) |
| `exc` | Present only when an exception is attached (formatted traceback) |

Robustness rules (`JsonFormatter`): a field orjson cannot serialize is
stringified via `default=str` rather than dropping the line; logging must
never raise into the hot path. `log_event(logger, level, event, **fields)`
is the single emission helper — `fields` land as top-level JSON keys.

Routing (`setup_logging`, wired in `triarb/cli.py`): JSON goes to stdout by
default; `--log-file` appends to a file; with `--dashboard` the terminal
belongs to the dashboard, so logs default to `triarb-observer.jsonl`.
Third-party debug chatter (`websockets`, `aiohttp`) is capped at WARNING so
the JSON stream stays parseable.

## 2. Event catalog

All **Implemented**. Emitters: `triarb/observer.py` unless noted.

### `universe` — startup discovery result (`Observer.prepare`)

| Field | Meaning |
|---|---|
| `venue`, `quote` | Active venue and quote (settlement) currency |
| `online_symbols`, `currencies` | Size of the tradeable universe |
| `raw_cycles` | Cycles enumerated before filtering |
| `dropped_no_ticker`, `dropped_volume`, `dropped_spread`, `dropped_cap` | Filter attrition, by cause |
| `triangles_kept`, `subscribed_symbols` | Final hot-path universe |
| `notional` | Quote-currency size every edge is priced at |
| `fee_rate_per_leg`, `fee_hurdle_bps` | Configured fee and the compounded 3-leg break-even hurdle (`triarb/pricing/fees.py::hurdle_bps`) |
| `edge_threshold_bps` | Detection threshold |
| `examples` | First 10 `tri_id`s kept |

### `cycle_detected` — a triangle crossed the threshold (`Observer._evaluate`)

Emitted once per opportunity, on the *first* evaluation at/above
`edge_threshold_bps`; subsequent evaluations update the active opportunity
in place without re-logging.

| Field | Meaning |
|---|---|
| `tri_id` | Cycle identity, e.g. `USDT>BTC>ETH` |
| `legs` | Human-readable leg list (`buy BTCUSDT / buy ETHBTC / sell ETHUSDT`) |
| `gross_bps` | Depth-walked edge before fees |
| `net_bps` | Depth-walked edge after compounded per-leg taker fees (float screen, `triarb/pricing/edge.py`) |
| `net_after_rounding_bps` | Exact-Decimal edge after venue rounding of all three simulated IOC orders (`triarb/pricing/feasibility.py`); `null` when the simulation rejected |
| `feasible` | `true` only if the rounded Decimal simulation completed *and* stayed net-positive |
| `reject_reason` | Feasibility reject cause (`qty_rounds_to_zero`, `min_qty`, `min_notional`, `insufficient_depth`, `missing_book`, `missing_metadata`) or `null` |
| `stranded_dust` | Per-currency dust the rounding would strand (Decimal strings) |
| `notional` | Size priced |
| `max_book_age_ms` | Oldest leg book's local age at evaluation — attach-your-own-staleness for post-analysis (see `docs/ORDER_BOOK_ENGINE.md` §5) |
| `worst_prices` | Worst (marginal) fill price per leg from the float walk |

### `cycle_ended` — the edge disappeared (`Observer._end_opportunity`)

| Field | Meaning |
|---|---|
| `tri_id` | Cycle identity |
| `end_reason` | `edge_gone` (evaluated below threshold), `stale_book` (evaluation or sweeper), `ws_disconnect`, `insufficient_depth`, `missing_book` |
| `persisted_ms` | Last-positive minus first-positive evaluation time — measured from the feed's own updates (snapshot-channel caveat: quiet feeds produce no evaluations) |
| `evals_while_active` | Evaluations while the edge was live |
| `peak_net_bps` | Best float net edge seen |
| `peak_net_after_rounding_bps` | Best rounded Decimal edge seen (`null` if never feasible-simulated) |
| `ever_feasible` | Whether any evaluation of this opportunity passed rounding feasibility net-positive |

### `stats` — periodic counter/histogram snapshot (`Observer._stats_loop`)

Emitted every `observer.stats_interval_s` (default 60 s) with the full
`Metrics.snapshot()` payload (§3): `counters`, `reprice_latency_us`,
`net_edge_bps`. This line is the durable record — in-process metrics reset
on restart. The `shutdown` event (`triarb/cli.py`) carries a final snapshot.

### `stream_status` — WebSocket control events (`Observer._on_stream_status`)

| Field | Meaning |
|---|---|
| `kind` | `connected` / `disconnected` / `error` (`StreamStatus` in `triarb/exchange/base.py`) |
| `detail` | URL, exception repr, or venue error text |

A `disconnected` gap marks **all** books stale and force-ends every active
opportunity with `end_reason="ws_disconnect"` — persistence numbers never
silently span a gap.

Supporting events: `startup` (mode/venue/config; notes "Phase 1: no order
code exists in this build"), `shutdown` (final metrics), `symbols_degraded`
and `metadata_refreshed` (`Observer._metadata_loop`).

## 3. Counters and histograms

**Implemented** in `triarb/monitoring/metrics.py` — dependency-free,
allocation-light (observed on every book update in the hot path). No
external metrics system exists; the periodic `stats` line is the export.

Counters (incremented in `triarb/observer.py`):

| Counter | Meaning |
|---|---|
| `book_updates` | Snapshots applied to the `BookStore` |
| `evaluations` | Triangle re-prices attempted |
| `stale_skips` | Evaluations rejected on the staleness bound |
| `insufficient_depth` | Evaluations where visible depth could not absorb the notional (never extrapolated) |
| `cycles_detected` / `cycles_ended` | Opportunity lifecycle transitions |
| `rounding_killed` | Detections whose float edge was positive but whose exact rounded simulation was not — the README's "rounding kills marginal edges" made measurable |
| `ws_disconnects` / `ws_errors` | Stream health |
| `metadata_refresh_failures` | Failed symbol-metadata refreshes |

Histograms (`Histogram`, fixed buckets; snapshot gives `total`, `mean`, and
per-bucket counts):

| Histogram | Buckets | Observed |
|---|---|---|
| `reprice_latency_us` | 50 … 50 000 µs (`LATENCY_BUCKETS_US`) | Per book update: full handler time covering every triangle re-priced for that symbol (`Observer._on_book`) |
| `net_edge_bps` | −50 … +50 bps (`EDGE_BUCKETS_BPS`) | Every successful evaluation's net edge — the live distribution of edges after fees, which is expected to sit almost entirely below zero (README fee-hurdle table) |

## 4. The rich dashboard

**Implemented** in `triarb/monitoring/dashboard.py`; enabled with
`triarb observe --dashboard`. A `rich.live.Live` view refreshed by the
observer's 0.25 s sweeper (render cap 2/s) showing: a header (venue, uptime,
book count, triangle count, message/evaluation totals), a counters table
(`cycles_detected`, `cycles_ended`, `rounding_killed`,
`insufficient_depth`, `stale_skips`, `ws_disconnects`), and the top 10
active opportunities by current net bps with peak, age, and feasibility.

Boundaries, stated plainly:

- The dashboard is cosmetic. Any render error is logged and swallowed; the
  JSON log stream is always the source of truth. When the dashboard owns
  the terminal, the CLI routes JSON to a file (§1).
- The §28 administrative dashboard (balances, executing cycles, kill
  switches, reconciliation health, …) is **Planned** (`CLAUDE.md` §28) —
  none of its subject matter exists in this build. Its rule that every
  financial result displays its mode (`THEORETICAL` / `PAPER` / `SHADOW` /
  `LIVE`) is honored today degenerately: every figure shown is
  THEORETICAL, because no other kind can exist (§6 below).

## 5. jq recipes

**Implemented** against the fields above (first two from the README):

```bash
# Detected cycles: id, net edge, rounded edge, feasibility
jq -r 'select(.event=="cycle_detected") | [.tri_id, .net_bps, .net_after_rounding_bps, .feasible] | @tsv' observer.jsonl

# Persistence distribution (ms)
jq 'select(.event=="cycle_ended") | .persisted_ms' observer.jsonl | sort -n | uniq -c

# The decision-rule count: feasible cycles that outlived your round trip (here 200 ms)
jq -r 'select(.event=="cycle_ended" and .ever_feasible==true and .persisted_ms > 200) | .tri_id' observer.jsonl | sort | uniq -c

# Detections priced off aging legs (tighten past staleness_ms in analysis)
jq 'select(.event=="cycle_detected" and .max_book_age_ms > 100) | {tri_id, net_bps, max_book_age_ms}' observer.jsonl

# Stream gaps that invalidated persistence measurements
jq -r 'select(.event=="stream_status") | [.ts, .kind, .detail] | @tsv' observer.jsonl

# Counter trend over the run
jq 'select(.event=="stats") | {ts, c: .counters}' observer.jsonl
```

The README's decision rule applies: if a multi-day run shows ~0 cycles with
`feasible == true` persisting comfortably beyond your measured round-trip
latency, later phases cannot make money at this fee tier and latency.

## 6. §27 performance measurement: Implemented vs Planned

The master prompt §27 defines the measurement funnel. Exact §27 names, with
status:

| §27 funnel stage | Status | Today's nearest measurement |
|---|---|---|
| theoretical opportunities | **Implemented** | `cycles_detected` counter + `cycle_detected` events (float net edge ≥ threshold at the configured notional) |
| depth-qualified opportunities | **Implemented** (observer-grade) | Detections with `feasible == true` (depth absorbed the notional *and* rounding survived); `insufficient_depth` and `rounding_killed` count the failures |
| risk-approved opportunities | **Planned** (§23, §27) | No risk engine exists |
| submitted cycles | **Planned** (§16–§18, §27) | No execution exists |
| completed cycles | **Planned** (§18, §27) | — |
| recovered cycles | **Planned** (§21, §27) | — |
| failed cycles | **Planned** (§18, §27) | — |
| false positives | **Planned** (§27) | Requires paper/shadow comparison against subsequent market activity; today's precursor is `persisted_ms` vs your measured round-trip latency |

§27 quantity tracking:

| §27 quantity | Status | Notes |
|---|---|---|
| Gross profit / Net realized profit / Recovery loss / Fill ratio / Partial-fill rate / Rejection rate / Recovery rate | **Planned** (§27) | Nothing is realized in this build; presenting any observer figure as these would violate Invariant 20 |
| Trading fees | Partial — **Implemented** as *modeled* fees only | Compounded per-leg taker fees inside `net_bps` (`triarb/pricing/fees.py`); actual-vs-estimated fee comparison is **Planned** (§12) |
| Slippage | **Planned** (§27) | Depth walking prices the *current* book; realized slippage needs fills |
| Rounding loss | **Implemented** (expected form) | `net_bps − net_after_rounding_bps` per detection, `stranded_dust`, `rounding_killed` counter |
| Profit in basis points | **Implemented** (theoretical form only) | `gross_bps`, `net_bps`, `net_after_rounding_bps` — expected values, never realized |
| Opportunity-to-order / Order-to-fill / Full-cycle latency | **Planned** (§27) | Only in-process latency exists today: `reprice_latency_us` |
| Profit by exchange / triangle / starting size / hour / strategy version | **Planned** (§27) | Grouping keys already exist on events (`venue` on `universe`/`startup`, `tri_id` and `ts` everywhere) for theoretical figures |

## 7. The §27 never-mix rule and the mode label

§27 (verbatim): never mix **theoretical profit**, **simulated profit**,
**shadow profit**, and **live realized profit**.

**Implemented — structural enforcement today.** The only mode this build can
start in is MARKET_DATA_ONLY: `ensure_supported_mode`
(`triarb/config/models.py`) refuses PAPER_TRADING, SHADOW_TRADING, and
LIVE_TRADING at startup, and no execution path exists. Therefore every
number in every log line is THEORETICAL by construction; the other three
categories cannot be produced, so no mixture is possible. Field names keep
the distinction visible: events say `net_bps` / `peak_net_bps`
(*expected*), never "profit" or "realized".

**Planned — label enforcement when other modes exist** (`CLAUDE.md` §27,
§28; Invariant 20):

- Every result-bearing event and accounting record will carry an explicit
  mode label from the §28 set — `THEORETICAL`, `PAPER`, `SHADOW`, `LIVE` —
  derived from the `Mode` enum that already exists
  (`triarb/config/models.py`), not from free text.
- Per-mode results stay in separate streams/ledgers; aggregation across
  labels is rejected at the schema level, and the §28 dashboard displays
  the label next to every financial figure.
- Invariant 20 ("theoretical profit is never presented as realized
  profit") gets an automated test when the first non-theoretical figure can
  exist, i.e. with Phase 2 paper trading.

## 8. TBD register (per CLAUDE.md §6)

- **TBD-OBS-1 — Prometheus-compatible metrics export.** §5 recommends it;
  not implemented. *Safest reversible assumption recorded:* the periodic
  `stats` JSON line is the interim durable/scrapeable record; an exporter
  can be added behind config later without changing the `Metrics` API,
  since `snapshot()` already produces a stable structure.
- **TBD-OBS-2 — mode-label mechanics.** Whether the Planned label is a
  per-event `mode` field, per-mode log files, or both is undecided.
  *Safest reversible assumption:* both — an explicit `mode` field on every
  result-bearing event *and* separate output files per mode, selected by
  config; redundancy is harmless and either half can be dropped later.
- **TBD-OBS-3 — log rotation/retention.** The file handler is append-only
  with no built-in rotation (`triarb/monitoring/jsonlog.py`). *Safest
  reversible assumption:* external rotation (e.g. logrotate with copy or
  restart-based truncation) is the operator's tool for multi-day runs;
  in-process rotation is revisited in Phase 4 operations hardening.
