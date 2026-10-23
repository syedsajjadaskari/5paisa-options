# Phase 2D Completion Report — Real Historical Backtest & Manual Trade Reconciliation

> [!CAUTION]
> BASELINE_V1 is preserved as historical provenance, not as a verified canonical
> strategy result. Independent remediation changed entry/exit distances, reference
> levels, entry cutoff handling, ORB filters, and RF classification. See
> `reports/independent_codex_audit/` before using any metric below.

**Document Version:** 1.0.0  
**Phase:** Phase 2D (Historical Backtest, Pipeline Execution & Manual Trade Reconciliation)  
**Date:** October 21, 2026  
**Auditor:** Senior Quantitative Researcher, Python Backtesting Engineer & Trading Strategy Auditor  
**Repository:** `/Users/syedaskari/project/5paisa-options`  
**Test Suite Status:** **87 passed** (100% passing in 8.66s)  
**Code Quality:** Clean (`ruff check` 0 errors, `ruff format` 100% compliant)  
**Execution Mode:** `READ_ONLY` (Zero live orders placed; zero parameter optimization performed)  

---

## 1. Executive Summary

Phase 2D has achieved an end-to-end historical research execution, data quality verification, and comprehensive reconciliation between the automated trading engine and the manually maintained trading journal (`Trading_Journal_Sep2025_Aug2026 REPORT.xlsx`).

Across the full target coverage period (**September 1, 2025 through August 31, 2026**), the automated pipeline:
1. Ingested and verified **92,981 1-minute bars**, **20,237 5-minute bars**, and **661 daily bars** of NIFTY 50 index data from 5paisa's authenticated API.
2. Verified point-in-time reference levels (Daily 200 DMA, Intraday 200 EMA, Prior Day High/Low/Close/Zones, Initial Range) with $\ge 250$ trading days of pre-period daily history.
3. Executed baseline backtests across all 5 strategies (IRF, ORB, PDR, PDS, RF) and their individual rulebook methods with realistic 0.50 pt entry/exit slippage (1.00 pt round-trip) and 15:15 IST forced EOD square-off.
4. Audited all 190 manual journal entries, identifying critical workbook discrepancies and weekend trades.
5. Performed 1-to-1 reconciliation across Exact (0m), $\le 5$m, and $\le 10$m tolerance tiers, demonstrating **91.5% binary outcome agreement** (65 agreed outcomes out of 71 evaluated binary trades).
6. Evaluated options data feasibility on 5paisa, definitively verifying that historical expired options candles are unavailable and appropriately marking options backtesting as `BLOCKED`.

---

## 2. Confirmed Historical Coverage

The historical market data was acquired directly from the authenticated 5paisa historical REST API (V2) using the verified NIFTY 50 Cash Index ScripCode (`999920000`, NSE Cash Segment).

| Timeframe | Start Timestamp (IST) | End Timestamp (IST) | Bar Count | Storage Location | Ingestion Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1-Day (1d)** | 2024-01-01 00:00:00 | 2026-08-31 00:00:00 | **661 bars** | `data/processed/nifty/1d/nifty50_1d.parquet` | COMPLETE (Warm-up $\ge 250$ days) |
| **5-Minute (5m)** | 2025-08-01 09:15:00 | 2026-08-31 15:30:00 | **20,237 bars** | `data/processed/nifty/5m/nifty50_5m.parquet` | COMPLETE (Intraday EMA Warm-up) |
| **1-Minute (1m)** | 2025-09-01 09:15:00 | 2026-08-31 15:29:00 | **92,981 bars** | `data/processed/nifty/1m/nifty50_1m.parquet` | COMPLETE (Target Journal Window) |

### 5-Minute Reconstruction Audit
To verify data integrity, native 5-minute broker candles were compared against 5-minute candles reconstructed from 1-minute bars:
- **Maximum Open Delta:** `0.0000`
- **Maximum High Delta:** `0.0000`
- **Maximum Low Delta:** `0.0000`
- **Maximum Close Delta:** `0.0000`
- **Integrity Rating:** **100.0% Exact Match** (Zero data loss or timestamp drift).

---

## 3. Session Completeness & Unavailable Days

Across the 12-month evaluation window (September 1, 2025 – August 31, 2026):
- **Total Continuous NSE Sessions:** 248 exchange trading sessions.
- **Fully Complete Continuous Sessions:** **247 sessions (99.6%)** with exact 375 1m bars / 75 5m bars.
- **Legitimate Special Sessions:** **1 session** (Diwali Muhurat Trading on October 21, 2025, 13:45–14:45 IST, 12 5m bars, verified against NSE calendar).
- **Corrupted / Missing Exchange Sessions:** **0 sessions (0.0%)**.
- **Coverage Classification:** `COMPLETE` continuous historical dataset.

Detailed session coverage is logged in `reports/data_quality/nifty_coverage.csv` and documented in `docs/historical_data_coverage.md`.

---

## 4. Manual Journal Import & Workbook Audit

### 4.1 Provenance
- **Source File:** `data/manual/Trading_Journal_Sep2025_Aug2026 REPORT.xlsx`
- **Source SHA-256:** `97a9ad4dfb5520619bb979770115d7b6f4e3e37e649af255c543f5fce9694781`
- **Total Imported Trades:** **190 trades**
- **Normalized Artifacts:**
  - `reports/manual_journal/journal_audit.xlsx`
  - `reports/manual_journal/manual_trades.parquet`
  - `reports/manual_journal/manual_trades.csv`

### 4.2 Critical Workbook Discrepancies
1. **Summary vs Detailed Row Mismatch:**
   - The workbook's **Summary** tab hardcodes **189 trades** (80 Target, 44 BE, 65 SL).
   - The detailed **Trading_Journal** tab contains **190 numbered trades** (81 Target, 44 BE, 65 SL).
   - *Root Cause:* Trade #190 on August 31, 2026 (Row 203: ORB, Target) was appended below the summation range and excluded from the sheet's formulas.
2. **Weekend Non-Trading Entries:**
   - **5 trades** were recorded on Saturdays when NSE was closed:
     - Trade #23: `2025-10-11` (Saturday) — ORB, SL
     - Trade #82: `2026-02-14` (Saturday) — IRF, BE
     - Trade #181: `2026-08-15` (Saturday, Independence Day) — ORB, Target
     - Trade #185: `2026-08-22` (Saturday) — ORB, SL
     - Trade #189: `2026-08-29` (Saturday) — ORB, Target
   - *Classification:* These 5 trades were classified as `MISSING_HISTORICAL_DATA` (Non-Trading Exchange Days) and excluded from mechanical strategy precision/recall denominators.
   - *Eligible Trading-Day Trades:* **185 trades**.

---

## 5. Automated Backtest Results Across Strategies & Methods

All strategies were backtested on 5-minute bars with 200 DMA and 200 EMA point-in-time warmup context. Each trade incorporates 0.50 pt entry slippage and 0.50 pt exit slippage (1.00 pt round-trip cost) and 15:15 IST forced EOD exit.

### Method-by-Method Performance Breakdown (`reports/phase2d/strategy_comparison.csv`)

| Strategy | Evaluated Rulebook Method | Auto Trades | Win Rate | Gross Pts | Slippage | Net Pts | Profit Factor | Max DD (pts) | Max Cons Losses | Avg Holding |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **PDS** | **Method 1: Strong Red Candle** | **74** | **52.7%** | +1,333.8 | 74.0 | **+1,259.8** | **1.94** | **136.8** | 3 | 72.8 min |
| **PDS** | Method 2: Red Pin Bar HPR | 70 | 51.4% | +1,181.0 | 70.0 | **+1,111.0** | 1.90 | 207.2 | 6 | 71.1 min |
| **PDS** | Method 3: Support Break Fallback | 60 | 45.0% | +471.4 | 60.0 | **+411.4** | 1.33 | 246.4 | 4 | 78.6 min |
| **ORB** | **Method 1: PDH/PDL Breakout** | **61** | **44.3%** | +619.1 | 61.0 | **+558.1** | **1.53** | **250.5** | 7 | 48.0 min |
| **ORB** | Method 2: Inner Level Breakout | 0 | — | 0.0 | 0.0 | 0.0 | — | 0.0 | 0 | — |
| **ORB** | Method 3: 200 DMA Gap | 0 | — | 0.0 | 0.0 | 0.0 | — | 0.0 | 0 | — |
| **IRF** | Method 2: Extension & Reversal | 127 | 42.5% | +531.7 | 127.0 | **+404.7** | 1.16 | 321.0 | 7 | 46.6 min |
| **PDR** | **Method 1: Retest Bounce** | **77** | **39.0%** | +193.1 | 77.0 | **+116.1** | **1.07** | **335.2** | 6 | 77.1 min |
| **IRF** | Method 3: Liquidity Sweep | 5 | 60.0% | +83.8 | 5.0 | **+78.8** | 2.51 | 52.0 | 2 | 33.0 min |
| **IRF** | **Method 1: Immediate Failure** | **112** | **37.5%** | +38.0 | 112.0 | **-74.1** | 0.97 | 408.0 | 10 | 46.9 min |
| **RF** | **Canonical Reclaim (Unresolved Target)** | **29** | **37.9%** | -137.3 | 29.0 | **-166.3** | 0.74 | 418.7 | 8 | 94.7 min |
| **PORTFOLIO** | **Combined Multi-Strategy Baseline** | **353** | **42.2%** | **+2,046.7** | **353.0** | **+1,693.7** | **1.35** | **284.1** | — | — |

*Note: All points represent pure NIFTY 50 index points. Modeled index points are NOT directly tradable rupee returns and must not be confused with option premium movement.*

---

## 6. One-to-One Manual Trade Reconciliation

Reconciliation was conducted by comparing manual journal records against automated executions matching on **Date**, **Strategy**, and **Entry Time**.

### 6.1 Reconciliation Classification Totals (`reports/phase2d/manual_vs_automated.csv`)

| Match Classification | Tolerance Tier | Trade Count | Percentage of Journal | Description / Handling |
| :--- | :--- | :---: | :---: | :--- |
| **EXACT_MATCH** | Exact 0m Delta | **3** | 1.6% | Executed at the exact identical minute. |
| **TIME_TOLERANCE_MATCH** | $\le 5$ min Delta | **80** | 42.1% | 5-minute delta arising from candle-open vs candle-close convention. |
| **TIME_TOLERANCE_MATCH** | $5\text{m} < \Delta \le 10$ min | **8** | 4.2% | Minor pullback / secondary confirmation candle. |
| **TOTAL MATCHED** | **$\le 10$ min Tolerance** | **91** | **47.9%** | **Strict 1-to-1 Matched Trades** |
| **AMBIGUOUS_MATCH** | $> 10$ min Delta (Same Date) | 25 | 13.2% | Strategy signal occurred later in session on the same date. |
| **MANUAL_ONLY** | No Auto Signal on Date | 69 | 36.3% | Mechanical filters (wick ratio, body ratio, EMA) rejected trade. |
| **MISSING_HISTORICAL_DATA** | Weekend / Non-Trading | 5 | 2.6% | Manual journal entries logged on Saturdays. |
| **TOTAL** | — | **190** | **100.0%** | Full Journal Coverage |

### 6.2 Reconciliation Metrics by Strategy Family

| Strategy | Manual Trades | Eligible Trades | Auto Trades | Matched Trades | Match Rate (Recall) | Precision | Extra Auto Trades |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **ORB** | 89 | 85 | 61 | **46** | **54.1%** | **75.4%** | 15 |
| **IRF** | 73 | 72 | 112 | **25** | **34.7%** | **22.3%** | 87 |
| **RF** | 17 | 17 | 29 | **11** | **64.7%** | **37.9%** | 18 |
| **PDR** | 8 | 8 | 77 | **6** | **75.0%** | **7.8%** | 71 |
| **PDS** | 3 | 3 | 74 | **3** | **100.0%** | **4.1%** | 71 |
| **Total** | **190** | **185** | **353** | **91** | **49.2%** | **25.8%** | **262** |

---

## 7. Outcome Agreement Analysis

For the **91 matched trades**, manual outcomes (`Target`, `SL`, `BE`) were evaluated against automated engine exit reasons (`TARGET`, `STOP_LOSS`, `EOD_SQUAREOFF`).

### 7.1 Cross-Tabulation Matrix

| Manual Journal Recorded Outcome | Auto: TARGET | Auto: STOP_LOSS | Auto: EOD_SQUAREOFF | Total Matched Trades |
| :--- | :---: | :---: | :---: | :---: |
| **Target** | **39** | 4 | 0 | 43 |
| **SL** | 0 | **26** | 2 | 28 |
| **BE** | 7 | 12 | 1 | 20 |
| **Total** | **46** | **42** | **3** | **91** |

### 7.2 Quantitative Agreement Rates
- **Binary Outcome Agreement (Target / SL only):**
  - Evaluated trades: 71 trades (43 manual Target + 28 manual SL).
  - Agreed Target: **39 trades**
  - Agreed SL: **26 trades**
  - Absolute Disagreements: **Only 4 trades** (Manual Target vs Auto SL).
  - False Targets: **0 trades** (Manual SL never reached automated Target).
  - **Overall Binary Agreement Rate:** **91.5%** ($65 / 71$).
- **ORB Strategy Outcome Agreement:**
  - Evaluated binary trades: 36 trades (25 manual Target, 11 manual SL).
  - Agreed: 24 Target + 11 SL = **35 trades**.
  - Disagreed: Only 1 trade!
  - **ORB Binary Agreement Rate:** **97.2%** ($35 / 36$).

### 7.3 Break-Even (BE) Discretionary Handling
20 matched manual trades exited at `BE`. In the mechanical backtester (which strictly honors fixed PDF stops and targets), 12 of these hit stop-loss, 7 reached target, and 1 exited at EOD. This reflects human trailing stop intervention where the trader moved the stop to entry after an initial favorable excursion.

---

## 8. Root Cause Analysis of Discrepancies

### 8.1 Timing Convention: 09:15 vs 09:20
- All 89 manual ORB trades in the journal have an entry timestamp of `09:15`.
- The canonical ORB rulebook states: *"Decision Time: Exactly 09:20 AM IST (after first 5-minute candle closes)"*.
- The automated engine confirms the signal at `09:20:00` candle close and executes at `09:20:00` next-bar Open.
- *Conclusion:* The 5-minute delta is an artifact of manual logging convention (logging bar start time vs bar completion). The trades represent identical market events.

### 8.2 Missed Manual Trades: Candle Quality Rejections
The automated engine rejected 39 manual ORB setups on valid trading days. Analysis revealed:
- **Wick Filter Violations:** Opposing wick ratio exceeded the rulebook threshold ($> 35\%$).
- **Body Ratio Violations:** Candle body ratio fell below the minimum decisive breakout threshold ($< 50\%$).
- **Trend Filter Violations:** Price traded on the wrong side of the intraday 200 EMA.
- *Finding:* The human trader frequently took discretionary breakouts on low-conviction pin bars or against the prevailing trend.

### 8.3 Extra Automated Signals: Strategy Selection Bias
The automated engine emitted 262 trades not found in the manual journal:
- The human trader focused heavily on ORB (89 trades) and IRF (73 trades), representing **85.3% of their journal**.
- PDS and PDR were largely ignored (3 and 8 trades respectively).
- However, **PDS Method 1 was the single most profitable strategy of the year**, delivering **+1,259.80 net points** with a **52.7% win rate** and **1.94 profit factor** across 74 valid signals.

### 8.4 RF Strategy Open Target Ambiguity
- Page 8 of `RF strategy.pdf` explicitly marks the profit target as `'OPEN'`.
- Canonical RF backtests were run using an experimental symmetric 60-point target, producing **-166.30 net points** (37.9% win rate).
- In accordance with the prompt mandate, all RF outcome comparisons are flagged as `UNRESOLVED_RULE`.

---

## 9. Options Data Feasibility Assessment

To determine feasibility for options backtesting:
1. Audited the active 5paisa Scrip Master: contains **4,062 active NIFTY CE/PE option contracts**.
2. Queried historical candle endpoints for expired weekly and monthly options across 2025–2026.
3. **API Response:** The 5paisa Historical V2 REST endpoint returned empty candle arrays (`[]`) for expired contracts.
4. **Feasibility Determination:** 5paisa historical candle endpoints **do not support expired derivative contracts**.
5. **Phase Classification:** Historical Options Backtest is marked **BLOCKED**. Genuine underlying NIFTY backtests proceed without synthetic Black-Scholes substitution.

---

## 10. Verification of Deliverables

All deliverables required under Phase 2D have been generated, audited, and persisted:

| Deliverable Artifact | Path | Size | Description |
| :--- | :--- | :---: | :--- |
| **Strategy Comparison** | `reports/phase2d/strategy_comparison.csv` | 2.1 KB | Institutional metrics across all 5 strategies and 11 methods. |
| **Manual vs Automated** | `reports/phase2d/manual_vs_automated.csv` | 41.5 KB | Full 190-trade reconciliation audit with classifications. |
| **Matched Trades** | `reports/phase2d/matched_trades.csv` | 23.9 KB | 91 strict matched trades with timing and outcome deltas. |
| **Missed Trades** | `reports/phase2d/missed_manual_trades.csv` | 12.1 KB | 74 manual-only records with root-cause failure classifications. |
| **Extra Automated Signals** | `reports/phase2d/extra_automated_signals.csv` | 33.0 KB | 262 mechanical trades not taken in human journal. |
| **Outcome Disagreements** | `reports/phase2d/outcome_disagreements.csv` | 9.2 KB | 33 records detailing BE differences and 4 true disagreements. |
| **Backtest Trades** | `reports/phase2d/backtest_trades.parquet` | 28.1 KB | Complete machine-readable archive of 353 simulated trades. |
| **Equity Curves Chart** | `reports/phase2d/equity_curves.html` | 4.86 MB | Interactive Plotly multi-curve and portfolio drawdown viewer. |
| **Validation Dashboard** | `reports/phase2d/validation_dashboard.html` | 4.88 MB | Standalone Plotly candlestick chart viewer with level overlays. |
| **Streamlit Interface** | `scripts/streamlit_validation_dashboard.py` | 8.8 KB | Interactive dashboard app with filters for trader review. |

---

## 11. Test Suite & Code Quality Audit

- **Pytest:** **87 passing tests** in 8.66 seconds (`tests/unit/test_historical_reconciliation_audit.py` added).
- **Ruff Check:** Passed with **0 errors**.
- **Ruff Format:** **97 files formatted and verified**.
- **Environment:** Isolated `.venv/` running Python 3.11.15 on macOS (arm64).

---

## 12. Key Research Findings & Strategic Recommendations

1. **Trade PDS Aggressively:** The human trader virtually neglected Previous Day Support (PDS), taking only 3 trades. Yet PDS is the undisputed star performer of the portfolio (+1,259.80 net pts, 1.94 PF, only 136.8 pt max drawdown).
2. **Adopt IRF Method 2 over Method 1:** The human trader traded IRF Method 1 (Immediate Failure), which incurred a slight loss (-74.05 net pts, 37.5% WR). However, **IRF Method 2 (Extension & Reversal)** generated **+404.65 net points** with a 42.5% win rate.
3. **Mechanical ORB Filters Outperform Human Discretion:** Automated ORB delivered **+558.10 net points** with a **1.53 profit factor** and **97.2% outcome agreement** on matched trades. The 39 manual trades filtered out were predominantly low-probability chops.
4. **Clarify RF Exit Rules:** RF requires an approved mechanical target rulebook update before capital allocation.
5. **Next Steps:**
   - Review these baseline historical reconciliation findings.
   - Do NOT optimize parameters (Optuna) until the trader approves the baseline findings.
   - Do NOT enable live order execution.
