# Strategy Specification: Previous Day Resistance (PDR)

**Source Document:** `PDR_Strategy_Guide.pdf` (4 pages)  
**Status:** Canonical Baseline Specification  
**Classification:** Intraday Gap-Up Support Retest (Long Playbook)  

---

## 1. Executive Summary & Parameters

| Parameter | Specification | Source Reference |
| :--- | :--- | :--- |
| **Market** | National Stock Exchange of India (NSE) | Page 1 |
| **Instrument** | NIFTY 50 Index (Intraday) | Page 1 |
| **Concept** | Yesterday's resistance zone is expected to act as support after a gap-up open | Page 1 |
| **Trading Bias** | Buy (Long) ONLY | Page 1 |
| **Chart Timeframe** | 5-Minute OHLC | Page 1 |
| **Zone Definition** | Rectangle covering candle body to upper wick of previous day's high/reversal | Pages 1, 2 |
| **Next-Day Condition** | Market must open Gap Up ($Open_{day} > Close_{prev}$) | Pages 1, 2 |
| **Target ($T$)** | Fixed 70 points from entry | Pages 1, 2 |
| **Stop-Loss ($SL$)** | Fixed 35 or 40 points, depending on the situation | Pages 1, 2 |
| **Risk-Reward** | 2.0 : 1 (at 35 pt SL) or 1.75 : 1 (at 40 pt SL) | Page 2 |

---

## 2. Sequence of Execution

```
[Previous Day High & Reversal] 
       │
       ▼
[Draw Resistance Zone (Body to Upper Wick)] 
       │
       ▼
[Next Day Opens Gap-Up] 
       │
       ▼
[Price Pulls Back toward Marked Zone] 
       │
       ▼
[Zone Tested (Wick, Body, Inside, or Proximity)] 
       │
       ▼
[Bullish Confirmation: Green Candle Forms] 
       │
       ▼
[Long Entry at Close of Green Candle] 
       │
       ▼
[SL: 35 or 40 pts | Target: 70 pts]
```

---

## 3. Zone Marking Rules

1. Identify the high from the previous session where price reached a peak and reversed.
2. Upper boundary of zone: Highest point of the upper wick.
3. Lower boundary of zone: Relevant candle-body level (open or close).
4. Draw horizontal rectangle and extend across the next session.

---

## 4. Retest Interaction & Entry Trigger

1. **Gap-Up Gatekeeper:** The session must open gap-up. If price opens flat or gap-down, PDR is invalidated. Do not buy at open.
2. **Zone Interaction Types:**
   - Wick touches zone.
   - Candle body touches zone.
   - Price enters inside the rectangle.
   - Price comes very close to the zone (documented proximity threshold).
3. **Trigger:** After the zone is tested, wait for a bullish (green) candle to form. Enter Long on the close of that green candle.
4. **Risk Parameters:**
   - Fixed Target: 70 points ($Target = Entry + 70$).
   - Stop-Loss: 35 points ($SL = Entry - 35$) or 40 points ($SL = Entry - 40$).
