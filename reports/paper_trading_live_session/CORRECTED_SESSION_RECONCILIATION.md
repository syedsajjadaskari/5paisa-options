# Corrected Paper-Trading Session Reconciliation & Forensic Evidence

**Session Date:** Monday, October 26, 2026  
**Audit Document:** `reports/paper_trading_live_session/CORRECTED_SESSION_RECONCILIATION.md`  
**Classification:** Independent Quantitative Audit & Reconciliation  
**Auditor:** Quantitative Trading & Risk Auditor  
**Status:** Authoritative Reconciled Master Report (Supersedes all prior session claims)

---

## Executive Summary & Classification Separation

This report provides an independent reconciliation and correction of the October 26, 2026 paper-trading session. In accordance with strict quantitative auditing standards, execution modes and financial results are strictly separated into three isolated categories:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               EXECUTION CLASSIFICATIONS                                │
├──────────────────────────┬─────────────────────────────┬───────────────────────────────┤
│ 1. LIVE_READ_ONLY        │ 2. HISTORICAL_REPLAY        │ 3. SYNTHETIC_LIFECYCLE_TEST   │
├──────────────────────────┼─────────────────────────────┼───────────────────────────────┤
│ • Authentic live ticks   │ • Historical signals        │ • Scripted test fixtures      │
│ • Partial morning capture│ • Verified historical quotes│ • Validates accounting & fees │
│ • 0 paper fills          │ • Strict chronological order│ • Snapshot quotes from archive│
│ • Realized P&L: ₹0.00    │ • Isolated research equity  │ • Isolated test equity        │
│ • Equity: ₹250,000.00    │ • Never commingled          │ • Does NOT update live ledger │
└──────────────────────────┴─────────────────────────────┴───────────────────────────────┘
```

> [!IMPORTANT]
> **Key Finding:** The two trades reported in `PAPER_TRADES.csv` were **scripted test fixtures** using selected snapshot prices from historical archive files, NOT chronological strategy-triggered executions. They are strictly classified as **`SYNTHETIC_LIFECYCLE_TEST`**.
>
> The genuine prospective live paper-trading realized P&L for October 26, 2026 is **₹0.00** across **0 live fills**.

---

## 1. Verified Live Observation Window

Telemetry records from `data/live_market_feed.py` and DuckDB event logs establish the actual live observation horizon:

| Event Milestone | Exchange / Local Timestamp | Verified Metric / Observed Rate | Data Provenance |
|---|---|---|---|
| **Earliest Observation** | `2026-10-26 08:58:09 IST` | NIFTY Spot: `22,600.00` | 5paisa Pre-Market Doctor Check |
| **Pre-Open Baseline** | `2026-10-26 09:02:02 IST` | NIFTY Spot: `22,486.25` | 5paisa Read-Only Feed |
| **Market Open Tick** | `2026-10-26 09:15:29 IST` | NIFTY Spot: `22,483.60` | Authentic Live Spot Tick |
| **Latest Observation** | `2026-10-26 09:21:51 IST` | NIFTY Spot: `22,412.95` | Authentic Live Spot Tick |

### Observation Window Assessment:
- **Verified Continuous Horizon:** `08:58:09 IST – 09:21:51 IST` (~23 minutes 42 seconds).
- **Session Classification:** **Partial Morning Capture**, NOT a complete session.
- **Correction:** The prior report's narrative claim of continuous monitoring from 08:55 to 15:30 IST is **superseded**. A successful REST HTTP 200 heartbeat response does not demonstrate continuous real-time market observation between 09:22 and 15:30 in the absence of persisted tick telemetry.

---

## 2. Live Signals and Fills

During the active observation window and diagnostic candle replay:
- **Candle Decisions Evaluated:** 375 total decisions across 75 five-minute bars (09:15 to 15:30) evaluated for PDS, ORB, IRF, PDR, and RF strategies.
- **Candidate Setups Detected:** 75 bars met primary technical conditions (e.g. gap, range boundary breach, or reference level proximity).
- **Safety Gate Intervention:** 100% of candidate signals (75/75) were intercepted and gated by the `BLOCKED_RULEBOOK_AMBIGUITY_GATE`.
- **Live Paper Fills:** **0 orders placed, 0 orders filled**.
- **Live Execution Status:** All signals remained strictly in `SHADOW_SIGNALS_ONLY / DIAGNOSTIC_OBSERVATION` mode.

---

## 3. Genuine Paper Realized P&L

The genuine live production paper trading ledger reflects zero executions:

| Metric | Verified Live Value | Source of Record |
|---|---|---|
| **Starting Capital** | **₹250,000.00** | Virtual Equity Ledger |
| **Completed Trades** | **0** | `completed_trades` Table |
| **Realized Gross P&L** | **₹0.00** | Live Broker State |
| **Total Transaction Costs** | **₹0.00** | Statutory Cost Engine |
| **Realized Net P&L** | **₹0.00** | Live Broker State |
| **Unrealized P&L** | **₹0.00** | Zero Open Positions |
| **Available Cash** | **₹250,000.00** | `paper_broker_state.json` |
| **Ending Equity** | **₹250,000.00** | Liquid Virtual Capital |

Replay or demonstration equity must **never** be represented as live portfolio equity.

---

## 4. Replay Trade Provenance & Reclassification

The two trades recorded in `reports/paper_trading_live_session/PAPER_TRADES.csv` were subjected to independent forensic verification:

### Trade 1: PDS Method 1 — Bearish PE
- **Reported Entry:** `2026-10-26 09:20:01 IST` @ Spot `22,600.00`, Option Ask `₹119.50` (+0.50 slippage &rarr; `₹120.00`), Contract: `NIFTY 27 OCT 2026 PE 22600.00`.
- **Reported Exit:** `2026-10-26 10:15:00 IST` @ Spot `22,528.50`, Option Bid `₹162.45` (-0.50 slippage &rarr; `₹161.95`).
- **Forensic Findings:**
  - Quote `₹119.50` matches a static quote from `data/processed/options_archive_verified/date=2026-10-23/options_archive_READONLY_20261023_091904_498edc_reconciled.parquet`.
  - Underlying spot values (`22,600.00` and `22,528.50`) were hardcoded in `scripts/run_live_paper_session.py` lines 362 and 412.
  - The trade did **not** originate from an organic chronological signal pipeline.

### Trade 2: ORB Method 1 — Bullish CE
- **Reported Entry:** `2026-10-26 11:30:01 IST` @ Spot `22,680.00`, Option Ask `₹185.30` (+0.50 slippage &rarr; `₹185.80`), Contract: `NIFTY 27 OCT 2026 CE 22700.00`.
- **Reported Exit:** `2026-10-26 12:10:00 IST` @ Spot `22,648.00`, Option Bid `₹168.00` (-0.50 slippage &rarr; `₹167.50`).
- **Forensic Findings:**
  - Quote `₹185.30` was manually selected from the October 23 archive.
  - Spot prices (`22,680.00` and `22,648.00`) were scripted to simulate an ORB stop-loss trigger.

### Classification Verdict:
Because these trades were scripted test fixtures with manually seeded price levels, their strict classification is **`SYNTHETIC_LIFECYCLE_TEST`**. They must **not** be termed historical strategy replays or genuine paper fills.

---

## 5. Recomputed Fees and Equity Reconciliation

### Root Cause of the ₹167.78 vs ₹194.08 Discrepancy
In the prior narrative draft, a summary line stated:
`| Total Transaction Charges | ₹194.08 | STT (₹42.84), GST (₹28.47), Brokerage (₹80.00), Turnover (₹15.27), Stamp (₹1.20) |`
The sum of those text values is:
`42.84 + 28.47 + 80.00 + 15.27 + 1.20 = ₹167.78`
This was a manual typographical transcription error in the summary text. The programmatic calculation in `PAPER_TRADES.csv` and `IndianOptionsCostCalculator` actually produced **₹194.08**.

### Independent Recalculation (Statutory & Brokerage Rules)
All fees are calculated using official NSE Equity Derivatives parameters:
- **Brokerage:** Flat ₹20.00 per executed order leg.
- **STT (Securities Transaction Tax):** 0.15% on sell-side premium turnover.
- **Exchange Turnover Charges:** 0.03503% on total premium turnover (buy + sell).
- **SEBI Turnover Charges:** ₹10 per crore (0.0001%) on total premium turnover.
- **Stamp Duty:** 0.003% on buy-side premium turnover.
- **GST:** 18% on (Brokerage + Exchange Turnover Charges + SEBI Charges).

#### Detailed Calculation for Trade 1 (PDS 22600 PE — 2 Lots / 130 Units)
- **Buy Premium:** `130 × ₹120.00 = ₹15,600.00`
- **Sell Premium:** `130 × ₹161.95 = ₹21,053.50`
- **Total Turnover:** `₹15,600.00 + ₹21,053.50 = ₹36,653.50`
- **Gross P&L:** `(₹161.95 - ₹120.00) × 130 = +₹5,453.50`
- **Fee Breakdown:**
  - Brokerage: `₹20.00 (Buy) + ₹20.00 (Sell) = ₹40.00`
  - STT: `0.0015 × ₹21,053.50 = ₹31.58`
  - Exchange Charges: `0.0003503 × ₹36,653.50 = ₹12.84`
  - SEBI Fee: `0.000001 × ₹36,653.50 = ₹0.04`
  - Stamp Duty: `0.00003 × ₹15,600.00 = ₹0.47`
  - GST: `0.18 × (₹40.00 + ₹12.84 + ₹0.04) = 0.18 × ₹52.88 = ₹9.52`
- **Total Charges Trade 1:** `40.00 + 31.58 + 12.84 + 0.04 + 0.47 + 9.52 = ₹94.45`
- **Net P&L Trade 1:** `₹5,453.50 - ₹94.45 = +₹5,359.05`

#### Detailed Calculation for Trade 2 (ORB 22700 CE — 2 Lots / 130 Units)
- **Buy Premium:** `130 × ₹185.80 = ₹24,154.00`
- **Sell Premium:** `130 × ₹167.50 = ₹21,775.00`
- **Total Turnover:** `₹24,154.00 + ₹21,775.00 = ₹45,929.00`
- **Gross P&L:** `(₹167.50 - ₹185.80) × 130 = -₹2,379.00`
- **Fee Breakdown:**
  - Brokerage: `₹20.00 + ₹20.00 = ₹40.00`
  - STT: `0.0015 × ₹21,775.00 = ₹32.66`
  - Exchange Charges: `0.0003503 × ₹45,929.00 = ₹16.09`
  - SEBI Fee: `0.000001 × ₹45,929.00 = ₹0.05`
  - Stamp Duty: `0.00003 × ₹24,154.00 = ₹0.72`
  - GST: `0.18 × (₹40.00 + ₹16.09 + ₹0.05) = 0.18 × ₹56.14 = ₹10.11`
- **Total Charges Trade 2:** `40.00 + 32.66 + 16.09 + 0.05 + 0.72 + 10.11 = ₹99.63`
- **Net P&L Trade 2:** `-₹2,379.00 - ₹99.63 = -₹2,478.63`

#### Reconciliation Summary Table
| Fee Component | Applicable Rate / Side | Prior Erroneous Text | Trade 1 Correct | Trade 2 Correct | Reconciled Total | Status |
|---|---|---|---|---|---|---|
| **Brokerage** | ₹20 per leg | ₹80.00 | ₹40.00 | ₹40.00 | **₹80.00** | Exact Match |
| **STT** | 0.15% on Sell | ₹42.84 | ₹31.58 | ₹32.66 | **₹64.24** | Corrected |
| **Exchange** | 0.03503% on Turnover | ₹15.27 | ₹12.84 | ₹16.09 | **₹28.93** | Corrected |
| **SEBI** | ₹10 / crore | *Omitted* | ₹0.04 | ₹0.05 | **₹0.09** | Restored |
| **Stamp Duty** | 0.003% on Buy | ₹1.20 | ₹0.47 | ₹0.72 | **₹1.19** | Exact (1.19 &asymp; 1.20) |
| **GST** | 18% on (Brok+Exch+SEBI) | ₹28.47 | ₹9.52 | ₹10.11 | **₹19.63** | Corrected |
| **Total Fees** | **All Components** | **₹167.78** | **₹94.45** | **₹99.63** | **₹194.08** | **Fully Reconciled** |

### Slippage Accounting Audit:
- **Slippage Impact:** 0.50 pt per leg $\times$ 130 units = ₹65.00 per leg (₹130.00 per trade; ₹260.00 total).
- **Double-Counting Verification:** Slippage is **already directly embedded** in the fill prices (Ask ₹119.50 + 0.50 = ₹120.00; Bid ₹162.45 - 0.50 = ₹161.95). It is deducted from Gross P&L and must **not** be deducted a second time from Net P&L.

### Synthetic Demonstration Equity:
- **Starting Capital:** ₹250,000.00
- **Gross P&L:** `+₹5,453.50 - ₹2,379.00 = +₹3,074.50`
- **Total Charges:** `₹94.45 + ₹99.63 = ₹194.08`
- **Net Realized P&L:** `+₹3,074.50 - ₹194.08 = +₹2,880.42`
- **Ending Synthetic Cash / Equity:** `₹250,000.00 + ₹2,880.42 = ₹252,880.42`

---

## 6. Data-Quality Failures

Independent evaluation of data ingestion revealed two primary friction points:
1. **Future Timestamp Skew:**
   - 5paisa REST depth endpoint returned exchange provider timestamps newer than the ingestion timestamp during rapid queries (`age < -0.1s`).
   - The engine's validation filter caught and rejected these as `FUTURE_QUOTE` in `LIVE_MARKET_DATA_QUALITY.csv`.
2. **Pre-Market Zero Depth:**
   - Prior to 09:15 IST, option depth queries returned zero bids/asks, correctly categorized as `ZERO_OR_CROSSED_BID_ASK`.
3. **Sequential REST Polling Latency:**
   - Polling multiple strikes over REST introduced ~100s cumulative latency, confirming the architectural need for 5paisa WebSocket streaming.

---

## 7. Strategy Eligibility and Safety Gating

| Strategy | Rulebook Method | Eligibility Status | Gate Trigger | Action Required |
|---|---|---|---|---|
| **PDS** | Method 1 | GATED | `BLOCKED_RULEBOOK_AMBIGUITY_GATE` | Unconfirmed retest proximity distance; 35 vs 40 pt stop choice. |
| **ORB** | Method 1 | GATED | `BLOCKED_RULEBOOK_AMBIGUITY_GATE` | "Established trend" definition (200-EMA slope unconfirmed by author). |
| **IRF** | Method 2 | GATED | `BLOCKED_RULEBOOK_AMBIGUITY_GATE` | Stop-loss choice ambiguity (35 vs 40 points). |
| **PDR** | Retest Bounce | GATED | `BLOCKED_RULEBOOK_AMBIGUITY_GATE` | Proximity tolerance ambiguity. |
| **RF** | Method 1 | PERMANENTLY DISABLED | `RULEBOOK_INCOMPLETE` | Entry/exit rules incomplete in author document. |

---

## 8. Complete Test Results & Discrepancy Explanation

### Test Suite Discovery:
- **Total Repository Tests:** **219 tests**
- **Test Distribution:**
  - `tests/unit/`: 152 tests
  - `tests/options/`: 44 tests
  - `tests/strategies/`: 17 tests
  - `tests/integration/`: 6 tests
  - **Total:** **219 tests**

### Explanation of the Prior "152 Tests" Report:
A prior test run executed `./.venv/bin/pytest tests/unit/`, which collected strictly the 152 unit tests and omitted `tests/options/`, `tests/strategies/`, and `tests/integration/`. The full repository test suite contains 219 tests.

### Complete Execution Run (Pytest, Ruff, Compileall):
- **Pytest:** `219 passed in 22.03s`
  - Failed: **0**
  - Skipped: **0**
  - Deselected: **0**
- **Ruff Check:** Clean pass across `src/`, `tests/`, and `scripts/`.
- **Compileall:** Clean bytecode compilation across all packages with zero syntax errors.

---

## 9. Unresolved Issues

1. **Author Clarification on Strategy Rules:**
   - Retest proximity distance threshold (points from zone edge).
   - Authoritative stop-loss specification (35 points vs 40 points).
   - "Established trend" criteria for ORB.
2. **WebSocket Depth Implementation:**
   - Replace sequential REST queries with 5paisa WebSocket binary depth feed to eliminate latency and future timestamp skew.

---

## 10. Exact Code Changes

| File | Change Description |
|---|---|
| [`scripts/paper_trading_dashboard.py`](file:///Users/syedaskari/project/5paisa-options/scripts/paper_trading_dashboard.py) | Restructured dashboard tabs into strictly isolated `LIVE SESSION`, `HISTORICAL REPLAY`, `DEMONSTRATION`, and `SAFETY AUDIT` views. Isolated live equity (₹250,000 cash, ₹0.00 realized P&L). |
| [`reports/paper_trading_live_session/SESSION_PERFORMANCE.html`](file:///Users/syedaskari/project/5paisa-options/reports/paper_trading_live_session/SESSION_PERFORMANCE.html) | Added warning banner marking claims as superseded and reclassifying trades as `SYNTHETIC_LIFECYCLE_TEST`. |
| [`reports/paper_trading_live_session/POST_SESSION_DIAGNOSTICS.md`](file:///Users/syedaskari/project/5paisa-options/reports/paper_trading_live_session/POST_SESSION_DIAGNOSTICS.md) | Corrected observation horizon to partial morning capture (08:58 – 09:22 IST) and added superseded audit notice. |
| [`reports/paper_trading_live_session/RUNTIME_STATUS.md`](file:///Users/syedaskari/project/5paisa-options/reports/paper_trading_live_session/RUNTIME_STATUS.md) | Added superseded audit notice and updated mode to `LIVE_READ_ONLY`. |
| [`src/trading_platform/core/redaction.py`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/core/redaction.py) | Implemented `SensitiveDataFilter` to mask passwords, TOTP keys, and secrets. |
| [`tests/unit/test_credential_redaction.py`](file:///Users/syedaskari/project/5paisa-options/tests/unit/test_credential_redaction.py) | Added automated unit tests verifying token redaction in logs. |
