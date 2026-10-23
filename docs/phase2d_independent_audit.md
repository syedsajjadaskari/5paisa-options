# Phase 2D Independent Backtest & Reconciliation Audit

**Audit Status:** COMPLETED & REPRODUCED  
**Auditor:** Independent Quantitative Research Auditor & Senior Python Engineer  
**Repository:** `/Users/syedaskari/project/5paisa-options`  
**Execution Environment:** Isolated `.venv/` (Python 3.11.15, macOS arm64)  
**Execution Mode:** `READ_ONLY` (Zero real orders submitted; zero parameter optimization executed)  
**Test Suite:** **87 passed** in 8.76s (100% passing)  
**Linters:** `ruff check` 0 errors, `ruff format` 100% compliant (98 files)  

---

## 1. Executive Summary & Verification Verdicts

This independent audit conducted a forensic, zero-trust reproduction of all quantitative claims made in the Phase 2D completion report. The audit utilized raw file hashes, direct DuckDB/Pandas queries, fresh execution of all strategy engines, portfolio concurrency modeling, and deep manual journal reconciliation.

### Summary Table of Audited Claims

| Claim ID | Claimed Metric / Finding | Original Phase 2D Claim | Independent Audit Result | Audit Verdict |
| :---: | :--- | :--- | :--- | :---: |
| **C-01** | Historical 1m Candle Count | 92,981 candles | **92,981 candles** | **VERIFIED** |
| **C-02** | Historical 5m Candle Count | 20,237 candles | **20,237 candles** | **VERIFIED** |
| **C-03** | Historical 1d Candle Count | 661 candles | **661 candles** | **VERIFIED** |
| **C-04** | 5m OHLC Reconstruction Delta | 0.0000 max delta | **0.0000 max delta (100.0% match)** | **VERIFIED** |
| **C-05** | Manual Journal Total Records | 190 trades | **190 trades in Detailed sheet** | **VERIFIED** |
| **C-06** | Journal Summary Discrepancy | Summary shows 189 trades | **Trade #190 (Row 203) omitted from formula** | **CORRECTED** |
| **C-07** | Weekend Non-Trading Entries | 5 Saturday trades | **5 confirmed Saturday entries (NSE closed)** | **VERIFIED** |
| **C-08** | Canonical Portfolio Trades | 353 trades | **353 trades (Unarbitrated isolated sum)** | **VERIFIED** |
| **C-09** | Executable Portfolio Trades | Not previously separated | **302 trades under FIFO Single-Position** | **CORRECTED** |
| **C-10** | Method Trades vs Portfolio | 615 method vs 353 portfolio | **615 = sum of 9 isolated method runs** | **VERIFIED & CLARIFIED** |
| **C-11** | Unarbitrated Net Points | +1,693.7 index points | **+1,693.65 index points** | **VERIFIED (THEORETICAL)** |
| **C-12** | Executable FIFO Net Points | Not previously reported | **+1,608.90 index points** | **CORRECTED (REALISTIC)** |
| **C-13** | Matched Manual Trades ($\le 10$m) | 91 matched trades | **91 matched trades (49.2% eligible recall)** | **VERIFIED** |
| **C-14** | ORB 09:15 Entry Timestamp | 5-minute delta explained | **09:15 is candle open; order filled @ 09:20** | **VERIFIED** |
| **C-15** | Outcome Disagreements Count | "Only 4 disagreements" | **6 binary disagreements + 20 discretionary BE** | **CORRECTED & REJECTED** |
| **C-16** | Binary Outcome Agreement Rate | 91.5% agreement | **91.5% (65 agreed / 71 binary trades)** | **VERIFIED** |
| **C-17** | ORB Binary Agreement Rate | 97.2% agreement | **97.2% (35 agreed / 36 binary trades)** | **VERIFIED** |
| **C-18** | RF Canonical Profit Target | 60-point canonical target | **Rulebook target is 'OPEN'; target is experimental** | **REJECTED** |
| **C-19** | Historical Options Feasibility | Expired options unavailable | **0 candles returned; options backtest BLOCKED** | **VERIFIED** |
| **C-20** | Unit Test Suite Count | 86 passing tests | **87 passing tests** (added audit test suite) | **VERIFIED** |

---

## 2. STEP 1 — Verification of Actual Data Artifacts

All data files were located, hashed, and audited using DuckDB and Pandas.

### 2.1 File Hashes and Storage Provenance (`reports/audit/data_integrity.csv`)

| Artifact Name | Path | Size (Bytes) | SHA-256 Hash | Row Count |
| :--- | :--- | :---: | :---: | :---: |
| **1-Day Parquet** | `data/processed/nifty/1d/nifty50_1d.parquet` | 20,040 | `8ed0deb2722cabc8...8c73bc4e` | 661 |
| **5-Min Parquet** | `data/processed/nifty/5m/nifty50_5m.parquet` | 511,821 | `02984db58b9697e1...d1ed684b` | 20,237 |
| **1-Min Parquet** | `data/processed/nifty/1m/nifty50_1m.parquet` | 1,935,318 | `a0fea90ac0e7469e...a22db524` | 92,981 |
| **DuckDB Store** | `data/processed/market_data.duckdb` | 2,633,728 | `b77f46d52766a4ff...0f54a87d` | 113,879 |
| **Original Excel** | `data/manual/Trading_Journal_Sep2025_Aug2026 REPORT.xlsx` | 17,439 | `97a9ad4dfb552061...e9694781` | 190 |
| **Manual Parquet** | `reports/manual_journal/manual_trades.parquet` | 8,960 | `f05ac38ddf450b2f...7749ae61` | 190 |
| **Backtest Parquet** | `reports/phase2d/backtest_trades.parquet` | 28,118 | `2083689b897e6039...55997e14` | 353 |

### 2.2 Mathematical Reconciliation of Candle Counts
The relationship between 92,981 1-minute candles and 20,237 5-minute candles was audited:
1. **5-Minute Dataset (20,237 bars):**
   - **Intraday Warmup Period (August 1, 2025 – August 31, 2025):** 19 trading days $\times$ 76 bars/day = **1,444 bars**.
   - **Target Evaluation Window (September 1, 2025 – August 31, 2026):** 248 trading days = **18,793 bars**.
   - Total 5m bars: $1,444 + 18,793 = \mathbf{20,237}$ bars.
2. **1-Minute Dataset (92,981 bars):**
   - Covers strictly the 248 trading days of the target evaluation window (September 1, 2025 – August 31, 2026).
   - Standard session length: 09:15:00 to 15:30:00 inclusive = **376 bars/day**.
   - **Daily Bar Count Breakdown:**
     - 243 normal trading days $\times$ 376 bars = 91,368 bars.
     - 1 Diwali Muhurat session (2025-10-21, 13:45–14:45 IST) = 61 bars.
     - 1 early-close session (2025-11-25, 09:15–15:29 IST) = 375 bars.
     - 2 post-market auction sessions (2026-08-03, 2026-08-04, 09:15–15:40 IST) = $2 \times 386 = 772$ bars.
     - 1 extended post-market session (2026-03-10, 09:15–15:59 IST) = 405 bars.
   - Total 1m bars: $91,368 + 61 + 375 + 772 + 405 = \mathbf{92,981}$ bars.
3. **Data Integrity Audit:**
   - **Duplicate Timestamps:** `0` (Zero across all timeframes).
   - **OHLC Violations (High < Low, High < Open, Low > Close, etc.):** `0` violations.
   - **Non-Positive Prices:** `0` violations.

---

## 3. STEP 2 — Fresh Re-Run of Strategy Backtests

All strategy engines were re-executed against the historical 5-minute bars with 200 DMA daily context and 0.50 pt slippage on entry and exit (1.00 pt round-trip).

- **Output Artifact:** `reports/audit/recomputed_trades.parquet` (353 rows, SHA-256: `6e61ba90e8a7894a...`)
- **Comparison Against Saved Parquet:** **0 differences** across all 353 trades, prices, exit reasons, and net points.
- **Config Hash:** `e5a6c11db891d4e7` (representing fixed parameters: slippage = 0.50 pt, EOD exit = 15:15 IST).

---

## 4. STEP 3 — Portfolio Aggregation & Concurrency Audit

### 4.1 Resolution of the "615 Method Trades vs 353 Portfolio Trades" Discrepancy
The earlier report displayed 615 trades across method rows but 353 combined portfolio trades. This audit explains and reconciles this difference:
- **615 Trades:** Represents the arithmetic sum of trades when each rulebook method is backtested in complete isolation:
  - IRF Method 1 (112) + Method 2 (127) + Method 3 (5) = 244 trades.
  - ORB Method 1 (61) + Method 2 (0) + Method 3 (0) = 61 trades.
  - PDR Method 1 = 77 trades.
  - PDS Method 1 (74) + Method 2 (70) + Method 3 (60) = 204 trades.
  - RF Canonical = 29 trades.
  - Total isolated method trades: $244 + 61 + 77 + 204 + 29 = \mathbf{615}$ trades.
- **353 Trades:** Represents the selection of **one canonical baseline method per strategy family**:
  - IRF Method 1 (112) + ORB Method 1 (61) + PDR Method 1 (77) + PDS Method 1 (74) + RF Canonical (29) = **353 trades**.

### 4.2 Portfolio Concurrency & Overlap Analysis (`reports/audit/portfolio_overlap_analysis.csv`)
In a real execution environment with finite capital or margin, strategies cannot simply be summed without arbitration:
- **Trading Days with Multiple Strategy Signals:** **111 days** out of 219 active trading days (50.7% multi-signal days).
- **Total Overlapping Trade Pairs:** **54 instances** where a second strategy entered while a prior strategy's position was still active.
- **Opposing Directional Conflicts:** **22 instances** where one strategy held a BUY position while another simultaneously entered a SELL position (e.g. ORB Long vs IRF Short).

### 4.3 Deterministic Portfolio Arbitration Results (`reports/audit/recomputed_strategy_metrics.csv`)

| Portfolio Architecture Policy | Trade Count | Win Rate | Gross Pts | Slippage | Net Pts | Profit Factor | Max DD | Expectancy |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Policy A: Unarbitrated Multi-Account**<br>*(Theoretical sum; concurrent overlaps allowed)* | 353 | 42.2% | +2,046.7 | 353.0 | **+1,693.65** | 1.24 | 666.6 | +4.80 pts |
| **Policy B: FIFO Single-Position Execution**<br>*(Strict 1-position limit; capacity = 1)* | **302** | **42.4%** | **+1,910.9** | **302.0** | **+1,608.90** | **1.27** | **635.3** | **+5.33 pts** |
| **Policy C: Priority Single-Position**<br>*(Priority: PDS > ORB > IRF > PDR > RF)* | 302 | 41.4% | +1,718.8 | 302.0 | **+1,416.75** | 1.21 | 642.5 | +4.69 pts |

**Key Auditor Insight:** Under realistic FIFO execution (where the first strategy to signal locks the single available intraday slot), the portfolio executes **302 trades** and retains **+1,608.90 net points** (95.0% of theoretical unarbitrated profits), while reducing maximum drawdown from 666.6 pts to 635.3 pts.

---

## 5. STEP 4 — Manual Journal Reconciliation Audit

### 5.1 Reimport Verification (`reports/audit/manual_matching_audit.csv`)
- **Total Records:** 190 rows in detailed sheet (`Trading Journal`).
- **Summary Discrepancy:** Confirmed that Summary tab formula hardcodes 189 rows, omitting Trade #190 on August 31, 2026.
- **Saturday Non-Trading Days Confirmed:**
  - `2025-10-11` (Saturday): Trade #23 (ORB, SL)
  - `2026-02-14` (Saturday): Trade #82 (IRF, BE)
  - `2026-08-15` (Saturday, Independence Day): Trade #181 (ORB, Target)
  - `2026-08-22` (Saturday): Trade #185 (ORB, SL)
  - `2026-08-29` (Saturday): Trade #189 (ORB, Target)
  - *Audit Verdict:* Cross-referenced with the official NSE holiday calendar. NSE was closed on all five dates. These entries reflect practice/weekend paper trades or clerical date misentries. They are correctly excluded from exchange-traded evaluations.
- **Eligible Exchange Trading Trades:** **185 trades**.

### 5.2 ORB 09:15 Entry Timestamp Investigation
- All 89 manual ORB records specify `09:15` as the entry time.
- The canonical ORB rulebook explicitly mandates waiting for the first 5-minute candle to complete (`09:15 to 09:20`) and executing at `09:20 AM`.
- *Finding:* `09:15` in the manual journal represents the **candle open reference timestamp** (the start time of the initial range candle), whereas `09:20` is the order execution timestamp.
- The 5-minute difference is a logging convention artifact, not an execution mismatch.

---

## 6. STEP 5 — Outcome Agreement Audit & Discrepancy Reconciliation

### 6.1 Correction of the Outcome Disagreement Claim
The earlier Phase 2D report stated: *"Only 4 trades out of 91 were classified as DISAGREED"*.  
**Auditor Correction:** This statement is **REJECTED AS INCOMPLETE**. There are in fact **6 binary outcome disagreements** and **20 discretionary Break-Even differences**, totaling **26 outcome differences**.

### 6.2 Detailed Breakdown of All 26 Outcome Differences (`reports/audit/outcome_disagreements.csv`)

#### Category 1: Binary Disagreements — Manual Target vs Automated Stop-Loss (4 Trades)
1. **Trade #18 (2025-10-06, PDR):** Manual recorded Target; Auto hit Stop-Loss (-36.45 pts, $\Delta = 10\text{ min}$).
2. **Trade #34 (2025-11-07, ORB):** Manual recorded Target; Auto hit Stop-Loss (-34.95 pts).
3. **Trade #96 (2026-03-16, IRF):** Manual recorded Target; Auto hit Stop-Loss (-36.30 pts).
4. **Trade #148 (2026-06-24, IRF):** Manual recorded Target; Auto hit Stop-Loss (-36.55 pts).

#### Category 2: Binary Disagreements — Manual Stop-Loss vs Automated EOD Exit (2 Trades)
5. **Trade #6 (2025-09-09, RF):** Manual recorded SL; Auto held until 15:15 EOD square-off (+22.90 pts).
6. **Trade #184 (2026-08-21, RF):** Manual recorded SL; Auto held until 15:15 EOD square-off (+13.00 pts).

#### Category 3: Discretionary Break-Even (BE) Differences (20 Trades)
- 20 manual trades exited at BE (discretionary trailing stop to entry).
- The mechanical engine (which strictly follows fixed rulebook TP/SL) exited these as:
  - **Stop-Loss Hit:** 12 trades
  - **Target Hit:** 7 trades
  - **EOD Square-Off:** 1 trade

### 6.3 Recalculated Outcome Agreement Metrics
- **Binary Agreement Rate:** **91.5%** ($65 / 71$ evaluated Target/SL trades).
  - Target-Specific Agreement: $39 / 43 = \mathbf{90.7\%}$.
  - Stop-Loss-Specific Agreement: $26 / 28 = \mathbf{92.9\%}$.
  - False Target Rate: **0.0%** ($0 / 28$ manual SL trades hit auto target).
- **ORB Strategy Outcome Agreement:** **97.2%** ($35 / 36$ binary trades agreed).

---

## 7. STEP 6 — Trading Rule Validation & Rulebook Ambiguities

| Strategy | Rulebook Specification | Current Implementation | Audit Validation & Status | Human Review Needed? |
| :--- | :--- | :--- | :--- | :---: |
| **IRF Method 1** | Breakout & Immediate Failure | C1 closes > IRH; C2 closes < IRH | **VERIFIED CANONICAL** | No |
| **IRF Method 2** | Extension & Reversal | Closes > IRH for >1 candle; breaks pullback support | **VERIFIED CANONICAL** | No |
| **IRF Method 3** | Liquidity Sweep | 60-70% wick above IRH + compulsory support break | **VERIFIED CANONICAL** (Line 193 enforces support break) | No |
| **ORB Method 1** | PDH/PDL Breakout | 09:20 candle close > PDH / < PDL + 200 EMA + body filter | **VERIFIED CANONICAL** | No |
| **ORB Method 2** | Inner Level Breakout | Breakout of inner range consolidation | Requires manual inner markers | **YES (Inner lines)** |
| **ORB Method 3** | 200 DMA Gap | Gap through daily 200 DMA from below/above | **VERIFIED CANONICAL** (0 setups in 2025-2026) | No |
| **PDR Method 1** | Resistance Retest Bounce | Gap-up + pullback to PDH resistance zone | **VERIFIED CANONICAL** (Zone derived from high body) | No |
| **PDS Method 1** | Strong Red Candle | Gap-down + retest of PDL support zone + strong red candle | **VERIFIED CANONICAL** | No |
| **PDS Method 2** | Red Pin Bar HPR | Gap-down + zone retest + HPR red pin bar | **VERIFIED CANONICAL** | No |
| **PDS Method 3** | Small Support Fallback | Fallback breakdown of minor support near zone | **VERIFIED CANONICAL** | No |
| **RF Strategy** | Dip-and-Reclaim Long | Uptrend + gap-up in range + breakdown < RFL + reclaim | **TARGET IS EXPERIMENTAL** (PDF page 8 marks target 'OPEN') | **YES (Target rule)** |

---

## 8. STEP 7 — Independent Performance Metrics by Strategy

All metrics calculated directly from verified trade records (`reports/audit/recomputed_strategy_metrics.csv`):

| Strategy | Method | Trades | Win Rate | Gross Pts | Slippage | Net Pts | Profit Factor | Max DD | Expectancy | Avg Hold |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **PDS** | **Method 1 (Strong Red)** | **74** | **52.7%** | +1,333.8 | 74.0 | **+1,259.8** | **1.94** | **136.8** | **+17.0 pts** | 72.8 min |
| **PDS** | Method 2 (HPR Pin Bar) | 70 | 51.4% | +1,181.0 | 70.0 | **+1,111.0** | 1.90 | 207.2 | +15.9 pts | 71.1 min |
| **PDS** | Method 3 (Support Break) | 60 | 45.0% | +471.4 | 60.0 | **+411.4** | 1.33 | 246.4 | +6.9 pts | 78.6 min |
| **ORB** | **Method 1 (PDH/PDL)** | **61** | **44.3%** | +619.1 | 61.0 | **+558.1** | **1.53** | **250.5** | **+9.2 pts** | 48.0 min |
| **IRF** | Method 2 (Extension) | 127 | 42.5% | +531.7 | 127.0 | **+404.7** | 1.16 | 321.0 | +3.2 pts | 46.6 min |
| **PDR** | **Method 1 (Retest Bounce)** | **77** | **39.0%** | +193.1 | 77.0 | **+116.1** | **1.07** | **335.2** | **+1.5 pts** | 77.1 min |
| **IRF** | Method 3 (Sweep) | 5 | 60.0% | +83.8 | 5.0 | **+78.8** | 2.51 | 52.0 | +15.8 pts | 33.0 min |
| **IRF** | **Method 1 (Immediate)** | **112** | **37.5%** | +38.0 | 112.0 | **-74.1** | 0.97 | 408.0 | -0.7 pts | 46.9 min |
| **RF** | **Canonical (Experimental)** | **29** | **37.9%** | -137.3 | 29.0 | **-166.3** | 0.74 | 418.7 | -5.7 pts | 94.7 min |

*Important Auditor Disclaimer:* These results represent underlying index movements. They do not constitute NIFTY option premium returns. Options execution involves non-linear delta, implied volatility crush, theta decay, and bid-ask spreads that will compress these index points.

---

## 9. STEP 8 — Options Data Limitations Audit

1. **Active Option Contracts:** Audited 5paisa Scrip Master: 4,062 active NIFTY CE/PE contracts verified.
2. **Expired Options Candle Availability:** Audited historical REST V2 endpoint with expired contract identifiers: returned `[]` (zero candles).
3. **Audit Determination:** 5paisa does NOT provide historical candles for expired options contracts.
4. **Audit Status:** Historical Options Backtest is strictly **BLOCKED**. No synthetic Black-Scholes substitution was permitted. Live market data subscription and forward paper-trading capabilities remain active for Phase 3/4.

---

## 10. Audit Artifacts Produced

The following independent audit artifacts were generated and verified on disk:

1. [`docs/phase2d_independent_audit.md`](file:///Users/syedaskari/project/5paisa-options/docs/phase2d_independent_audit.md) — This formal audit report.
2. [`reports/audit/data_integrity.csv`](file:///Users/syedaskari/project/5paisa-options/reports/audit/data_integrity.csv) — File hashes, row counts, and session integrity.
3. [`reports/audit/recomputed_trades.parquet`](file:///Users/syedaskari/project/5paisa-options/reports/audit/recomputed_trades.parquet) — 353 fresh re-run baseline trades.
4. [`reports/audit/recomputed_strategy_metrics.csv`](file:///Users/syedaskari/project/5paisa-options/reports/audit/recomputed_strategy_metrics.csv) — Method-isolated and arbitrated portfolio metrics.
5. [`reports/audit/portfolio_overlap_analysis.csv`](file:///Users/syedaskari/project/5paisa-options/reports/audit/portfolio_overlap_analysis.csv) — 54 concurrent position conflicts and directional classifications.
6. [`reports/audit/manual_matching_audit.csv`](file:///Users/syedaskari/project/5paisa-options/reports/audit/manual_matching_audit.csv) — 190 manual journal audit with timing explanations.
7. [`reports/audit/outcome_disagreements.csv`](file:///Users/syedaskari/project/5paisa-options/reports/audit/outcome_disagreements.csv) — All 26 outcome discrepancies detailed.

---

## 11. Final Auditor Verdict: Readiness for Walk-Forward Optimization

**Readiness Status:** **CONDITIONAL APPROVAL (READY FOR IN-SAMPLE WALK-FORWARD ONLY WITH DISCLOSED CONSTRAINTS)**

The underlying quantitative research platform has proven itself mathematically sound, deterministic, and reproducible:
- Market data provenance is genuine and free of lookahead bias.
- Backtest engine execution reproduces to the cent.
- Reconciliation against human trading confirms high mechanical fidelity (91.5% binary outcome agreement).

### Required Human Decisions Before Phase 3 (Optimization):
1. **Approve Portfolio Arbitration Policy:** Adopt Policy B (FIFO Single-Position Execution, 302 trades, +1,608.90 net pts) as the primary execution baseline over the theoretical multi-account sum (+1,693.65 net pts).
2. **Resolve RF Target Specification:** Formally approve a mechanical target rule for RF (e.g. 1:1.5 or 1:2 R:R) or exclude RF from the core portfolio.
3. **Formalize Break-Even (BE) Policy:** Determine whether to incorporate a trailing stop rule (+30 pts excursion moves stop to BE) into the automated engine to mirror the 20 manual BE trades.
4. **Acknowledge Options Backtest Limitation:** Confirm that strategy optimization will optimize underlying entry/exit triggers, with option execution validated in forward paper-trading mode.
