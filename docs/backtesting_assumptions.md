# Backtesting Engine Assumptions, Execution Mechanics & Cost Model

**Document Version:** 1.0.0  
**Phase:** Phase 2C Complete Strategy Validation  
**Date:** October 21, 2026  
**Repository:** `5paisa-options`

---

## 1. Executive Summary

This document specifies the institutional execution mechanics, anti-lookahead safeguards, conflict resolution rules, and statutory Indian derivatives transaction cost schedule implemented in `UnderlyingBacktestEngine` and `OptionsBacktestEngine`.

The engine adheres to zero-lookahead, realistic next-bar execution, conservative fill assumptions, and strict Data Availability Gating.

---

## 2. Order Execution Mechanics & Anti-Lookahead Safeguards

### 2.1 Next-Bar Open Execution
- **Signal Generation:** Strategy rules evaluate strictly after candle $T$ completes at time $T_{\text{close}}$ (e.g. 09:20:00 IST for the 09:15–09:20 bar).
- **Order Emission:** If a signal is valid, it is enqueued as `pending_signal`.
- **Order Execution:** The fill occurs at the **Open price of the subsequent bar $T+1$** (at 09:20:00 IST open):
  $$\text{Fill Price}_{\text{Buy}} = \text{Open}_{T+1} + \text{Slippage}$$
  $$\text{Fill Price}_{\text{Sell}} = \text{Open}_{T+1} - \text{Slippage}$$
- Under no circumstances does the engine fill orders at bar $T$'s Close price or within bar $T$'s high/low.

### 2.2 Point-in-Time Market Context (Anti-Lookahead)
- **Reference Levels:** Previous Day High (`PDH`), Low (`PDL`), Close (`PDC`), Open (`PDO`), and Zones (`pd_res_body`, `pd_supp_body`) are derived solely from completed daily sessions prior to the trade date.
- **200 EMA (5-minute):** Evaluated recursively bar-by-bar using closed prices. Requires a mandatory warm-up of 200 intraday candles. Signals are blocked until warm-up is satisfied.
- **200 DMA (Daily):** Evaluated using completed daily closes only. Requires 200 prior trading days. Signals requiring 200 DMA (such as ORB Method 3) emit `REJECTED_INSUFFICIENT_DMA_WARMUP` if the daily count is $< 200$.

### 2.3 Intrabar Gap-Through Handling
When holding an active position across candle boundaries:
- **Gap Against Position (Through Stop-Loss):**
  If bar Open gaps beyond the Stop-Loss:
  $$\text{Exit Price}_{\text{Long}} = \min(\text{Stop Loss}, \text{Bar Open}) - \text{Slippage}$$
  $$\text{Exit Price}_{\text{Short}} = \max(\text{Stop Loss}, \text{Bar Open}) + \text{Slippage}$$
  The position is penalized with the full gap price rather than the theoretical stop price.
- **Gap In Favor of Position (Through Target):**
  If bar Open gaps beyond the Profit Target:
  $$\text{Exit Price} = \text{Bar Open}$$
  The trade benefits from favorable opening prints.

### 2.4 Same-Bar Conflict Resolution (Conservative Tie-Breaking)
If a single 5-minute candle spans both the Stop-Loss and the Profit Target (Extreme Volatility / Whipsaw):
- **Conservative Institutional Assumption:** The Stop-Loss is assumed to have been triggered **FIRST**.
- **Audit Tagging:** The trade is recorded as a loss and tagged with `is_uncertain_fill = True`.

### 2.5 Mandatory End-of-Day (EOD) Square-Off
- All open intraday positions are forcefully closed at **15:15 IST** at the bar's open/close, ensuring zero overnight exposure across gap openings.

---

## 3. Statutory Indian Derivatives Transaction Cost Schedule

The cost calculator (`IndianOptionsCostCalculator` in `src/trading_platform/backtesting/indian_options_costs.py`) reflects the exact statutory tax and fee structure of the National Stock Exchange of India (NSE), incorporating the **Union Budget 2024 amendments effective October 1, 2024**:

| Cost Component | Regulatory / Broker Authority | Rate (Pre-Oct 1, 2024) | Rate (Post-Oct 1, 2024 - Budget 2024) | Calculation Basis |
| :--- | :--- | :---: | :---: | :--- |
| **Brokerage** | 5paisa / Discount Broker | ₹20 per executed order | ₹20 per executed order | Fixed fee per trade leg |
| **Securities Transaction Tax (STT)** | Central Board of Direct Taxes (CBDT) | **0.0625%** | **0.100% (₹10,000 / crore)** | Sell side option premium turnover |
| **Exchange Turnover Charges** | National Stock Exchange (NSE) | 0.050% (₹5,000 / crore) | 0.050% (₹5,000 / crore) | Total premium turnover (Buy + Sell) |
| **SEBI Turnover Fee** | SEBI | ₹10 per crore (0.0001%) | ₹10 per crore (0.0001%) | Total premium turnover (Buy + Sell) |
| **Stamp Duty** | State Government Revenue | 0.003% (₹300 / crore) | 0.003% (₹300 / crore) | Buy side premium turnover |
| **Goods & Services Tax (GST)** | Central & State GST (18%) | 18% | 18% | Applied on (Brokerage + Exchange + SEBI) |

---

## 4. Dual Engine Architecture & Data Availability Gate

### 4.1 UnderlyingBacktestEngine
- Evaluates strategy logic in pure NIFTY index points.
- Measures raw price-action edge without option premium noise, Greeks, or contract liquidity decay.
- **Classification:** Research output only (non-executable directly on index).

### 4.2 OptionsBacktestEngine & The Data Availability Gate
- Connects verified underlying signals to genuine historical option chain records.
- Implements:
  - Dynamic ATM / ITM moneyness contract selection.
  - Historical contract-specific lot sizes (e.g. 75, 50, 65, 25).
  - Three premium exit modes (Underlying Trigger, Premium Stop/Target, Trailing SL).
- **The Data Availability Gate Rule:**
  > If genuine historical options candle data is absent from the contract archive, the engine **strictly halts execution** and raises `OPTIONS BACKTEST BLOCKED — HISTORICAL OPTION DATA UNAVAILABLE`.
  > It will **NEVER** fabricate option prices using Black-Scholes formulas or synthetic drift models.
