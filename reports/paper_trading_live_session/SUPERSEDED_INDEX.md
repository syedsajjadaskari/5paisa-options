# Superseded and stale evidence (index)

Marked 2026-10-29. Byte-exact copies of every file that was edited or labelled are in
`archive/evidence_originals_2026-10-29/` (checksums in `MANIFEST.sha256`; see its README.md). Nothing was deleted.

Current sources of truth:

* `README_PAPER_TRADING.md` and `docs/live_app_architecture.md` - how the live paper app works
* `docs/STRATEGY_EXECUTION_STATUS.md` - what each strategy does today (tier, provisional rules, who approved)
* `docs/AUTHOR_CONFIRMATION_REQUEST.md` - the document the strategy author should answer
* `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825` - current backtest (184 trades, +813.60 index points, PF 1.22, window 2025-09-01..2026-08-31; in-sample; no option P&L)

| file (in this folder unless a path is given) | status | why | use instead |
|---|---|---|---|
| `STRATEGY_AUTHOR_FINAL_SIGNOFF.pdf` | SUPERSEDED (companion notice; PDF bytes unchanged) | Asks the author to approve a pre-V3 engine (10 pt proximity, 5-bar pivot, 360/288 counts), omits all new rules, carries an unsupported 'Antigravity ... Verified' footer. | `docs/AUTHOR_CONFIRMATION_REQUEST.md` |
| `AMBIGUOUS_RULE_EXPOSURE.csv` | SUPERSEDED (record_status column added) | Pre-V3 assumptions ('5-bar swing pivot', '10pt zone buffer') and counts. | `docs/AUTHOR_CONFIRMATION_REQUEST.md; regenerate via scripts/generate_final_paper_decision_artifacts.py` |
| `CORRECTED_STRATEGY_AUTHORIZATION_MATRIX.md` | SUPERSEDED (banner) | Claims '100% Reconciled ... Source Code' for a pre-V3 engine; cites 10pt buffer, 5-bar pivot, 38/38/135 signals. | `docs/STRATEGY_EXECUTION_STATUS.md` |
| `STRATEGY_AUTHORIZATION_MATRIX.md` | SUPERSEDED (banner) | Same pre-V3 engine description. | `docs/STRATEGY_EXECUTION_STATUS.md` |
| `PAPER_TRADES.csv` | SYNTHETIC FIXTURE (labelled in place + notice) | Two scripted lifecycle-test trades tagged 'VALID_TWO_SIDED_EXCHANGE_QUOTE'; never a real session. | `none (no live fills exist); runtime/journal/trades.csv of the live app` |
| `TRADE_EVENT_LOG.csv` | SYNTHETIC FIXTURE (labelled in place + notice) | Scripted events tagged 'AUTHENTIC_EXCHANGE_DATA'; row quotes a 24,076-24,083 zone next to a 22,600 spot. | `none; runtime/journal/events_YYYYMMDD.jsonl of the live app` |
| `VIRTUAL_PORTFOLIO_SUMMARY.csv` | SYNTHETIC FIXTURE (labelled in place + notice) | 'Approved virtual equity ledger', +2,880.42 'profit', +1.15%, PF 2.16 are fixture arithmetic; limits quoted (5,000 / 2%) are the legacy stack's. | `none; live app limits are in configs/live_paper.yaml` |
| `STRATEGY_SIGNAL_TIMELINE.csv` | STALE (companion notice only; not modified) | Historical 2026-08-31 shadow rows from the pre-V3 strategy code; not a fixture. | `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825/signals.csv` |
| `PAPER_TRADING_GO_NO_GO.md` | SUPERSEDED (banner) | Names removed paper/engine.py and paper_execution_enabled; GO steps run the archived DuckDB collector; 'GO' for data collection rests on the old collector. | `README_PAPER_TRADING.md; docs/STRATEGY_EXECUTION_STATUS.md` |
| `PREMARKET_CHECKLIST.md` | SUPERSEDED (banner) | PASS for 'prior session 2026-10-23 loaded' is false: daily history ends 2026-08-31; levels are the 2026-08-28 values. | `scripts/run_paper_trading.py --doctor` |
| `SAFETY_GATE_REPORT.md` | SUPERSEDED (banner) | Describes the retired paper/ + DuckDB stack's gates. | `docs/live_app_architecture.md` |
| `DAILY_OPERATIONAL_REPORT.md` | SUPERSEDED (banner) | Retired collector/DuckDB stack; header date 2026-10-23 vs 10-27 expiry quotes (internally inconsistent). | `README_PAPER_TRADING.md` |
| `BACKTEST_VERSION_RECONCILIATION.md` | SUPERSEDED (banner) | Pre-V3 numbers (274 trades, +2,400.90 pts, PF 1.46), roughly 3x the current engine. | `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825` |
| `PORTFOLIO_PERFORMANCE.md` | SUPERSEDED (banner) | Same pre-V3 numbers. | `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825` |
| `PAPER_EXECUTION_BLOCKERS.md` | SUPERSEDED (banner) | Blockers/GO steps refer to the retired stack. | `README_PAPER_TRADING.md` |
| `FORWARD_PAPER_TRADING_READINESS.md` | SUPERSEDED (banner) | Ledger/risk manager of the retired stack. | `docs/live_app_architecture.md` |
| `HISTORICAL_CASE_VALIDATION.md` | SUPERSEDED (banner) | Cases produced by pre-V3 strategy code. | `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825/signals.csv` |
| `LIVE_SESSION_OPERATIONAL_CHECKLIST.md` | SUPERSEDED (banner) | Checklist names archived ledger/risk manager. | `README_PAPER_TRADING.md` |
| `../../COMBINED_PORTFOLIO_TRADES.csv (repo root)` | SUPERSEDED (companion notice only; dashboard reads it) | Pre-V3 FIFO portfolio. | `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825/portfolio_trades.csv` |
| `../../docs/phase4_runbook.md, ../../docs/phase4b_runbook.md` | SUPERSEDED (banner) | Commands `python -m trading_platform.paper.cli ...` fail with ModuleNotFoundError. | `README_PAPER_TRADING.md` |
| `IN_WINDOW_SIGNAL_DISPOSITION_RECONCILIATION.csv, MANUAL_MATCH_QUALITY_AUDIT.csv, MANUAL_VS_AUTOMATED_COMPARISON.csv, REAL_HISTORICAL_SIGNAL_CASES.csv, REPRODUCIBLE_HISTORICAL_SIGNAL_CASES.csv, true_signal_reproduction/**` | STALE (listed only; not modified) | Computed with the pre-V3 strategies (340 in-window signals / 315 standalone / 274 portfolio). Useful as history, wrong for the current rules. | `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825/signals.csv, portfolio_trades.csv, rejected_signals.csv` |
| `../../docs/phase4_completion_report.md, phase4_paper_trading_architecture.md, phase4_safety_audit.md, phase4b_supervised_validation.md` | STALE (listed only; not modified) | Describe the retired Phase-4 stack. | `docs/live_app_architecture.md` |
| `CORRECTED_SESSION_RECONCILIATION.md, RUNTIME_STATUS.md, POST_SESSION_DIAGNOSTICS.md, SESSION_PERFORMANCE.html, LIVE_MARKET_DATA_QUALITY.csv` | CURRENT as history (already carry their own SYNTHETIC/SUPERSEDED disclosures) | Accurate account of the 2026-10-26 partial read-only capture: 0 live fills, realized P&L 0.00. They do not mention that daily history ends 2026-08-31. | - |

Why this matters: a signature on the old sign-off PDF would approve rules the code does not implement, and the labelled-as-authentic synthetic CSVs show a profitable session that never happened. Nothing in this folder is author-approved evidence; no strategy is AUTHOR_APPROVED.
