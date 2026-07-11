# INCIDENT_RESPONSE.md — incident handling for the MARKET_DATA_ONLY phase

> Status labels: **Implemented** = true of the code today, with a file
> reference. **Planned** = required by the master prompt (`CLAUDE.md`
> Part B, section cited) for a later phase. This build contains **no
> order, balance, withdrawal, or transfer code**, so no incident in this
> phase can lose money directly. What an incident *can* do is corrupt or
> interrupt the observation data that the Phase-2 go/no-go decision rests
> on — that is what this document protects. Nothing here implies the
> system is profitable or production-ready.

## 1. Severity classification (current phase)

| Severity | Class | Why it ranks here today |
|---|---|---|
| SEV-1 | **Data integrity**: wrong prices, misparsed payloads, stale books treated as fresh | Silently poisons the observer's evidence. A run that overstates edges could justify building (or worse, later funding) a strategy the data does not support. |
| SEV-2 | **Availability**: WS flapping, REST outages, process crash | Loses coverage and invalidates persistence measurement across every gap, but fails loudly and corrupts nothing. |
| SEV-3 | **Degradation**: metadata refresh failures, degraded symbols, dashboard errors | Observer keeps running on aging inputs; bounded, visible, recoverable by restart. |
| — | **Financial**: duplicate orders, unreconciled fills, stranded inventory, balance mismatch | **Planned.** Cannot occur in this build — no order code exists. Becomes the top severity the moment execution ships (master prompt §18–§24). |

The ranking is deliberate: in an observer, bad data is worse than no data.

## 2. SEV-1 — data-integrity incidents

Wrong prices in books, misparsed venue payloads, or evaluations running on
data that should have been rejected.

### Detection signals (all Implemented)

- `cycle_detected` lines with implausible `net_bps` (well above the fee
  hurdle, persisting) — the README's economics say persistent large edges
  are usually an artifact, not profit. Cross-check the venue's own UI at
  the logged `ts` before believing any of them.
- `net_edge_bps` histogram mass far from 0 in `stats`
  (`triarb/monitoring/metrics.py`) — a healthy universe centers near or
  below zero after fees.
- `universe` anomalies at startup: symbol/currency counts or filter
  attrition wildly different from previous runs on the same config.
- REST-boundary failures: pydantic validates every REST payload; schema
  mismatches raise `RestError` with endpoint context, and individually
  malformed entries are dropped and counted, never coerced
  (`triarb/exchange/base.py`, adapter docstrings in
  `triarb/exchange/bitget.py` / `binance.py` / `bybit.py` / `okx.py`).
- Bybit-specific: a delta sequence gap makes the adapter emit an **empty**
  snapshot (unpriceable — fail safe), a `stream_status` `error`, and a
  topic resubscribe (`triarb/exchange/bybit.py`). Repeated occurrences
  are a data-integrity signal even though the failure mode is safe.
- Stale-as-fresh cannot happen silently by design: staleness is measured
  from local monotonic receive time (`triarb/pricing/orderbook.py`), and
  every `cycle_detected` carries `max_book_age_ms` so aging-leg
  detections can be discarded in analysis. If evidence ever contradicts
  this, it is a SEV-1 bug.

### Immediate action

1. Stop the observer (SIGINT — clean shutdown logs the final metrics
   snapshot, `triarb/cli.py`).
2. Preserve evidence first (§5); do not edit or truncate the JSONL.
3. Mark the affected run's data **untrusted** for the Phase-2 decision —
   record the invalidated time window in the incident record.
4. Reproduce the misparse offline: the parse-test pattern in
   `tests/test_bitget_parse.py` / `test_binance_parse.py` /
   `test_bybit_parse.py` / `test_okx_parse.py` is fixture-based and needs
   no network — add the offending payload as a failing test before fixing.
5. Restart observation only after the fix lands with its regression test
   and `pytest`, `ruff check .`, `mypy triarb` are clean (`CLAUDE.md`
   Part A). Master prompt §35 ranks incorrect order-book state as P0.

## 3. SEV-2 — availability incidents

### 3.1 Process crash

**Implemented behavior:** any unexpected exception in an observer task
cancels the whole `asyncio.TaskGroup` and the process exits loudly with a
traceback — no silent degradation (`triarb/observer.py`). Preserve the
traceback and JSONL, file an incident, add a regression test, restart.
Nothing needs reconciliation: no orders or balances exist.

### 3.2 WebSocket flapping

**Detection:** repeated `stream_status` `disconnected`/`connected` pairs;
`ws_disconnects` climbing across `stats` lines. Each disconnect marks all
books stale and closes every active opportunity with
`end_reason=ws_disconnect` (`Observer._on_stream_status`), so persistence
data spanning the gaps is invalid.

**Immediate action:** adapters already reconnect with exponential backoff
(1 s → 30 s cap) and resubscribe (`triarb/exchange/bitget.py`); do not
restart-loop by hand on top of that. Check the venue's status page and
your host's network. Distinguish expected causes (Binance's routine 24 h
connection drop, `triarb/exchange/binance.py`) from real flapping. If the
run's purpose was persistence measurement, note the window as degraded in
the incident record.

### 3.3 REST outages

**Detection:** startup aborts with `RestError` (symbols/tickers fetch in
`Observer.prepare`), or the `metadata_refresh_failures` counter climbs
mid-run while the observer continues on previous metadata
(`Observer._metadata_loop`).

**Immediate action:** startup failure — check venue status, retry later;
nothing to clean up, the process never subscribed. Mid-run — tolerate
isolated failures (designed-for); sustained failure means feasibility
checks run on aging precision/min-notional data, so restart once REST
recovers and treat feasibility results from the degraded window with
suspicion.

## 4. SEV-3 — degradation

**Detection:** `symbols_degraded` WARNING (a subscribed symbol left the
venue's online set — Phase 1 does not rebuild triangles mid-run;
`Observer._metadata_loop`), or `dashboard render failed` errors (cosmetic
by design — the JSONL stream is the source of truth,
`triarb/monitoring/dashboard.py`).

**Immediate action:** for degraded symbols, restart at a convenient point
to rebuild the universe; note the boundary in analysis. Dashboard errors
need no immediate action; file a low-priority fix.

## 5. Evidence to preserve — every incident, before any fix

**Implemented sources.** Copy into the incident directory before touching
anything:

1. **JSONL logs** — the complete file(s) for the run (`--log-file` /
   `triarb-observer.jsonl` / captured stdout), unedited.
2. **Exact config** — the `config/config.yaml` actually used (the
   `startup` event records the path, not the contents).
3. **Code identity** — `git rev-parse HEAD`, plus `git status --porcelain`
   and `git diff` if the working tree was dirty.
4. **Environment** — `pip freeze`, Python version, host OS/region (region
   determines latency interpretation per the README).
5. **Venue context** — status-page/announcement snapshots for the incident
   window; for SEV-1, the raw offending payload if capturable (it becomes
   the regression-test fixture).

Storage location is **TBD** (repo owner decision, per `CLAUDE.md` §6);
safest reversible assumption until decided: commit records under
`docs/incidents/INC-<YYYYMMDD>-<NN>.md` with large log excerpts referenced
by path/offset rather than committed wholesale.

## 6. Post-incident record template

```markdown
# INC-<YYYYMMDD>-<NN>: <one-line title>
- Severity: SEV-1 | SEV-2 | SEV-3
- Class: data-integrity | availability | degradation
- Venue / mode: <venue> / MARKET_DATA_ONLY
- Git SHA: <rev-parse HEAD> (dirty: yes/no)
- Config: <path or inline copy>
- Detection: <log signal + first bad `ts`>
- Timeline (UTC): <first bad ts> → <detected> → <stopped> → <fixed> → <restarted>
- Impact: <observation windows invalidated for the Phase-2 decision>
- Root cause: <what actually happened>
- Evidence: <paths to preserved JSONL / payloads / status snapshots>
- Corrective action: <fix + the regression test that now covers it>
- Status: open | fixed-pending-soak | closed
```

Fields mirror what §18/§21 will require per execution cycle later, so
Phase-1 records stay comparable with future ones.

## 7. Escalation path — Planned

Today's whole path is **Implemented** as: stop the process, preserve
evidence, fix with a regression test, restart. There are no funds to
protect, so nothing escalates beyond the operator. When execution exists:

- **§21 recovery process** (Planned — `CLAUDE.md` Part B §21): freeze new
  cycles on the affected exchange → reconcile orders → reconcile fills →
  reconcile balances → calculate unintended exposure → evaluate safe
  return paths → choose the lowest-risk approved unwind → execute
  protected recovery orders → reconcile again → generate incident record.
  Human intervention is required whenever uncertainty remains; recovery
  never blindly market-sells.
- **§24 kill switches** (Planned — `CLAUDE.md` Part B §24): global /
  exchange / account / triangle / symbol / asset / strategy-version
  switches plus the automatic daily-loss, repeated-failure, stale-data,
  reconciliation, and exchange-disconnection switches. Activation blocks
  new cycles, preserves recovery capability, cancels safe-to-cancel
  orders, reconciles, notifies operators, records the reason, and
  requires an **authorized restart** — an incident will not end with a
  casual process restart the way Phase-1 incidents do.
- Cycles stuck in `RECOVERY_REQUIRED` or `MANUAL_INTERVENTION`
  (Planned — `CLAUDE.md` Part B §18 state machine) are standing incidents
  until resolved and recorded.

## 8. Contact and authorization chain — TBD

Who is paged, who may authorize a restart after a kill switch, and who
signs off on incident closure is **TBD** (repo owner decision, recorded
per `CLAUDE.md` §6). Safest reversible assumption until decided:

- Single-operator model: whoever runs the observer owns its incidents
  end-to-end.
- After a SEV-1, restarting observation requires the fix plus regression
  test to be merged first — no "restart and watch" on known-bad parsing.
- No escalation contact implies no unattended operation posture beyond
  what a crash-loud observer already provides; anything requiring an
  on-call rotation waits for the phase that puts money at risk, where §24
  operator notification and authorized-restart rules become mandatory.

## 9. TBD register (per CLAUDE.md §6)

| Item | Status | Safest reversible assumption recorded |
|---|---|---|
| Incident record storage location | TBD | `docs/incidents/INC-<YYYYMMDD>-<NN>.md`, logs referenced by path (§5). |
| Contact / authorization chain | TBD | Single operator owns incidents; SEV-1 restart requires merged fix + test (§8). |
| Alerting channel (nothing pushes notifications today) | TBD | Operator monitors JSONL/dashboard; automated notification arrives with §24 kill switches (Planned). |
