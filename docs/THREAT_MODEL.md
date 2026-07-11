# Threat Model

STRIDE-style analysis of the build as it exists today — the MARKET_DATA_ONLY
observer — followed by the threat surface the later phases will open.
Companion document: `docs/SECURITY_MODEL.md`. Governing spec: `CLAUDE.md`
Part B §4, §13, §15, §19, §23, §29, §31.

Claims are labeled **Implemented** (in code today, file cited) or
**Planned** (required by the cited CLAUDE.md section for a later phase).

## Assets and trust boundaries (current build)

What an attacker could actually get from this build:

| Asset | Why it matters |
| --- | --- |
| Integrity of observer logs | The JSON-lines output is the *sole input* to the go/no-go decision on building Phase 2. Poisoned logs → capital risked later on false evidence. This is the highest-value asset today. |
| The operator's host | Code execution via a dependency or parser bug inherits whatever else runs on the box. |
| Availability | A wedged or flapping observer produces gap-ridden data and wastes the multi-day soak. |

Explicitly **not** at risk in this build: funds, exchange credentials,
orders. There are none — no order, balance, withdrawal, or transfer code
exists, and no credential is read anywhere
(`docs/SECURITY_MODEL.md` §1.1, verified by grep).

Trust boundaries: (1) venue REST/WS endpoints → adapter parsers; (2) local
YAML config → pydantic models; (3) PyPI → installed dependencies; (4) log
files → downstream analysis tooling (jq, dashboards, humans).

**Severity scale:** rated twice — *now* (observer, no funds) and *later*
(same threat once execution phases exist). Ratings are deliberately
unimpressed: most current threats bottom out at "misleading logs."

---

## Current threats

### T1 — Poisoned market data from a compromised or misbehaving venue feed

**STRIDE:** Spoofing / Tampering (data plane). **Severity now: Low-Medium.
Later: High.**

Vector: a venue feed (or an on-path attacker) delivers fabricated prices,
absurd sizes, wrong symbols, or malformed frames. In this phase the worst
outcome is fake `cycle_detected` events that make the strategy look viable
when it is not — an economic-decision attack, not a fund-loss attack.

Existing mitigations (all **Implemented**):

- TLS to constant, non-configurable HTTPS/WSS venue URLs; certificate
  verification is the library default and is never disabled
  (`triarb/exchange/bitget.py` and peers; no URL fields in
  `triarb/config/models.py`).
- Boundary validation: REST payloads parse into frozen pydantic models;
  entries that fail validation are dropped and counted, never coerced
  (`triarb/exchange/base.py`, `fetch_symbols`/`fetch_tickers` in each
  adapter). Venue data is never evaluated as code — `orjson.loads` only, no
  `eval`/`exec`/`pickle` anywhere in `triarb/` (verified by grep).
- A malformed WS frame is logged (truncated) and skipped; it cannot kill the
  stream loop (`parse_ws_message` contract, `triarb/exchange/bitget.py`).
- Staleness bounds: book age is measured from local **monotonic** receive
  time, so venue clock skew or forged exchange timestamps cannot hide a
  stalled feed (`triarb/pricing/orderbook.py`). Books older than
  `staleness_ms` are unusable; a background sweep closes opportunities whose
  legs went stale between updates (`triarb/observer.py::_sweep_loop`), and a
  disconnect marks *all* books stale (`BookStore.mark_all_stale`).
- Books are stored verbatim — never averaged, smoothed, or extrapolated;
  walks that exhaust visible depth return `insufficient_depth` rather than
  guessing (`triarb/pricing/orderbook.py`, `triarb/pricing/edge.py`).
- Every detection logs `max_book_age_ms` and the exact-Decimal feasibility
  verdict, so post-analysis can discard detections priced off aging or
  implausible legs (`triarb/observer.py::_evaluate`).

Residual risk: a venue feed that lies *plausibly* (valid schema, fresh
timestamps, realistic prices) is undetectable from inside one venue's data —
there is no cross-venue sanity check and no checksum on the snapshot depth
channels (incremental+checksum channels are deliberately out of scope this
phase, `triarb/exchange/okx.py` header). Accepted for the observer; the §13
book-validity rules (sequence numbers, checksums, cross-book skew rejection)
are **Planned** for the phase that adopts incremental channels, and must
land before any execution mode consumes this data.

### T2 — Dependency supply chain

**STRIDE:** Tampering / Elevation of privilege. **Severity now: Medium.
Later: High.**

Vector: a compromised release of any of the six runtime dependencies
(`aiohttp`, `websockets`, `orjson`, `pydantic`, `PyYAML`, `rich` —
`pyproject.toml`) executes arbitrary code in the observer process at install
or import time.

Existing mitigations (**Implemented**): the dependency set is small,
mainstream, and pinned to minimum versions; there is no plugin system, no
dynamic import, and tests run fully offline (`tests/`). The blast radius
today is bounded by the host, not the bot: the process holds no credentials
and can move no funds.

Residual risk — stated plainly: version specifiers are `>=` floors, so a
fresh `pip install` can pull a newer, potentially compromised release; there
is no lockfile, no hash pinning, no SBOM, and no automated vulnerability
scan. This is the largest *unmitigated* gap in the current build. Lockfile +
hash pinning + security scans in the commit gate are **Planned** (§35 step
5; `docs/SECURITY_MODEL.md` §2.4, TBD phase with Phase 4 as the natural
home). This gap must close before the process ever holds a credential.

### T3 — Log injection via venue-controlled strings

**STRIDE:** Tampering / Repudiation (against the analysis pipeline).
**Severity now: Low. Later: Medium.**

Vector: venue-controlled strings — symbol names, error `msg` fields,
`StreamStatus.detail` — carry newlines, fake JSON, or terminal escape
sequences, attempting to forge log lines or corrupt the jq-based analysis
the README's decision rule depends on.

Existing mitigations (all **Implemented**):

- Every log line is serialized as a single JSON object by `orjson.dumps`
  (`triarb/monitoring/jsonlog.py::JsonFormatter`); control characters
  (including newlines and `ESC`) are JSON-escaped, so a venue string cannot
  terminate a line early, inject a sibling line, or emit raw terminal
  escapes into a `cat`'d log.
- Venue strings land as JSON *values*, never as keys or structure, in the
  event paths that matter (`stream_status.detail`, dropped-entry warnings).
- Raw unparseable frames are logged via `%.300r` — `repr()` renders escapes
  inertly and the frame is truncated (`triarb/exchange/bitget.py`).
- Third-party logger chatter is capped at WARNING so library-formatted
  output cannot flood the JSON stream (`setup_logging`).

Residual risk: downstream consumers that *trust field content* — a jq
pipeline keyed on a symbol string, or an operator eyeballing a misleadingly
named instrument — remain foolable; JSON escaping protects structure, not
meaning. Venue symbols also become dict keys in internal state
(`triarb/observer.py::by_symbol`), which is a collision concern only if a
venue serves duplicate/aliased symbols; entries failing validation are
dropped at the boundary. Accepted for this phase.

### T4 — Resource exhaustion: subscribe floods, reconnect storms

**STRIDE:** Denial of service. **Severity now: Low. Later: Medium** (a
starved execution engine mis-prices; a starved observer just logs gaps).

Vector: a venue (or on-path interference) drops connections rapidly,
rejects subscriptions, or the bot itself floods the venue and gets banned;
alternatively unbounded internal state growth.

Existing mitigations (all **Implemented**):

- Reconnect uses exponential backoff, 1 s doubling to a 30 s cap, in all
  four adapters (`_BACKOFF_START_S`/`_BACKOFF_CAP_S` in
  `triarb/exchange/{bitget,binance,bybit,okx}.py`); the backoff resets only
  after a successful connect. The bot cannot hammer a refusing venue faster
  than once per 30 s in steady state.
- Outbound subscribe requests are chunked with inter-chunk sleeps tuned per
  venue's documented limits (bitget 20/0.15 s, bybit 10/0.25 s, binance
  50/0.5 s, okx 20/0.15 s), so the bot does not cause its own ban.
- A 45 s receive timeout (`_RECV_TIMEOUT_S`) converts a silently dead
  connection into a clean reconnect instead of an indefinite hang; app-level
  heartbeats keep live connections open.
- Internal state is bounded: triangle count is capped by config
  (`max_triangles` ≤ 2000, `triarb/config/models.py`), which bounds the
  subscription list; snapshot channels have fixed depth; per-symbol books
  are replaced in place (`triarb/pricing/orderbook.py`); the active
  opportunity map is bounded by triangle count (`triarb/observer.py`).
- During any gap all books are marked stale and open opportunities close
  with `end_reason=ws_disconnect` — degraded availability never becomes
  degraded data integrity (`triarb/observer.py::_on_stream_status`).

Residual risk: a venue flapping at exactly the backoff cadence yields a
choppy, low-value soak (visible in `stream_status`/`ws_disconnects`
metrics, so at least honest); JSON log files grow without rotation
(operator concern; rotation is **Planned**, Phase 4 hardening — TBD, safest
assumption: operator-side logrotate). No per-venue REST rate-limit budget
exists yet (**Planned**, Phase 4 per README); Phase 1's REST usage is a
handful of startup and hourly-refresh calls.

### T5 — Config tampering

**STRIDE:** Tampering / Elevation of privilege. **Severity now: Low.
Later: High** (config will eventually carry risk limits).

Vector: an attacker (or an operator mistake) edits `config/config.yaml` to
subvert behavior.

Existing mitigations (all **Implemented**):

- The most important property: **there is nothing dangerous to enable.**
  The mode gate (`ensure_supported_mode`, `triarb/config/models.py`) refuses
  every mode except `market_data_only`, with tests
  (`tests/test_config.py::TestModeGate`, Invariant 17). No config key can
  reach an order because no order code exists.
- `yaml.safe_load` only — a config file cannot construct objects or execute
  code (`triarb/config/loader.py`).
- `extra="forbid"` on every model plus range-validated fields
  (`staleness_ms > 0`, fee bps 0–100, `max_triangles` 1–2000, whitelist
  regex): unknown keys and out-of-range values abort startup with a precise
  error rather than being silently accepted.
- Venue endpoints are not configurable, so config tampering cannot redirect
  the bot to a hostile feed (see T1).

Residual risk: within its validated ranges, a tampered config can still
skew the *analysis* (e.g. absurdly high `staleness_ms` or a zero fee rate
makes the logs optimistic) — the values are echoed in the `universe` startup
event (`triarb/observer.py::prepare`) for after-the-fact detection, but
nothing blocks it. Config integrity controls (signed/audited config, risk
limits outside the ordinary config file, "do not lower thresholds
automatically" enforcement per §15, immutable risk decisions per §23) are
**Planned** for the execution phases.

---

## Future-phase threat surface (nothing below exists in code today)

These threats arrive with credentials and execution. Mitigations are
**Planned**, cited to the CLAUDE.md section that mandates them; severity is
rated for the phase in which the threat becomes real.

### F1 — Credential theft. Severity: Critical

Theft of API keys from `.env`, process memory, logs, or backups. Planned
defense in depth (§29): keys restricted to read + spot trade with startup
rejection of withdrawal-enabled keys (Invariant 16 — the exchange-side
permission model bounds even total key compromise: no withdrawals), IP
allowlisting, application-layer encryption at rest, rotation, redaction in
every output channel, credential health monitoring. See
`docs/SECURITY_MODEL.md` §2 for landing phases and TBDs.

### F2 — Order forgery / risk-engine bypass. Severity: Critical

A code path (or compromised component) submits orders without risk
approval. Planned: the execution engine must never bypass the central risk
engine and the opportunity detector must never call a trading endpoint (§7);
every live order passes the risk engine (Invariant 13); adapters cannot
bypass central execution controls (Invariant 23); immutable audit record per
order (Invariant 14, §18 state machine).

### F3 — Duplicate submission. Severity: High

Replay, retry-after-timeout, or duplicate WS events cause the same leg to
execute twice. Planned: deterministic client order IDs encoding
bot/exchange/cycle/leg/attempt (§19); reconcile-before-resubmit — query by
client order ID, inspect order stream and executions, prove non-execution
first (§19); a timeout can never auto-resubmit (Invariant 10); duplicate
events cannot create duplicate orders (§32 property tests).

### F4 — Risk-limit tampering. Severity: High

Limits weakened at runtime — by an attacker, an operator under pressure, or
the system itself chasing trades. Planned: thresholds are never lowered
automatically (§15); risk decisions are immutable structured records (§23);
weakening risk limits requires recorded human authorization (§38);
kill-switch activations require authorized restart (§24).

### F5 — Insider LLM in the execution path. Severity: Critical

The failure mode where a language model — including the one building this
repository — decides live orders, computes final live quantities, alters
risk limits during execution, approves its own trades, or generates
unvalidated prices. Banned outright by §4: the live engine must be
deterministic, versioned, auditable, reproducible, and independently
testable, with **no LLM in the low-latency execution path**. Planned
enforcement: an automated test for Invariant 22, and independent review of
critical components — the agent implementing a component must not be its
only reviewer (§34). Today this threat is structurally absent only because
no execution path exists; the invariant is what keeps it absent.

---

## Standing review triggers

Re-run this threat model (per §35/§37 the security review must pass before
any release candidate) whenever: a phase boundary is crossed, a credential
is first read, a new dependency is added, a venue adapter gains a private
endpoint, or any management/API surface is introduced.

TBDs recorded in this document: log rotation approach (safest assumption:
operator-side logrotate until Phase 4); lockfile/hash-pinning phase (safest
assumption: minimal deps in a dedicated venv, close the gap before any
credential exists); cross-venue plausibility checks for T1 (safest
assumption: none in the observer — single-venue data is labeled as such and
the economic decision accounts for it).
