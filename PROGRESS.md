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

**Work completed:**
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
- Official-doc research: 4 documentation agents + 4 adversarial verifiers
  over Bitget/Bybit/OKX/Binance spot docs → `docs/EXCHANGE_CAPABILITY_MATRIX.md`.
- (This entry is finalized at commit time with adapter + docs results.)

**Assumptions:** recorded in `docs/DECISIONS.md` (stack preservation,
snapshot-channel strategy per venue, mode gate placement).

**Risks remaining:** venue payloads verified against official docs and
offline fixtures only — a live soak run per venue (`triarb observe`) is the
next validation step; no execution/risk/recovery code exists yet by design.

**Next best action:** live soak of each new adapter in MARKET_DATA_ONLY
mode; then Phase 2 specification (paper execution: leg sequencer,
pessimistic fill model, risk kill switch, accounting) per
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
