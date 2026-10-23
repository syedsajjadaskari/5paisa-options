# Detailed Findings

Line references are to the audited working tree on 2026-10-22. “Fixed” means a code change and regression evidence exist; it does not mean deployment is approved.

## F-001 — Raw broker client exposed transactional transport

Severity: **P0 — fixed**  
Location: `src/trading_platform/brokers/fivepaisa.py:40-110`; `tests/options/test_5paisa_read_only.py`  
Expected: every broker object reachable by the paper engine must reject place, modify, cancel, and generic transactional transport.  
Actual: the installed SDK contains a generic `order_request`; a raw client reference would have been a bypass.  
Evidence: installed py5paisa source showed `OP`, `OM`, `OC`, and `order_request`; proxy tests block 11 transport paths.  
Impact: possible real order if a future callback accessed raw SDK methods.  
Fix: read-only proxy at the boundary; no order was sent.  
Limitation: a future dependency/API change needs a new boundary audit.

## F-002 — Provider timestamp was not trustworthy

Severity: **P0/P1 — fixed for new data; historical archive blocked**  
Location: `src/trading_platform/data/live_market_feed.py:19`; `src/trading_platform/paper/recorder.py:396-404`; tests in `test_phase4b_heartbeat_and_status.py`.  
Expected: use exchange/provider time, reject malformed/missing time, and calculate age independently.  
Actual: old paths could substitute receipt/local time; archive rows had provider time equal to receipt time.  
Evidence: `/Date(1729571156000+0530)/` = 2024-10-22 09:55:56 IST; 8,257 DuckDB and 8,140 parquet option rows all had equal provider/receipt timestamps.  
Impact: stale/future or fabricated option fills and false “fresh” status.  
Fix: strict parser, strict recorder, independent age calculation.  
Remaining: historical archive is unusable and needs a new provider-timestamp-valid soak.

## F-003 — Paper accounting charged entry costs twice

Severity: **P0 — fixed**  
Location: `src/trading_platform/paper/portfolio.py:40,101-145`; `src/trading_platform/paper/engine.py:556-567`.  
Expected: deduct each leg once, round each leg according to the cost model, and reconcile cash/equity/net P&L.  
Actual: entry charges were deducted at entry and again in prior round-trip accounting; portfolio realized P&L omitted entry charges.  
Evidence: qty65 example: correct total charges ₹63.76; old route differed by the ₹26.53 entry charge.  
Fix: `entry_charges` is persisted on the active position; entry and exit costs are independently rounded; reconciliation assertions added.

## F-004 — Persisted broker, portfolio, and report state disagreed

Severity: **P0/P1 — fail-closed remediation**  
Location: `src/trading_platform/paper/engine.py:1077-1235`; `paper/broker.py:319-368`; `paper/portfolio.py:172-217`.  
Expected: atomic, same-generation snapshots with matching cash and positions.  
Actual: broker cash ₹102,429.65 conflicted with portfolio cash ₹250,000; old report labeled the session passed.  
Fix: shared generation ID, atomic temp/fsync/replace writes, load-time generation/cash/position checks, kill switch on mismatch.  
Impact: without this, restart could double-count or silently erase a position.  
Remaining: the old production state remains contaminated and must be archived/reset only with explicit owner approval.

## F-005 — Configuration could fail open

Severity: **P0 — fixed**  
Location: `src/trading_platform/paper/engine.py:86,113-164`; `config/paper_trading.yaml:5`; tests added to heartbeat/status suite.  
Expected: missing or malformed configuration cannot enable paper execution.  
Actual: dataclass default was true and parse fallback returned a default object.  
Fix: default false; missing/malformed fallback is `LIVE_READ_ONLY` and false.  
Evidence: both new fail-closed tests pass; `start` refuses with the production config.

## F-006 — Public launcher selected a duplicate legacy engine

Severity: **P1 — fixed at entry point; duplicate remains**  
Location: `scripts/run_paper_trading.py`; legacy `src/trading_platform/execution/paper_trading_engine.py`.  
Expected: one audited operational implementation.  
Actual: top-level launcher imported the older engine with a 15:00 cutoff and different persistence/config behavior.  
Fix: launcher now runs `trading_platform.paper.cli`; the duplicate is explicitly dormant.  
Remaining: remove or consolidate only after compatibility inventory and owner approval.

## F-007 — Backtest next-bar risk levels were not rebased

Severity: **P1 — fixed**  
Location: `src/trading_platform/backtesting/engine.py`; `tests/unit/test_backtest_execution_integrity.py`.  
Expected: fixed-distance target/stop is measured from actual tradable fill.  
Actual: prior logic could measure from signal reference.  
Evidence: signal 100, fill 110.5, 60-point target: old 160; corrected 170.5.  
Fix: target/stop offsets retained in metadata and rebased on next-bar fill.

## F-008 — Same-time option bars and slippage handling were unsafe

Severity: **P1 — fixed**  
Location: `src/trading_platform/backtesting/option_execution.py:5,75`; `indian_options_costs.py`; option execution tests.  
Expected: fill only from a strictly later option observation and apply slippage/costs once.  
Actual: prior boundary permitted same timestamp and cost path could double slippage.  
Fix: strict `>` comparison and separated execution price/cost calculation.

## F-009 — Future/inconsistent reference levels could alter signals

Severity: **P1 — fixed for code path**  
Location: `src/trading_platform/data/reference_levels.py:88-172`; `tests/unit/test_reference_levels.py`.  
Expected: prior official daily values for PDH/PDL/PDC/PDO/DMA; intraday body zones only when required.  
Actual: intraday summaries, including post-close records, could define prior-day references.  
Fix: official daily map preferred, strict date filtering, intraday restricted to 09:15–15:25, future-leakage tests.

## F-010 — Strategy code substituted undocumented levels and fallbacks

Severity: **P1 — fixed/disabled where unresolved**  
Location: `strategies/irf.py`, `orb.py`, `pdr.py`, `pds.py`, `rf.py`.  
Expected: only source-rule-supported signals.  
Actual: missing context could be replaced with invented levels; ORB could signal without trend context; RF experimental exit was mixed with canonical results.  
Fix: require context, remove synthetic levels, add PDS prerequisite, disable canonical RF and label the 60-point path experimental.  
Remaining: qualitative rule thresholds and exact zone/proximity definitions are unresolved.

## F-011 — Entry cutoff was not consistently enforced

Severity: **P1 — fixed**  
Location: `src/trading_platform/backtesting/engine.py:79,162,242`; `paper/engine.py:369`; cutoff tests.  
Expected: no new signal or pending fill at/after 14:30.  
Actual: old ledger contains 20 entries at/after cutoff, including two at 15:15.  
Fix: cutoff applies to signal generation and pending fills; corrected candidate is separately reported.

## F-012 — Historical option archive is not usable for option backtesting

Severity: **P0/P1 — blocked**  
Location: archive files under `data/processed/options`; recorder fix in `paper/recorder.py`.  
Expected: provider-time, positive, timestamped bid/ask observations with contract identity.  
Actual: all archived provider/receipt times equal; 250–270 nonpositive prices.  
Impact: option selection, freshness, liquidity, and premium P&L cannot be verified.  
Fix: quarantine by policy; recorder now fails closed.  
Remaining: collect a clean archive.

## F-013 — Static calendar misses special sessions

Severity: **P1 — open**  
Location: `src/trading_platform/data/calendar.py`; calendar tests.  
Expected: official regular, Muhurat, Budget, and ad-hoc schedule handling.  
Actual: regular holidays corrected, but 2025-10-21 Muhurat and 2026-02-01 Budget session are not represented as dynamic exceptions.  
Impact: false market-closed/open decisions and invalid candle expectations.  
Fix status: regular holiday correction complete; special-session model remains blocked.

## F-014 — Research charts contained hard-coded trade overlays

Severity: **P1 — disabled**  
Location: `scripts/run_phase3c_research.py:516-519`; research integrity test.  
Expected: every plotted value is derived from the current ledger.  
Actual: charts included hard-coded dates/prices, returns, and correlation matrix.  
Fix: generator now raises rather than emit misleading charts; prior docs have caution/supersession banners.  
Remaining: rewrite chart generation from typed ledger inputs before publication.

## F-015 — Report said PASSED without evidence

Severity: **P2 — fixed for new reports**  
Location: `src/trading_platform/paper/reporting.py:165-198`.  
Expected: session validation reflects actual evidence and is run-scoped.  
Actual: old validation hard-coded `PASSED` and queries were not consistently scoped.  
Fix: parameterized run ID and evidence/not-verified statuses.  
Remaining: previously generated reports are not retroactively corrected.

## F-016 — Optimization WFER claim does not reconcile

Severity: **P1 — open/invalidated**  
Location: optimization artifacts and `walk_forward.py`; research reports.  
Expected: WFER calculated from non-overlapping, reproducible fold ledgers.  
Actual: saved fold sums imply ORB ratio 0.2740, while report claims 0.81–0.82 using different numbers.  
Fix status: no new optimization run; artifacts marked invalid by corrected engine/chart integrity.  
Impact: no robust out-of-sample claim is supportable.

## F-017 — Paper process and persisted account required operator intervention

Severity: **P1 — remediated operationally**  
Evidence: old PID 62986 was found and stopped; it was flat, but used old code. Dashboard PID 63501 remains.  
Fix: engine stopped; start gate false; public launcher corrected.  
Remaining: dashboard should be treated as informational only until state/archive reset and clean soak complete.

## F-018 — Security/credential inventory

Severity: **P1 — no tracked-secret finding**  
Evidence: `.env` and token cache are ignored and mode 600; no tracked secret-like file was found. `doctor` output was not copied with values.  
Remaining: rotate credentials only if the user believes any exposed logs or prior external sharing occurred; no rotation was performed.

