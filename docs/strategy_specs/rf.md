# Strategy Specification: Reverse Failure (RF)

**Source Document:** `RF strategy.pdf` (9 pages)  
**Status:** Canonical Baseline Specification  
**Classification:** Intraday Uptrend Dip-and-Reclaim Long Setup (Opposite of IRF)  

---

## 1. Executive Summary & Parameters

| Parameter | Specification | Source Reference |
| :--- | :--- | :--- |
| **Market** | National Stock Exchange of India (NSE) | Page 1 |
| **Instrument** | NIFTY 50 Index (Intraday) | Page 1 |
| **Trend Requirement** | Established Uptrend ONLY (Price above 200 EMA + sustained trend) | Pages 1, 2, 5 |
| **Trading Bias** | Buy (Long) ONLY | Pages 1, 2 |
| **Chart Timeframe** | 5-Minute OHLC | Page 1 |
| **Opening Requirement** | Gap-Up Open within Yesterday's Range ($Close_{prev} < Open_{today} \le High_{prev}$) | Pages 2, 6, 7 |
| **Reference Range** | First 5-minute candle (09:15–09:20 AM IST), wicks included | Pages 2, 3 |
| **Stop-Loss ($SL$)** | 35–40 points | Pages 1, 8 |
| **Target ($T$)** | Marked OPEN in rulebook (to be determined in research/testing) | Page 8 |
| **Exact Entry** | Marked OPEN in rulebook (canonical interpretation: close back inside range) | Pages 2, 4, 8 |

---

## 2. The Four-Step Activation Sequence

```
1. [Confirm Established Uptrend] (Sustained trend, price above 200 EMA)
              │
              ▼
2. [Gap-Up within Yesterday's Range] (Gap up, but Open <= Yesterday's High)
              │
              ▼
3. [Mark First 5-Min Range (09:15-09:20 AM)] (RF High and RF Low with wicks)
              │
              ▼
4. [Breakdown Below RF Low] (Candle closes strictly below RF Low)
              │
              ▼
5. [Reclaim: Return Inside RF Range] (Subsequent candle closes back inside)
              │
              ▼
6. [Long Entry Activated]
```

---

## 3. Mandatory Gatekeeper Filters

1. **Uptrend Filter:**
   - Must be an established, sustained uptrend.
   - Price must be trading above 200 EMA.
   - Sudden large gaps (300–400 points) from below the 200 EMA that land above it are REJECTED.
2. **Yesterday's Range Filter (Gap Size Gatekeeper):**
   - The open must be a gap-up relative to previous day close ($Open_{today} > Close_{yesterday}$).
   - The open must NOT exceed yesterday's high ($Open_{today} \le High_{yesterday}$).
   - Any gap opening above yesterday's high is classified as an unconfirmed "huge gap" and REJECTED.
3. **Invalidating Scenarios:**
   - Gap-down open $\rightarrow$ Immediate rejection.
   - Flat open $\rightarrow$ Rejection.
   - Wick-only dip below RF Low without a confirmed close below $\rightarrow$ Sequence not started.

---

## 4. Open Specifications & Unresolved Items (From Page 8 of PDF)

The RF strategy document explicitly includes an audit table on Page 8 highlighting what is defined vs. open:

| Rule Component | Rulebook Status | Canonical Note |
| :--- | :--- | :--- |
| **Uptrend + 200 EMA** | **DEFINED** | Established uptrend, not merely sitting above 200 EMA |
| **Gap-up Requirement** | **DEFINED** | Gap down or sudden reversal gap rejects the setup |
| **Gap within Yesterday's Range** | **DEFINED** | Gap above yesterday's high is rejected |
| **First 5-minute Range** | **DEFINED** | Full candle (09:15–09:20 AM), wicks included |
| **Activation Sequence** | **DEFINED** | Close below RF Low, followed by return inside range |
| **Stop-Loss Size** | **PARTIAL** | 35–40 points; fixed vs. candle extreme not fully stated |
| **Exact Entry Price** | **OPEN** | Document states "Not yet specified" |
| **Profit Target** | **OPEN** | Document states "Not yet specified" |
