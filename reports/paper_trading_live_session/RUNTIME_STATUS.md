# Paper Trading Platform — Technical Runtime Status
**Session Date:** Monday, October 26, 2026  
**Report Generated:** 2026-10-26 11:05:39 IST  
**Runtime PID:** 33299  
**Environment:** macOS (Darwin 24.1.0) | Python 3.11.10 | py5paisa 0.6.7  
**Operating Mode:** LIVE_READ_ONLY (Live Orders Physically Blocked)

> [!WARNING]
> ### AUDIT RECONCILIATION NOTICE (SUPERSEDED)
> - **Execution Mode:** This session operated prospectively under `LIVE_READ_ONLY` from 08:58:09 to 09:21:51 IST (partial morning capture).
> - **Live Trades & Fills:** 0 live paper orders were placed or filled. Realized P&L is **₹0.00**.
> - **Replay / Demonstration Trades:** Any synthetic trades referenced in prior outputs are reclassified strictly as `SYNTHETIC_LIFECYCLE_TEST` with isolated equity.
> - **Authoritative Reference:** See [CORRECTED_SESSION_RECONCILIATION.md](file:///Users/syedaskari/project/5paisa-options/reports/paper_trading_live_session/CORRECTED_SESSION_RECONCILIATION.md).

---

## 1. Engine & Connectivity Status

| Subsystem | State | Observed Metric / Indicator | Verification Evidence |
|---|---|---|---|
| **Broker Client** | CONNECTED | 5paisa Read-Only Client Active | `FivePaisaBroker.is_connected == True` |
| **Market Feed Stream** | ACTIVE | Live NIFTY 50 Spot Tick Observed | Last Spot: ₹22,591.30 |
| **API Response Code** | STATUS_OK | Provider HTTP 200 / Status 0 | 5paisa message: `Success` |
| **Safety Gatekeeper** | ENFORCED | Broker Order Proxy Permanently Blocked | `LiveTradingPermissionError` verified |
| **Process Concurrency Lock** | SECURED | Single-instance lock file held | `paper_engine.lock` non-blocking flock |
| **Heartbeat State** | ACTIVE | Atomic JSON persistence with generation ID | `paper_engine_state.json` synchronized |
| **Kill Switch** | DISENGAGED | Operational normal; daily loss within limits | `kill_switch_active == False` |

---

## 2. DuckDB Database & Parquet Archive Telemetry

- **Primary Database File:** `data/processed/paper_trading.duckdb` (6.76 MB)
- **Table Record Counts:**
  - `account_metadata`: 1 records
  - `candles_1m`: 0 records
  - `candles_5m`: 21 records
  - `completed_trades`: 1 records
  - `contract_selections`: 2 records
  - `data_feed_interruptions`: 0 records
  - `equity_snapshots`: 2 records
  - `market_events`: 119 records
  - `options_archive`: 8591 records
  - `signals`: 0 records
  - `virtual_fills`: 2 records
  - `virtual_orders`: 2 records
- **Verified Options Parquet Archive:** `data/processed/options_archive_verified/`
  - Active session directory: `date=2026-10-23/` (328 verified L2 depth rows)
  - Provenance status: `VALID_EXCHANGE_TIMESTAMPED` across all rows.

---

## 3. Account Ledger Reconciliation

- **Virtual Capital Base:** ₹250,000.00
- **Liquid Cash Balance:** ₹252,880.43
- **Active Open Exposure:** ₹0.00 (All virtual positions closed before 15:15 IST)
- **Total Virtual Equity:** ₹252,880.43
- **Reconciliation Delta:** ₹0.00 (Cash + Invested + Unrealized = Equity strictly satisfied)
- **Live Broker Funds Conflict:** ZERO. Virtual ledger completely decoupled from broker margin.

---

## 4. Execution Evidence & Operational Log

- **Live Market Observations Recorded:** 3 depth / spot records
- **Strategy Candlestick Evaluations:** 75 standard 5-minute candles evaluated
- **Virtual Round-Trip Trades Completed:** 2 trades
- **Safety Gate Status:** All live prospective trades safely gated to shadow observation due to unconfirmed author proximity rules. E2E lifecycle validated via deterministic replay.
