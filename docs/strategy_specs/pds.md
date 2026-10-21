# Strategy Specification: Previous Day Support (PDS)

**Source Document:** `PDS strategy.pdf` (7 pages)  
**Status:** Canonical Baseline Specification  
**Classification:** Intraday Gap-Down Role-Reversal Short-Selling Playbook  

---

## 1. Executive Summary & Parameters

| Parameter | Specification | Source Reference |
| :--- | :--- | :--- |
| **Market** | National Stock Exchange of India (NSE) | Page 1 |
| **Instrument** | NIFTY 50 Index (Intraday) | Page 1 |
| **Concept** | Yesterday's support zone acts as resistance after a gap-down open | Pages 1, 2 |
| **Trading Bias** | Sell (Short) ONLY | Page 1 |
| **Chart Timeframe** | 5-Minute OHLC | Page 1 |
| **Zone Definition** | Rectangle covering candle body and lower wick of previous day's support low | Pages 2, 3 |
| **Next-Day Condition** | Market must open Gap Down ($Open_{day} < Close_{prev}$) | Pages 1, 2, 3 |
| **Target ($T$)** | Fixed 70 points from entry | Pages 1, 2, 5 |
| **Stop-Loss ($SL$)** | 35 or 40 points, based on recent green candle distance | Pages 1, 2, 5 |
| **Risk-Reward** | 2.0 : 1 (at 35 pt SL) or 1.75 : 1 (at 40 pt SL) | Page 1 |

---

## 2. Sequence of Execution

```
[Previous Day Low / Support Area] 
       │
       ▼
[Draw Support Zone (Lower Wick to Candle Body)] 
       │
       ▼
[Next Day Opens Gap-Down] 
       │
       ▼
[Price Rises from Below toward Marked Zone] 
       │
       ▼
[Zone Tested as Resistance (Wick, Body, Inside, or Proximity)] 
       │
       ▼
[Entry Confirmation: Method 1, 2, or 3] 
       │
       ▼
[Short Entry Taken] 
       │
       ▼
[SL: 35 or 40 pts | Target: 70 pts]
```

---

## 3. Zone Marking & Gap Requirement

1. **Marking the Zone:**
   - Locate the low where price found support and moved upward in the previous session.
   - Lower boundary: Lowest point of the lower wick.
   - Upper boundary: The candle body level (Open or Close).
   - Draw rectangle and project forward into the current session.
2. **Gap-Down Open Gatekeeper:**
   - Today's opening price must be strictly below yesterday's support zone.
   - Do NOT short immediately at the open. Wait for price to rally back upward and test the zone.

---

## 4. Entry Methods at the Resistance Zone

1. **Method 1: Good Red Candle**
   - The first red candle interacting with the zone exhibits a strong bearish body and close near low.
   - Enter Short at the close of this red candle.
2. **Method 2: Red Pin Bar with Higher Price Rejection (HPR)**
   - A pin bar where a long upper wick pushes into or above the zone and is decisively rejected.
   - The candle MUST close as a RED candle ($Close < Open$).
   - Enter Short at the close of this red pin bar.
3. **Method 3: Break of a Small Support (Fallback)**
   - Used when the first red candle at the zone is weak, small-bodied, or inconclusive.
   - Wait for price action to establish a minor intraday support level near the zone.
   - Enter Short when price breaks and closes below that minor support.

---

## 5. Stop-Loss & Target Rules

* **Target:** Fixed 70 points below entry price ($Target = Entry - 70$).
* **Stop-Loss Selection:**
  - Opposite candle = the recent green candle before the entry trigger.
  - If the green candle distance is $\le 35$ points $\rightarrow$ Use $SL = 35$ points.
  - If the green candle distance is $> 35$ points $\rightarrow$ Use $SL = 40$ points.
* **Stop-Loss Discipline:** Once placed, stop-loss is never moved or widened.
