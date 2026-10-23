# NIFTY 50 Historical Data Ingestion & Quality Audit

**Audit Date:** October 21, 2026 11:31 IST  
**Data Provider:** 5paisa Historical API (V2 REST)  
**Primary Symbol:** NIFTY 50 Cash Index (ScripCode `999920000`, NSE Cash Segment)  
**Target Coverage Window:** September 1, 2025 – August 31, 2026 (12 Months)  

---

## 1. Executive Summary

All required multi-timeframe market data covering the target manual trading journal period (September 2025 to August 2026) and prerequisite warm-up periods has been downloaded, verified, and persisted.

### Summary Ingestion Totals:
- **Daily Candles (1d):** **661 bars** (Warm-up from 2024-01-01 to 2026-08-31). Provides >600 completed daily closes, satisfying the 200 DMA warm-up gate.
- **5-Minute Candles (5m):** **20,237 bars** (Warm-up from 2025-08-01 to 2026-08-31). Satisfies the intraday 200 EMA warm-up.
- **1-Minute Candles (1m):** **92,981 bars** (Full journal period from 2025-09-01 to 2026-08-31).
- **Journal Target Session Completeness:** **247 / 248 sessions (99.6%)** verified as `COMPLETE` continuous sessions.

---

## 2. Reconstructed vs. Native 5-Minute Candle Verification

To confirm bar timestamp alignment and price fidelity, 5-minute candles were independently reconstructed from 1-minute bars using open-first, high-max, low-min, close-last resampling and compared against 5paisa's native 5-minute candles:

| OHLC Component | Maximum Discrepancy (Delta) | Reconciliation Status |
| :--- | :---: | :---: |
| **Open Price** | **0.0000 pts** | **100.0% EXACT MATCH** |
| **High Price** | **0.0000 pts** | **100.0% EXACT MATCH** |
| **Low Price** | **0.0000 pts** | **100.0% EXACT MATCH** |
| **Close Price** | **0.0000 pts** | **100.0% EXACT MATCH** |

---

## 3. Storage Architecture

| Dataset | Storage Path | Format | Compression |
| :--- | :--- | :--- | :--- |
| **1-Day Processed** | `data/processed/nifty/1d/nifty50_1d.parquet` | Apache Parquet | ZSTD |
| **5-Min Processed** | `data/processed/nifty/5m/nifty50_5m.parquet` | Apache Parquet | ZSTD |
| **1-Min Processed** | `data/processed/nifty/1m/nifty50_1m.parquet` | Apache Parquet | ZSTD |
| **DuckDB Database** | `data/processed/market_data.duckdb` | DuckDB Engine | Tables: `nifty_1d`, `nifty_5m`, `nifty_1m` |
| **Raw Chunks** | `data/raw/fivepaisa/nifty/` | Bounded Parquet | Chunked by 60–90 days |

---

## 4. Session Completeness Breakdown

- **Total Trading Days Evaluated:** 248
- **Fully Complete Continuous Sessions:** 247
- **Incomplete / Partial Sessions:** 1
- **Special Sessions / Muhurat / Budget:** 0

Detailed per-session candle counts, daily high/low/open/close, and integrity tags are logged in `reports/data_quality/nifty_coverage.csv`.
