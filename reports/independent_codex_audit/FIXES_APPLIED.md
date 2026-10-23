# Fixes Applied

This is the audit change log. It describes changes made in the existing dirty worktree; no commit or push was performed.

| Area | Change | Regression/evidence |
|---|---|---|
| Timestamp parser | Strict provider epoch/ISO parsing; no local-now fallback; timezone-aware IST | Parser, known-epoch, malformed, and freshness-boundary tests |
| Option archive | Require raw `TimeStamp`/`TickDt`; skip missing/malformed/nonpositive records | Archive inspection; recorder tests |
| Paper costs | Store entry charges on position; independently round buy/sell legs; reconcile net/equity/cash | Lifecycle/accounting tests and hand calculation |
| Config safety | Default `paper_execution_enabled=False`; missing/malformed YAML forces `LIVE_READ_ONLY` | New missing/malformed-config tests; start refusal |
| Broker boundary | Read-only raw-client proxy blocks public transaction methods and generic `order_request` | 11 transport boundary tests |
| Persistence | Atomic temp/fsync/replace writes; shared generation ID; cash/position/generation checks; kill switch on mismatch | Mixed-state restart test |
| Process lock | Exclusive creation, same-process duplicate rejection, stale inode checks, run-ID owner release | Lock tests |
| Backtest fills | Next-bar fill only; target/stop rebased from actual fill; no double slippage | Backtest execution integrity tests |
| Backtest timing | 14:30 cutoff applies to signals and pending fills | Cutoff regression tests; old late entries documented |
| Reference levels | Official daily PDH/PDL/PDC/PDO/DMA prior-only map; intraday body-zone window ends 15:25 | Reference-level official override and no-leak tests |
| Strategy safety | Removed fabricated live levels; ORB requires trend context/body filter; IRF M3 requires swing low; PDR/PDS require real zones; PDS M3 requires weak first red | Strategy unit tests |
| RF | Canonical unresolved exit emits no trade; 60-point form renamed experimental | RF strategy tests |
| Calendar | Corrected 2026 NSE F&O holidays using official circular evidence | Calendar additions/removals tests |
| Research integrity | Disabled hard-coded chart generator; added supersession/caution banners to historical reports | Research-integrity test |
| Reporting | Run-scoped SQL and evidence-based validation status | Reporting tests and source inspection |
| CLI | Public `scripts/run_paper_trading.py` now routes to audited `trading_platform.paper.cli` | Launcher help and gate-refusal command |
| Historical regression | Updated stale auditor expectations to corrected 89 matches / ORB 45, with comments explaining superseded legacy values | Full suite |

## Deliberately not changed

- No strategy parameters were optimized or tuned.
- No real broker order, modify, cancel, or transaction endpoint was called.
- `BASELINE_V1` artifacts were not overwritten.
- The corrupt paper state and option archive were not deleted; they remain forensic evidence.
- The dormant duplicate engine was not removed because that would be a broader compatibility change.

