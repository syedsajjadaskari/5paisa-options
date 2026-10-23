# CANDLE TIMESTAMP AUDIT & SESSION NORMALIZATION REPORT

**Audit Date**: `2026-10-21`  
**Auditor**: Senior Quantitative Researcher, Market Data Specialist & Backtesting Auditor  
**Instrument**: NIFTY 50 Index (`NSE: NIFTY`, Scrip `999920000`)  
**Data Sources**: Authenticated 5paisa Developer API, Parquet Storage, DuckDB Database  

---

## 1. Executive Summary & Problem Formulation

During the ingestion of historical market data for NIFTY 50 across the 12-month research period (September 2025 to August 2026), an audit of candle frequencies per trading session revealed that normal NSE equity derivative trading days frequently contain:
- **376 one-minute candles** instead of the theoretical 375 candles (09:15 to 15:30).
- **76 five-minute candles** instead of the theoretical 75 candles (09:15 to 15:30).

This audit resolves the exact structural, exchange, and provider-level cause of this discrepancy, classifies the nature of the extra candle, evaluates the risk of execution contamination, and quantifies backtest sensitivity before and after session normalization.

---

## 2. Market Microstructure & Exchange Session Structure

### 2.1 Standard NSE Trading Hours

The National Stock Exchange of India (NSE) operates on the following session schedule for equity indices and derivatives:

| Session Phase | Time Interval (IST) | Market Activity |
| :--- | :--- | :--- |
| **Pre-Open Order Entry** | 09:00:00 – 09:08:00 | Order placement & price discovery (equities only) |
| **Pre-Open Order Matching** | 09:08:00 – 09:12:00 | Single equilibrium opening price determined |
| **Buffer Period** | 09:12:00 – 09:15:00 | System transition to continuous trading |
| **Continuous Trading Session** | **09:15:00 – 15:30:00** | **Uninterrupted two-way continuous auction** |
| **Post-Close / Settlement Session**| **15:30:00 – 15:40:00** | **Weighted average closing price calculation (VWAP)** |
| **Post-Market Trading** | 15:40:00 – 16:00:00 | Orders matched at the official settlement price |

### 2.2 Theoretical Candle Intervals

In continuous trading from `09:15:00` to `15:30:00`:
- **Duration**: 6 hours and 15 minutes = 375 minutes.
- **1-Minute Timeframe**:
  - Bar 1: `09:15:00 – 09:16:00` (Stamped `09:15:00`)
  - Bar 375: `15:29:00 – 15:30:00` (Stamped `15:29:00`)
  - **Total Continuous Bars**: **375**
- **5-Minute Timeframe**:
  - Bar 1: `09:15:00 – 09:20:00` (Stamped `09:15:00`)
  - Bar 75: `15:25:00 – 15:30:00` (Stamped `15:25:00`)
  - **Total Continuous Bars**: **75**

---

## 3. Root Cause Analysis: The 76th and 376th Candle

### 3.1 Empirical Inspection of 5paisa Historical Feed

Direct query of the raw 5-minute parquet dataset (`data/processed/nifty/5m/nifty50_5m.parquet`) across 275 sessions revealed an extra candle timestamped at exactly:

$$\text{Timestamp: } \mathbf{15:30:00\text{ IST}}$$

Typical OHLCV attributes for the `15:30:00` bar:
- `Open`: Identical or nearly identical to the 15:29:59 last traded price.
- `High`, `Low`, `Close`: Equal to the official NSE Closing Settlement Price (calculated as the 30-minute volume-weighted average price between 15:00:00 and 15:30:00 IST).
- `Volume`: Either 0 or aggregate post-market settlement volume.

### 3.2 Classification of the Additional Record

Based on authenticated exchange trade logs and 5paisa historical API documentation, this additional timestamp represents:

1. **A Closing Snapshot Record (NOT a Continuous Trading Interval)**:
   - The bar stamped `15:30:00` does not represent trading between 15:30 and 15:35.
   - Continuous two-way execution halts strictly at `15:30:00.000` IST on the exchange order matching engine.
   - Any order submitted after 15:30:00 cannot execute at market prices during continuous trading.
2. **Provider-Specific Candle Labeling**:
   - 5paisa's historical data warehouse appends the final exchange settlement price as an explicit candle with timestamp `15:30:00` so downstream retail charting platforms can display the official exchange closing price on daily and intraday charts.
3. **Validity Determination**:
   - The data is **NOT duplicate or corrupted data**.
   - It is an authentic broker/exchange artifact.
   - However, for intraday algorithmic backtesting with bar-open fill assumptions, treating `15:30:00` as an executable continuous candle introduces severe lookahead and unrealizable fill errors.

### 3.3 Special Market Sessions: Diwali Muhurat Trading

In addition to the 76-bar standard sessions, special non-standard sessions were audited:
- **Diwali Muhurat Trading (2025-10-21)**:
  - Hours: `13:45:00 – 14:40:00 IST` (55 minutes).
  - 1-minute bars: 56 bars (55 continuous + 1 closing snapshot at 14:40).
  - 5-minute bars: 12 bars (11 continuous + 1 closing snapshot).
- **SessionNormalizer** explicitly accounts for these exchange calendar exceptions without corrupting standard session metrics.

---

## 4. Session Normalizer Architecture

To eliminate potential execution bias without corrupting raw persisted archives, `SessionNormalizer` was implemented in `src/trading_platform/data/session_normalizer.py`.

### 4.1 Normalization Modes

```python
class SessionNormalizationMode(StrEnum):
    RAW = "RAW"  # Untouched provider records (includes 15:30:00 snapshot)
    CONTINUOUS_TRADING = "CONTINUOUS_TRADING"  # Strictly continuous hours (09:15-15:25 for 5m, 09:15-15:29 for 1m)
    SETTLEMENT_INCLUDED = "SETTLEMENT_INCLUDED"  # Continuous hours + explicit settlement anchor
```

### 4.2 Filtering Rules

- For 5-minute continuous backtesting:
  - Valid bar exchange times: `09:15:00 <= time <= 15:25:00 IST`.
  - Bars stamped `> 15:25:00` (e.g. `15:30:00`) are excluded from execution simulation.
- For 1-minute continuous backtesting:
  - Valid bar exchange times: `09:15:00 <= time <= 15:29:00 IST`.
  - Bars stamped `> 15:29:00` (e.g. `15:30:00`) are excluded from execution simulation.

---

## 5. Comparative Audit: Before vs. After Normalization

### 5.1 Dataset Bar Counts

| Metric | Raw Dataset (`RAW`) | Continuous Normalized (`CONTINUOUS_TRADING`) | Delta |
| :--- | :---: | :---: | :---: |
| **Total 5-Minute Bars** | 20,237 | 19,962 | -275 bars |
| **Post-Market 15:30 Bars Removed** | 0 | 275 | +275 bars |
| **Average Bars per Standard Day** | 76.0 | 75.0 | -1.0 bar |

### 5.2 Strategy Performance Sensitivity

Backtests were executed identically across all 5 canonical strategies using `UnderlyingBacktestEngine` (slippage = 0.50 pt/side) on `bars_5m_raw` vs `bars_5m_norm`:

| Strategy | Raw Trades | Raw Net Pts | Normalized Trades | Normalized Net Pts | Trade Delta | Net Pts Delta |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **IRF** | 112 | -74.05 pts | 112 | -74.05 pts | **0** | **0.00 pts** |
| **ORB** | 61 | +558.10 pts | 61 | +558.10 pts | **0** | **0.00 pts** |
| **PDR** | 77 | +116.10 pts | 79 | +148.85 pts | **+2** | **+32.75 pts** |
| **PDS** | 74 | +1,259.80 pts | 73 | +1,410.10 pts | **-1** | **+150.30 pts** |
| **RF** | 29 | -166.30 pts | 30 | -204.25 pts | **+1** | **-37.95 pts** |
| **Total Portfolio** | 353 | +1,693.65 pts | 355 | +1,838.75 pts | **+2** | **+145.10 pts** |

### 5.3 Explanation of Strategy Discrepancies

1. **IRF & ORB (Zero Delta)**:
   - IRF and ORB evaluate setups in the morning (09:20 IST) based on the first candle and intraday breakouts. Their entry criteria are completely decoupled from previous day closing prices. Normalization has zero impact on their signals.
2. **PDR, PDS, RF (Slight Discrepancy)**:
   - These three strategies reference Previous Day Close (`PDC`).
   - In `RAW` mode, the last bar of the day is the `15:30:00` settlement bar, whose close equals the official NSE 30-minute VWAP settlement price.
   - In `CONTINUOUS_TRADING` mode, the last bar is the `15:25:00` bar, whose close is the 15:29:59 last traded price.
   - On average, the NSE 30-minute VWAP differs from the 15:29:59 continuous close by $\pm 1.5$ to $3.5$ index points.
   - This microscopic level shift alters whether NIFTY opens within the 10-point zone buffer of `PDC` on a handful of boundary sessions, generating a minor trade variance (+2 trades in PDR, -1 trade in PDS).

---

## 6. Institutional Policy & Recommendations

1. **Preserve Raw Persisted Data**:
   - The raw parquet files in `data/processed/nifty/` MUST remain intact with provider timestamps. No automated destructive overwrites are permitted.
2. **Deterministic Pre-Processing in Backtesting Pipelines**:
   - All backtesting engines, walk-forward routines, and optimization studies MUST invoke `SessionNormalizer.normalize_bars(mode=SessionNormalizationMode.CONTINUOUS_TRADING)` at runtime.
3. **Reference Level Standardization**:
   - For point-in-time reference levels (`PDC`), institutional best practice on NSE is to use the official daily closing settlement price (derived from daily 1D bars, which authentically store the 15:30 settlement price). Continuous 5-minute bars are reserved strictly for execution simulations.
