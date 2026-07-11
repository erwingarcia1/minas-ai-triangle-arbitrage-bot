# PROGRESS.md — durable iteration memory

Read `CLAUDE.md` first. This file is the append-only iteration log required
by master prompt §35 step 9. Newest iteration at the top. Never delete
entries; correct them by appending.

---

## Iteration 2 — Multi-venue market data + spec alignment + documentation (2026-07-11, in progress)

**Feature IDs:** F-MODE-GATE, F-ADAPTER-BINANCE-MD, F-ADAPTER-BYBIT-MD,
F-ADAPTER-OKX-MD, F-CAPABILITY-MATRIX, F-DOCS-TREE (see
`docs/FEATURE_MATRIX.md`).

**Objective:** Align the existing Phase 1 observer with the master prompt:
(1) explicit operating-mode gate with `market_data_only` as the only
startable mode; (2) public market-data adapters for Binance, Bybit, and OKX
behind the existing `ExchangeAdapter` contract so all four venues run the
safest vertical slice; (3) the `docs/` tree, capability matrix, and feature
traceability matrix; (4) `CLAUDE.md` + this file.

**Work completed** (commits `ab3a419`, `ce43294`, `51b411f`, + review
follow-ups):
- Baseline verified: 84 offline tests green, ruff clean; fixed 2 mypy nits
  (missing `types-PyYAML` dev dep; unused `type: ignore` in
  `triarb/graph/triangles.py`).
- Added `Mode` enum (`market_data_only` | `paper_trading` | `shadow_trading`
  | `live_trading`) + `ensure_supported_mode` startup gate
  (`triarb/config/models.py`), wired into `triarb/cli.py`. Only
  `market_data_only` starts; `live_trading` is refused with the §3
  authorization-chain message. Tests in `tests/test_config.py::TestModeGate`
  (Invariant 17 evidence).
- `.env.example`: `LIVE_TRADING_ENABLED=false`,
  `WITHDRAWAL_PERMISSION_ALLOWED=false`,
  `AUTOMATIC_EXTERNAL_TRANSFERS=false` defaults documented.
- §9 normalization: `SymbolInfo` migrated from decimal-place precisions to
  exact `price_tick_size`/`quantity_step_size` Decimals; feasibility
  rounding is step-based (correct for non-power-of-ten increments), with
  unit tests (`tests/test_feasibility.py::TestStepRounding`).
- Official-doc research: 4 documentation agents + 4 adversarial verifiers
  over Bitget/Bybit/OKX/Binance spot docs; all four venues CONFIRMED
  (Binance/Bybit high confidence via official docs source repos;
  Bitget/OKX medium — doc sites block datacenter fetchers, claims
  corroborated via official-page phrase extraction + venue SDKs). Distilled
  into `docs/EXCHANGE_CAPABILITY_MATRIX.md`.
- Adapters: Binance (combined-stream `depth20@100ms` partial-depth
  snapshots; strict `isSpotTradingAllowed`; client protocol pings disabled
  to protect the 5 msg/s inbound budget), OKX (`books5` snapshots,
  4-element levels, text ping), Bybit (`orderbook.50` snapshot+delta merged
  by `BybitBookAssembler` with strict `u`-continuity; gap → empty book +
  topic resubscribe; assembler reset on reconnect; spot instruments
  unpaginated per docs). Registry wires all four venues;
  `depth_channel: auto` resolves per venue. Quote-denominated 24h volumes
  are converted to USDT via same-batch `<CCY>USDT` prices for discovery
  filtering (unknown quote → volume 0, conservatively excluded).
- Tests: 84 → 175 offline (venue parse suites from documented payload
  shapes, Bybit assembly/sequencing suite, cross-venue adapter contract
  suite `tests/test_adapter_contract.py`, mode gate, step rounding).
  ruff + mypy strict clean throughout.
- Docs: `CLAUDE.md` (master prompt + operating guide), this file, and the
  full 20-file `docs/` tree; an independent cross-doc consistency reviewer
  reported findings F1–F11 (stale placeholder claims, a stale SymbolInfo
  schema row, stale counts, "paper is the default forever" wording) — all
  applied in `51b411f`.

**Tests run:** `pytest` (220 passed, all offline, incl. property-based and
fake-transport stream suites), `ruff check .`, `mypy triarb` (strict) —
green at every commit. End-to-end offline simulation (scripted synthetic
market through the real pipeline) passes all six ground-truth scenarios;
hot-path mean ~39 µs/update on this hardware.

**Review findings:** docs consistency review F1–F11 → fixed (`51b411f`).
Independent 5-lens code review (quant / market-data / security /
adversarial / QA, 12 agents incl. adversarial verification of P0/P1
findings) surfaced 17 findings; all applied (`3597023`, `d505d05`):
Bybit assembler atomicity (P1), nested-frame AttributeError process-kill
(P1, verifier-confirmed), min-notional denomination + order-notional
semantics (P2×2), exact step floor/ceil beyond Decimal context precision
(P3), Bybit gap-resubscribe dedup and failed-ack reconnect (P3×2),
data-flow-gated backoff reset (P3), bounded venue-controlled pagination +
repr-escaped venue strings (P3×2), channel-cadence startup gate (P3), and
five QA coverage P1s (stream-level fail-safe, USDT-volume derivation,
snapshot-replace, CLI gate wiring, credential-test strengthening). The
adversarial reviewer additionally verified that attacks on stale-book
pricing, float-authoritative money, corrupt-sequence books, and
untradeable-symbol admission all fail against this code.

**Assumptions:** recorded in `docs/DECISIONS.md` D-001…D-005 and the TBD
registers in `docs/FEATURE_MATRIX.md` / per-doc TBD tables.

**Risks remaining:**
- Venue payloads verified against official docs and offline fixtures only.
  A per-venue live soak (`triarb observe --duration 3600`) is REQUIRED
  before trusting the three new adapters operationally. Attempted from
  this sandbox on 2026-07-11: blocked — the environment's egress policy
  returns CONNECT 403 for all four exchange hosts. The soak must run from
  an operator environment with open egress.
- Bitget/OKX capability rows carry medium confidence (doc sites block
  robots); re-verify on first live contact per D-005.
- No execution/risk/recovery/accounting code exists yet — by design.

**Blockers:** live soak blocked by sandbox egress policy (above); nothing
else.

**Next best action:** operator-run live soak per venue in
MARKET_DATA_ONLY mode; then Phase 2 specification (paper execution: leg
sequencer, pessimistic fill model, risk kill switch, accounting) per
`docs/PAPER_TRADING.md`.

---

## Iteration 1 — Phase 1 observer (pre-existing baseline)

**Objective:** Single-exchange triangular arbitrage observer (Bitget), no
order code.

**Delivered (commit 5269e70):**
- `ExchangeAdapter` contract (public market data only) + native Bitget
  adapter (REST symbols/tickers, `books15` snapshot WS, app-level `ping`
  heartbeat, reconnect with backoff).
- Currency graph + triangle enumeration (Q→A→B→Q, both directions),
  liquidity/spread filters, whitelist mode.
- Two-stage pricing: float depth-walk screen (~µs) + exact Decimal rounding
  feasibility (lot steps, precision, min qty, min notional, dust
  accounting).
- Opportunity lifecycle logging (`cycle_detected`/`cycle_ended` with
  persistence, peaks, feasibility), JSON-lines logs, metrics, rich
  dashboard, `triarb observe` CLI.
- 84 offline tests; ruff + mypy strict.

**Known limitations at end of iteration 1:** Bitget only; snapshot depth
channels only (15 levels); fees are configured, not fetched; no
execution/risk/recovery/accounting layers (deliberate — later phases).
