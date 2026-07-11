# Security Model

Governing spec: `CLAUDE.md` Part B §2 (clean-room), §3 (operating modes),
§29 (secret and API-key security), §30 (multi-tenancy), §31 (invariants 16,
17, 22, 24). Companion document: `docs/THREAT_MODEL.md`.

Every statement below is labeled **Implemented** (true of the code today,
with a file reference) or **Planned** (required by the master prompt for a
later phase). This build is the MARKET_DATA_ONLY observer: it contains **no
order, balance, withdrawal, or transfer code**, and nothing in this document
should be read as implying otherwise.

---

## 1. Current posture (Implemented)

### 1.1 Public endpoints only; zero credentials in code

- **Implemented.** Phase 1 touches only public REST and WebSocket endpoints:
  symbols, tickers, and public depth channels
  (`triarb/exchange/bitget.py`, `triarb/exchange/binance.py`,
  `triarb/exchange/bybit.py`, `triarb/exchange/okx.py`). No adapter has a
  private-endpoint method; the `ExchangeAdapter` contract
  (`triarb/exchange/base.py`) defines only `fetch_symbols`, `fetch_tickers`,
  `stream_books`, and `aclose`.
- **Implemented (verified 2026-07-11).** A case-insensitive grep of
  `triarb/` for `environ|getenv|api_key|secret|dotenv|passphrase|credential`
  returns exactly one hit: a docstring in `triarb/cli.py` describing the
  *future* `CONFIRM_LIVE` requirement. The same grep over `tests/` returns
  nothing. **No code path reads an environment variable, key file, or
  credential of any kind.** There is no signing code, no auth header
  construction, and no `.env` loader dependency in `pyproject.toml`.
- **Implemented.** REST and WS base URLs are module-level constants
  (`BITGET_REST_URL`, `BITGET_WS_PUBLIC_URL`, and equivalents per adapter),
  all HTTPS/WSS, not overridable from the YAML config
  (`triarb/config/models.py` exposes no URL fields). TLS certificate
  verification is the library default; nothing in the codebase disables it.

### 1.2 Secret hygiene ahead of need

- **Implemented.** `.gitignore` excludes `.env` and `.env.*` (keeping only
  `.env.example`), so a credential file created for a later phase can never
  be committed by default.
- **Implemented.** `.env.example` documents the key-permission checklist
  before any key exists: spot trade permission only, withdrawals disabled,
  transfers disabled, IP whitelist pinned to the bot host. This is
  documentation, not enforcement — programmatic enforcement is Planned
  (§2.2 below).
- **Implemented.** `.env.example` pins the three safety flags to their only
  supported values:

  ```text
  LIVE_TRADING_ENABLED=false
  WITHDRAWAL_PERMISSION_ALLOWED=false
  AUTOMATIC_EXTERNAL_TRANSFERS=false
  ```

  In this build the flags are inert in the strongest possible sense: no code
  reads them, and there is no behavior they could enable.

### 1.3 Mode gate

- **Implemented.** `triarb/config/models.py` defines the `Mode` enum with
  exactly the four §3 mode names — `MARKET_DATA_ONLY`, `PAPER_TRADING`,
  `SHADOW_TRADING`, `LIVE_TRADING` — and the `ensure_supported_mode` startup
  gate. Only `market_data_only` starts. `paper_trading` and `shadow_trading`
  fail with a "requires the execution engine planned for a later phase"
  error; `live_trading` fails with a distinct message restating the §3
  authorization chain (paper/shadow qualification, security and risk review,
  recorded human authorization). The gate runs in `triarb/cli.py` before any
  network connection.
- **Implemented.** `tests/test_config.py::TestModeGate` is the standing
  evidence for Invariant 17 (live mode disabled by default): default mode is
  `MARKET_DATA_ONLY`, unknown modes fail pydantic validation, unimplemented
  modes and `live_trading` each raise `UnsupportedModeError`.
- **Implemented.** Live trading cannot be enabled by configuration alone:
  there is no execution code for any flag or config key to activate, and the
  CLI (`triarb/cli.py`) exposes exactly one command, `observe`, with no
  `--live` flag.

### 1.4 Withdrawal and transfer code: banned permanently

- **Implemented (as a standing repository rule).** `CLAUDE.md` Part A
  ("How to work in this repo", rule 4) bans withdrawal/transfer code from
  this repository permanently — not per-phase. The adapter contract
  docstring (`triarb/exchange/base.py`) restates the ban. The master prompt
  §2 forbids enabling withdrawals; Invariant 16 (withdrawal-enabled keys are
  rejected) is the Planned enforcement test for when keys first exist.

### 1.5 Input handling and code-execution surface

- **Implemented.** Config is parsed with `yaml.safe_load` only
  (`triarb/config/loader.py`) — no object construction from YAML. Every
  config model uses `extra="forbid"` and validated ranges
  (`triarb/config/models.py`), so a typo'd or injected key aborts startup
  rather than silently defaulting.
- **Implemented.** Venue data is parsed with `orjson.loads` and pydantic
  models at the REST boundary (`SymbolInfo`, `Ticker` in
  `triarb/exchange/base.py`); WS frames are parsed into plain-float
  `BookSnapshot` objects. There is no `eval`, `exec`, `pickle`,
  `subprocess`, or dynamic import anywhere in `triarb/` (verified by grep,
  2026-07-11). Venue bytes are data, never code.
- **Implemented.** Structured JSON-lines logging
  (`triarb/monitoring/jsonlog.py`) serializes every log line with
  `orjson.dumps`, which escapes control characters — venue-controlled
  strings cannot forge log lines or emit raw terminal escapes (see
  `docs/THREAT_MODEL.md` T3). Unparseable WS frames are logged truncated
  (`%.300r` in `triarb/exchange/bitget.py`).

### 1.6 Dependency surface

- **Implemented.** Six runtime dependencies, all mainstream and minimal for
  the job: `aiohttp`, `websockets`, `orjson`, `pydantic`, `PyYAML`, `rich`
  (`pyproject.toml`). Tests run fully offline (`tests/`, no network).
- Residual risk (no lockfile or hash pinning yet) is assessed in
  `docs/THREAT_MODEL.md` T2; remediation is Planned (§2.4 below).

---

## 2. Planned requirements — §29 secret and API-key security

None of the following exists in code today. Phase names refer to the README
phase plan (Phase 2 paper execution, Phase 3 live minimum size, Phase 4
hardening). Where the landing phase is genuinely undecided it is marked TBD
with the safest reversible assumption, per CLAUDE.md §6.

### 2.1 Credential intake (first credentialed phase)

- **Planned (§29).** Keys will be read exclusively from the gitignored
  `.env`, never from config YAML, CLI arguments, or source. Lands in the
  first phase that calls any private endpoint. **TBD:** whether that is
  Phase 2 (only if paper mode fetches account fee rates) or Phase 3;
  `.env.example` says "Phase 2+". Safest reversible assumption, recorded
  here: **no credential is read until a phase demonstrably requires a
  private endpoint**, and paper mode ships first with configured
  (conservative) fees exactly as the observer uses today
  (`triarb/config/models.py` `FeesConfig`).
- **Planned (§29, Invariant 16).** On startup with credentials present, the
  build must query the venue's key-permission endpoint and **refuse to run
  if the key has withdrawal permission** (and warn on unnecessary
  margin/futures permission). The automated withdrawal-permission rejection
  test is Invariant 16 and lands in the same change that first reads a key —
  not later.
- **Planned (§29).** Key permissions restricted to read + spot trade only.
  Enforced two ways: the operator checklist (already in `.env.example`,
  Implemented as documentation) and the programmatic permission check above.

### 2.2 IP allowlisting

- **Planned (§29).** Programmatic support: warn (or refuse, configurable) at
  startup when the venue reports the key has unrestricted IP access and the
  venue supports allowlisting. Lands with the credential-intake phase.
  The operator-side instruction exists today (`.env.example` checklist).

### 2.3 Secret handling at rest and in memory

- **Planned (§29).** Application-layer secret encryption and managed key
  storage where available. **TBD phase**; required no later than the
  security review that precedes any `LIVE_TRADING` qualification (§3, §33,
  §37). Safest reversible assumption until then: single-operator host,
  gitignored plaintext `.env` with OS file permissions, no secrets in any
  other location.
- **Planned (§29).** Key rotation procedure (documented and tested,
  including revocation of the old key). **TBD phase**; required before
  `LIVE_TRADING`.
- **Planned (§29).** Secret redaction: credentials must never appear in
  logs, errors, browser responses, metrics, traces, database output,
  screenshots, or support exports. Redaction lands in the same change that
  first reads a key, as a filter in `triarb/monitoring/jsonlog.py` plus a
  test that a deliberately leaked key string is masked in every sink.
- **Planned (§29).** Credential health monitoring (detect revoked/expiring
  keys, permission drift) — lands with the private-stream health checks
  that the §15 Profitability Gate requires (`Exchange API healthy`,
  `Private order stream healthy`), i.e. the execution phase.

### 2.4 Access control and audit

- **Planned (§29).** Role-based access and administrator step-up
  authentication. These attach to the management API / administrative
  dashboard of §28, which has no scheduled phase. **TBD phase.** Safest
  reversible assumption: no management API is built until then; the only
  control surface is the local CLI on a single-operator host, so there are
  no roles to separate yet.
- **Planned (§29, Invariant 14).** Audit logs for every credentialed action
  and every live order, immutable, landing with the execution state machine
  (§18).
- **Planned (supply chain).** Dependency lockfile with hashes and a security
  scan in the pre-commit gate (§35 step 5). **TBD phase** (Phase 4
  hardening is the natural home). Safest reversible assumption until then:
  keep the dependency count minimal and install into a dedicated venv.

---

## 3. Planned requirements — §30 multi-tenancy

- **Planned (§30).** If the platform ever serves multiple users or
  organizations, each tenant gets separate exchange credentials, balances,
  cycles, risk settings, audit logs, performance accounting, and encryption
  contexts, with automated tests proving one tenant cannot access another
  tenant's keys, exchanges, balances, orders, cycles, opportunities,
  reports, or risk settings (Invariant 24).
- **TBD:** whether multi-tenancy is ever in scope; no phase is assigned
  anywhere in the repo. Safest reversible assumption, recorded here: **the
  platform is single-tenant, single-operator**, and every design decision
  until a multi-tenancy decision is recorded in `docs/DECISIONS.md` must
  avoid baking a shared-credential or shared-ledger assumption into storage
  schemas. Invariant 24 tests are a hard precondition of any multi-tenant
  deployment.

---

## 4. Non-negotiables carried into every phase

These hold now and are never relaxed by a later phase:

1. Withdrawal/transfer code is banned from this repository permanently
   (`CLAUDE.md` Part A rule 4; master prompt §2). **Implemented** as a
   standing rule; Invariant 16 adds key-level enforcement when keys exist.
2. `LIVE_TRADING` stays disabled by default and can never be enabled by a
   config or frontend change alone (§3; Invariant 17). **Implemented** today
   by `ensure_supported_mode` (`triarb/config/models.py`) — currently it
   refuses live mode unconditionally.
3. No LLM participates in the live execution path, decides a live order,
   calculates a final live quantity, or alters risk limits during execution
   (§4; Invariant 22). **Planned** enforcement test lands with the execution
   engine; today there is no execution path for an LLM to be in.
4. Full credentials never appear in any output channel (§29). **Planned**
   redaction; today there are no credentials to leak.
5. This document never describes the system as profitable or
   production-ready; release-candidate status requires the full §37
   evidence list, including a passed security review.

---

## 5. TBD register (this document)

| Item | Safest reversible assumption on record |
| --- | --- |
| Phase that first reads credentials (2 vs 3) | No credential read until a private endpoint is demonstrably required; paper mode uses configured fees |
| Phase for secret encryption at rest / key rotation | Plaintext gitignored `.env` on a single-operator host; both land before any `LIVE_TRADING` qualification |
| Phase for RBAC + step-up auth (§28 management API) | No management API exists; local CLI on single-operator host only |
| Multi-tenancy in scope at all (§30) | Single-tenant; schemas must not bake in shared-credential assumptions; Invariant 24 tests gate any change |
| Dependency lockfile / hash pinning phase | Minimal dependency set in a dedicated venv until Phase 4 hardening |
