> [!CAUTION]
> ### SUPERSEDED - DO NOT RELY ON (marked 2026-10-29)
> This document was written for an earlier engine / the retired Phase-4 stack and is kept only as history. **What is stale:**
> - It describes the retired `trading_platform.paper` / `read_only_collector` / DuckDB stack (archived in `archive/legacy_2026-10-28/`); `paper_execution_enabled`, `paper/engine.py` and `python -m trading_platform.paper.cli` no longer exist.
> - The ledger location (`data/cache/paper_broker_state.json`, DuckDB `completed_trades`) and `PaperRiskManager` (src/trading_platform/paper/risk.py) are archived; the live app's limits are in `configs/live_paper.yaml` (1 position, 2 lots, 20% premium, 10% daily loss).
> - Byte-exact original: `archive/evidence_originals_2026-10-29/reports/paper_trading_live_session/FORWARD_PAPER_TRADING_READINESS.md`. Index of all stale files: `reports/paper_trading_live_session/SUPERSEDED_INDEX.md`.
>
> **Current sources of truth:** `README_PAPER_TRADING.md` (how the app really works), `docs/live_app_architecture.md`, `docs/STRATEGY_EXECUTION_STATUS.md` (what each strategy does today), `docs/AUTHOR_CONFIRMATION_REQUEST.md` (what the strategy author must still confirm), and `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825` (current backtest: 184 portfolio trades, +813.60 index points, profit factor 1.22 in the 2025-09..2026-08 window; NIFTY index points only, in-sample, no option P&L).
> The text below is unchanged history.

---

# Forward Paper-Trading Technical Readiness & Operational Audit

**Document:** `FORWARD_PAPER_TRADING_READINESS.md`  
**Classification:** Pre-Execution Architecture Verification & Production Readiness Audit  
**Auditor:** Quantitative Trading & Risk Operations Engineer  
**Status:** Complete Forensic Assessment

---

## 1. Executive Summary

This report establishes the technical and operational readiness of the NIFTY options paper-trading platform for supervised forward execution. 

Following the forensic reconciliation of the October 26, 2026 session, the platform has eliminated all conflation between synthetic demonstration fixtures, historical research replays, and genuine live market observation. The core architecture has been verified against strict institutional standards:
- **Broker Order Protection:** Physical isolation verified via [`_ReadOnlyFivePaisaClientProxy`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/brokers/fivepaisa.py#L52-L93) (all 21 order routing methods raise `PermissionError`).
- **Data Ingestion & Candle Reconstruction:** Real-time spot feed and Level 2 depth streaming operate with microsecond deduplication, boundary-crossing bar aggregation, and strict rejection of stale or future-skewed quotes.
- **Incremental Strategy Processing:** Closed 5-minute candles are evaluated chronologically without lookahead bias.
- **Isolated Virtual Accounting:** The production paper trading ledger remains at **₹250,000.00 cash** with **0 trades** and **₹0.00 realized P&L**.

---

## 2. Execution Mode Architecture & Ledger Isolation

The platform enforces three completely isolated execution modes:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              ISOLATED EXECUTION MODES                                  │
├──────────────────────────┬─────────────────────────────┬───────────────────────────────┤
│ 1. LIVE_READ_ONLY        │ 2. HISTORICAL_REPLAY        │ 3. SYNTHETIC_DEMONSTRATION    │
├──────────────────────────┼─────────────────────────────┼───────────────────────────────┤
│ • Authentic live ticks   │ • Historical signals        │ • Scripted test fixtures      │
│ • Real Level 2 depth     │ • Historical option quotes  │ • Snapshot quotes from archive│
│ • Shadow signal logging  │ • Strict chronological order│ • Tests cost engine & ledger  │
│ • Production ledger:     │ • Isolated research ledger  │ • Isolated demo ledger        │
│   Cash: ₹250,000.00      │ • Never touches live ledger │ • Never touches live ledger   │
│   Realized P&L: ₹0.00    │                             │                               │
└──────────────────────────┴─────────────────────────────┴───────────────────────────────┘
```

### Virtual Account Mutation Firewall
1. **Production Paper Ledger:** Located in `data/cache/paper_broker_state.json` and DuckDB `completed_trades`. It can **only** be modified by live paper fills when `paper_execution_enabled = True` in a verified live trading configuration.
2. **Replay & Demonstration Isolation:** Historical backtest replays write to independent research tables (`backtest_trades`, `equity_snapshots_replay`). Synthetic lifecycle demonstrations exist strictly as transient test fixtures and cannot write to production tables.

---

## 3. Market Data Ingestion & Authoritative Candle Reconstruction

### Real-Time Ingestion Architecture
- **Market Feed:** Handled by [`LiveMarketFeed`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/data/live_market_feed.py#L40-L150) connecting to 5paisa market endpoints.
- **Spot Observation Pipeline:** Spot ticks are timestamped by the exchange (`provider_timestamp`) and recorded upon ingestion (`ingestion_timestamp`).
- **Quote Freshness & Skew Validation:**
  - Quote staleness threshold: $5.0$ seconds.
  - Skew rejection: Quotes with exchange timestamps $> 0.1$s ahead of local ingestion time are rejected as `FUTURE_QUOTE`.
  - Stale ticks (unchanged timestamp or zero rate) are marked `is_stale = True` and rejected.

### Authoritative 5-Minute Candle Aggregation
- **Aggregator:** [`TickAggregator`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/data/live_market_feed.py#L180-L240) aggregates ticks into 5-minute buckets (`09:15–09:20`, `09:20–09:25`, etc.).
- **Boundary-Crossing Logic:** A candle is closed **only** when a validated tick crosses into the subsequent 5-minute interval. This ensures that intra-candle price ticks are never prematurely treated as completed bar closes.
- **Missing Interval Detection:** If a gap in feed delivery spans an entire 5-minute window, the aggregator flags `candle_missing_intervals` in `ReadOnlyCollector` telemetry, preventing false breakout signals on discontinuous data.

### Option Chain Depth Archival
- **Depth Archival:** ATM strikes ($\pm 2$ strikes around spot) are queried at each observation cycle.
- **Level 2 Metrics:** Captures `best_bid`, `best_ask`, `bid_qty`, `ask_qty`, `spread_pts`, and `spread_pct`.
- **Executable Bid/Ask Rules:** If the spread exceeds $2.0\%$ of premium, or if bid/ask quantities equal 0, the quote is marked `INVALID_QUOTE` and order execution is prohibited.

---

## 4. Strategy Evaluation & Incremental Execution Workflow

When a 5-minute candle closes, the execution workflow executes the following steps:

```
[5-Minute Candle Closes]
         │
         ▼
[Check Time Boundary: Time < 14:30 IST?] ──No──► [Reject: ENTRY_CUTOFF_REACHED]
         │ Yes
         ▼
[Check Risk Manager: Kill Switch Active?] ──Yes─► [Reject: KILL_SWITCH_ENGAGED]
         │ No
         ▼
[Evaluate Active Strategy Rules on Closed Bar]
         │
         ├───► Strategy = RF ───────────────────► [Reject: STRATEGY_PERMANENTLY_DISABLED]
         │
         ├───► Strategy = PDS / PDR / IRF M2 ───► [Gated: BLOCKED_RULEBOOK_AMBIGUITY_GATE]
         │
         └───► Strategy = ORB M1 / IRF M1
                   │
                   ▼
         [Candidate Signal Generated?] ──No──► [Log: NO_SETUP]
                   │ Yes
                   ▼
         [Contract Selection (ATM Call/Put)]
                   │
                   ▼
         [Check Level 2 Depth: Spread <= 2%, Age <= 5s] ──Fail──► [Reject: STALE_OR_WIDE_SPREAD]
                   │ Pass
                   ▼
         [Check Risk Limits: Allocation <= 20%, Positions == 0] ──Fail──► [Reject: RISK_LIMIT_BREACH]
                   │ Pass
                   ▼
         [Simulate Fill via PaperBroker with 0.50pt Slippage]
                   │
                   ▼
         [Monitor Position Intraday for Target / Stop-Loss / 15:15 EOD Square-Off]
```

---

## 5. Risk Management & Portfolio Constraints

| Constraint Parameter | Configured Value | Verification Evidence |
|---|---|---|
| **Virtual Starting Capital** | **₹250,000.00** | Configured in `PaperEngineConfig` |
| **Max Concurrent Positions** | **1 Position** | Enforced by [`PaperRiskManager`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/paper/risk.py#L30-L75) |
| **Max Lots Per Trade** | **2 Lots (130 Units)** | Enforced by `PaperRiskManager` |
| **Max Capital Allocation** | **₹50,000.00 (20%)** | Single-trade premium debit ceiling |
| **Daily Loss Limit** | **₹5,000.00 (2%)** | Triggers automated Kill Switch |
| **Entry Cutoff Time** | **14:30 IST** | No new positions opened after 14:30 |
| **Forced Square-Off Time** | **15:15 IST** | All intraday positions closed at 15:15 |
| **Simulated Slippage** | **0.50 points / leg** | Added to entry ask; deducted from exit bid |
| **Statutory & Brokerage Engine** | **NSE Equity Derivatives** | [`IndianOptionsCostCalculator`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/backtesting/indian_options_costs.py#L66-L160) |

---

## 6. Dashboard Observability

The dashboard ([`scripts/paper_trading_dashboard.py`](file:///Users/syedaskari/project/5paisa-options/scripts/paper_trading_dashboard.py)) has been fully refactored to ensure transparent observability:
- **`🟢 LIVE SESSION` Tab:** Shows real-time NIFTY 50 spot rates, Level 2 depth quotes, 5-minute candle charts, and candidate strategy evaluations. When paper execution is gated, it displays real bid/ask quotes and shadow decisions with zero fills.
- **`📜 HISTORICAL REPLAY` Tab:** Dedicated to verified historical backtest runs with distinct equity curves and research trade logs.
- **`🧪 DEMONSTRATION` Tab:** Shows isolated accounting test fixtures and full fee reconciliation tables, clearly marked as non-production.
- **`🛡️ Safety & Safeguards Audit` Tab:** Displays live status of client proxy method blocking and test suite health.

---

## 7. What Remains Necessary Before the First Genuine Paper Trade

Before prospective paper fills can be unlocked for any strategy, the following two operational gates must be cleared:

1. **Strategy Author Rule Confirmations:**
   - Formal sign-off on the **Priority 1 & Priority 2** questions defined in [`STRATEGY_AUTHORIZATION_MATRIX.md`](file:///Users/syedaskari/project/5paisa-options/reports/paper_trading_live_session/STRATEGY_AUTHORIZATION_MATRIX.md).
   - In the absence of author confirmation, only **ORB Method 1** and **IRF Method 1** may be evaluated for forward execution under strict fail-closed constraints.
2. **Pre-Session Operational Checklist Sign-Off:**
   - Satisfactory completion of all 10 gates in [`LIVE_SESSION_OPERATIONAL_CHECKLIST.md`](file:///Users/syedaskari/project/5paisa-options/reports/paper_trading_live_session/LIVE_SESSION_OPERATIONAL_CHECKLIST.md) on the morning of trading.
