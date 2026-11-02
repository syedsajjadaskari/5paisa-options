# NIFTY Options Virtual Paper Trading (5paisa, read-only)

A manually launched, fully automated **virtual** paper-trading application for NIFTY options. It reads
live market data from 5paisa through a **read-only** broker boundary, runs the price-action strategies
on completed 5-minute candles, and simulates fills, exits, costs and P&L entirely in memory and in a
tiny runtime journal. **No real order is ever placed**: the 5paisa client is wrapped in an allowlist
proxy that makes every order/modify/cancel endpoint unreachable.

> Start here: **[README_PAPER_TRADING.md](README_PAPER_TRADING.md)** (how to run, configure and read the
> app) and **[docs/live_app_architecture.md](docs/live_app_architecture.md)** (design contract).
> Repository cleanup history: **[docs/CLEANUP_MANIFEST.md](docs/CLEANUP_MANIFEST.md)**.

## Strategies and authorization

| strategy | rule modelled | direction |
|---|---|---|
| PDS | Previous-Day Support, Method 1 | PE |
| ORB | Opening Range Breakout, Method 1 | CE / PE |
| IRF | Initial Range Failure, Method 2 | per signal |
| PDR | Previous-Day Resistance retest | CE |
| RF | **disabled** (never evaluated) | - |

All four active strategies are **`SHADOW_ONLY` by default** because some rulebook rules are still
unresolved by the strategy author (see `README_PAPER_TRADING.md` and `configs/live_paper.yaml`; the files
under `reports/paper_trading_live_session/` are evidence from earlier engine versions, some carry
SUPERSEDED / STALE / SYNTHETIC notices, and none of them is an instruction).
In that tier signals are evaluated and journaled but no virtual fill is created. Simulated fills require an
explicit research-paper opt-in (config acknowledgement plus a CLI flag) and are labelled
`RESEARCH_PAPER_PROVISIONAL` on every record. A clearly labelled `REPLAY_SIMULATED` mode exists for demos;
it uses a separate runtime root and never touches the live ledger.

## Layout

```
configs/            live_paper.yaml (live app), strategy_provisional/ (provisional rule values),
                    app/market/data/risk.yaml + strategies/ (legacy ConfigManager inputs)
src/trading_platform/
  live/             the app: engine, risk, ledger, authorization, providers (5paisa, replay),
                    journal/checkpoint/lock/snapshot, terminal UI
  dashboard/        Streamlit views (read-only; launched via scripts/paper_trading_dashboard.py)
  strategies/       PDS, ORB, IRF, PDR (+ RF, disabled) and provisional rule loader
  backtesting/      historical backtest + portfolio runner (research only)
  brokers/          read-only 5paisa broker and SDK allowlist proxy
  data/             calendar, reference levels, candle quality, live feed helpers, historical pipeline
  options/          ATM/expiry/moneyness helpers, historical contract selection
  core/ analytics/ optimization/ execution/ (backtest risk manager only)
scripts/            run_paper_trading.py (manual start), paper_trading_dashboard.py, run_backtest.py,
                    verify_installation.py, historical download/import, one-shot research/audit scripts
tests/              live/ (the app), unit/ options/ strategies/ integration/ backtesting/
docs/               architecture, specifications, rulebooks (docs/strategy_rulebooks/ is read-only evidence)
data/ reports/      historical datasets and generated evidence (gitignored where large)
archive/            retired legacy code, tests, configs and duplicate reports (see the manifest)
runtime/            created at run time (journal, checkpoint, live_state.json); gitignored
```

There is **no market-data database** in the live path. DuckDB/Parquet remain only for historical research
(`data/storage.py`, `data/historical_options.py`).

## Setup and checks

```bash
uv venv .venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env                 # 5paisa credentials (never commit .env)

python scripts/verify_installation.py   # offline check: imports, config, SHADOW_ONLY defaults, read-only boundary
pytest                                  # archive/ is excluded from collection
ruff check src tests scripts            # archive/ is excluded from linting
```

## Safety summary

1. Broker access is read-only; transactional SDK members raise `ReadOnlyBoundaryError` (a `LiveTradingPermissionError`).
2. Strategies are deny-by-default (`SHADOW_ONLY`); RF is permanently disabled.
3. Fills use a fresh post-signal ask plus slippage; exits use the bid. Missing quotes keep an entry/exit
   pending, never a fabricated fill. Positions are squared off by 15:15 IST; entries stop at 14:30 IST.
4. Backtests fill on the next bar open and assume stop-loss first on same-bar conflicts.
5. Credentials and tokens are redacted from logs; `.env` and `data/cache/` are gitignored.
6. There is **no kill switch**: stop the app with `Ctrl+C` / `kill -TERM`. The `.env.example` risk,
   `OPERATIONAL_MODE` and `EMERGENCY_KILL_SWITCH` lines and `configs/risk.yaml` / `live_trading.yaml`
   are legacy and not read by the paper app; its real limits are in `configs/live_paper.yaml`.
7. `scripts/run_paper_trading.py --doctor` logs in to 5paisa (read-only), downloads the public Scrip
   Master into `data/cache/` and writes a token cache there; see `README_PAPER_TRADING.md`.
8. The dashboard binds to localhost only: `streamlit run scripts/paper_trading_dashboard.py
   --server.address 127.0.0.1` (use `PAPER_RUNTIME_DIR` for a custom `--runtime-dir`).

## Retired legacy code

The Phase-4 DuckDB paper engine (`paper/`, `execution/paper_trading_engine.py`, `brokers/paper_broker.py`,
the DuckDB collector) and its one-shot report scripts were moved to `archive/legacy_2026-10-28/`
with restore commands in `docs/CLEANUP_MANIFEST.md`. Nothing was deleted except regenerable caches.
