# Strategy Specification: Opening Range Breakout (ORB)

**Source Document:** `ORB_Strategy_Rulebook-2.pdf` (13 pages)  
**Status:** Canonical Baseline Specification  
**Classification:** Intraday Trend & Breakout / Breakdown Playbook  

---

## 1. Executive Summary & Parameters

| Parameter | Specification | Source Reference |
| :--- | :--- | :--- |
| **Market** | National Stock Exchange of India (NSE) | Page 1 |
| **Instrument** | NIFTY 50 Index (Intraday) | Page 1 |
| **Decision Time** | Exactly 09:20 AM IST (after first 5-min candle completes) | Pages 1, 2 |
| **First Candle** | 09:15–09:20 AM IST | Page 2 |
| **Chart Timeframe** | 5-Minute OHLC | Page 1 |
| **Trading Direction** | Long (bullish breakout in uptrend) & Short (bearish breakdown in downtrend) | Pages 1, 2 |
| **Target ($T$)** | 60 points from entry | Pages 1, 11 |
| **Stop-Loss ($SL$)** | 30 points from entry | Pages 1, 11 |
| **Risk-Reward** | 1:2 ($30 : 60$) | Pages 1, 11 |

---

## 2. Core Setups / Entry Methods

### Method 1: Previous-Day Major Level Breakout
* **Long Rules:**
  1. Market is in an established uptrend (price above 200 EMA + bullish market structure).
  2. First 5-minute candle (09:15–09:20 AM) breaks above and closes above previous day's resistance (PDH / major high).
  3. Candle quality: Strong green body, close near high, minimal upper-wick rejection.
  4. Enter Long at 09:20 AM close.
* **Short Rules:**
  1. Market is in an established downtrend (price below 200 EMA + bearish market structure).
  2. First 5-minute candle breaks below and closes below previous day's support (PDL / major low).
  3. Candle quality: Strong red body, close near low, minimal lower-wick rejection.
  4. Enter Short at 09:20 AM close.

### Method 2: Trendline & Inner Resistance / Support Breakout
* **Context:** Used when major previous-day level sits further away, but a well-defined consolidation or swing level exists inside the previous day's range supported by a trendline.
* **Long Rules:**
  1. Established uptrend, rising trendline connecting recent swing lows.
  2. First candle breaks and closes above the inner resistance inside yesterday's range.
  3. Enter Long at 09:20 AM close (even if major outer resistance remains unbroken).
* **Short Rules:**
  1. Established downtrend, falling trendline connecting recent swing highs.
  2. First candle breaks and closes below inner support inside yesterday's range.
  3. Enter Short at 09:20 AM close.

### Special Setup (Method 3): Gap Above / Below 200 DMA / EMA
* **Context:** Exception to the prior established-trend requirement.
* **Long Rules:**
  1. Market was below 200 DMA, but opens with a gap-up above the 200 DMA.
  2. First 5-minute candle closes above the 200 DMA.
  3. Strong green body or bullish pin bar.
  4. Enter Long at 09:20 AM close.
* **Short Rules:**
  1. Market was above 200 DMA, but opens with a gap-down below the 200 DMA.
  2. First 5-minute candle closes below the 200 DMA.
  3. Strong red body or bearish pin bar.
  4. Enter Short at 09:20 AM close.

---

## 3. Candle Quality & Rejection Filter

* **Preferred:** Large body in trade direction, closing within top 20% (long) or bottom 20% (short) of candle range, minimal opposing wick.
* **Conditional:** Pin bar with long tail rejecting opposing prices, closing favorably relative to the key level.
* **Rejected:**
  - For Long: Long upper wick showing rejection of higher prices by sellers.
  - For Short: Long lower wick showing rejection of lower prices by buyers.

---

## 4. Execution & Risk Parameters

* **Entry:** Market execution immediately at 09:20 AM IST bar close.
* **Long:** $SL = Entry - 30$, $Target = Entry + 60$.
* **Short:** $SL = Entry + 30$, $Target = Entry - 60$.
* **Time Exit:** 15:15 IST intraday squared-off.
