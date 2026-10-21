# Strategy Implementation & Rulebook Reconciliation Matrix

**Document Version:** 1.0.0  
**Phase:** Phase 2C Complete Strategy Validation  
**Date:** October 21, 2026  
**Repository:** `5paisa-options`

---

## 1. Executive Summary

This matrix documents the method-by-method reconciliation between the five primary strategy rulebook PDFs and their programmatic Python implementations within `src/trading_platform/strategies/`.

Each strategy method has been categorized into one of five validation stages:
1. `CODE_TESTED`: Method written in code, passes automated unit/integration tests with synthetic fixtures.
2. `RULE_VALIDATED`: Method logic strictly audited and reconciled against the PDF rulebook specifications.
3. `DATA_VALIDATED`: Executed on genuine, multi-year, tick/candle data from verified exchange feeds. *(Pending live broker data)*
4. `BACKTEST_COMPLETED`: Underlying index and option premium backtests executed over multiple market regimes. *(Pending live options data)*
5. `OPTIMIZATION_READY`: Ready for parameter exploration, sensitivity testing, and Walk-Forward optimization.

---

## 2. Comprehensive Method-by-Method Audit Table

| Strategy | Method ID & Rulebook Name | PDF Pages | Entry Trigger Logic | Stop-Loss Rules | Profit Target | Filters & Warmup | Rejection Conditions | Validation Status | Test Reference |
| :--- | :--- | :---: | :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **IRF** | `IRF_METHOD_1_IMMEDIATE_FAILURE` (Breakout & Immediate Failure) | pp. 5–6 | C1 closes strictly above IRH; immediate next candle C2 closes back inside range (< IRH). Short at C2 close. | Dynamic 35/40 pts based on distance to previous green candle (<=35 → 35, >35 → 40). | Fixed 60 pts below entry | Initial Range High (09:15 bar) established. Short ONLY. | C2 fails to close inside range; time >= 15:15 IST. | `RULE_VALIDATED` / `CODE_TESTED` | [test_irf_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_irf_methods.py#L30) |
| **IRF** | `IRF_METHOD_2_SUPPORT_BREAK` (Extension & Support Break) | pp. 7–8 | C1 closes > IRH, extends upward for >= 2 bars. Red candle closes below recent pullback support. Short at close. | Dynamic 35/40 pts based on distance to prior green high. | Fixed 60 pts below entry | Uptrend extension above IRH; red confirmation candle. | Extension < 2 bars; red candle does not close below support; time >= 15:15. | `RULE_VALIDATED` / `CODE_TESTED` | [test_irf_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_irf_methods.py#L58) |
| **IRF** | `IRF_METHOD_2_RESISTANCE_BREAK` (Extension & Resistance Failure) | pp. 9–10 | Extension above IRH tests prior resistance; red candle closes below resistance level. Short at close. | Dynamic 35/40 pts. | Fixed 60 pts below entry | Resistance level identified during extension; red candle close. | Candle closes above resistance; non-red candle. | `RULE_VALIDATED` / `CODE_TESTED` | [test_irf_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_irf_methods.py#L58) |
| **IRF** | `IRF_METHOD_3_LIQUIDITY_SWEEP` (Advanced Sweep Tail) | pp. 11–12 | Small candles inside IR; red sweep candle spikes with 60–75% upper wick above IRH, closes below IRH. Short at close. | Dynamic 35/40 pts. | Fixed 60 pts below entry | Upper wick ratio >= 60% of total candle range outside IRH. | Upper wick < 60% above IRH; candle closes outside range; green body. | `RULE_VALIDATED` / `CODE_TESTED` | [test_irf_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_irf_methods.py#L86) |
| **ORB** | `ORB_METHOD_1_PDH_BREAKOUT` (PDH Level Breakout) | pp. 3–4 | First candle (09:15-09:20) closes > PDH with solid body (body >= 50% range) and upper wick <= 35%. Long at close. | 30 pts below entry | Fixed 60 pts above entry | Price >= 200 EMA (5-min intraday) for Long. | Upper wick > 35%; body < 50%; close < 200 EMA; decision time past 09:20 IST. | `RULE_VALIDATED` / `CODE_TESTED` | [test_orb_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_orb_methods.py#L30) |
| **ORB** | `ORB_METHOD_1_PDL_BREAKDOWN` (PDL Level Breakdown) | pp. 3–4 | First candle closes < PDL with solid body and lower wick <= 35%. Short at close. | 30 pts above entry | Fixed 60 pts below entry | Price <= 200 EMA (5-min intraday) for Short. | Lower wick > 35%; body < 50%; close > 200 EMA; decision time past 09:20 IST. | `RULE_VALIDATED` / `CODE_TESTED` | [test_orb_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_orb_methods.py#L30) |
| **ORB** | `ORB_METHOD_2_INNER_RESISTANCE_BREAKOUT` (Inner Level Breakout) | pp. 4–7 | First candle closes > Inner Resistance inside yesterday's range. Long at close. | 30 pts below entry | Fixed 60 pts above entry | Price >= 200 EMA. | Upper wick > 35%; close < Inner Resistance; close < 200 EMA. | `RULE_VALIDATED` / `CODE_TESTED` | [test_orb_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_orb_methods.py#L51) |
| **ORB** | `ORB_METHOD_2_INNER_SUPPORT_BREAKDOWN` (Inner Level Breakdown) | pp. 4–7 | First candle closes < Inner Support inside yesterday's range. Short at close. | 30 pts above entry | Fixed 60 pts below entry | Price <= 200 EMA. | Lower wick > 35%; close > Inner Support; close > 200 EMA. | `RULE_VALIDATED` / `CODE_TESTED` | [test_orb_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_orb_methods.py#L51) |
| **ORB** | `ORB_METHOD_3_GAP_ABOVE_200DMA` (Gap Above 200 DMA) | pp. 8–9 | Yesterday close < 200 DMA; Session opens and first candle closes > 200 DMA. Long at close. | 30 pts below entry | Fixed 60 pts above entry | Strictly requires **Daily 200 DMA** (minimum 200 completed daily closes). | DMA history < 200 days (`REJECTED_INSUFFICIENT_DMA_WARMUP`); candle closes < 200 DMA. | `RULE_VALIDATED` / `CODE_TESTED` | [test_orb_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_orb_methods.py#L84) |
| **ORB** | `ORB_METHOD_3_GAP_BELOW_200DMA` (Gap Below 200 DMA) | pp. 8–9 | Yesterday close > 200 DMA; Session opens and first candle closes < 200 DMA. Short at close. | 30 pts above entry | Fixed 60 pts below entry | Strictly requires **Daily 200 DMA**. | DMA history < 200 days; candle closes > 200 DMA. | `RULE_VALIDATED` / `CODE_TESTED` | [test_orb_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_orb_methods.py#L70) |
| **PDS** | `PDS_METHOD_1_STRONG_RED_CANDLE` (Gap-Down & Good Red Candle) | pp. 4–5 | Session opens gap down (< PDC); tests PDL support zone; forms decisive red candle (body >= 45%). Short at close. | 35 or 40 pts based on distance to recent green candle. | Fixed 70 pts below entry | Gap-Down verified at open; PDL zone tested. Short ONLY. | Session does not open gap-down; zone not touched; non-red candle. | `RULE_VALIDATED` / `CODE_TESTED` | [test_pds_pdr_rf_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_pds_pdr_rf_methods.py#L30) |
| **PDS** | `PDS_METHOD_2_HPR_PIN_BAR` (Gap-Down & Red HPR Pin Bar) | p. 5 | Zone retested; candle prints long upper rejection wick (>= 45% range) and closes red. Short at close. | 35 or 40 pts. | Fixed 70 pts below entry | Gap-down verified; upper wick >= 45%; candle must close red. | Upper wick < 45%; green body; no zone test. | `RULE_VALIDATED` / `CODE_TESTED` | [test_pds_pdr_rf_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_pds_pdr_rf_methods.py#L52) |
| **PDS** | `PDS_METHOD_3_SMALL_SUPPORT_BREAK` (Minor Support Breakdown Fallback) | p. 5 | Triggered after weak/indecisive candles consolidate near PDL zone; red candle breaks minor support low. Short at close. | 35 or 40 pts. | Fixed 70 pts below entry | Consolidation below/at zone; break of minor low. | No minor support established; close above minor support. | `RULE_VALIDATED` / `CODE_TESTED` | [test_pds_pdr_rf_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_pds_pdr_rf_methods.py#L30) |
| **PDR** | `PDR_RETEST_BOUNCE` (Gap-Up Pullback & Support Bounce) | pp. 1–6 | Session opens gap up (> PDC); price pulls back to test PDH resistance-turned-support zone; green bounce candle closes. Long at close. | 35 or 40 pts below entry. | Fixed 70 pts above entry | Gap-Up verified at open; PDH zone retested. Long ONLY. | Session does not open gap-up; PDH zone not touched; red candle. | `RULE_VALIDATED` / `CODE_TESTED` | [test_pds_pdr_rf_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_pds_pdr_rf_methods.py#L72) |
| **RF** | `RF_CANONICAL_RECLAIM_LONG` (Uptrend Dip & Reclaim) | pp. 1–8 | 4-step sequence: (1) Uptrend (price > 200 EMA); (2) Gap-up within yesterday's range (PDC < Open <= PDH); (3) Breakdown below 09:15 candle low (RFL); (4) Reclaim candle closes > RFL. Long at close. | 35 or 40 pts below entry. | Fixed 60 pts (Canonical interpretation; PDF marks 'OPEN') | Established uptrend; 5-min 200 EMA warmup; gap-up inside range. | Downtrend (price < 200 EMA); gap outside range; no reclaim; strict mode flags open target. | `RULE_VALIDATED` / `CODE_TESTED` | [test_pds_pdr_rf_methods.py](file:///Users/syedaskari/project/5paisa-options/tests/strategies/test_pds_pdr_rf_methods.py#L96) |

---

## 3. Detailed Audit of Documented Ambiguities & Resolutions

### 3.1 Reverse Failure (RF) Profit Target (`OPEN` in PDF)
- **Source Finding:** Page 8 of `RF strategy.pdf` lists the profit target as **"OPEN"**, leaving the exact exit discretion to the trader.
- **Engine Resolution:** The system implements a canonical symmetric target of **+60 points** (matching IRF / ORB risk-reward profiles).
- **Compliance Safeguard:** `RFStrategy` includes a `strict_rulebook_mode` configuration flag. When set to `True`, it flags the trade with `AMBIGUITY_TARGET_OPEN_IN_PDF` and requires explicit user parameter alignment prior to automated execution.

### 3.2 IRF / PDS Dynamic Stop-Loss (35 vs 40 points)
- **Source Finding:** The IRF and PDS PDFs specify: *"SL is 35 or 40 points based on the distance to the previous green/red candle."*
- **Engine Resolution:** The rule is formalized as:
  $$\text{Distance} = |\text{Entry Close} - \text{Anchor Candle Extreme}|$$
  $$\text{Stop Points} = \begin{cases} 35.0, & \text{if } \text{Distance} \le 35.0 \\ 40.0, & \text{if } \text{Distance} > 35.0 \end{cases}$$
  This avoids arbitrary discretization while adhering to the rulebook bounds.

### 3.3 ORB Method 3 Indicator Requirement: 200 DMA vs 200 EMA
- **Source Finding:** Page 8 of `ORB_Strategy_Guide.pdf` explicitly requires the **200 DMA** (Daily Moving Average based on daily closes), in contrast to Methods 1 & 2 which use the intraday 5-minute **200 EMA**.
- **Engine Resolution:** `ReferenceLevelCalculator` computes both:
  1. `ema_200`: Running 5-minute Exponential Moving Average (requires 200 intraday bars).
  2. `dma_200`: Daily Moving Average (requires 200 completed trading days).
  If fewer than 200 daily closes exist in the dataset, ORB Method 3 emits `REJECTED_INSUFFICIENT_DMA_WARMUP` and refuses to trade, preventing indicator lookahead or erroneous proxying.

---

## 4. Current Validation Status Summary

```
Total Rulebook Methods Audited:       15
Methods with Full Code Implementation: 15 (100%)
Passing Unit / Integration Tests:     86 (100%)
Status Classification:                RULE_VALIDATED / CODE_TESTED
Data Validation Status:               BLOCKED ON GENUINE HISTORICAL DATA
```
