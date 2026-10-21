# Execution Assumptions & Realism Framework

## 1. Core Principles of Execution Realism

A fatal flaw in retail quantitative research is optimistic backtesting. This platform enforces realistic, conservative institutional assumptions to ensure backtested performance translates faithfully to live markets.

---

## 2. Anti-Lookahead & Order Timing Rules

1. **No Bar-Close Fills:**
   - A signal generated at the close of candle $t$ (e.g., at 09:20:00 IST) cannot be filled at the close price of candle $t$. The close price is only known after the bar is completed.
   - **Canonical Rule:** All market orders are executed at the **Open price of the next candle ($t+1$)**, plus/minus slippage:
     $$\text{Fill Price}_{\text{BUY}} = Open_{t+1} + \text{Slippage}$$
     $$\text{Fill Price}_{\text{SELL}} = Open_{t+1} - \text{Slippage}$$
2. **Deterministic Intrabar Sequence & Same-Bar Conflict:**
   - In 5-minute candle backtesting, if a candle's $High \ge Target$ and $Low \le StopLoss$ within the same bar, the order of touch is inherently ambiguous without tick data.
   - **Conservative Rule (Worst-Case Assumption):** The simulator **always assumes the Stop-Loss is hit first**. Favorable target fills are never granted on ambiguous same-bar conflicts.
   - An `UNCERTAIN_FILL` audit flag is logged on such trades for sensitivity analysis.
3. **Gap Handling for SL / TP:**
   - If candle $t$ opens below the Stop-Loss price for a Long position ($Open_t < SL$), the exit is filled at the **Open price of candle $t$** (the gap price), NOT the more favorable Stop-Loss limit price.

---

## 3. Tradability & Instrument Mapping

1. **Index Spot is Non-Tradable:**
   - NIFTY 50 (`NIFTY 50`) is a cash index; it cannot be directly bought or sold.
   - Trading signals derived from NIFTY 50 index price action must map to an executable derivative instrument:
     - **NIFTY Futures:** Next monthly expiry contract.
     - **NIFTY Options:** At-The-Money (ATM) or In-The-Money (ITM) Call (CE) for Long signals, or Put (PE) for Short signals.
2. **Delta and Strike Selection:**
   - In Phase 1 research, strategies are benchmarked in NIFTY index points.
   - In derivative execution (Phase 3+), option strikes are dynamically mapped using ATM $\pm 50$ point grid and theoretical or historical option delta ($\approx 0.50$ for ATM).

---

## 4. Indian Market Transaction Costs & Taxes

All backtests calculate net performance after deducting statutory Indian market charges and broker commissions:

| Charge Type | Rate / Structure | Regulatory Authority |
| :--- | :--- | :--- |
| **Brokerage** | ₹20 per executed order (or flat ₹0 on certain plans) | 5paisa |
| **STT (Securities Transaction Tax)** | 0.0625% on sell side (Options premium) / 0.0125% (Futures) | Government of India |
| **Exchange Turnover Charges** | 0.0505% on premium (NSE Options) / 0.0019% (Futures) | NSE |
| **GST (Goods & Services Tax)** | 18% on (Brokerage + Exchange Charges) | Government of India |
| **SEBI Turnover Charges** | ₹10 per crore (0.0001%) | SEBI |
| **Stamp Duty** | 0.003% on buy side (Options) / 0.002% (Futures) | State / Central |
| **Slippage** | Configurable: Default 1 to 2 NIFTY index points (or ~0.50 pt on option premium) | Market microstructure |
