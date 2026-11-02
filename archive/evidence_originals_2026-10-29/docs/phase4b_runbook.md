# Phase 4B — Paper Trading Platform Operator Runbook

## Overview

This runbook provides tested, deterministic macOS terminal commands to operate, monitor, and troubleshoot the persistent NIFTY options paper trading platform.

> [!CAUTION]
> **SIMULATION GUARANTEE:**
> This platform runs in **strictly paper simulation mode**. Broker order endpoints are physically disconnected and hard-blocked by `LiveTradingPermissionError`. Real orders can never be transmitted.

---

## 1. Quick Command Cheatsheet

All commands run inside the repository root (`/Users/syedaskari/project/5paisa-options`) using the `.venv` Python environment:

| Action | Command | Purpose |
| :--- | :--- | :--- |
| **1. Doctor Check** | `./.venv/bin/python -m trading_platform.paper.cli doctor` | Verify credentials, token cache, and read-only feed |
| **2. Deterministic Replay** | `./.venv/bin/python -m trading_platform.paper.cli replay --bars 20` | Validate offline bar processing & portfolio state |
| **3. Live Observation** | `./.venv/bin/python -m trading_platform.paper.cli observe --cycles 10` | Stream genuine 5paisa quotes with 0 orders |
| **4. Start Paper Trading** | `./.venv/bin/python -m trading_platform.paper.cli start --confirm-paper-only --session` | Activate live paper execution until 15:30 IST |
| **5. Status Inspection** | `./.venv/bin/python -m trading_platform.paper.cli status` | Inspect virtual cash, open positions, quote age |
| **6. Launch Dashboard** | `./.venv/bin/streamlit run scripts/paper_trading_dashboard.py` | Open live web dashboard on `http://localhost:8501` |
| **7. Export Reports** | `./.venv/bin/python -m trading_platform.paper.cli report` | Generate daily CSV, Parquet, and HTML reports |
| **8. Emergency Stop** | `./.venv/bin/python -m trading_platform.paper.cli stop` | Engage kill switch and trigger safe position exit |

---

## 2. Step-by-Step Operator Procedures

### Step 1 — Pre-Market Setup (08:45–09:10 IST)
1. Open a new Terminal window on macOS:
   ```bash
   cd /Users/syedaskari/project/5paisa-options
   source .venv/bin/activate
   ```
2. Run the environment and connectivity doctor:
   ```bash
   ./.venv/bin/python -m trading_platform.paper.cli doctor
   ```
   **Expected Output:**
   - Python Environment: `OK (Python 3.11+)`
   - 5paisa Credentials: `ClientCode=OK, UserKey=OK, EncryptionKey=OK`
   - Cached Session Token: `FOUND`
   - Read-Only Market Feed: `[SUCCESS] Live NIFTY Spot: ... | Status: 0 (OK)`
   - Safety Gatekeeper: `VERIFIED (Live orders permanently blocked)`

3. Verify existing portfolio and persistent run ID:
   ```bash
   ./.venv/bin/python -m trading_platform.paper.cli status
   ```

### Step 2 — Market Open Observation (09:15–09:20 IST)
Before activating paper trading, run an observation cycle to confirm genuine market quotes:
```bash
./.venv/bin/python -m trading_platform.paper.cli observe --cycles 5
```
Verify that `Stale: False` appears during active market hours (quote age < 5.0 seconds).

### Step 3 — Supervised Paper Trading Session Launch (09:20 IST)
Launch the persistent paper trading engine in full-session mode:
```bash
./.venv/bin/python -m trading_platform.paper.cli start --confirm-paper-only --session
```
- **Operating Window:** Runs continuously from 09:20 until 15:30 IST.
- **Entry Gate:** No new entries permitted after 14:30 IST.
- **Auto Square-Off:** Automatically checks underlying targets/stop losses on every tick and attempts intraday square-off at 15:15 IST.
- **Safe Exit:** Press `Ctrl+C` at any time to initiate a safe shutdown with continuous state persistence.

### Step 4 — Real-Time Telemetry & Web Dashboard Monitoring
In a separate terminal tab or window:
```bash
cd /Users/syedaskari/project/5paisa-options
./.venv/bin/streamlit run scripts/paper_trading_dashboard.py
```
Open browser at `http://localhost:8501`.
- **Top Metrics:** Live NIFTY Spot, Available Cash, Total Equity, Realized/Unrealized P&L, Daily Drawdown.
- **Feed Status:** Data Feed Connected badge, Market Session Open badge, Spot Quote Age (s), Feed Interruptions counter.
- **Tab 1 (5m Chart):** Live NIFTY 50 candlestick chart with overlaid Entry, SL, and TP horizontal lines.
- **Tab 2 (Signals):** Strategy signal events, contract selections, bid/ask depth, and rejection reasons.
- **Tab 3 (Dual Ledger):** Completed trades table reconciling index points vs rupee P&L and transaction fees.
- **Tab 4 (Orders):** Virtual order and fill audit log.
- **Tab 5 (Equity Curve):** Interactive Plotly daily equity curve.
- **Tab 6 (Safety):** Institutional safety parameters and feed interruptions log.

### Step 5 — Post-Market Reporting (15:35 IST)
Generate the end-of-day audit reports:
```bash
./.venv/bin/python -m trading_platform.paper.cli report
```
Artifacts created in `reports/paper_trading/`:
- `paper_journal_<DATE>_<RUN_ID>.csv` & `.parquet`
- `strategy_performance_<DATE>_<RUN_ID>.csv`
- `dual_accounting_<DATE>_<RUN_ID>.csv`
- `paper_journal_<DATE>_<RUN_ID>.html` (styled HTML report)
- `session_validation.csv`
- `quote_quality.csv`
- `signal_audit.csv`

---

## 3. Troubleshooting & Operational Edge Cases

### Issue A: "REJECTED_MARKET_CLOSED"
- **Cause:** Attempting to execute simulated trades outside regular NSE hours (09:15–15:30 IST) or on exchange holidays.
- **Remedy:** Ensure local clock is synchronized to IST (`Asia/Kolkata`). To test outside market hours, use the deterministic replay mode:
  ```bash
  ./.venv/bin/python -m trading_platform.paper.cli replay --bars 20
  ```

### Issue B: "NO_EXECUTABLE_QUOTE" / Pending Exit Engaged
- **Cause:** Underlying spot hit Stop Loss or Target, or 15:15 IST square-off triggered, but option Level 2 Bid quote was unavailable or stale (> 5.0s old).
- **Behavior:** The engine **refuses to fabricate an exit price**. The position remains open, and `pending_exit_reason` is set.
- **Remedy:** The engine automatically retries on every subsequent polling cycle. As soon as a fresh quote is observed, the exit order executes immediately.

### Issue C: Feed Interruptions / Network Glitches
- **Cause:** Transient network disconnect or 5paisa rate limit.
- **Behavior:** The engine records an interruption in `data_feed_interruptions` in DuckDB, increments the interruption counter, sleeps for 1 second, and resumes polling without crashing.
- **Recovery:** No manual intervention required. If the token expired, run `doctor` to refresh the session token.

### Issue D: Emergency Kill Switch & Clean Shutdown
If an operator needs to instantly halt the platform and square off positions:
```bash
./.venv/bin/python -m trading_platform.paper.cli stop
```
This signals the running engine process via `SIGINT`, sets `kill_switch_active = True`, squares off open virtual positions, safely clears `data/cache/paper_engine.lock`, and persists state as `STOPPED`.

### Issue E: Duplicate Engine Process Lock Rejection
- **Cause:** Starting `python -m trading_platform.paper.cli start ...` while an engine process is already actively running.
- **Behavior:** The engine checks `data/cache/paper_engine.lock`, verifies process PID liveness and heartbeat age (&le; 15s), and rejects duplicate launches to prevent race conditions or conflicting simulated orders.
- **Remedy:** Inspect the active process using `status`:
  ```bash
  ./.venv/bin/python -m trading_platform.paper.cli status
  ```
  If an engine process crashed ungracefully leaving a stale lock, the lock manager automatically verifies process death or stale heartbeat (> 15s) and clears it safely on the next start.

### Issue F: Understanding Market Feed `Success` Terminal Logs
- **Cause:** The vendor `py5paisa` library prints `{time} | Success` on standard output whenever an HTTP 200 REST call succeeds.
- **Meaning:** This indicates **only** that the 5paisa REST endpoint replied successfully. It does **not** guarantee quote freshness or engine data consumption.
- **Verification:** Always check engine liveness and quote age via the persistent status command:
  ```bash
  ./.venv/bin/python -m trading_platform.paper.cli status
  ```
  The status command dynamically distinguishes:
  1. `RUNNING_CONNECTED`: Process alive, heartbeat fresh (< 15s), market open, spot quote fresh (< 10s).
  2. `RUNNING_DATA_STALE`: Process alive, but exchange quote timestamp is > 10s old.
  3. `RUNNING_DISCONNECTED`: Process alive, but market feed connection dropped or returned empty data.
  4. `STOPPED`: Engine process is not running during open market hours.
  5. `MARKET_CLOSED`: Outside official NSE hours (09:15–15:30 IST) or exchange holidays.
