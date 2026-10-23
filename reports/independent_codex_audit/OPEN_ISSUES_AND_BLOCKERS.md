# Open Issues and Blockers

The platform is **not approved for supervised paper trading**. Items below are ordered by operational risk.

## P0 — Do not enable execution

1. **No clean timestamp-authentic option history.** Existing archive provider times equal receipt times and includes nonpositive prices. A clean provider-timestamp/bid/ask archive is required before any premium fill or option P&L claim.
2. **State/account contamination.** Broker cash ₹102,429.65 conflicts with portfolio cash ₹250,000 and the old report. The new engine rejects it, but the operator must explicitly archive/reset/reconcile the state before a new run.
3. **Order isolation regression risk.** The audited proxy is safe today, but every py5paisa upgrade or new broker callback needs transport-boundary tests. No transactional API should be enabled as part of recovery.

## P1 — Must be resolved before a paper soak

4. **Strategy specification gaps.** ORB “established trend,” IRF wick/support tolerances, PDR/PDS zone/proximity/stop choice, and RF entry/exit are not fully executable from the PDFs. Obtain written deterministic definitions or keep affected methods disabled.
5. **Special-session calendar.** Add explicit, authoritative session intervals for Muhurat, Budget Sunday, and future ad-hoc sessions. Do not use a weekday/holiday boolean as an executable-market proof.
6. **Live-data soak missing.** The only real read-only check returned an API response; `observe` hit an SSL handshake timeout. Demonstrate multiple market-session cycles with provider timestamps, receipt timestamps, quote ages, spread, contract identity, and reconnect/auth-expiry behavior.
7. **Genuine option E2E missing.** With read-only live data only, show contract selection, ask/bid observation, simulated fill, target/stop trigger, pending exit, and restart recovery using valid timestamps. No order endpoint is needed or permitted.
8. **Optimization claim invalid.** Rebuild the walk-forward ledger only after engine/data corrections, with non-overlapping OOS periods and reproducible trial artifacts. Do not present WFER 0.82.
9. **Daily loss limit semantics.** Document that the limit is a gate and not a guaranteed loss ceiling under gaps, missing quotes, or pending exits; add explicit stress tests.

## P2 — Required hardening

10. **Duplicate engine module.** Consolidate or clearly deprecate `src/trading_platform/execution/paper_trading_engine.py`; differing cutoff/defaults are a future operator hazard.
11. **DuckDB transactions and migrations.** Add schema versioning, explicit transaction boundaries, and crash/reopen tests for each write group. Current atomic JSON files do not make DuckDB multi-table writes atomic.
12. **Process identity.** Locking checks PID and command heuristics; add process start-time identity to reduce PID-reuse ambiguity.
13. **Report/archive lifecycle.** Mark all old reports and option files as superseded/quarantined in UI and export paths; prevent “PASSED” or fresh labels from inherited state.
14. **Dashboard access.** Confirm bind address/authentication and ensure it cannot expose credential-adjacent diagnostics beyond the local operator.

## P3 — Documentation/maintenance

15. Replace caution-banded historical reports after a ledger-derived chart generator exists.
16. Add a machine-readable audit manifest tying code revision, data hashes, calendar revision, and report run ID together.

## Release gate

Paper execution may be reconsidered only after P0 and P1 items have evidence-backed closure, the state is clean, the option archive is genuine, and a read-only soak passes. Live broker trading remains outside this audit and is prohibited.

