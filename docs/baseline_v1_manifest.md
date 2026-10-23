# BASELINE_V1 — RESEARCH REPRODUCIBILITY MANIFEST

**Identifier**: `BASELINE_V1`  
**Classification**: Canonical Unoptimized Research Baseline  
**Audit Timestamp**: `2026-10-21T14:25:00+05:30`  
**Status**: PERMANENTLY FROZEN — Immutable Reference Baseline  

---

## 1. Executive Summary & Audit Mandate

This document serves as the formal freezing declaration for `BASELINE_V1` in the `5paisa-options` algorithmic trading repository. All subsequent modifications, experimental variants (such as trailing stop alterations or session normalizations), and parameter optimizations performed during Phase 3B and beyond MUST be evaluated against this immutable baseline. 

Under no circumstances may the canonical rules, parameter settings, or execution models documented herein be overwritten or mutated.

---

## 2. Source-Data Provenance & Cryptographic Hashes

All market data utilized in this baseline was ingested directly via the authenticated 5paisa Developer API (Broker Adapter Version 2.4.0) for the underlying instrument `NIFTY 50 Index` (`Exchange: NSE`, `Scrip Code: 999920000`).

### 2.1 File Integrity Hashes (SHA-256)

| Dataset | Relative File Path | Size (Bytes) | Record Count | SHA-256 Cryptographic Hash |
| :--- | :--- | :--- | :--- | :--- |
| **NIFTY 1-Minute OHLCV** | `data/processed/nifty/1m/nifty50_1m.parquet` | 1,935,318 | 92,981 | `a0fea90ac0e7469e150fc732dba6f61f5984bd4a75a614ec5497e206a22db524` |
| **NIFTY 5-Minute OHLCV** | `data/processed/nifty/5m/nifty50_5m.parquet` | 511,821 | 20,237 | `02984db58b9697e1bb8862b2a1a0284e94648d1536b8d9a5c2e61306d1ed684b` |
| **NIFTY Daily (1D) OHLCV**| `data/processed/nifty/1d/nifty50_1d.parquet` | 20,040 | 661 | `8ed0deb2722cabc82e0528bd120a1947bf8e2668910f1a0ea97589a78c73bc4e` |
| **NSE Scrip Master** | `data/reference/scrip_master.parquet` | 4,003,901 | 178,229 | `8991a989bdf3bf33b546cb51f7593ec420517494083183cc0014dbe9f5ae4286` |
| **Manual Trading Journal** | `data/manual/Trading_Journal_Sep2025_Aug2026 REPORT.xlsx` | 17,439 | 202 | `97a9ad4dfb5520619bb979770115d7b6f4e3e37e649af255c543f5fce9694781` |
| **DuckDB Analytics Store** | `data/processed/market_data.duckdb` | ~2.5 MB | Multi-table | Live analytical storage |

---

## 3. Strategy Code Version & System Environment

- **Repository Root**: `/Users/syedaskari/project/5paisa-options`
- **Git Commit Hash**: `72756466930511318319e8a25b3fe49e2a821577`
- **Active Branch**: `main`
- **Python Runtime**: `CPython 3.11.15` (macOS Darwin aarch64 Apple Silicon)
- **Primary Dependencies**:
  - `pandas`: 2.2.3
  - `duckdb`: 1.2.0
  - `pyarrow`: 19.0.0
  - `pydantic`: 2.10.6
  - `optuna`: 5.0.0
  - `pytest`: 8.3.4
  - `ruff`: 0.9.6

---

## 4. Canonical Strategy Configurations

Each strategy is executed with its exact, original rulebook configuration without optimization:

```json
{
  "IRF": {
    "strategy_name": "Initial Range Failure",
    "method": "METHOD_1",
    "description": "Breakout and immediate failure back into 09:15-09:20 range",
    "target_points": 60.0,
    "default_stop_points": 35.0,
    "max_stop_points": 40.0,
    "direction": "SELL_ONLY"
  },
  "ORB": {
    "strategy_name": "Opening Range Breakout",
    "method": "METHOD_1",
    "description": "09:15-09:20 candle breaks PDH/PDL with body filter",
    "target_points": 60.0,
    "stop_points": 30.0,
    "min_body_ratio": 0.50,
    "max_opposing_wick_ratio": 0.35,
    "direction": "BUY_AND_SELL"
  },
  "PDR": {
    "strategy_name": "Previous Day Retest",
    "method": "METHOD_1",
    "description": "Touch and bounce off PDH/PDL/PDC within 10 points",
    "target_points": 70.0,
    "stop_points": 35.0,
    "zone_buffer_points": 10.0,
    "direction": "BUY_AND_SELL"
  },
  "PDS": {
    "strategy_name": "Previous Day Scalp",
    "method": "METHOD_1",
    "description": "Strong red/green candle piercing PDH/PDL with 60% body ratio",
    "target_points": 60.0,
    "stop_points": 35.0,
    "min_body_ratio": 0.60,
    "direction": "BUY_AND_SELL"
  },
  "RF": {
    "strategy_name": "Range Failure / Reclaim",
    "method": "CANONICAL_RECLAIM",
    "description": "Candle pierces level and reclaims within 2 bars",
    "status": "ENTRY_VALIDATION_ONLY",
    "target_points": 60.0,
    "stop_points": 35.0,
    "strict_rulebook_mode": false,
    "direction": "BUY_AND_SELL",
    "disclaimer": "Exit target of 60 pts is an experimental heuristic; PDF rulebook notes target as OPEN."
  }
}
```

---

## 5. Execution & Backtesting Assumptions

1. **Evaluation Window**:
   - **Start Date**: `2025-09-01`
   - **End Date**: `2026-08-31`
   - **Total Trading Days**: 246 sessions
2. **Bar Processing & Fill Model**:
   - 5-minute OHLCV candles evaluated sequentially.
   - Signal generation occurs upon **bar close** (e.g. 09:20 bar completes).
   - Order execution occurs at the **Open of the immediately subsequent bar** (e.g. 09:25:00 IST open price).
   - Intrabar stop-loss and target-profit evaluation: High and Low of subsequent bars are inspected. Favorable fills require High >= Target (for BUY) or Low <= Target (for SELL).
3. **Slippage & Transaction Costs**:
   - Slippage model: Fixed **0.50 NIFTY points per side** (**1.00 point round-trip** per executed trade).
   - Gross points minus 1.00 pt = Net Index Points.
4. **End-of-Day (EOD) Square-off**:
   - Strict intraday square-off at **15:15:00 IST**. No positions carried overnight.
5. **No Trailing Stop**:
   - Zero trailing stop in `BASELINE_V1`. Stop-loss remains fixed at defined initial distance until exit or EOD.

---

## 6. Frozen Performance Metrics

### 6.1 Standalone Strategy Baselines (Slippage = 0.50 pt/side)

| Strategy | Rulebook Method | Trades | Wins | Losses | Win Rate (%) | Gross Pts | Slippage | Net Pts | Profit Factor | Status |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **IRF** | Method 1: Immediate Failure | 112 | 42 | 70 | 37.50% | +37.95 | -112.00 | **-74.05** | 0.97 | FROZEN_BASELINE_V1 |
| **ORB** | Method 1: PDH/PDL Breakout | 61 | 27 | 34 | 44.26% | +619.10 | -61.00 | **+558.10** | 1.53 | FROZEN_BASELINE_V1 |
| **PDR** | Method 1: Retest Bounce | 77 | 30 | 47 | 38.96% | +193.10 | -77.00 | **+116.10** | 1.07 | FROZEN_BASELINE_V1 |
| **PDS** | Method 1: Strong Red Candle | 74 | 39 | 35 | 52.70% | +1,333.80 | -74.00 | **+1,259.80** | 1.94 | FROZEN_BASELINE_V1 |
| **RF** | Canonical Reclaim (Unresolved) | 29 | 11 | 18 | 37.93% | -137.30 | -29.00 | **-166.30** | 0.74 | ENTRY_VALIDATION_ONLY |

### 6.2 Portfolio Aggregations

| Portfolio Architecture | Capacity Limit | Trades Evaluated | Accepted | Rejected | Net Index Pts | Win Rate (%) | Profit Factor | Max Drawdown |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Unarbitrated Theoretical Sum** | $\infty$ | 353 | 353 | 0 | **+1,693.65** | 42.21% | 1.24 | 666.6 pts |
| **FIFO Single-Position (Capacity=1)** | **1** | **353** | **302** | **51** | **+1,608.90** | **42.38%** | **1.27** | **635.3 pts** |
| **Priority Single-Position (Sensitivity)**| 1 | 353 | 302 | 51 | **+1,871.30** | 44.70% | 1.34 | 426.5 pts |

---

## 7. Audit Attestation

I attest that `BASELINE_V1` represents an uncorrupted, reproducible reproduction of the 5 canonical NIFTY strategies over the 12-month period from September 1, 2025 to August 31, 2026. All source data, execution models, and parameters are permanently frozen under this manifest.

**Lead Quantitative Auditor**: Senior Quantitative Researcher & Auditor  
**Date**: October 21, 2026
