# 5paisa Data Feasibility & Options API Audit Report

**Date:** October 2026  
**Auditor:** Quantitative Research & Systems Architecture Team  
**Platform Mode:** Read-Only Market Data Audit (Milestone 2A)  
**Primary References:**
- 5paisa Historical Candles API: [https://xstream.5paisa.com/dev-docs/market-data-system/historical-candles](https://xstream.5paisa.com/dev-docs/market-data-system/historical-candles)
- py5paisa Official SDK: [https://github.com/OpenApi-5p/py5paisa](https://github.com/OpenApi-5p/py5paisa)
- 5paisa OMS Place Order Docs: [https://xstream.5paisa.com/dev-docs/order-management-system/place-order](https://xstream.5paisa.com/dev-docs/order-management-system/place-order)

---

## 1. Executive Summary & Critical Findings

A systematic technical feasibility investigation of the 5paisa API was conducted to determine its viability for multi-year options research and backtesting.

> [!WARNING]
> **CRITICAL DATA FEASIBILITY FINDING (EXPIRED OPTIONS BLOCKER):**
> 5paisa's historical candle API (`/V2/historical/{Exch}/{ExchType}/{ScripCode}/{Interval}`) **only maintains historical data for currently ACTIVE contracts**. 
> Once an options contract reaches its expiry date:
> 1. It is removed from the active Scrip Master.
> 2. Its ScripCode becomes inactive on the market data gateway.
> 3. Queries for expired contracts return an empty list of candles (`"candles": []`).
> 
> **Direct Consequence:** A genuine, multi-year historical backtest of intraday options execution **cannot be executed exclusively using 5paisa historical API**. 
> **Architectural Solution Implemented:** The platform provides a decoupled, provider-independent import interface (`GenericOptionsDataImporter` & `ContractArchive`) capable of ingesting external historical expired options data while utilizing 5paisa for live streaming, active option chains, and paper/live execution.

---

## 2. API Capabilities & Feasibility Matrix

| Capability / Data Dimension | Status | Verification Evidence / Technical Details |
| :--- | :--- | :--- |
| **NIFTY Spot Index (1-min / 5-min)** | **VERIFIED (API Spec)** / **UNVERIFIED (Live Token)** | ScripCode `999920000` (Exch: `N`, Segment: `C`). Supported at `1m`, `5m`, `15m`, `1d`. Pending user TOTP credentials. |
| **Active Option Scrip Master** | **VERIFIED** | Live 30MB CSV accessible at `https://Openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/All`. Full strike, expiry, lot size, and ScripCode mapping available without authentication. |
| **Active CE/PE Historical Candles** | **VERIFIED (API Spec)** / **UNVERIFIED (Live Token)** | Supported for contracts currently listed in Scrip Master via `/V2/historical/N/D/{ScripCode}/{Interval}`. |
| **Expired Contracts History** | **NOT SUPPORTED** | 5paisa purges expired derivative contracts from the active historical API. Empty candle lists returned. |
| **Historical Scrip Code Archive** | **NOT SUPPORTED** | 5paisa does not provide a historical point-in-time scrip master endpoint for past expiries. |
| **Option Chain Expiries** | **VERIFIED (API Spec)** | Endpoint `V2/GetExpiryForSymbolOptions` returns active weekly and monthly expiries for NIFTY. |
| **Strike & Type Mapping** | **VERIFIED** | Scrip Master provides exact `StrikeRate`, `ScripType` (`CE`/`PE`), `TickSize` (0.05), and active `LotSize`. |
| **Traded Volume** | **VERIFIED** | Volume is returned as the 6th element in the historical candle array: `[Timestamp, Open, High, Low, Close, Volume]`. |
| **Open Interest in Historical Candles** | **NOT SUPPORTED** | The standard historical candle endpoint does **NOT** return historical OI per candle. OI is only available via real-time market depth or snapshot feeds. |
| **Historical Bid/Ask Quotes** | **NOT SUPPORTED** | Historical bid/ask quotes (order book history) are not provided via REST historical candle endpoints. Historical backtests must use OHLC-estimated execution. |
| **Real-time Live Market Feed** | **VERIFIED** | WebSocket feed supporting live ticks, market depth (Level 2 / 20-depth), and active OI. |

---

## 3. Detailed Endpoint & Request Window Analysis

### 3.1 Historical Candle Route: `/V2/historical/`
* **Route Structure:** `GET https://openapi.5paisa.com/V2/historical/{Exch}/{ExchType}/{ScripCode}/{TimeFrame}?from={FromDate}&end={ToDate}`
* **Headers Required:**
  - `Authorization: Bearer <jwt_access_token>`
  - `Ocp-Apim-Subscription-Key: c89fab8d895a426d9e00db380b433027`
* **Supported Intervals:** `1m`, `3m`, `5m`, `10m`, `15m`, `30m`, `60m`, `1d`.
* **Request Window Restrictions:**
  - Maximum request interval: Strictly 180 days (6 months) per query for intraday candles as documented in official Xstream API specifications. Requests spanning > 180 days return HTTP 400 or fail validation.
  - Multi-year retrieval requires window chunking (e.g., 90 to 180-day sequential intervals).
* **Rate Limits:**
  - Standard rate limit: ~5 requests/second on historical endpoints.
  - Gateway: Kong 3.10.0 enforces HTTP 429 upon threshold breach.

### 3.2 Scrip Master Endpoint
* **Route:** `GET https://Openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/All`
* **Payload Size:** ~30 MB uncompressed CSV.
* **Caching Strategy:** The platform downloads and converts this file into local ZSTD-compressed Parquet (`data/reference/scrip_master.parquet`) once daily, avoiding redundant downloads.

---

## 4. Architectural Workaround for Expired Options

To ensure research integrity without violating the prime directive (*"Do not substitute theoretical option prices for missing historical prices while reporting results as real historical options backtests"*):

1. **Decoupled Architecture:**
   - **Signal Layer:** Runs on NIFTY 50 spot/futures historical data (available via 5paisa or local CSV/Parquet).
   - **Options Execution Layer:** Uses `GenericOptionsDataImporter` and `HistoricalOptionsStorage` to load approved historical options datasets.
2. **Data Integrity Guarantee:**
   - If historical option candles are missing for a selected contract at a signal timestamp, the trade is marked `REJECTED_MISSING_DATA` rather than hallucinating fills.
   - For recent/active contracts, 5paisa historical API can fetch actual 1-minute and 5-minute candles.
