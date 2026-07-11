# DECISIONS.md — architecture decision record

Append-only. Each entry: context → decision → consequences → revisit
criteria. Newest at the bottom. Required by `CLAUDE.md` §6; consequential
reversals also go to `PROGRESS.md`.

---

## D-001: Preserve the existing Python stack instead of the greenfield Rust default

- **Date:** 2026-07-11 · **Status:** Accepted
- **Context:** Master prompt §5 recommends Rust/Tokio *for a greenfield
  implementation* but instructs first: "inspect the existing repository and
  preserve a sound existing stack." The repo arrived with a working,
  disciplined Python 3.11 observer: 84 offline tests, ruff + mypy strict,
  native WebSocket protocol handling, measured ~3 µs float re-price hot
  path, exact-Decimal cold path.
- **Decision:** Keep Python (asyncio + `websockets` + `aiohttp` + `orjson`
  + pydantic v2). Continue the two-stage pricing pattern: float math is a
  screen only; `Decimal` is authoritative for anything reported or (later)
  executed.
- **Consequences:** Faster iteration and full reuse of the tested pricing
  core. Python is not competitive with colocated Rust/C++ HFT — but network
  latency dominates by orders of magnitude (README "Latency expectations"),
  and the observer's own measurements will show whether engine latency is
  ever the binding constraint.
- **Revisit when:** measured in-process latency (reprice_latency_us
  histogram, later order-path latency) becomes a material fraction of
  round-trip network latency, or Phase 4 hardening demands it.

## D-002: Snapshot depth channels only while observing; per-venue channel strategy

- **Date:** 2026-07-11 · **Status:** Accepted
- **Context:** Master prompt §13 requires full sequence/checksum book
  handling. Incremental books introduce an entire class of merge/resync
  bugs, and Phase 1 places no orders — corrupt books cost only log quality.
- **Decision:** Every venue adapter feeds the observer with *complete
  snapshots*. Bitget: `books15` (snapshot channel). Binance: partial
  book-depth streams `<symbol>@depth20@100ms` (each push is a full
  snapshot). OKX: `books5` (snapshot every push). Bybit has no multi-level
  spot snapshot channel, so its adapter consumes `orderbook.50`
  (snapshot + delta), merges internally with `u`-sequence gap detection,
  resubscribes on any gap, and emits only complete snapshots downstream —
  the sequence handling stays contained in one adapter.
- **Consequences:** Depth visible to pricing is capped (5–50 levels by
  venue); walks that exhaust visible depth are rejected as
  `insufficient_depth`, never extrapolated — conservative by construction.
  Full incremental books with checksums remain a later-phase upgrade
  (`docs/ORDER_BOOK_ENGINE.md`).
- **Revisit when:** sizing analysis shows profitable size exceeding visible
  snapshot depth, or execution phases need deeper books.

## D-003: Operating-mode gate lives in config, enforced at CLI startup

- **Date:** 2026-07-11 · **Status:** Accepted
- **Context:** Master prompt §3 defines four modes; only MARKET_DATA_ONLY
  exists in this build. Configs written for later phases should parse, not
  explode with unknown-key errors, but must never run.
- **Decision:** `Mode` enum on `Settings` (default `market_data_only`);
  `ensure_supported_mode()` is called by the CLI before any network
  connection and refuses every other mode — `live_trading` with a distinct
  message naming the §3 authorization chain. There is no code path that
  bypasses the CLI entry point.
- **Consequences:** Invariant 17 ("live mode remains disabled by default")
  has direct test evidence (`tests/test_config.py::TestModeGate`) before any
  execution code exists. Future phases extend the supported set explicitly,
  one mode at a time, each behind its own review gate.
- **Revisit when:** Phase 2 lands paper trading (gate grows
  `paper_trading` after its review), never by config alone for
  `live_trading`.

## D-004: Multi-venue = one venue per process, not one process for all venues

- **Date:** 2026-07-11 · **Status:** Accepted
- **Context:** §"Core Product Goal" requires each exchange to operate as an
  independent venue (own balances, books, limits, state). The observer is
  single-venue by construction (`Settings.venue`).
- **Decision:** Independence across exchanges is achieved by running one
  observer process per venue with its own config file. No cross-venue state
  is shared; there is no cross-exchange transfer logic anywhere (banned).
- **Consequences:** Simplest possible isolation (process boundary), honest
  per-venue accounting for free, and a crash on one venue cannot corrupt
  another. A supervising multi-venue orchestrator is deferred until a real
  operational need appears.
- **Revisit when:** operating ≥3 venues routinely makes per-process ops
  burdensome, or a shared dashboard (§28) needs a unified control plane.

## D-005: Exchange capability claims require official-doc citation + adversarial verification

- **Date:** 2026-07-11 · **Status:** Accepted
- **Context:** §8: "Never assume support based on another exchange"; wrong
  depth-channel semantics silently corrupt books.
- **Decision:** `docs/EXCHANGE_CAPABILITY_MATRIX.md` rows carry an
  official-documentation URL, and the implementation-critical claims
  (depth-channel snapshot/delta semantics, heartbeat protocol, field names)
  were independently re-verified by a second adversarial research pass
  before adapter code was written. Unverifiable items are recorded as
  `unverified`, never guessed. Automated capability tests against live/demo
  endpoints land with the phases that use those capabilities.
- **Consequences:** Slower to claim capabilities, but adapter code is
  written against verified protocol facts; the matrix records verification
  dates so drift is detectable.
- **Revisit when:** any adapter observes behavior contradicting the matrix
  (that is a P1 incident: fix matrix + adapter + add a regression fixture).
