# Repository cleanup manifest (2026-10-28)

Purpose: retire the Phase-4 DuckDB paper stack and one-shot report scripts now that the in-memory `trading_platform.live` app (see `docs/live_app_architecture.md`) is the only live/paper path.

Rules followed: nothing is deleted except regenerable caches (section C); everything else is moved with plain `mv` under `archive/legacy_2026-10-28/` keeping its relative layout; every item below carries a one-line restore command run from the repo root. A full pre-cleanup copy of the working tree also exists at `/Users/syedaskari/project/5paisa-options_SNAPSHOT_2026-10-28` (untouched).

Status: **EXECUTED** (all moves/deletes below were applied and verified).

Restore everything at once: `rsync -a archive/legacy_2026-10-28/ ./` (then remove the new rewritten `scripts/verify_installation.py` copy and the three ported test files if you want the exact old tree).

Out of scope and never touched: `.venv/`, `.git/`, `.env*`, `data/**` (incl. `data/cache/**`, `data/processed/*.duckdb`, `data/.DS_Store`), `docs/strategy_rulebooks/**`, `docs/baseline_v1_manifest.md`, `reports/phase2d/**`, `reports/phase3a/**`, `reports/independent_codex_audit/**`, `reports/manual_journal/**`.

## B. ARCHIVED (moved, never deleted)

### B1. Source modules

| path | size | reason | replacement | restore |
|---|---|---|---|---|
| `src/trading_platform/execution/paper_trading_engine.py` | 48 KB | Legacy 1241-LOC paper engine: no authorization gate, fabricates reference levels (pdl=low-50 ...), DuckDB recorder, 15:00 cutoff. Importers: only the retired safety test. | live/engine.py (SessionEngine) | `mkdir -p src/trading_platform/execution && mv archive/legacy_2026-10-28/src/trading_platform/execution/paper_trading_engine.py src/trading_platform/execution/paper_trading_engine.py` |
| `src/trading_platform/brokers/paper_broker.py` | 16 KB | Near-duplicate of paper/broker.py; only used by the legacy engine, test_broker_interface and the Phase-1 verify_installation. | live/ledger.py (VirtualAccount) | `mkdir -p src/trading_platform/brokers && mv archive/legacy_2026-10-28/src/trading_platform/brokers/paper_broker.py src/trading_platform/brokers/paper_broker.py` |
| `src/trading_platform/paper/` | 184 KB | Phase-4 DuckDB paper stack (engine, broker, portfolio, risk, accounting, recorder, reporting, shadow, cli). Persists market data to DuckDB, mode gate defaulted to REPLAY_TEST, reference levels were a deliberate no-op so it could never emit a real signal. | live/* (in-memory, authorization tiers, journal/checkpoint) | `mkdir -p src/trading_platform && mv archive/legacy_2026-10-28/src/trading_platform/paper src/trading_platform/paper` |
| `src/trading_platform/data/read_only_collector.py` | 16 KB | DuckDB-backed continuous market-data collector (violates the 'no market-data database' rule). | live/providers/fivepaisa.py (in-memory poll) | `mkdir -p src/trading_platform/data && mv archive/legacy_2026-10-28/src/trading_platform/data/read_only_collector.py src/trading_platform/data/read_only_collector.py` |
| `src/trading_platform/data/offline_replay.py` | 12 KB | Offline replay of the DuckDB collector (writes collector.duckdb); only used by the 4E3 validation script and its test. | live/providers/replay.py (REPLAY_SIMULATED) | `mkdir -p src/trading_platform/data && mv archive/legacy_2026-10-28/src/trading_platform/data/offline_replay.py src/trading_platform/data/offline_replay.py` |

`src/trading_platform/execution/` keeps `options_risk_manager.py` (still imported by `backtesting/engine.py` and `backtesting/option_execution.py`).

### B2. Scripts

| path | size | reason | replacement | restore |
|---|---|---|---|---|
| `scripts/run_phase4d_read_only_capture.py` | 20 KB | Phase-4D DuckDB capture; superseded by 4E collector, then by live/. | live app | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/run_phase4d_read_only_capture.py scripts/run_phase4d_read_only_capture.py` |
| `scripts/run_phase4e_collector.py` | 4 KB | CLI for the retired DuckDB ReadOnlyCollector. | live app | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/run_phase4e_collector.py scripts/run_phase4e_collector.py` |
| `scripts/audit_phase4e_collector.py` | 16 KB | Audit of the retired collector's state/lock files (untracked, preserved). | - | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/audit_phase4e_collector.py scripts/audit_phase4e_collector.py` |
| `scripts/run_live_paper_session.py` | 68 KB | Not a live app: hard-coded SESSION_DATE 2026-10-26, imports duckdb + paper.*, builds the synthetic demo trades (untracked, preserved). | scripts/run_paper_trading.py (live/app.py) | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/run_live_paper_session.py scripts/run_live_paper_session.py` |
| `scripts/generate_phase4e_reports.py` | 12 KB | One-shot report generator 4E (imports paper.shadow); superseded by 4E1..4E3. | - | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/generate_phase4e_reports.py scripts/generate_phase4e_reports.py` |
| `scripts/generate_phase4e1_reports.py` | 24 KB | One-shot report generator 4E1 (imports paper.shadow); superseded. | - | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/generate_phase4e1_reports.py scripts/generate_phase4e1_reports.py` |
| `scripts/generate_phase4e2_reports.py` | 20 KB | One-shot report generator 4E2 (imports paper.shadow); superseded. | - | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/generate_phase4e2_reports.py scripts/generate_phase4e2_reports.py` |
| `scripts/run_phase4e3_offline_validation.py` | 20 KB | 4E3 offline validation (imports offline_replay + paper.shadow). | - | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/run_phase4e3_offline_validation.py scripts/run_phase4e3_offline_validation.py` |
| `scripts/build_phase4e3_clarification_pdf.py` | 16 KB | 4E3 PDF builder; no importers. | - | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/build_phase4e3_clarification_pdf.py scripts/build_phase4e3_clarification_pdf.py` |
| `scripts/restore_phase4d_success_report.py` | 12 KB | One-shot restore of 4D reports; no importers. | - | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/restore_phase4d_success_report.py scripts/restore_phase4d_success_report.py` |
| `scripts/audit_option_archive.py` | 8 KB | Classifier for the quarantined legacy option archive; one-shot, no importers. | - | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/audit_option_archive.py scripts/audit_option_archive.py` |
| `scripts/generate_phase3c_reports.py` | 116 KB | 117 KB one-shot PDF generator with hard-coded analytics ('do not rerun'); outputs live in reports/final/. | - | `mkdir -p scripts && mv archive/legacy_2026-10-28/scripts/generate_phase3c_reports.py scripts/generate_phase3c_reports.py` |
| `scripts/verify_installation.py` (COPY, original) | 8 KB | Phase-1 smoke test importing brokers.paper_broker + DuckDB storage. Original copied to the archive; rewritten in place as a network-free check of the live app. | scripts/verify_installation.py (new) | `cp archive/legacy_2026-10-28/scripts/verify_installation.py scripts/verify_installation.py` |

### B3. Configs

| path | size | reason | replacement | restore |
|---|---|---|---|---|
| `config/` | 4 KB | Duplicate config dir (config/paper_trading.yaml is byte-identical to configs/paper_trading.yaml); docs/live_app_architecture.md retires it. Readers (paper.cli, paper.engine, run_phase4d) are archived. | configs/live_paper.yaml | `mv archive/legacy_2026-10-28/config config` |
| `configs/paper_trading.yaml` | 4 KB | Legacy paper-engine config (mode LIVE_READ_ONLY); its only readers are the archived paper.* modules. | configs/live_paper.yaml | `mkdir -p configs && mv archive/legacy_2026-10-28/configs/paper_trading.yaml configs/paper_trading.yaml` |
| `configs/backtest.yaml` | 4 KB | Read by nothing (grep src/scripts/tests); stale cost constants (STT 0.0625%). | backtesting/constants.py + live/costs.py | `mkdir -p configs && mv archive/legacy_2026-10-28/configs/backtest.yaml configs/backtest.yaml` |

### B4. Root-level generated reports that are byte-identical (`cmp`) to `reports/paper_trading_live_session/<same name>`

The canonical copy stays in `reports/paper_trading_live_session/`; the root duplicate is archived.

| path | size | reason | replacement | restore |
|---|---|---|---|---|
| `CORRECTED_STRATEGY_AUTHORIZATION_MATRIX.md` | 16 KB | byte-identical duplicate | `reports/paper_trading_live_session/CORRECTED_STRATEGY_AUTHORIZATION_MATRIX.md` | `mv archive/legacy_2026-10-28/CORRECTED_STRATEGY_AUTHORIZATION_MATRIX.md CORRECTED_STRATEGY_AUTHORIZATION_MATRIX.md` |
| `DAILY_OPERATIONAL_REPORT.md` | 16 KB | byte-identical duplicate | `reports/paper_trading_live_session/DAILY_OPERATIONAL_REPORT.md` | `mv archive/legacy_2026-10-28/DAILY_OPERATIONAL_REPORT.md DAILY_OPERATIONAL_REPORT.md` |
| `FORWARD_PAPER_TRADING_READINESS.md` | 12 KB | byte-identical duplicate | `reports/paper_trading_live_session/FORWARD_PAPER_TRADING_READINESS.md` | `mv archive/legacy_2026-10-28/FORWARD_PAPER_TRADING_READINESS.md FORWARD_PAPER_TRADING_READINESS.md` |
| `HISTORICAL_CASE_VALIDATION.md` | 16 KB | byte-identical duplicate | `reports/paper_trading_live_session/HISTORICAL_CASE_VALIDATION.md` | `mv archive/legacy_2026-10-28/HISTORICAL_CASE_VALIDATION.md HISTORICAL_CASE_VALIDATION.md` |
| `IN_WINDOW_SIGNAL_DISPOSITION_RECONCILIATION.csv` | 84 KB | byte-identical duplicate | `reports/paper_trading_live_session/IN_WINDOW_SIGNAL_DISPOSITION_RECONCILIATION.csv` | `mv archive/legacy_2026-10-28/IN_WINDOW_SIGNAL_DISPOSITION_RECONCILIATION.csv IN_WINDOW_SIGNAL_DISPOSITION_RECONCILIATION.csv` |
| `LIVE_SESSION_OPERATIONAL_CHECKLIST.md` | 12 KB | byte-identical duplicate | `reports/paper_trading_live_session/LIVE_SESSION_OPERATIONAL_CHECKLIST.md` | `mv archive/legacy_2026-10-28/LIVE_SESSION_OPERATIONAL_CHECKLIST.md LIVE_SESSION_OPERATIONAL_CHECKLIST.md` |
| `MANUAL_MATCH_QUALITY_AUDIT.csv` | 32 KB | byte-identical duplicate | `reports/paper_trading_live_session/MANUAL_MATCH_QUALITY_AUDIT.csv` | `mv archive/legacy_2026-10-28/MANUAL_MATCH_QUALITY_AUDIT.csv MANUAL_MATCH_QUALITY_AUDIT.csv` |
| `MANUAL_VS_AUTOMATED_COMPARISON.csv` | 44 KB | byte-identical duplicate | `reports/paper_trading_live_session/MANUAL_VS_AUTOMATED_COMPARISON.csv` | `mv archive/legacy_2026-10-28/MANUAL_VS_AUTOMATED_COMPARISON.csv MANUAL_VS_AUTOMATED_COMPARISON.csv` |
| `PAPER_EXECUTION_BLOCKERS.md` | 16 KB | byte-identical duplicate | `reports/paper_trading_live_session/PAPER_EXECUTION_BLOCKERS.md` | `mv archive/legacy_2026-10-28/PAPER_EXECUTION_BLOCKERS.md PAPER_EXECUTION_BLOCKERS.md` |
| `REAL_HISTORICAL_SIGNAL_CASES.csv` | 4 KB | byte-identical duplicate | `reports/paper_trading_live_session/REAL_HISTORICAL_SIGNAL_CASES.csv` | `mv archive/legacy_2026-10-28/REAL_HISTORICAL_SIGNAL_CASES.csv REAL_HISTORICAL_SIGNAL_CASES.csv` |
| `REPRODUCIBLE_HISTORICAL_SIGNAL_CASES.csv` | 8 KB | byte-identical duplicate | `reports/paper_trading_live_session/REPRODUCIBLE_HISTORICAL_SIGNAL_CASES.csv` | `mv archive/legacy_2026-10-28/REPRODUCIBLE_HISTORICAL_SIGNAL_CASES.csv REPRODUCIBLE_HISTORICAL_SIGNAL_CASES.csv` |
| `STRATEGY_AUTHORIZATION_MATRIX.md` | 12 KB | byte-identical duplicate | `reports/paper_trading_live_session/STRATEGY_AUTHORIZATION_MATRIX.md` | `mv archive/legacy_2026-10-28/STRATEGY_AUTHORIZATION_MATRIX.md STRATEGY_AUTHORIZATION_MATRIX.md` |

### B5. Tests tied to retired code (moved so `testpaths=['tests']` no longer collects them)

| path | size | reason / where the coverage went | restore |
|---|---|---|---|
| `tests/unit/test_phase4_paper_broker_safety.py` | 20 KB | Legacy engine + brokers.paper_broker + paper.recorder. Selector/feed/proxy halves ported to test_live_contract_selector.py / test_live_market_feed.py; behaviours re-covered by tests/live. | `mkdir -p tests/unit && mv archive/legacy_2026-10-28/tests/unit/test_phase4_paper_broker_safety.py tests/unit/test_phase4_paper_broker_safety.py` |
| `tests/unit/test_broker_interface.py` | 4 KB | Tests the retired brokers.paper_broker.PaperBroker; read-only proxy is covered by test_broker_readonly_allowlist.py. | `mkdir -p tests/unit && mv archive/legacy_2026-10-28/tests/unit/test_broker_interface.py tests/unit/test_broker_interface.py` |
| `tests/unit/test_phase4_paper_platform.py` | 12 KB | paper.engine/broker/portfolio/risk. Selector + broker-read-only halves ported; rest covered by tests/live. | `mkdir -p tests/unit && mv archive/legacy_2026-10-28/tests/unit/test_phase4_paper_platform.py tests/unit/test_phase4_paper_platform.py` |
| `tests/unit/test_phase4b_heartbeat_and_status.py` | 24 KB | paper.engine heartbeat/status (DuckDB). Replaced by tests/live/test_engine_snapshot.py + test_persist_snapshot.py. | `mkdir -p tests/unit && mv archive/legacy_2026-10-28/tests/unit/test_phase4b_heartbeat_and_status.py tests/unit/test_phase4b_heartbeat_and_status.py` |
| `tests/unit/test_phase4b_trade_lifecycle.py` | 24 KB | paper.engine lifecycle (DuckDB). Replaced by tests/live/test_engine_lifecycle.py / _exits.py / _entries.py. | `mkdir -p tests/unit && mv archive/legacy_2026-10-28/tests/unit/test_phase4b_trade_lifecycle.py tests/unit/test_phase4b_trade_lifecycle.py` |
| `tests/unit/test_phase4c_remediation.py` | 8 KB | paper.accounting/broker/portfolio/recorder. Feed + calendar halves ported; ledger parity covered by tests/live/test_core_ledger.py. | `mkdir -p tests/unit && mv archive/legacy_2026-10-28/tests/unit/test_phase4c_remediation.py tests/unit/test_phase4c_remediation.py` |
| `tests/unit/test_phase4d_read_only.py` | 4 KB | paper.recorder (DuckDB). Timestamp tests ported to test_live_market_feed.py. | `mkdir -p tests/unit && mv archive/legacy_2026-10-28/tests/unit/test_phase4d_read_only.py tests/unit/test_phase4d_read_only.py` |
| `tests/unit/test_phase4e_read_only.py` | 16 KB | ReadOnlyCollector + recorder + shadow. Aggregator/candle-quality/calendar tests ported. | `mkdir -p tests/unit && mv archive/legacy_2026-10-28/tests/unit/test_phase4e_read_only.py tests/unit/test_phase4e_read_only.py` |
| `tests/unit/test_phase4e3_offline.py` | 4 KB | data.offline_replay (retired). | `mkdir -p tests/unit && mv archive/legacy_2026-10-28/tests/unit/test_phase4e3_offline.py tests/unit/test_phase4e3_offline.py` |
| `tests/unit/test_phase4e2_context.py` (COPY, original) | 4 KB | Point-in-time context tests stay (kept code); the 2 tests that import paper.shadow are removed. Full original copied to the archive. | `cp archive/legacy_2026-10-28/tests/unit/test_phase4e2_context.py tests/unit/test_phase4e2_context.py` |

Also under the archive: `archive/README.md` (what it is + restore) — new file.

### B6. Moved into the reports tree (not archived — still live evidence)

Root-only artifacts (no copy existed under `reports/`). Moved with unchanged names; nothing is lost. `generate_final_paper_decision_artifacts.py` still writes some of these at the repo root when re-run (request logged for the integrator).

| path | size | reason | restore |
|---|---|---|---|
| `BACKTEST_VERSION_RECONCILIATION.md` -> `reports/paper_trading_live_session/BACKTEST_VERSION_RECONCILIATION.md` | - | root clutter; decision evidence kept | `mv reports/paper_trading_live_session/BACKTEST_VERSION_RECONCILIATION.md BACKTEST_VERSION_RECONCILIATION.md` |
| `PAPER_TRADING_GO_NO_GO.md` -> `reports/paper_trading_live_session/PAPER_TRADING_GO_NO_GO.md` | - | root clutter; decision evidence kept | `mv reports/paper_trading_live_session/PAPER_TRADING_GO_NO_GO.md PAPER_TRADING_GO_NO_GO.md` |
| `PORTFOLIO_PERFORMANCE.md` -> `reports/paper_trading_live_session/PORTFOLIO_PERFORMANCE.md` | - | root clutter; decision evidence kept | `mv reports/paper_trading_live_session/PORTFOLIO_PERFORMANCE.md PORTFOLIO_PERFORMANCE.md` |
| `AMBIGUOUS_RULE_EXPOSURE.csv` -> `reports/paper_trading_live_session/AMBIGUOUS_RULE_EXPOSURE.csv` | - | root clutter; decision evidence kept | `mv reports/paper_trading_live_session/AMBIGUOUS_RULE_EXPOSURE.csv AMBIGUOUS_RULE_EXPOSURE.csv` |
| `STRATEGY_AUTHOR_FINAL_SIGNOFF.pdf` -> `reports/paper_trading_live_session/STRATEGY_AUTHOR_FINAL_SIGNOFF.pdf` | - | root clutter; decision evidence kept | `mv reports/paper_trading_live_session/STRATEGY_AUTHOR_FINAL_SIGNOFF.pdf STRATEGY_AUTHOR_FINAL_SIGNOFF.pdf` |

## A. KEPT

| path | why kept |
|---|---|
| `COMBINED_PORTFOLIO_TRADES.csv` (repo root) | src/trading_platform/dashboard/data.py:485 (legacy-run option) and scripts/generate_final_paper_decision_artifacts.py:136,213 read it at the repo root. Move into reports/paper_trading_live_session/ only after both are repointed (request logged). |
| `README.md` | REWRITTEN (short overview; points at `README_PAPER_TRADING.md` and the architecture doc) |
| `pyproject.toml` | additive edit: `[tool.ruff] extend-exclude = ["archive"]`, pytest `norecursedirs`; dependency tidy limited to adding the missing `reportlab` (imported by `generate_final_paper_decision_artifacts.py`, installed 5.0.1); nothing removed because duckdb/pyarrow/optuna/scikit-learn/pypdf/pymupdf are still imported by kept historical/research code |
| `.gitignore` | unchanged by this package (`runtime/` is added by another agent; `archive/` is deliberately tracked) |
| `.env.example`, `LICENSE`, `.python-version` | project metadata |
| `src/trading_platform/live/**`, `dashboard/**` | the new app |
| `src/trading_platform/strategies/**` | strategy classes (PDS/ORB/IRF/PDR; RF kept but disabled) |
| `src/trading_platform/backtesting/**` | backtest consolidation (imports `execution/options_risk_manager.py`) |
| `src/trading_platform/execution/options_risk_manager.py` | still used by backtesting |
| `src/trading_platform/brokers/{base,fivepaisa}.py` | read-only 5paisa broker + proxy |
| `src/trading_platform/data/live_market_feed.py` | Level2Quote/SpotTick/parse helpers used by the 5paisa provider |
| `src/trading_platform/data/{reference_levels,point_in_time_context,candle_quality,calendar,instruments,option_chain,validation,session_normalizer,...}.py` | shared data helpers |
| `src/trading_platform/data/storage.py`, `historical_options.py` | DuckDB/Parquet for HISTORICAL research only (not the live path); keep with their tests |
| `src/trading_platform/options/**` | ATM/moneyness/expiry helpers used by live/ and backtesting; `live_contract_selector.py` is now only used by its own ported tests (candidate for later retirement) |
| `src/trading_platform/core/**` (incl. `redaction.py`) | shared enums/models/config/redaction |
| `src/trading_platform/{analytics,optimization}/**`, `cli.py` | research tooling, untouched |
| `configs/{app,market,data,risk,options,optimization,live_trading}.yaml` | app/market/data/risk are read by `ConfigManager`; options/optimization/live_trading are not read by code but are documented (`docs/broker_capabilities.md`) — marked for a later audit, not moved |
| `configs/strategies/*.yaml` | LEGACY reference rules. Superseded by `configs/strategy_provisional/` + strategy code, but still read by `ConfigManager.get_strategy_config` and `tests/unit/test_config.py`, so kept |
| `configs/live_paper.yaml`, `configs/strategy_provisional/**` | live app config (single source) |
| `docs/**` | kept in place. `docs/phase4_runbook.md`, `phase4b_runbook.md`, `phase4_paper_trading_architecture.md`, `phase4_completion_report.md`, `phase4_safety_audit.md` describe the retired DuckDB design: SUPERSEDED by `live_app_architecture.md` (not edited here) |
| `data/**`, `reports/**` | untouched (incl. `data/processed/paper_trading.duckdb`, `market_data.duckdb`, `data/cache/*`: gitignored, archive manually if/when the old recorder output is no longer wanted) |
| `tests/live/**`, `tests/backtesting/**`, `tests/strategies/**`, `tests/options/**`, `tests/integration/**` | tests of kept code |
| `tests/unit/*` except section B5 | tests of kept code (`test_research_integrity.py` imports the kept `scripts/run_phase3c_research.py`) |

Kept scripts: `compare_backtest_versions.py`, `download_historical_nifty.py`, `generate_final_paper_decision_artifacts.py`, `generate_phase2d_audit_artifacts.py`, `import_historical_data.py`, `paper_trading_dashboard.py`, `run_backtest.py`, `run_paper_trading.py`, `run_phase2d_reconciliation.py`, `run_phase3a_preparation.py`, `run_phase3c_research.py`, `run_true_signal_reproduction.py`, `setup_environment.py`, `streamlit_validation_dashboard.py`, `verify_installation.py`.

Note: `scripts/run_paper_trading.py` (the manual-start entry point) now launches `trading_platform.live.app` (rewritten by the app owner); it no longer routes to the retired `trading_platform.paper.cli`.

## C. DELETED (pure regenerable caches only, explicit paths, each inspected first)

| path | what | restore |
|---|---|---|
| `.pytest_cache` | pytest cache (regenerable) | regenerated automatically (nothing to restore) |
| `.ruff_cache` | ruff cache (regenerable) | regenerated automatically (nothing to restore) |
| `.DS_Store` | macOS Finder metadata (gitignored) | regenerated automatically (nothing to restore) |
| `tmp` | empty directory (0 entries) | regenerated automatically (nothing to restore) |
| `scripts/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/analytics/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/backtesting/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/brokers/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/core/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/dashboard/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/data/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/data/providers/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/execution/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/live/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/live/providers/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/optimization/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/options/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/paper/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `src/trading_platform/strategies/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `tests/backtesting/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `tests/integration/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `tests/live/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `tests/options/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `tests/strategies/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |
| `tests/unit/__pycache__` | Python bytecode cache (`*.pyc` only) | regenerated on next import |

Not deleted although listed as candidates: `data/.DS_Store` (inside `data/`, which this cleanup does not descend into; it is gitignored), empty `artifacts/experiments/` and `notebooks/research/` (placeholders the .gitignore refers to).

## D. Safety-assertion coverage check for retired tests

Every behaviour the retired suites asserted was mapped to the new code before the tests were moved.

| retired assertion | new coverage |
|---|---|
| Broker proxy blocks every order/modify/cancel endpoint | `tests/unit/test_broker_readonly_allowlist.py`; `place_order` block ported to `tests/unit/test_live_market_feed.py` |
| Stale quote rejected | `tests/live/test_core_risk.py::test_quote_freshness_judged_at_moment_of_use`, `test_engine_entries.py::test_stale_quote_keeps_entry_pending_then_timeout_with_exact_detail`, `test_quote_older_than_signal_is_never_used`; selector-level ported to `test_live_contract_selector.py` |
| Future-dated / missing provider timestamp rejected | `tests/live/test_core_risk.py::test_quote_from_future_is_rejected`, `test_prov_poll.py::test_spot_identity_and_timestamp_validation_fails_closed`; feed-level ported to `test_live_market_feed.py` |
| Invalid / crossed bid-ask, wide spread | `test_core_risk.py::test_zero_crossed_invalid_book_rejected`, `test_wide_spread_points_and_pct`; `test_engine_entries.py::test_crossed_quote_not_filled`, `test_wide_spread_rejected_then_times_out` |
| Capital insufficiency | `test_core_ledger.py::test_insufficient_cash_rejected_atomically`, `test_core_risk.py::test_insufficient_cash`, `test_engine_entries.py::test_premium_budget_and_insufficient_funds_reject_with_reason` |
| Position concurrency (max 1) | `test_engine_lifecycle.py::test_no_second_entry_while_position_open_and_signal_rejected`, `test_core_ledger.py::test_only_one_position_and_no_trade_id_reuse` |
| Duplicate signal prevention | `test_engine_entries.py::test_duplicate_signal_id_from_checkpoint_is_rejected`, `test_duplicate_and_older_candles_are_not_reevaluated` |
| Missing candles handled | `test_engine_entries.py::test_gap_blocks_strategies_until_backfilled`, `test_on_candle_missing_blocks_with_reason_no_substitution`; aggregator case ported |
| Market disconnection | `test_prov_poll.py::test_disconnected_broker_blocks_all_sdk_calls`, `test_repeated_empty_spot_responses_flip_status_to_disconnected`; `test_engine_exits.py::test_dead_feed_at_squareoff_still_starts_exit_pending_via_clock` |
| Restart recovery without duplicating the position | `test_engine_recovery.py::test_restart_with_open_position_resumes_without_duplicate_entry` and siblings in the same file; `test_engine_integration.py::test_restart_mid_day_with_restore_candles_matches_a_continuous_run`; `test_persist_checkpoint.py` |
| 15:15 IST square-off | `test_engine_lifecycle.py::test_eod_square_off_by_clock_with_cached_bid`, `test_engine_exits.py::test_stale_spot_makes_no_exit_decision_but_squareoff_proceeds`, `test_core_risk.py::test_square_off_due_blocks_entry` |
| Entry cutoff | `test_core_risk.py` (cutoff x5), `test_engine_entries.py` (cutoff x3) |
| Daily loss limit | `test_core_risk.py::test_daily_loss_*`, `test_engine_lifecycle.py::test_daily_loss_halts_and_closes_with_risk`, `test_engine_exits.py::test_realized_loss_over_limit_halts_and_rejects_later_signals` |
| Single-instance lock | `tests/live/test_persist_lock.py` |
| Paise-exact accounting | `test_core_ledger.py::test_paise_exact_no_float_drift_over_many_trades`, `test_equity_identity_cash_plus_invested_plus_unrealized` |
| Live account is not a paper funding source | ported (`get_margins` namespace) to `test_live_market_feed.py`; paper state is now a separate checkpoint (`test_engine_recovery.py::test_run_mode_mismatch_is_recovery_required`) |
| Candle quality (alignment/gap), special sessions/holidays | ported to `test_live_market_feed.py` / `test_calendar_special_sessions.py` |
| Unauthorized strategies never fill (shadow) | `test_engine_entries.py::test_shadow_only_never_fills_and_is_first_class`, `tests/live/test_core_authorization.py` |

**Gaps (not silently dropped):**

- Manual **kill switch**: the old engine had a persisted `kill_switch_active` flag. The new engine has the daily-loss RISK halt (persisted; `test_engine_recovery.py::test_halt_flag_survives_restart`) and BLOCKED/RECOVERY_REQUIRED states, but no operator-toggled kill switch. Nothing to port; flagged to the integrator.
- `paper.shadow.classify_rejection_reason` taxonomy (stable rejection categories, `test_all_shadow_rejection_categories_are_stable`) is not carried over; the new engine records SHADOW_ONLY signals with its own reasons. Archived with the old test.
- DuckDB persistence/dedupe/archive-provenance tests (`MarketDataRecorder`, `ReadOnlyCollector`) are intentionally dropped: that persistence no longer exists by design.
- Legacy safety-tests #9 (`on_candle_close` twice) and #11 (`fetch_spot_tick` offline) used weak disjunctive assertions; #11 was ported as-is, #9 is superseded by the engine duplicate-candle tests.

## E. Summary counts

| bucket | items |
|---|---|
| ARCHIVED (moved or copied under `archive/legacy_2026-10-28/`) | 43 |
| MOVED into `reports/paper_trading_live_session/` | 5 |
| DELETED (caches / empty tmp, explicit paths) | 26 |
| NEW tests ported from retired suites | 4 |
| KEPT root-level report left in place | 1 |

