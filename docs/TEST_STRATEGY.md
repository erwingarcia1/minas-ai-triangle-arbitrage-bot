# TEST_STRATEGY.md — test inventory, invariant map, and testing roadmap

> Status labels: **Implemented** = true of the code today (file cited).
> **Planned** = required by the master prompt (`CLAUDE.md` Part B, section
> cited) for a later phase. This build is MARKET_DATA_ONLY and contains **no
> order, balance, withdrawal, or transfer code**; every "execution",
> "recovery", or "risk engine" test below is therefore Planned by
> definition — there is nothing to test yet, deliberately.

## 1. How to run (Implemented)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest            # 175 tests, all offline, <1 s
.venv/bin/ruff check .      # lint
.venv/bin/mypy triarb       # strict mode (pyproject.toml)
```

All three gates must be clean before any commit (`CLAUDE.md` Part A).
`pytest` is configured in `pyproject.toml` (`testpaths = ["tests"]`,
`asyncio_mode = "auto"` so async tests need no decorator).

## 2. The no-network rule (Implemented as convention)

Every test in `tests/` runs offline: no exchange endpoint, no DNS lookup, no
socket. Adapter behavior is tested against **fixture payloads recorded from
the official exchange documentation** (each parse-test module's docstring
says so, e.g. `tests/test_binance_parse.py`: *"against recorded spot API
shapes (no network)"*), and the observer pipeline is driven by a
`FakeAdapter` (`tests/test_observer.py`). This keeps CI deterministic and
means a red build is always a code defect, never a venue outage.

**TBD-TS-1** — the rule is currently enforced by convention and review, not
by machinery. Safest reversible assumption (recorded per `CLAUDE.md` §6):
keep the convention, and add an autouse pytest fixture that fails any test
attempting a socket connection when the suite grows execution code. That
guard is additive and removable; until it exists, any test needing network
access must be rejected in review.

Live-venue behavior is validated separately by a manual, bounded soak run
(`triarb observe`) — see `PROGRESS.md` "Next best action". Soak runs are an
operational check, not part of the test suite.

## 3. Suite inventory (Implemented — 14 files, 175 tests)

| File | Tests | What it proves |
|---|---:|---|
| `tests/conftest.py` | — | Shared synthetic fixtures: a hand-priced USDT→BTC→ETH→USDT triangle (+400 bps gross), `SymbolInfo`/`BookStore` builders. |
| `tests/test_depth.py` | 13 | Depth-walk math (`triarb/pricing/depth.py`): exact fills, level boundaries, spanning levels, empty/shallow books return `None` (never extrapolated), zero-size levels skipped. |
| `tests/test_edge.py` | 8 | Cycle evaluation (`triarb/pricing/edge.py`): gross/net bps against hand-computed values, zero-fee equivalence, stale-book rejection at the staleness bound, `missing_book` / `insufficient_depth` reasons, depth walk strictly worse than top-of-book when depth is thin. |
| `tests/test_fees.py` | 8 | Fee compounding (`triarb/pricing/fees.py`): `hurdle_bps` reproduces the README fee-tier table (3 legs at 0.1% ≈ 30.06 bps, not 30), break-even at the hurdle, `FeesConfig` token-discount arithmetic. |
| `tests/test_feasibility.py` | 14 | Exact-Decimal rounding simulation (`triarb/pricing/feasibility.py`): hand-computed rounded PnL to the last Decimal digit, rounding always costs edge, coarse steps turning profit into loss, `qty_rounds_to_zero` / `min_qty` / `min_notional` / `insufficient_depth` / `missing_metadata` rejects, non-power-of-ten tick/step rounding (§9), unspent-quote dust recovery. |
| `tests/test_orderbook.py` | 4 | `BookStore` (`triarb/pricing/orderbook.py`): in-place update, age/staleness math, `mark_all_stale` on disconnect makes age infinite. |
| `tests/test_triangles.py` | 14 | Graph + enumeration (`triarb/graph/`): both cycle directions found, offline symbols excluded, leg sides compiled correctly per direction, volume/spread/cap filters, whitelist mode incl. rejection of malformed entries. |
| `tests/test_config.py` | 15 | Settings validation (`triarb/config/models.py`): example config valid, unknown keys/venues rejected, and `TestModeGate` — the Invariant 17 evidence (see §4). |
| `tests/test_registry.py` | 5 | Every `Venue` member resolves to its adapter via `create_adapter` (`triarb/exchange/registry.py`); adding a venue without wiring it fails the suite. Runs over **all four** adapters. |
| `tests/test_bitget_parse.py` | 10 | Bitget adapter (`triarb/exchange/bitget.py`): symbol/ticker parsing from recorded v2 payloads, `books15` snapshot frames, pong/ack ignored, error frames become `StreamStatus`, malformed frames raise for the caller to log. |
| `tests/test_binance_parse.py` | 22 | Binance adapter (`triarb/exchange/binance.py`): filter-based symbol parsing (incl. legacy `MIN_NOTIONAL` name; symbols missing rounding filters are **dropped**, never guessed), quote-volume→USDT conversion, depth-stream frames, channel validation. |
| `tests/test_bybit_parse.py` | 24 | Bybit adapter (`triarb/exchange/bybit.py`): symbol/ticker parsing plus `BybitBookAssembler` snapshot+delta assembly — upserts, zero-size deletes, **sequence-gap detection drops the book** until a fresh snapshot, delta-before-snapshot is a gap, service-restart snapshot resets, reconnect `reset()` forgets all books, depth-limit capping. |
| `tests/test_adapter_contract.py` | 16 | Cross-venue contract suite (§32): every `Venue` adapter constructs without credentials or network, `name` matches the venue, junk depth channels are rejected **before** any connection attempt, `aclose` is idempotent. |
| `tests/test_okx_parse.py` | 18 | OKX adapter (`triarb/exchange/okx.py`): v5 symbol/ticker parsing, 4-element `books5` levels, non-power-of-ten tick preserved, control frames, channel validation. |
| `tests/test_observer.py` | 4 | Offline end-to-end of the MARKET_DATA_ONLY pipeline (`triarb/observer.py`): detect → persist → end lifecycle, threshold gating, **disconnect ends opportunities and stales all books**, `rounding_killed` counter when the float edge is positive but the Decimal simulation is infeasible. |

## 4. §31 invariant map

The master prompt (§31) requires automated tests for 24 invariants. Most
concern execution machinery that does not exist yet. Rows marked *analogue*
have real evidence for the detection-side counterpart today; the
execution-side test is still Planned for the phase that introduces the code.

| # | Invariant | Status | Evidence today / Planned phase |
|---:|---|---|---|
| 1 | No cycle executes on stale books | Analogue Implemented / execution Planned | `test_edge.py::TestEvaluateCycle::test_stale_book_rejected`, `test_observer.py::test_disconnect_ends_opportunities_and_stales_books`. Execution-side re-check: Phase 2 (§15). |
| 2 | No cycle executes on an unsynchronized book | Analogue Implemented / execution Planned | `test_bybit_parse.py::TestBookAssembly::test_sequence_gap_detected_and_book_dropped` (+ delta-before-snapshot, reconnect reset). Other venues use snapshot channels, which cannot desynchronize. Execution-side: Phase 2. |
| 3 | All three legs on the same exchange | Structural today / explicit test Planned | One venue per process (`Settings.venue`, single adapter via `triarb/exchange/registry.py`); triangles are compiled from that venue's symbols only (`triarb/graph/triangles.py`). Explicit negative test arrives with the execution engine (Phase 2, §18). |
| 4 | Every leg uses the actual previous-leg fill | Analogue Implemented / execution Planned | `simulate_rounded_cycle` chains each leg from the prior leg's *received* amount (`test_feasibility.py::TestHappyPath`). Real-fill chaining: Phase 2 (§16 Mode A). |
| 5 | All fees included | Implemented (pricing) | `test_fees.py`, `test_edge.py::test_gross_and_net_edge`, per-leg fee in `test_feasibility.py`. Account-fee fetching still Planned (§12). |
| 6 | Quantities satisfy exchange increments | Implemented (simulation) | `test_feasibility.py::TestStepRounding` incl. non-power-of-ten steps. |
| 7 | Orders satisfy minimum quantities | Implemented (simulation) | `test_feasibility.py::TestExchangeMinimums::test_min_qty_rejection`. |
| 8 | Orders satisfy minimum notionals | Implemented (simulation, proxy) | `test_feasibility.py::test_min_notional_rejection`; the check is a USDT-proxy with haircut (documented limit in `triarb/pricing/feasibility.py`). Exact per-leg valuation: Phase 2. |
| 9 | Locked funds cannot be double spent | Planned | Phase 2 balance reservation (§22). No balance code exists. |
| 10 | Timeout cannot cause duplicate submission | Planned | Phase 2 client-order-ID + reconcile-before-resubmit (§19). |
| 11 | Accepted ≠ filled | Planned | Phase 2 execution confirmation via fill events (§17). |
| 12 | Partial fills trigger recalculation | Planned | Phase 2 (§20). |
| 13 | Every live order passes the risk engine | Planned | Phase 2+ central risk engine (§23). |
| 14 | Every live order has an immutable audit record | Planned | Phase 2+ (§18, §23). |
| 15 | Incomplete cycles survive restart | Planned | Phase 2 persisted state machine (§18). |
| 16 | Withdrawal-enabled keys rejected | Planned | §29. Today **no code reads any API key** (`CLAUDE.md` Part A safety posture); the rejection test arrives with the first credential-touching code. |
| 17 | Live mode disabled by default | **Implemented** | `tests/test_config.py::TestModeGate` — default is `market_data_only`; `paper_trading`/`shadow_trading` refused as "later phase"; `live_trading` refused with the §3 authorization-chain message (`triarb/config/models.py::ensure_supported_mode`). |
| 18 | Kill switch blocks new cycles | Planned | Phase 2 (§24). |
| 19 | Recovery orders linked to their cycle | Planned | Phase 2+ (§21). |
| 20 | Theoretical profit never presented as realized | Trivially true today / explicit test Planned | No realized-profit code exists; all logged figures are detection-side (`net_bps`, `net_after_rounding_bps`). Explicit label-separation test (§27) arrives with accounting. |
| 21 | Binary floats never used for money | Implemented (design + evidence) | The float hot path is a screen only; every actionable figure is re-simulated in exact `Decimal` with venue rounding — `test_feasibility.py` asserts results to the exact Decimal digit (e.g. `final_quote == Decimal("103.37652000")`). Future accounting/execution code must be Decimal-only (`CLAUDE.md` Part A money rule). |
| 22 | No LLM in the live execution path | Structural / test Planned | No execution path exists to contaminate (§4). Enforced at review; automated check TBD with the execution engine. |
| 23 | Adapter cannot bypass central execution controls | Structural today / test Planned | Stronger than required today: `ExchangeAdapter` (`triarb/exchange/base.py`) has **no trading methods at all**. Bypass tests arrive with the §7 execution planner. |
| 24 | Tenant isolation | Planned | §30. Single-tenant build; multi-tenancy is not scheduled before live phases. |

## 5. §32 category coverage

| §32 category | Today | Planned |
|---|---|---|
| **Unit** | Implemented: directed-edge calculations, sell/buy conversions, fee deduction, Decimal precision, price/quantity rounding, minimum notionals, route generation/deduplication, depth simulation, profitability screening (files in §3 above). | Optimal sizing (§14), risk rules, state transitions, balance reservations — with their Phase 2 code. |
| **Property-based** | Not present. Example-based analogues exist (`test_rounding_always_costs_edge`, `insufficient_depth` never extrapolates) but they are single points, not properties. | Phase 2: the §32 property list (output never exceeds depth, higher fees never raise profit, rounding cannot create funds, reservations cannot be overspent, duplicate events cannot duplicate orders, ledger stays balanced). **TBD-TS-2**: framework undecided; safest reversible assumption is Hypothesis (additive dev dependency, removable). |
| **Adapter contract** | Partially: each of the four adapters has its own offline parse suite built on fixture payloads from official docs, and `tests/test_registry.py` runs one parametrized check over **all** venues. | §32 requires **one shared contract suite that every adapter must pass** — a single parametrized suite asserting identical semantics (symbol normalization, drop-don't-guess on malformed entries, `StreamStatus` on errors, channel validation) across all four adapters, so a fifth venue inherits every obligation automatically. **TBD-TS-3**: suite shape undecided; safest reversible assumption is a parametrized module over `(adapter, fixture_dir)` pairs, which subsumes today's per-venue files without deleting them. |
| **Integration** | None by design (no-network rule; no private endpoints, no database). Live public-WS behavior is covered by manual soak runs only. | Phase 2+: private WebSockets, REST authentication, order placement/cancellation, fill processing, balance sync, persistence, restart recovery — against exchange demo/test environments where supported, never in default CI. |
| **End-to-end** | Partially: `tests/test_observer.py` drives the complete MARKET_DATA_ONLY pipeline offline (books in → detection → persistence → end, including the disconnect path). | Paper-mode and shadow-mode complete cycles (Phase 2/3); demo-environment cycles where the venue provides one (§8 capability matrix). |
| **Chaos** | Partially: injected disconnect (`test_observer.py`), sequence gap (`test_bybit_parse.py`), malformed frames and venue error frames (all four parse suites), stale book (`test_edge.py`, `test_orderbook.py`). | The full §32 injection list — REST timeout, ack loss, duplicate fill, out-of-order fill, partial fill, rate limit, database restart, process crash, clock drift, key revocation, symbol suspension — with the Phase 2 machinery those failures exercise. |

## 6. Planned: deterministic market replay (§26)

Not implemented. Required before paper-trading results can be trusted:

- Record raw venue frames (book snapshots/deltas, and later private order
  and fill events, connection failures, exchange errors) with local receive
  timestamps; replay them through the **same** parse and pricing code paths
  used live.
- Determinism contract: the same event sequence plus the same configuration
  must produce byte-identical results — this is what makes regression
  comparison and §26's evaluations (false opportunities, opportunity
  duration, executable profit, slippage, partial-fill frequency, recovery
  losses, latency/size/fee sensitivity) meaningful.
- **TBD-TS-4**: storage format undecided. Safest reversible assumption:
  JSON-lines of raw frames + `recv_ns`, matching the existing JSON-lines
  logging (`triarb/monitoring/jsonlog.py`); it can be migrated later without
  invalidating recordings since raw frames are kept verbatim.

Replay tests will run offline and therefore fit the no-network rule as-is.

## 7. TBD register

| ID | Question | Safest reversible assumption on record |
|---|---|---|
| TBD-TS-1 | Mechanical enforcement of the no-network rule | Convention + review now; add a socket-blocking autouse fixture when execution code lands. |
| TBD-TS-2 | Property-based framework | Hypothesis, as an additive dev dependency. |
| TBD-TS-3 | Shared adapter contract-suite shape | Parametrized suite over `(adapter, fixture_dir)`; keeps today's per-venue files. |
| TBD-TS-4 | Replay recording format | JSON-lines of verbatim raw frames + receive timestamps. |
