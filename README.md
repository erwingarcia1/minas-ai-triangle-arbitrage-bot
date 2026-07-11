# triarb — intra-exchange spot triangular arbitrage

A triangular arbitrage platform operating entirely within one spot exchange
at a time — Bitget, Binance Global, Bybit, and OKX Global all ship behind
the same adapter interface; each venue runs as an independent observer
process. Built in phases with a hard review gate between each; governed by
the master prompt in `CLAUDE.md` (read that first, then `PROGRESS.md`).

**Current status: Phase 1 — Observer (`mode: market_data_only`).** This
build streams order books, enumerates and prices triangles, and logs every
detected cycle. **It contains no order-placement, balance, withdrawal, or
transfer code of any kind**, and the startup mode gate refuses every mode
except `market_data_only`. Run it for several days before deciding whether
Phase 2 (paper execution) is worth building: the logs answer the only
question that matters — *do net-positive cycles exist at your fee tier,
your size, and your latency?*

---

## The strategy in one paragraph

A triangle is a cycle of three markets returning to the currency you hold,
e.g. USDT → BTC (buy BTC/USDT) → ETH (buy ETH/BTC) → USDT (sell ETH/USDT).
Profit exists only when the *product of the three executable exchange rates*,
after taker fees on **all three** legs, exceeds 1. "Executable" means walking
the order book at your size — not multiplying best bid/ask.

## Why most triangles won't be profitable (read this first)

- **The fee hurdle eats almost everything.** Three taker legs at 0.1% require
  ~30 bps of gross dislocation. Persistent dislocations on liquid pairs are
  typically 0–5 bps, because market makers price the cross pair *from* the
  two majors. What remains above 30 bps is usually one of: a stale book, an
  illiquid pair whose depth can't fill even $100, or a symbol in a halt/
  auction state.
- **You are racing colocated competitors.** Cycles vanish in tens of
  milliseconds. Co-located in the venue's region you see 5–15 ms round trip;
  from North America 200 ms+, meaning most detected cycles are gone before
  your first IOC arrives. The observer measures persistence so you can see
  exactly what fraction would have survived your latency.
- **Rounding kills marginal edges.** Lot steps and min-notional rules strand
  dust on every leg. A 5 bps paper edge at $100 size frequently rounds to a
  loss. The observer runs an exact Decimal simulation of the rounded orders
  and reports `net_after_rounding_bps` and a `rounding_killed` counter.
- **Adverse selection.** The cycles slow players get to hit are
  disproportionately the ones faster players declined.

If several days of observer logs show zero feasible net-positive cycles that
persisted longer than your round-trip latency — the honest expected outcome
at 0.1% taker from a non-colocated box — the correct next step is a better
fee tier or a different strategy, not Phase 3.

### Fee-tier impact (3 legs, hurdle = 1/(1−f)³ − 1)

| Taker per leg | Effective 3-leg hurdle | Note |
|---|---|---|
| 0.100% | **30.06 bps** | Bitget/Binance default tier |
| 0.080% | **24.04 bps** | 0.1% with BGB/BNB-style 20% token discount |
| 0.075% | **22.53 bps** | typical mid VIP tier |
| 0.060% | **18.02 bps** | high-volume tier |

The hurdle is compounded, not additive — three legs at 0.1% cost ~30.06 bps,
not 30. `triarb.pricing.fees.hurdle_bps` is the single source of these
numbers and is unit-tested against this table.

---

## Repo structure

```
triarb/
├── config/        # pydantic Settings + YAML loader (rejects unknown keys);
│                  # operating-mode gate (market_data_only only, in this build)
├── exchange/      # adapter interface; native WS+REST adapters for
│                  # bitget / binance / bybit / okx; registry
├── graph/         # currency graph, triangle enumeration, liquidity filters
├── pricing/       # book store, depth walking, fee compounding,
│                  # float edge screen, exact Decimal rounding feasibility
├── monitoring/    # JSON-lines logging, counters/histograms, rich dashboard
├── observer.py    # Phase 1 orchestrator (hot path lives here)
└── cli.py         # `triarb observe` — the only command in Phase 1
config/config.example.yaml
docs/              # architecture, math, threat model, capability matrix, runbooks
tests/             # offline test suite; no network required
```

Planned for later phases (deliberately absent now): `execution/` (IOC leg
sequencer + unwind state machine), `risk/` (kill switch, loss limits),
`accounting/` (per-cycle PnL, reconciliation).

## Design decisions

- **WebSocket library: `websockets`** (+ `aiohttp` for REST, `orjson` for
  parsing). Chosen over aiohttp's WS client because Bitget requires
  application-level heartbeats (literal `ping` text frames every ~25 s) and
  `websockets` gives explicit control of ping/pong and close semantics with
  a pure-asyncio API. ccxt is not on the hot path — native protocol only.
- **Depth channels: snapshots only, per venue.** Snapshot channels eliminate
  an entire class of book-corruption bugs while we only observe: Bitget
  `books15`, Binance partial-depth `depth20@100ms`, OKX `books5` — every
  push replaces the whole book. Bybit spot has no multi-level snapshot
  channel, so its adapter consumes `orderbook.50` (snapshot+delta), merges
  internally with strict `u`-sequence checking, resubscribes on any gap,
  and emits only complete snapshots downstream. Walks that exhaust visible
  levels are counted as `insufficient_depth`, never extrapolated. Full
  incremental books are a Phase 4 upgrade if size demands it (see
  `docs/DECISIONS.md` D-002).
- **Two-stage pricing.** Hot path: float depth walk + fee compounding
  (~2.8 µs per triangle re-price on commodity hardware, measured; the
  `reprice_latency_us` histogram tracks it live). Cold path, only for
  candidates above threshold: exact `Decimal` simulation of the three IOC
  orders with venue price/quantity precision, min-qty, min-notional, and
  dust accounting. Unroundable triangles are rejected *and logged* before
  any capital would be at risk.
- **Triangle enumeration.** Currencies are nodes, spot markets edges. For
  quote currency Q, every ordered pair (A, B) with markets Q↔A, A↔B, B↔Q
  yields the directed cycle Q→A→B→Q; both directions are kept as distinct
  opportunities. O(deg(Q)·avg-deg) at startup, then filtered by 24h USDT
  volume and quoted spread per leg, ranked by weakest-leg liquidity, capped
  (`max_triangles`). Or pin a whitelist and skip discovery.
- **Books are never averaged or smoothed.** Every snapshot replaces the book
  verbatim; staleness is measured from local monotonic receive time so venue
  clock skew can't hide a stalled feed.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                       # full suite, all offline

cp config/config.example.yaml config/config.yaml   # edit to taste
triarb observe --config config/config.yaml
# or with the terminal dashboard (JSON logs go to triarb-observer.jsonl):
triarb observe --config config/config.yaml --dashboard
# bounded run:
triarb observe --config config/config.yaml --duration 3600
```

**Phase 1 needs no API keys** — it touches only public endpoints. For later
phases, keys come exclusively from a gitignored `.env` (see `.env.example`):

- [ ] Spot **trade-only** permission
- [ ] Withdrawals **disabled** (this repo contains no withdrawal/transfer code, ever)
- [ ] Transfers **disabled**
- [ ] IP whitelist pinned to the bot host
- [ ] Live mode (Phase 3+) requires **both** `--live` and `CONFIRM_LIVE=yes` — paper is the default forever

## Reading the output

All logs are JSON lines. The events that matter:

| event | meaning |
|---|---|
| `universe` | discovery result: cycles found, dropped by volume/spread/cap, fee hurdle at your tier |
| `cycle_detected` | a triangle crossed your threshold: `gross_bps`, `net_bps` (after fees, at your notional, depth-walked), `net_after_rounding_bps`, `feasible`, `reject_reason`, `stranded_dust`, `max_book_age_ms` |
| `cycle_ended` | the edge disappeared: `persisted_ms`, `peak_net_bps`, `ever_feasible`, `end_reason` (`edge_gone` / `stale_book` / `ws_disconnect`) |
| `stats` | periodic counters + histograms (edge distribution, re-price latency) |
| `stream_status` | WS connect/disconnect/error — gaps mark all books stale |

Useful jq one-liners:

```bash
jq -r 'select(.event=="cycle_detected") | [.tri_id, .net_bps, .net_after_rounding_bps, .feasible] | @tsv' observer.jsonl
jq 'select(.event=="cycle_ended") | .persisted_ms' observer.jsonl | sort -n | uniq -c
```

The decision rule after a multi-day run: count cycles with
`feasible == true` and `persisted_ms` comfortably above your measured round
trip. If that count is ~0, Phase 2+ cannot make money at this tier/latency.

Honest caveat on persistence: snapshot channels only push on change, so
persistence is measured from the feed's own updates. `max_book_age_ms` is
attached to every detection so you can discard detections priced off aging
legs during analysis.

## Latency expectations

| Where you run | Book-update → your process | Realistic outcome |
|---|---|---|
| Same region as venue matching engine | ~5–15 ms | You see cycles while they exist |
| Same continent | ~30–80 ms | Marginal |
| North America → Asia venue | 200 ms+ | Most detected cycles are gone on arrival |

The in-process re-price (book update → signal) is ~3 µs per affected
triangle; network dominates everything. A proper region-deployment note
ships with Phase 4.

## Phase plan

- [x] **Phase 1 — Observer** (this build): feeds + graph + pricing for all
  four venues (Bitget, Binance, Bybit, OKX — one process per venue), cycle
  lifecycle logging, rounding feasibility, metrics, dashboard, operating-mode
  gate. No order code.
- [ ] **Phase 2 — Paper execution**: leg sequencer with re-validation between
  legs, IOC-only simulated fills with a pessimistic fill model (book moves
  one update against you between legs), unwind state machine, risk module
  (kill switch), accounting, daily report.
- [ ] **Phase 3 — Live, minimum size**: one whitelisted triangle, minimum
  notional, kill switch verified by deliberately triggering it first.
- [ ] **Phase 4 — Hardening**: per-venue rate-limit budget, error-code
  matrix, reconnect chaos tests, Docker, region deployment note.

Each phase stops for review before the next begins.

## Disclaimer

Educational/research software. Crypto trading can lose your entire stake;
nothing here is financial advice. The observer exists precisely so you can
verify — with your own data — that the strategy is (or is not) viable before
risking a dollar.
