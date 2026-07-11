# OPERATIONS_RUNBOOK.md — operating the MARKET_DATA_ONLY observer

> Status labels: **Implemented** = true of the code today, with a file
> reference. **Planned** = required by the master prompt (`CLAUDE.md`
> Part B, section cited) or the README phase plan for a later phase.
> This build is an observer: it streams public order books, prices
> triangles, and logs cycles. **It contains no order, balance, withdrawal,
> or transfer code of any kind.** Nothing in this runbook implies the
> system is profitable or production-ready — the README's economics
> section (fee hurdle, latency reality, adverse selection) is the honest
> frame for every number the observer emits.

## 1. What you are operating

**Implemented.** One process = one venue in `MARKET_DATA_ONLY` mode.
`triarb observe` (`triarb/cli.py`) loads a YAML config, passes it through
the `ensure_supported_mode` startup gate (`triarb/config/models.py`),
creates the venue adapter (`triarb/exchange/registry.py`), and runs the
`Observer` (`triarb/observer.py`). The gate refuses `PAPER_TRADING`,
`SHADOW_TRADING`, and `LIVE_TRADING` with precise messages; `LIVE_TRADING`
can never be enabled by config alone (authorization chain in `CLAUDE.md`
Part B §3). Phase 1 touches only public endpoints — **no API keys are read
anywhere**.

## 2. Setup

**Implemented.** Requires Python 3.11+ (`pyproject.toml`).

```bash
cd minas-ai-triangle-arbitrage-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest            # all tests are offline; no network required (175 passing at time of writing)
```

Before adopting any code change, all three gates must be clean
(`CLAUDE.md` Part A, "How to work in this repo"):

```bash
pytest && ruff check . && mypy triarb
```

## 3. Configuration walkthrough

**Implemented** in `triarb/config/models.py`, loaded by
`triarb/config/loader.py`. Copy `config/config.example.yaml` to
`config/config.yaml` and edit. Every model sets `extra="forbid"`: a typo'd
or unknown key aborts startup with the offending field — never a silent
default. A missing file or unparseable YAML also aborts before any network
connection.

| Key | Default | Meaning |
|---|---|---|
| `venue` | `bitget` | One of `bitget`, `binance`, `bybit`, `okx`. All four ship a public market-data adapter (`triarb/exchange/registry.py`). |
| `mode` | `market_data_only` | Only startable mode in this build. `paper_trading` / `shadow_trading` fail the gate as "requires the execution engine planned for a later phase"; `live_trading` gets the distinct §3 authorization-chain refusal. |
| `quote_currency` | `USDT` | The currency every triangle starts and ends in. Uppercased on load. |
| `triangles.auto_discovery` | `true` | `true`: enumerate all viable triangles from the live symbol list, filter, rank by weakest-leg liquidity, cap. `false`: use only `whitelist`. |
| `triangles.whitelist` | `[]` | Pinned cycles, format `USDT-BTC-ETH` (direction matters). Whitelisted triangles bypass `filters` — you pinned them deliberately. |
| `triangles.max_triangles` | `150` | Hard cap on triangles in the hot path (1–2000). |
| `filters.min_24h_quote_volume_usdt` | `2000000` | Every leg of a discovered triangle must clear this 24h USDT volume. |
| `filters.max_spread_bps` | `15.0` | Maximum quoted spread per leg at discovery time. |
| `fees.taker_bps` | `10.0` | Taker fee per leg in bps (0–100). **Configured, not fetched** — set it to your actual tier or every net figure is wrong. |
| `fees.maker_bps` | `10.0` | Reserved; the observer prices taker-only cycles. |
| `fees.token_discount_enabled` | `false` | BGB/BNB-style fee-token discount toggle. |
| `fees.token_discount_multiplier` | `0.8` | Multiplier applied to the taker rate when the discount is enabled. |
| `observer.notional` | `100.0` | Quote-currency size every edge is depth-walked at. Keep it at what you would actually trade. |
| `observer.edge_threshold_bps` | `0.0` | Net edge (after fees, before rounding) at/above which a cycle logs `cycle_detected`. |
| `observer.staleness_ms` | `300` | A leg book older than this makes the evaluation unusable (`stale_skips`) and closes active opportunities (`end_reason=stale_book`). |
| `observer.depth_channel` | `auto` | Venue-native depth channel, or `auto` for the adapter default (bitget `books15`, binance `depth20@100ms`, okx `books5`, bybit `orderbook.50`). Adapters reject channels they do not support. |
| `observer.stats_interval_s` | `60.0` | Interval of the periodic `stats` log line. |
| `observer.metadata_refresh_s` | `3600.0` | Symbol-metadata re-fetch interval; `0` disables (see §7.2). |

## 4. Running

**Implemented** (`triarb/cli.py`).

```bash
triarb observe --config config/config.yaml                  # JSON logs to stdout
triarb observe --config config/config.yaml --log-file run.jsonl   # stdout AND file
triarb observe --config config/config.yaml --dashboard      # rich terminal dashboard;
                                                            # logs go to triarb-observer.jsonl
triarb observe --config config/config.yaml --dashboard --log-file run.jsonl
triarb observe --config config/config.yaml --duration 3600  # bounded run (seconds)
triarb observe --config config/config.yaml --verbose        # DEBUG-level logging
```

Log routing rules (`setup_logging` in `triarb/monitoring/jsonlog.py`,
wired in `triarb/cli.py`):

- No flags: JSON lines to stdout.
- `--log-file PATH`: append to `PATH` **and** stdout.
- `--dashboard`: the terminal belongs to the dashboard, so logs go only to
  the file (default `triarb-observer.jsonl` if `--log-file` is omitted).
  The dashboard is cosmetic — any render error is logged and the observer
  keeps running; **the JSONL stream is always the source of truth**
  (`triarb/monitoring/dashboard.py`).

Running multiple venues today means multiple independent processes, one
per venue, each with its own config and log file (**Implemented** — the
registry ships all four adapters; per-process isolation is decision D-004
in `docs/DECISIONS.md`). Caveat from `PROGRESS.md`: the Binance/Bybit/OKX
adapters are verified against official docs and offline fixtures only; a
live soak per venue is the recorded next validation step.

## 5. Reading the JSONL events

**Implemented.** Every line is one JSON object: `ts` (ISO-8601 UTC),
`level`, `logger`, `event`, plus event fields at the top level
(`triarb/monitoring/jsonlog.py`). Full field-by-field catalog:
`docs/OBSERVABILITY.md` §2. Operational summary:

| Event | When | What to look at |
|---|---|---|
| `startup` | process start | `venue`, `config`, and the "no order code exists in this build" note |
| `universe` | after discovery | `triangles_kept`, filter attrition (`dropped_*`), `fee_hurdle_bps` — sanity-check these against your config before trusting a run |
| `cycle_detected` | first evaluation at/above threshold | `net_bps`, `net_after_rounding_bps`, `feasible`, `reject_reason`, `stranded_dust`, `max_book_age_ms` |
| `cycle_ended` | edge disappears / book stales / WS drops | `end_reason` (`edge_gone` / `stale_book` / `ws_disconnect` / `missing_book` / `insufficient_depth`), `persisted_ms`, `peak_net_bps`, `ever_feasible` |
| `stats` | every `stats_interval_s` | counters + `reprice_latency_us` + `net_edge_bps` histograms |
| `stream_status` | WS connect/disconnect/error | see §6 — gaps invalidate persistence measurements |
| `metadata_refreshed` / `symbols_degraded` | metadata loop | see §7.2 |
| `shutdown` | clean exit | final metrics snapshot for the whole run |

```bash
jq -r 'select(.event=="cycle_detected") | [.tri_id, .net_bps, .net_after_rounding_bps, .feasible] | @tsv' run.jsonl
jq 'select(.event=="cycle_ended") | .persisted_ms' run.jsonl | sort -n | uniq -c
jq 'select(.event=="stream_status")' run.jsonl
```

All figures are **theoretical** — depth-walked from public books at your
configured notional and fee. Nothing is realized, simulated, or executed.

## 6. Health signals

All **Implemented** (`triarb/observer.py`, `triarb/monitoring/metrics.py`).

- **`stream_status`** (WARNING level): `kind` is `connected`,
  `disconnected`, or `error`. On `disconnected` the observer marks **all**
  books stale (`BookStore.mark_all_stale`, `triarb/pricing/orderbook.py`),
  closes every active opportunity with `end_reason=ws_disconnect`, and
  resumes detection only when fresh snapshots arrive — nothing is
  interpolated across the gap. Adapters reconnect internally with
  exponential backoff (1 s → 30 s cap in `triarb/exchange/bitget.py`) and
  resubscribe.
- **`ws_disconnects` / `ws_errors` counters** (in `stats`): occasional
  disconnects are normal (Binance drops connections after 24 h by design —
  `triarb/exchange/binance.py`); a climbing rate means a flapping link or
  venue trouble, and every gap invalidates persistence measurement for the
  cycles it closed. See `docs/INCIDENT_RESPONSE.md` §3.2.
- **`stale_skips` counter**: evaluations skipped because a leg book
  exceeded `staleness_ms`. Staleness is measured from local monotonic
  receive time, so venue clock skew cannot mask a stalled feed
  (`triarb/pricing/orderbook.py`). A steadily climbing rate on a liquid
  universe means the feed, host, or event loop is falling behind.
- **`metadata_refresh_failures` counter**: a failed periodic
  `fetch_symbols` — the observer logs the exception, keeps the previous
  metadata, and retries next interval (`Observer._metadata_loop`).
  Sustained failures mean rounding-feasibility checks run on aging
  precision data.
- Secondary: `insufficient_depth` (walks that exhausted visible levels —
  counted, never extrapolated), `rounding_killed` (net-positive edges that
  died in exact-Decimal rounding), `book_updates`/`evaluations` rates, and
  the `reprice_latency_us` histogram (in-process re-price is ~µs;
  network dominates).

A healthy run: `stream_status connected` once per connection,
`book_updates` climbing steadily, `stale_skips`/`ws_disconnects` near
zero, `stats` arriving on schedule.

## 7. Routine tasks

### 7.1 Dependency updates

**Implemented** workflow (constraints in `pyproject.toml`):

```bash
source .venv/bin/activate
pip install -U -e ".[dev]"
pytest && ruff check . && mypy triarb    # all three must be clean before adopting
```

Pin/adjust version floors in `pyproject.toml`; record anything
architecture-level in `docs/DECISIONS.md` and log the iteration in
`PROGRESS.md` (`CLAUDE.md` Part A).

### 7.2 Metadata refresh

**Implemented** (`Observer._metadata_loop`, `triarb/observer.py`). Every
`metadata_refresh_s` the observer re-fetches symbol metadata to catch
precision changes and delistings (master prompt §9 requires refresh at
startup and on a schedule). Behavior:

- Success: `metadata_refreshed` logged; the metadata used by feasibility
  simulation is replaced.
- A subscribed symbol vanishes: `symbols_degraded` WARNING. **Phase 1 does
  not rebuild the triangle set mid-run — restart the process to rebuild
  the universe.**
- Failure: exception logged, `metadata_refresh_failures` incremented,
  previous metadata kept, retry next interval.

### 7.3 Log rotation and retention

**Implemented** limitation: `--log-file` uses an append-mode
`logging.FileHandler` with no built-in rotation
(`triarb/monitoring/jsonlog.py`). Multi-day runs grow one file
indefinitely. Retention policy is **TBD** (repo owner decision, recorded
per `CLAUDE.md` §6); safest reversible assumption until decided: rotate
externally at process restarts (e.g. date-stamped `--log-file` per run)
and keep raw JSONL for at least the full observation window it belongs
to, since the multi-day log **is** the Phase-2 go/no-go evidence.

## 8. Shutdown semantics

**Implemented** (`triarb/cli.py`):

- **SIGINT / SIGTERM**: signal handlers set a stop event; the observer
  task is cancelled and awaited, the adapter's network resources are
  released (`ExchangeAdapter.aclose`, idempotent), and a final `shutdown`
  event is logged carrying the complete metrics snapshot (counters + both
  histograms). In-process metrics reset on restart — the periodic `stats`
  lines and the final `shutdown` line are the durable record
  (`triarb/monitoring/metrics.py`).
- **`--duration N`**: same clean path, triggered by timeout instead of a
  signal.
- **Crash**: any unexpected exception in an observer task cancels the
  whole `asyncio.TaskGroup` and the process exits loudly with the
  traceback — no silent degradation (`triarb/observer.py` module
  docstring). Treat a crash as an incident (`docs/INCIDENT_RESPONSE.md`).

There is nothing financial to wind down: no open orders, balances, or
reservations exist in this build.

## 9. Planned operations (later phases — none of this exists today)

- **Orchestrated multi-venue deployment**: supervised one-process-per-venue
  operation with restart policy. **Planned** — README Phase 4; independent
  per-venue state is master prompt Part B introduction ("Each exchange
  must operate as an independent arbitrage venue").
- **Docker**: containerized deployment. **Planned** — README Phase 4;
  master prompt §5 lists Docker under supporting services.
- **Region deployment note**: placement guidance relative to venue
  matching engines (the README's latency table shows why: 5–15 ms
  same-region vs 200 ms+ cross-continent — most detected cycles are gone
  on arrival from a distant box). **Planned** — README Phase 4.
- **PAPER_TRADING / SHADOW_TRADING / LIVE_TRADING operation**: requires
  the execution, risk, and recovery engines of master prompt §16–§24,
  none of which exist. `LIVE_TRADING` additionally requires the full §3
  authorization chain; a config change or frontend action alone must
  never enable it.

## 10. TBD register (per CLAUDE.md §6)

| Item | Status | Safest reversible assumption recorded |
|---|---|---|
| Log rotation / retention policy | TBD | External rotation per run; keep raw JSONL for the full observation window (§7.3). |
| Process supervision (systemd vs Docker vs manual) | TBD until the Phase 4 Docker decision (README Phase 4) | Run manually or under any restart-on-exit supervisor; the observer is stateless across restarts (books rebuild from fresh snapshots; metrics reset). |
| Alerting delivery (nothing pushes notifications today) | TBD | Operator tails the JSONL / watches the dashboard; §24 operator notification is Planned with kill switches. |
