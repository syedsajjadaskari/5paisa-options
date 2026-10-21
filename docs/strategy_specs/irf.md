# Strategy Specification: Initial Range Failure (IRF)

**Source Document:** `IRF_Strategy_Guide Updated.pdf` (17 pages)  
**Status:** Canonical Baseline Specification  
**Classification:** Intraday Short-Selling Price-Action Framework  

---

## 1. Executive Summary & Parameters

| Parameter | Specification | Source Reference |
| :--- | :--- | :--- |
| **Market** | National Stock Exchange of India (NSE) | Page 2 |
| **Instrument** | NIFTY 50 Index (Intraday) | Page 2 |
| **Session Open** | 09:15 AM IST | Page 2 |
| **Initial Range Candle** | 09:15–09:20 AM IST (First 5-minute candle) | Page 2 |
| **Chart Timeframe** | 5-Minute OHLC | Page 2 |
| **Trading Direction** | Short Trades ONLY | Page 2 |
| **Initial Range (IR)** | High (IRH) and Low (IRL) of 09:15–09:20 candle, wicks included | Pages 2, 4 |
| **Target ($T$)** | Fixed ₹60 (60 index points) from entry | Pages 2, 14 |
| **Stop-Loss ($SL$)** | Fixed ₹35 or ₹40, determined by the most recent green candle | Pages 2, 10, 14 |
| **Risk-Reward** | ~1:1.5 to 1:1.71 | Derived from 60 / (35 or 40) |

---

## 2. Core Definitions

1. **Initial Range High (IRH):** Highest price reached during 09:15–09:20 AM IST (upper wick included).
2. **Initial Range Low (IRL):** Lowest price reached during 09:15–09:20 AM IST (lower wick included).
3. **Close Above IRH:** A 5-minute candle whose close is strictly greater than IRH. A wick piercing IRH with a close $\le$ IRH does *not* qualify.
4. **Close Back Inside Range:** A candle whose closing price is strictly below IRH ($Close < IRH$).
5. **Opposite Candle:** For this short strategy, the most recent bullish (green) candle ($Close > Open$).

---

## 3. Entry Methods

### Method 1: Breakout and Immediate Failure
* **Setup Sequence:**
  1. Candle $C_1$ (breakout candle at or after 09:20 AM) closes above IRH: $Close_{C_1} > IRH$.
  2. The very next candle $C_2$ closes back inside the initial range: $Close_{C_2} < IRH$.
* **Entry Trigger:** Enter short at the close of candle $C_2$.
* **Disqualifications:**
  - Wick-only breakout on $C_1$ ($High_{C_1} > IRH$, but $Close_{C_1} \le IRH$).
  - Candle $C_2$ wicks inside the range but closes $\ge IRH$.
  - Entering intra-candle before $C_2$ closes.
  - An intervening candle between $C_1$ and $C_2$ (documented as canonical Method 1).

### Method 2: Extended Upward Move and Reversal
* **Setup Sequence:**
  1. Price breaks and closes above IRH.
  2. Price extends further upward beyond the initial range ($> 1$ candle above IRH).
  3. A red candle forms during or at the top of the extension.
  4. Confirmation occurs via either:
     - **Confirmation A (Support Break):** A candle closes below a recent pullback support formed during the extension.
     - **Confirmation B (Resistance Break):** Price stalls at a recent resistance, attempts another push above it, fails, and a red candle closes back below that resistance level.
* **Entry Trigger:** Enter short at the close of the confirming candle.

### Method 3: Advanced IRF / Liquidity Sweep
* **Setup Sequence:**
  1. Small candles compress inside the initial range (between IRL and IRH).
  2. A red sweep candle spikes above IRH with a prominent upper wick.
  3. **Wick Criterion:** Approximately 60–70% of the upper wick lies outside (above) IRH:
     $$\text{Upper Wick} = High - Open$$
     $$\text{Portion Above IRH} = High - IRH$$
     $$\text{Share Outside} = \frac{High - IRH}{High - Open} \approx 0.60 \text{ to } 0.70$$
  4. **Compulsory Confirmation:** The sweep candle must close below recent support.
* **Entry Trigger:** Enter short on the close of the sweep candle below recent support.
* **Disqualification:** A wick sweep without breaking and closing below recent support is strictly disqualified.

---

## 4. Risk Management & Stop-Loss Logic

* **Target:** Fixed 60 points below entry price ($Target = Entry - 60$).
* **Stop-Loss Selection:**
  - Locate the most recent green candle prior to entry.
  - If the distance from entry to the high/close of the green candle is $< 35$ points $\rightarrow$ Use $SL = 35$ points ($SL = Entry + 35$).
  - If the distance from entry to the high/close of the green candle is $> 35$ points $\rightarrow$ Use $SL = 40$ points ($SL = Entry + 40$).
* **Trade Management:** Never widen the stop-loss once placed. Exit at EOD (15:15 IST) if neither target nor stop-loss is hit.
