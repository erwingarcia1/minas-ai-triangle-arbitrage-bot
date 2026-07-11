# Currency Graph

The directed conversion graph and triangle discovery pipeline, per master
prompt §10 (`CLAUDE.md` Part B). Claims are labeled **Implemented** (true of
the code today, file cited) or **Planned** (spec for a later phase, section
cited). This build is a MARKET_DATA_ONLY observer; the graph feeds pricing
and logging only — there is no execution consumer.

## The §10 directed-edge model

Every spot market `BASE/QUOTE` is two directed conversion edges:

- **Sell edge** `BASE → QUOTE`: sells the base asset into the quote asset.
  It consumes **bids** from the order book.
- **Buy edge** `QUOTE → BASE`: buys the base asset using the quote asset.
  It consumes **asks** from the order book.

**Implemented.** The consumption rule is realized in two places:

- `triarb/pricing/edge.py::evaluate_cycle` dispatches `Side.BUY` legs to
  `buy_base_with_quote(book.asks, …)` and `Side.SELL` legs to
  `sell_base_for_quote(book.bids, …)` (`triarb/pricing/depth.py`).
- `triarb/pricing/feasibility.py` does the same in exact `Decimal`
  (`_walk_buy_worst`/`_walk_buy_cost` over asks, `_walk_sell` over bids).

## Adjacency construction (`triarb/graph/builder.py`)

**Implemented.** `build_adjacency(symbols)` produces

```python
Adjacency = dict[str, dict[str, SymbolInfo]]   # currency -> currency -> market
```

- Only symbols with `status == "online"` are inserted (§9: reject suspended/
  offline/pre-open legs — applied at graph-build time).
- **Both directions of each market are inserted**: the same `SymbolInfo`
  serves `X → Y` and `Y → X`. The edge is direction-agnostic at this layer;
  the trade side is derived later (below).
- Collision handling: if a venue ever listed both orientations of the same
  pair (e.g. `ETH/BTC` and `BTC/ETH`), the second market to claim an
  `A → B` slot is **logged and ignored** — the builder never silently
  guesses which book to prefer. No known venue does this.

Evidence: `tests/test_triangles.py::TestEnumeration`
(`test_offline_symbols_excluded` among others).

## Side derivation (`triarb/graph/triangles.py`)

**Implemented.** `compile_triangle(cycle, adj)` turns a currency cycle into
three `Leg` objects. For each hop `cur_from → cur_to` it looks up the market
and derives the side from whether the **destination** currency is the
market's base:

```python
side = Side.BUY if market.base == cur_to else Side.SELL
```

i.e. acquiring the base is the buy edge (consumes asks); disposing of the
base is the sell edge (consumes bids). A missing market for any hop raises
`ValueError("no market for …")` at startup — compilation never invents
edges. Side derivation for both directions of the fixture triangle is pinned
in `tests/test_triangles.py::TestEnumeration::test_compile_sides`.

## Enumeration: Q → A → B → Q, both directions

**Implemented.** `enumerate_cycles(adj, quote)` in
`triarb/graph/triangles.py`: for the configured settlement currency `Q`,
every **ordered** pair `(A, B)` with markets `Q↔A`, `A↔B`, `B↔Q` yields the
directed cycle `Q → A → B → Q`. Self-loops and degenerate hops
(`A == Q`, `B == Q`, `B == A`) are excluded. Complexity is
`O(deg(Q) · avg-deg)` at startup.

### Deduplication semantics

§10 requires: *"Deduplicate equivalent cycles while preserving both trading
directions."* **Implemented** as a property of the enumeration itself:

- Because pairs are ordered, `Q→A→B→Q` and `Q→B→A→Q` are enumerated
  **separately** and kept as **distinct triangles** (`tri_id` `"Q>A>B"` vs
  `"Q>B>A"`). They price independently — one direction can be profitable
  while the other is not.
- Because every cycle is anchored at `Q`, no rotation duplicates exist:
  each undirected triangle appears exactly twice (once per direction),
  never more. There is no post-hoc dedup pass to get wrong.

Evidence: `tests/test_triangles.py::TestEnumeration::test_finds_both_directions`
asserts exactly `{("USDT","BTC","ETH"), ("USDT","ETH","BTC")}` for the
fixture universe.

### Scope: cycles through the settlement currency only

**Implemented (narrower than §10 by design).** §10 asks for *every* unique
three-edge cycle `A → B → C → A`; this build enumerates only cycles that
start and end in the configured settlement currency, because a Phase 1
observer models trading the currency you hold. Cycles not touching the
settlement currency are **Planned** — they only matter once
inventory-assisted execution (§16 Mode B) could hold intermediate assets,
which is gated far behind this phase.

## Configurable settlement currency

**Implemented.** `Settings.quote_currency` (`triarb/config/models.py`),
default `USDT`, upper-cased on validation — matching §1's safest default.
USDT triangles are not hard-coded: any held currency the venue quotes can be
configured (`config/config.example.yaml`).

**TBD:** §10 says "Allow configurable settlement asset**s**" (plural). This
build takes exactly one settlement currency per process run. Safest
reversible assumption per `CLAUDE.md` §6: run one observer process per
settlement currency; a multi-settlement universe in one process is a later
refactor that changes no on-disk state.

## Discovery pipeline (`triarb/graph/triangles.py::build_triangles`)

**Implemented.** `graph → cycles → liquidity filter → rank → cap`:

1. **Adjacency** from live `fetch_symbols()` (online symbols only).
2. **Enumeration** as above.
3. **Liquidity/spread filters** (auto-discovery mode), per leg using 24h
   tickers (`triarb/exchange/base.py::Ticker`):
   - a leg with no ticker drops the cycle (`dropped_no_ticker`);
   - `min(leg 24h USDT volumes) < filters.min_24h_quote_volume_usdt`
     (default 2 000 000) drops it (`dropped_volume`);
   - `max(leg quoted spread) > filters.max_spread_bps` (default 15) drops
     it (`dropped_spread`); a missing bid/ask makes `spread_bps` `+inf`, so
     half-quoted markets always fail this filter.
4. **Ranking** by weakest-leg liquidity: cycles sort descending by the 24h
   USDT volume of their least-liquid leg.
5. **Cap** at `triangles.max_triangles` (default 150; `dropped_cap` counts
   the excess).

If nothing survives, discovery **raises** rather than running an observer
with zero triangles. The drop accounting (`UniverseStats`) is logged at
startup as the `universe` event (`triarb/observer.py::prepare`).

### Whitelist mode

**Implemented.** With `triangles.auto_discovery: false`, only
`triangles.whitelist` is compiled. Entries look like `USDT-BTC-ETH`
(regex-validated at config load; direction matters — `USDT-BTC-ETH` means
`USDT → BTC → ETH → USDT`) and:

- must start at the configured settlement currency, else startup fails;
- **bypass volume/spread filters** (the user pinned them deliberately);
- **never bypass market existence** — a referenced market the venue does
  not list fails startup loudly with the missing edge named;
- an empty whitelist with auto-discovery off is a startup error.

Evidence: `tests/test_triangles.py::TestWhitelist` and `TestDiscovery`.

## §10 edge fields: implemented vs planned

§10 requires each edge to carry a specific field set. Where each stands
today:

| §10 edge field | Status | Where today |
|---|---|---|
| `exchange` | **Implemented (implicit)** | one venue per process (`Settings.venue`); every edge belongs to it. Becomes an explicit field when multi-venue state exists (§10) |
| `symbol` | **Implemented** | `SymbolInfo.symbol`, stored on each `Leg` |
| `source_asset` / `destination_asset` | **Implemented** | `Leg.cur_from` / `Leg.cur_to` (`triarb/pricing/edge.py`) |
| `side` | **Implemented** | `Leg.side` (`Side.BUY`/`Side.SELL`), derived at compile time |
| `order_book_reference` | **Implemented (by key)** | legs hold the venue symbol; books resolve through `BookStore.get(symbol)` at evaluation time (`triarb/pricing/orderbook.py`) rather than a stored object reference |
| `fee_model` | **Planned (§10, §12)** | fee rate is a single global config (`fees.taker_bps`), not stored per edge; per-edge maker/taker/fee-asset models arrive with fetched account fees |
| `trading_rules` | **Implemented (subset)** | tick/step/min-qty/min-notional in `SymbolInfo` (`triarb/exchange/base.py`), consumed by feasibility; the full §9 rule set (max quantity, max notional, market-order bounds, supported order types) is **Planned** |
| `status` | **Planned as a stored field** | used as a build-time filter (`online` only) and re-checked by the observer's metadata refresh, but not stored on the edge; mid-run route rejection on status change is **Planned** (§9) |
| `last_update_time` | **Implemented (on the book)** | `Book.recv_ns` / `age_ms` (`triarb/pricing/orderbook.py`); staleness is enforced per evaluation, not stored per edge |
| `sequence_number` | **Planned (§10, §13)** | Phase 1 uses snapshot depth channels — each update replaces the whole book, so there is no sequence to track. Arrives with the incremental-channel upgrade (checksums, gap detection) |

## Refresh behavior

**Implemented.** The graph and triangle set are built once at startup. The
observer's metadata loop (`triarb/observer.py::_metadata_loop`) re-fetches
symbol metadata on a timer and **warns** (`symbols_degraded`) if a
subscribed symbol leaves the online universe, but Phase 1 does not rebuild
the triangle set mid-run — the operator restarts to pick up a changed
universe. Automatic mid-run rebuild on instrument-status updates is
**Planned** (§9: trading rules refreshed after instrument-status updates).

## Test evidence

- `tests/test_triangles.py` — adjacency, both-direction enumeration, side
  compilation, offline exclusion, all filters, cap, whitelist rules,
  empty-universe failure.
- `tests/test_observer.py` — the compiled universe driving the live
  detection path offline.
