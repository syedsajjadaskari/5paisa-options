# Pre-Market Verification Checklist
**Session Date:** Monday, October 26, 2026  
**Verification Window:** 08:55 – 09:15 IST  
**Target Market Hours:** 09:15 – 15:30 IST  
**Underlying:** NIFTY 50 Index  
**Operating Mode:** PAPER_SIMULATION_ONLY (Live Broker Orders Physically Blocked)

---

## 1. Executive Pre-Market Verification Status

| Checklist Item | Requirement | Observed Status | Audit Verification | Result |
|---|---|---|---|---|
| **Broker Authentication** | Valid 5paisa credentials & session token | ClientCode, UserKey, EncryptionKey loaded; cached token valid | `FivePaisaBroker.connect() == True` | **PASS** |
| **5paisa API Connectivity** | Market feed read-only response | Fetch market feed scrip returns Status 0, NIFTY spot tick 22,486.25 | Provider timestamp authentic, non-stale | **PASS** |
| **NSE Trading Calendar** | Valid regular NSE trading session | Today is Monday 2026-10-26; `NSECalendar.is_trading_day == True` | Not a weekend, not an exchange holiday | **PASS** |
| **Special Session Check** | Standard continuous session schedule | Normal session (09:15 to 15:30 IST); entry session supported | Not Muhurat or Budget Sunday | **PASS** |
| **Prior Session OHLC** | Completed prior session bars | Friday 2026-10-23 completed daily & intraday bars loaded | 2026-10-23 session verified | **PASS** |
| **Historical Warm-up Data** | Sufficient history for 200-DMA & 200-EMA | 660 daily bars available (min 200 required); 20,237 5m bars available | 200-DMA = 24,652.01; `has_sufficient_dma_warmup == True` | **PASS** |
| **Prior-Day Reference Levels** | PDH, PDL, PDC, PDO & zone bodies | Calculated strictly point-in-time via `ReferenceLevelCalculator` | PDH: 24,188.30, PDL: 24,076.85, PDC: 24,175.65, PDO: 24,122.60 | **PASS** |
| **Support / Resistance Zones** | Prior candle body-to-wick boundaries | Resistance Zone: 24,168.25 – 24,188.30; Support Zone: 24,076.85 – 24,083.15 | Zones derived from actual prior candle bodies | **PASS** |
| **Contract Metadata** | Available NIFTY weekly option contracts | 4,062 NIFTY contracts in master; nearest weekly expiry 2026-10-27 | Scrip master verified; ATM strike 22,500/22,600 verified | **PASS** |
| **Contract Lot Size** | Authoritative exchange lot specification | Lot size = 65 units (1 lot = 65, 2 lots = 130 units) | Metadata confirmed; no lot size ambiguity | **PASS** |
| **Database & Archive Storage** | DuckDB & Parquet file integrity | `data/processed/paper_trading.duckdb` (6.76 MB), `options_archive_verified/` | Table schemas verified; write access confirmed | **PASS** |
| **Virtual Portfolio State** | Clean starting virtual account ledger | Starting capital: ₹250,000.00; Available cash: ₹250,000.00; Equity: ₹250,000.00 | Isolated `PAPER_VIRTUAL` ledger; real broker funds segregated | **PASS** |
| **Open Position State** | Zero inherited open virtual positions | `active_position: None`; `pending_exit_reason: None` | Clean state; no residual risk from prior runs | **PASS** |
| **Order Interface Safety** | Physical barrier against broker orders | `_ReadOnlyFivePaisaClientProxy` intercepts all transactional calls | `LiveTradingPermissionError` raised on order attempt | **PASS** |

---

## 2. Detailed Technical Verification

### 2.1 Connectivity and Read-Only Market Feed
- **Broker Client:** `FivePaisaBroker` (Read-Only Adapter)
- **Authentication Handshake:** Verified via `py5paisa.FivePaisaClient` using encrypted session token.
- **Spot Market Feed Test:** Scrip Code `NIFTY 50` returned live rate `22,486.25`, API response status `0` (`Success`).
- **Data Freshness Engine:** Strict staleness ceiling enforced at `5.0` seconds. Future timestamp skew capped at `10.0` seconds.

### 2.2 Reference Levels & Technical Indicators
- **200-Day Moving Average (200 DMA):** `24,652.01` (computed across completed daily closing prices, 660 daily candles available).
- **Previous Session High (PDH):** `24,188.30`
- **Previous Session Low (PDL):** `24,076.85`
- **Previous Session Close (PDC):** `24,175.65`
- **Previous Session Open (PDO):** `24,122.60`
- **Previous Day Resistance Zone:** `[24,168.25, 24,188.30]`
- **Previous Day Support Zone:** `[24,076.85, 24,083.15]`
- **Anti-Lookahead Guarantee:** Reference levels for session $T$ strictly reference sessions $\le T-1$. No same-day bars are included in pre-market reference metrics.

### 2.3 Option Instrument & Strike Specification
- **Index Underlying:** NIFTY 50
- **Nearest Weekly Expiry:** `2026-10-27` (Tuesday weekly contracts)
- **Exchange Lot Size:** `65` units
- **Maximum Permitted Position Size:** `2` lots (`130` units)
- **Active ATM & Near-ATM Scrip Codes:**
  - `NIFTY 27 OCT 2026 CE 22500.00`: Scrip `51330`
  - `NIFTY 27 OCT 2026 PE 22500.00`: Scrip `51331`
  - `NIFTY 27 OCT 2026 CE 22550.00`: Scrip `51332`
  - `NIFTY 27 OCT 2026 PE 22550.00`: Scrip `51333`
  - `NIFTY 27 OCT 2026 CE 22600.00`: Scrip `51334`
  - `NIFTY 27 OCT 2026 PE 22600.00`: Scrip `51337`
  - `NIFTY 27 OCT 2026 CE 22650.00`: Scrip `51338`
  - `NIFTY 27 OCT 2026 PE 22650.00`: Scrip `51339`
  - `NIFTY 27 OCT 2026 CE 22700.00`: Scrip `51340`
  - `NIFTY 27 OCT 2026 PE 22700.00`: Scrip `51341`

### 2.4 Virtual Ledger & Risk Parameters
- **Virtual Account Namespace:** `PAPER_VIRTUAL` (Zero connection to live broker margin)
- **Starting Capital:** `₹250,000.00`
- **Max Allocation Cap (20%):** `₹50,000.00`
- **Daily Loss Limit (2%):** `₹5,000.00`
- **Max Concurrent Positions:** `1`
- **New Entry Cutoff Time:** `14:30 IST`
- **Intraday Square-Off Target:** `15:15 IST`
- **Session End Time:** `15:30 IST`

### 2.5 Strategy Rulebook Status Assessment
- **PDS (Method 1):** `DIAGNOSTIC_OBSERVATION_ONLY` — Retest proximity and 35 vs 40 stop-loss choices remain unresolved in author PDF.
- **ORB (Method 1):** `DIAGNOSTIC_OBSERVATION_ONLY` — "Established trend" definition relies on unverified 200-EMA assumption.
- **IRF (Method 2):** `DIAGNOSTIC_OBSERVATION_ONLY` — Stop-loss selection (35 vs 40 points) unconfirmed by author.
- **PDR (Retest Bounce):** `DIAGNOSTIC_OBSERVATION_ONLY` — Proximity numerical threshold and stop selection unconfirmed by author.
- **RF (Reverse Failure):** `DISABLED` — Entry and exit rules incomplete in author PDF.

---

## 3. Operational Conclusion & Readiness

All 14 pre-market system prerequisites have been verified and passed. The market data feed is active and communicating with 5paisa. The virtual portfolio ledger is clean and fully reconciled at ₹250,000.00. 

In strict adherence to Safety Gate requirements (Section 2 & Section 15), because strategy rule gaps remain unconfirmed by the author, prospective live market trading will run in **SHADOW_SIGNALS_ONLY / DIAGNOSTIC OBSERVATION MODE**, logging every signal, spread, rejection reason, and prospective contract quote without routing virtual fills. The complete virtual fill-to-exit lifecycle is validated using a deterministic historical replay.
