# Historical Data Provenance and Authenticity Audit

**Document Version:** 1.0.0  
**Audit Date:** October 21, 2026  
**Audited Artifacts:**
- `data/samples/nifty50_5min_sample.csv`
- `data/processed/NIFTY_50/2024/bars_5m.parquet`

---

## 1. Executive Summary & Classification

An empirical audit was conducted to verify the origin, integrity, and authenticity of the 75-bar NIFTY 50 5-minute dataset currently stored in the repository.

### Classification: **UNVERIFIED / SYNTHETIC TEST FIXTURE**

> [!CAUTION]
> **RESEARCH INTEGRITY NOTICE**:  
> The 75-bar dataset currently in `data/samples/` and `data/processed/` is a **synthetic software test fixture**, generated during Phase 1 for software verification and data pipeline plumbing. It does **NOT** represent genuine historical trades from the National Stock Exchange of India (NSE) or the 5paisa API.  
> **Under no circumstances should backtest returns produced using this dataset be cited or interpreted as actual historical algorithmic performance.**

---

## 2. Technical Provenance & File Integrity

| Attribute | Specification |
| :--- | :--- |
| **Claimed Symbol** | NIFTY 50 (`999920000`) |
| **Claimed Trading Date** | October 18, 2024 |
| **Time Interval** | 5-Minute OHLCV |
| **Total Candles** | 75 bars (09:15 to 15:25 IST) |
| **Acquisition Timestamp** | October 20, 2024 (Phase 1 initialization) |
| **Original Generator** | Phase 1 synthetic data generation script |
| **CSV SHA-256 Checksum** | `202d07373e20fb431f8bd099b51501dcebf42ba4bcc840eff53a3c42a6ccb051` |
| **Parquet SHA-256 Checksum** | `8f3d13f6c140f88ec8ee112ab4e2f7735ab86ff33a32560723a2b09a8893dcec` |
| **Parquet Compression** | PyArrow ZSTD |

---

## 3. Empirical Exchange Verification (NSE vs. Sample Data)

A benchmark comparison was performed against the official National Stock Exchange of India (NSE) cash market Bhavcopy / index summary for **October 18, 2024**:

| Metric | Real NSE Official Market Data | Existing Repository Sample | Discrepancy (Delta) | Authenticity Status |
| :--- | :---: | :---: | :---: | :---: |
| **Day Open** | **24,664.95** | 24,800.00 | **+135.05 pts** | **FAILED (Synthetic)** |
| **Day High** | **24,886.20** | 24,838.01 | **-48.19 pts** | **FAILED (Synthetic)** |
| **Day Low** | **24,567.65** | 24,658.49 | **+90.84 pts** | **FAILED (Synthetic)** |
| **Day Close** | **24,854.05** | 24,693.58 | **-160.47 pts** | **FAILED (Synthetic)** |
| **Net Daily Change** | **+104.20 pts (+0.42%)** | -113.87 pts (-0.46%) | Opposing Direction | **FAILED (Synthetic)** |

### Key Observations:
1. **Opposite Trend Direction**: The genuine NSE market on October 18, 2024 was a net bullish recovery day (+0.42%), closing near its high at 24,854.05 after dipping to 24,567.65. The sample dataset reflects a bearish trend (-0.46%), closing near its low at 24,693.58.
2. **First Candle Inaccuracy**: The first 5-minute candle in the sample opens at 24,800.00, whereas the official NSE index open print was 24,664.95.
3. **Session Truncation**: The sample contains 75 bars ending at 15:25 IST. The final 15:25–15:30 closing candle is omitted.
4. **Volume Characteristics**: Volume numbers are randomized synthetic values (e.g., 81,203; 191,614) rather than aggregated underlying NSE turnover.

---

## 4. Options Historical Data Status

| Asset Class | Available Parquet Datasets | 5paisa Authenticated API Feasibility | Status |
| :--- | :---: | :--- | :---: |
| **NIFTY CE/PE Options** | **0 bars** | **Blocked**: 5paisa removes expired contracts from ScripMaster. Historical queries fail without active tokens. | **HISTORICAL OPTION DATA UNAVAILABLE** |

---

## 5. Protocols for Real Data Ingestion

To transition from synthetic fixtures to authentic research:
1. **Underlying Index Data**:
   - Run `scripts/download_historical_nifty.py` with verified 5paisa credentials to retrieve real 1-minute / 5-minute index candles.
   - Alternatively, ingest official NSE historical index data files via `CSVDataProvider`.
2. **Historical Options Data**:
   - Ingest provider-agnostic option archives through `ContractArchive` and `HistoricalOptionsDataIngester` from external options vendors.
   - For paper and live forward trading, record live option chains tick-by-tick into the Parquet lake.
