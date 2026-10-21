# Multi-Strategy Algorithmic Trading Research Platform

A professional, modular algorithmic trading research, backtesting, and paper execution platform for Indian Financial Markets (NSE India) specializing in NIFTY 50 intraday strategies and the 5paisa broker.

---

## 1. Executive Summary

This platform provides an institutional-grade, research-to-live workflow for 5 canonical price-action strategies:
- **IRF** — Initial Range Failure (3 entry methods: Breakout & Failure, Extension & Reversal, Liquidity Sweep)
- **ORB** — Opening Range Breakout (3 entry methods: Major Level Breakout, Trendline & Inner Level, 200 DMA Gap)
- **PDR** — Previous Day Resistance (Retest Support, Long bias)
- **PDS** — Previous Day Support (Retest Resistance, 3 entry methods: Good Red Candle, HPR Pin Bar, Small Support Break)
- **RF** — Reverse Failure (Opposite of IRF, strictly uptrend and gap-up within yesterday's range)

---

## 2. Platform Architecture & Layering

```
trading-platform/
├── configs/             # Centralized YAML configuration files
├── docs/                # Architectural docs, specifications & ambiguities
│   ├── strategy_rulebooks/  # Primary source PDFs
│   └── strategy_specs/      # Individual markdown specifications
├── data/                # Raw, processed (Parquet), reference, and samples
├── src/trading_platform/
│   ├── core/            # Models, Enums, Events, Market Clock, Exceptions
│   ├── data/            # Providers, Validation, Storage (DuckDB+Parquet), Calendar
│   ├── strategies/      # BaseStrategy, StrategyRegistry, Price Action helpers
│   ├── brokers/         # BaseBroker, PaperBroker, FivePaisaBroker
│   └── cli.py           # Platform CLI entrypoint
├── scripts/             # Verification, environment setup, and data import
└── tests/               # Unit, integration, and strategy test suites
```

---

## 3. Environment Setup & Installation

### Prerequisites
- macOS (Apple Silicon arm64 supported) or Linux
- Python 3.11+
- `uv` package manager (recommended) or standard Python `venv`

### Step-by-Step Installation

```bash
# 1. Clone/Open the repository
cd /Users/syedaskari/project/5paisa-options

# 2. Create virtual environment with Python 3.11
uv venv .venv --python 3.11
source .venv/bin/activate

# 3. Install dependencies and project in editable mode
uv pip install -e ".[dev]"

# 4. Initialize environment variables
cp .env.example .env
```

---

## 4. Verification and Testing

Execute the following commands from the project root:

```bash
# Run the complete Phase 1 subsystem verification check
python scripts/verify_installation.py

# Run all automated unit and integration tests
pytest -v

# Run code style and linting checks
ruff check src tests scripts

# Check CLI commands
trading-platform status
trading-platform strategies
```

---

## 5. Sample Data Pipeline (CSV -> Parquet -> DuckDB)

Import the provided NIFTY 50 5-minute sample dataset:

```bash
python scripts/import_historical_data.py --file data/samples/nifty50_5min_sample.csv --symbol "NIFTY 50" --interval 5m
```

---

## 6. Execution Realism & Safety Safeguards

1. **Default Mode:** Strictly `RESEARCH` and `PAPER`.
2. **Anti-Lookahead Rules:** Signals are generated at bar close ($t$), and executions are simulated strictly on the **next bar open ($t+1$)**.
3. **Worst-Case Same-Bar Assumptions:** In the event both Stop-Loss and Target are touched within the same 5-minute bar, the simulator assumes the **Stop-Loss was hit first**.
4. **Live Trading Gatekeeper:** Any live order submission attempt without `OPERATIONAL_MODE=LIVE` and explicit approval raises a `LiveTradingPermissionError`.
