# Triangular Arbitrage Math

How this build computes the executable return of a three-leg cycle. Every
claim is labeled **Implemented** (true of the code today, file cited) or
**Planned** (required by the master prompt in `CLAUDE.md` Part B, not built
yet). This build is a MARKET_DATA_ONLY observer: everything below produces
log lines, never orders.

## Forbidden price sources (§11)

**Implemented.** Profitability is never computed from:

- last trade price,
- mid-market price,
- candles,
- displayed percentage changes,
- a single top-of-book price when the desired quantity exceeds that level.

The only price inputs to the math are actual order-book levels walked at the
configured notional (`triarb/pricing/depth.py`). A book too shallow for the
size returns `None` and the cycle is rejected with `insufficient_depth` —
the walk never extrapolates past the last visible level.
`tests/test_edge.py::test_depth_walk_worse_than_top_of_book` proves the walk
reports less than the top-of-book fiction when depth thins out.

## Depth walking (`triarb/pricing/depth.py`)

**Implemented.** Two pure float functions over `list[tuple[price, size]]`;
they are the hot path and allocate nothing beyond the returned tuple.

### Buy: spend quote, walk the asks

`buy_base_with_quote(asks, quote_amount) -> (base_bought, worst_price) | None`

Asks ascending by price. For each level, `level_cost = price * size`:

- if `remaining <= level_cost`, the level finishes the fill:
  `base_out += remaining / price`, return;
- otherwise consume the whole level: `base_out += size`,
  `remaining -= level_cost`.

Returns `None` when the book empties before the quote amount is fully spent.
`worst_price` is the deepest level touched.

### Sell: spend base, walk the bids

`sell_base_for_quote(bids, base_amount) -> (quote_received, worst_price) | None`

Bids descending by price. Symmetric: `remaining <= size` finishes the fill
with `quote_out += remaining * price`; otherwise the level is consumed whole.
Returns `None` on exhaustion.

Zero/negative prices or sizes at a level are skipped defensively. Evidence:
`tests/test_depth.py`.

## Fee model (`triarb/pricing/fees.py`)

**Implemented.** Every leg is modeled as a taker fill (all legs cross the
spread; the planned executor uses IOC), and the fee is applied to the
**received** currency: proceeds are multiplied by `(1 - f)` on each leg.
This models venues that charge buy-leg fees in base and sell-leg fees in
quote — a uniform multiplier on the received amount captures both exactly.
Fees charged in a third asset (BGB/BNB deduction and similar) are folded in
as a rate discount, not modeled as a separate fee asset — see the fee-asset
note under Planned cost terms below.

### The compounded hurdle

Three legs at rate `f` multiply the cycle by `(1 - f)^3`, so break-even
requires a gross multiplier of `1 / (1 - f)^3`. In bps:

```
hurdle_bps(f) = (1 / (1 - f)^3 - 1) * 10_000
```

`triarb.pricing.fees.hurdle_bps` is the single source of these numbers and
is unit-tested against this table
(`tests/test_fees.py::TestHurdle::test_fee_tier_table`, mirrored in the
README):

| Taker per leg | Effective 3-leg hurdle | Note |
|---|---|---|
| 0.100% | **30.06 bps** | Bitget/Binance default tier |
| 0.080% | **24.04 bps** | 0.1% with BGB/BNB-style 20% token discount |
| 0.075% | **22.53 bps** | typical mid VIP tier |
| 0.060% | **18.02 bps** | high-volume tier |

The hurdle is compounded, not additive: three legs at 0.1% cost ~30.06 bps,
not 30. `net_multiplier(gross, f, legs)` returns
`gross * (1 - f)^legs`; break-even at the hurdle is tested to 1e-12.

### Where the rate comes from

**Implemented.** The rate is **configured, not fetched**:
`fees.taker_bps` plus an optional token discount multiplier
(`FeesConfig.effective_taker_rate`, `triarb/config/models.py`). This is
exactly the §12 fallback for when the exact account rate cannot be
retrieved: a configurable conservative rate, with results treated as
estimates. **Planned:** fetching per-account fee rates
(`fetchAccountFeeRates`, §8) and per-fill fee reconciliation
(`estimated_fee` vs `actual_fee` vs `actual_fee_asset`, §12).

## The float screen (`triarb/pricing/edge.py`)

**Implemented.** `evaluate_cycle(triangle, store, notional, fee_rate,
now_ns, staleness_ms)` walks all three legs at `notional` (quote units) and
returns gross and net edge in one pass over the triangle:

- Per leg, the book is fetched and rejected if missing (`missing_book`) or
  older than `staleness_ms` (`stale_book`). The max age across legs is
  reported in every result.
- `Side.BUY` legs call `buy_base_with_quote` on the asks; `Side.SELL` legs
  call `sell_base_for_quote` on the bids.
- Two running amounts are carried: `gross_amt` (fee-free) and `net_amt`
  (multiplied by `keep = 1 - fee_rate` after every leg). Each leg walks the
  same book once per amount, because fees shrink the size entering later
  legs — gross and net cannot share a single walk.
- Any leg returning `None` rejects the cycle with `insufficient_depth`.

The output is exactly the §11 definition,
`net_return = final_settlement_amount / initial_settlement_amount - 1`,
expressed in bps:

```
gross_bps = (gross_amt / notional - 1) * 10_000
net_bps   = (net_amt   / notional - 1) * 10_000
```

Worked example (the test fixture, `tests/conftest.py` and
`tests/test_edge.py`): 100 USDT → 0.002 BTC at ask 50 000 → 0.04 ETH at ask
0.05 → 104 USDT at bid 2600. Gross +400 bps; net
`= (1.04 × 0.999³ − 1) × 10⁴ ≈ +370 bps` at 10 bps taker.

This screen is deliberately binary float (~µs per re-price; the
`reprice_latency_us` histogram tracks it live). Per the money rule
(`CLAUDE.md` Part A, Invariant 21), floats are a *screen only*: anything
reported as actionable is re-simulated in exact `Decimal` below. Float
results are never authoritative for money.

## Exact Decimal rounding simulation (`triarb/pricing/feasibility.py`)

**Implemented.** `simulate_rounded_cycle` is the cold-path confirmation run
only on triangles whose float screen cleared the threshold. It simulates, in
exact `Decimal`, the three IOC limit orders a future executor would place —
today it only produces a `FeasibilityReport` for logging.

Rounding helpers are **step-based, not decimal-places**, because venue
increments are not always powers of ten (e.g. a 0.5 price tick):

```
_down(value, step) = floor(value / step) * step   # ROUND_DOWN
_up(value, step)   = ceil(value / step)  * step   # ROUND_UP
```

Steps come from `SymbolInfo.price_tick_size` / `quantity_step_size`
(`triarb/exchange/base.py`), which adapters populate from the venue's exact
increments. Feed prices (floats) are converted via `Decimal(str(x))`.

### BUY leg

1. `worst = _walk_buy_worst(asks, holding)` — the deepest ask price needed
   to spend the full holding; `None` → `insufficient_depth`.
2. `limit_price = _up(worst, price_tick_size)` — rounded **UP** so the limit
   stays marketable (an IOC at a rounded-down price could miss the fill).
3. `qty = _down(holding / limit_price, quantity_step_size)` — rounded
   **DOWN** so the order can never spend more than the holding.
4. Checks: `qty <= 0` → `qty_rounds_to_zero`; `qty < min_base_amount` →
   `min_qty`; min-notional proxy (below) → `min_notional`.
5. `cost = _walk_buy_cost(asks, qty)` — exact cost of that quantity off the
   asks. If `holding - cost < 0` (limit-price rounding made `qty × limit`
   exceed holdings by a hair), one `quantity_step_size` is shaved off and
   the cost re-walked. This enforces §9's rule: never round a quantity
   upward when it could spend more than the available balance.
6. `received = qty * (1 - f)` — fee charged in the received **base**.
7. Unspent quote (`holding - cost > 0`) is recorded as stranded in the
   source currency.

### SELL leg

1. `qty = _down(holding, quantity_step_size)` — quantity rounded **DOWN**.
2. Same `min_qty` / `min_notional` / `qty_rounds_to_zero` checks.
3. `(proceeds, worst) = _walk_sell(bids, qty)`; `None` →
   `insufficient_depth`.
4. `limit_price = _down(worst, price_tick_size)` — rounded **DOWN** so the
   sell limit stays marketable.
5. `received = proceeds * (1 - f)` — fee charged in the received **quote**.
6. The unrounded remainder (`holding - qty`) is recorded as stranded dust.

### Dust valuation and the final figure

- **Stranded non-quote dust is valued at ZERO** in the net figure
  (pessimistic) and reported separately in `FeasibilityReport.stranded`.
- **Unspent quote from leg 1 never left the account** and is counted back
  in: `recovered = stranded.pop(quote_currency)`.

```
total_out             = holding + recovered_quote
net_bps_after_rounding = (total_out - notional) / notional * 10_000
```

Evidence: `tests/test_feasibility.py` hand-computes a full cycle
(`final_quote == 103.37652000` exactly), proves rounding always costs edge
versus the unrounded screen, that a coarse step turns a +368 bps float edge
into a loss (`rounding_killed`), and that unspent leg-1 quote counts toward
PnL while intermediate dust does not.

### Min-notional proxy (model limit)

**Implemented, with a documented approximation.** `min_notional` (e.g.
Bitget `minTradeUSDT`) is checked against the **starting notional with a 5%
haircut** (`_MIN_NOTIONAL_HAIRCUT = 0.95`) as a proxy for each leg's USDT
value, and the check runs only when the cycle's quote currency is `USDT`
(skipped otherwise — tested in
`tests/test_feasibility.py::test_min_notional_skipped_for_non_usdt_quote`).

**TBD:** exact per-leg notional valuation needs conversion rates that only
the (Planned) executor will track. Safest reversible assumption recorded per
`CLAUDE.md` §6: keep the conservative haircut proxy for USDT-quoted cycles
and skip the check for other settlement currencies, since in this build a
wrong answer can only mislabel a log line, never place an order.

## Planned cost terms (§11)

§11 requires the full cost ledger: `gross_return`, `exchange_fees`,
`estimated_slippage`, `rounding_loss`, `latency_reserve`,
`failure_reserve`, `expected_net_profit`, `expected_net_profit_bps`, and
the decision rule *final ≥ start + all costs + required profit + safety
reserve*. Status:

| §11 term | Status | Where |
|---|---|---|
| `gross_return` | **Implemented** | `gross_bps`, `triarb/pricing/edge.py` |
| `exchange_fees` | **Implemented** | `net_bps` vs `gross_bps` (compounded per leg) |
| `rounding_loss` | **Implemented** | `net_bps` vs `net_bps_after_rounding` + `stranded`, `triarb/pricing/feasibility.py` |
| `estimated_slippage` | **Planned** (§11, §15 `MAX_SLIPPAGE_BPS`) | depth walking already prices depth impact at the fixed notional; movement between detection and execution is not reserved for |
| `latency_reserve` | **Planned** (§11, §15 `LATENCY_RESERVE_BPS`) | — |
| `failure_reserve` | **Planned** (§11, §15 `FAILURE_RESERVE_BPS`) | — |
| `expected_net_profit` incl. reserves | **Planned** (§11) | today's `net_bps_after_rounding` excludes the three reserves above |
| fee charged in a third asset | **Planned** (§12) | today folded into the rate via `token_discount_multiplier` |

**TBD:** default values for `LATENCY_RESERVE_BPS`, `FAILURE_RESERVE_BPS`,
and `MAX_SLIPPAGE_BPS` are undecided. Safest reversible assumption per
`CLAUDE.md` §6: when these land they will be configuration-backed with
conservative non-zero defaults, and until they exist no figure produced by
this build may be read as "expected net profit" in the §11 sense — the
observer's numbers omit those reserves by construction, and §15 forbids
lowering thresholds automatically to create more trades.

## Test evidence

- `tests/test_depth.py` — walk math, partial-level fills, exhaustion.
- `tests/test_fees.py` — hurdle table, compounding, break-even, config rate.
- `tests/test_edge.py` — gross/net bps, staleness, depth exhaustion,
  top-of-book vs walked depth.
- `tests/test_feasibility.py` — exact rounded PnL, rounding-kills cases,
  venue minimums, dust recovery.

All offline; no network required.
