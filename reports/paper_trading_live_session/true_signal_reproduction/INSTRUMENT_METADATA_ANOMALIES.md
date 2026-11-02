# Forensic Audit: Questionable Instrument Metadata & Symbol Verification

**Document:** `INSTRUMENT_METADATA_ANOMALIES.md`  
**Classification:** Market Data Forensic Audit & Contract Integrity Gate  
**Location:** `reports/paper_trading_live_session/true_signal_reproduction/`  
**Applicability:** NIFTY 50 Options Algorithmic Framework  
**Verdict:** **FORMAL REJECTION OF MALFORMED QUOTE ARTIFACT `24OCT26`**

---

## 1. Executive Summary

During the pre-execution audit of prospective data readiness, an observation reported in prior readiness drafts was flagged for forensic investigation:
> *"Live quotes retrieved on Oct 26: `NIFTY 24OCT26 22400 CE` (Bid: ₹142.10, Ask: ₹143.50, Spread: ₹1.40)."*

This investigation audited the **trading calendar, exchange symbology, broker scrip master, contract ID provenance, and observation timestamps**. 

### Conclusive Finding:
The symbol `NIFTY 24OCT26 22400 CE` **was not and could not have been a valid trading instrument on October 26, 2026**. It represents a synthetic test fixture artifact or typographical combination of a 2024 test token with a 2026 date. It is **formally rejected** and purged from prospective paper-trading readiness evidence.

---

## 2. Forensic Analysis & Calendar Verification

### 2.1. Calendar Impossibility
1. **Observation Date:** Monday, October 26, 2026.
2. **Purported Symbol Token:** `24OCT26`.
3. **Calendar Verification:**
   - October 24, 2026 was a **Saturday**.
   - The National Stock Exchange of India (NSE) **does not list, trade, or expire option contracts on Saturdays**.
   - Active weekly NIFTY options for late October 2026 expired on Tuesday/Thursday exchange cycles (specifically Tuesday, October 27, 2026 or Thursday, October 29, 2026).
   - Therefore, no contract expiring on `24OCT2026` could exist.

### 2.2. Year-Prefix Symbology Conflict
- In NSE standardized symbology:
  - Format: `<ROOT><YY><M/MMM><DD><STRIKE><CE/PE>`
  - A prefix of `24` indicates the year **2024** (e.g., `NIFTY24OCT...` was the October 2024 expiry).
  - An option for the year **2026** would strictly begin with `26` (e.g., `NIFTY26OCT...`).
  - If interpreted as a 2024 contract, `NIFTY 24OCT` expired **two full years earlier** (October 2024) and was completely defunct on October 26, 2026.

### 2.3. Broker Contract Master & Symbology Mismatch
In 5paisa's production API and scrip master, symbols are structured according to strict conventions:
- **5paisa Full Tradingsymbol Format:** `NIFTY 27 OCT 2026 CE 22600.00`
- **Exchange Token / Scrip Code:** Integer identifier (e.g., `54321` or `999920000` for spot index).
- In [`LIVE_MARKET_DATA_QUALITY.csv`](file:///Users/syedaskari/project/5paisa-options/reports/paper_trading_live_session/LIVE_MARKET_DATA_QUALITY.csv), the genuine live market quotes captured on October 26, 2026 were:
  ```csv
  2026-10-26T11:05:33.636291+05:30,2026-10-26T11:05:32+05:30,1636.3,NIFTY 27 OCT 2026 CE 22600.00,OPTION_CE,156.95,156.65,156.95,520,455,0.3,0.19,False,True,VALID_TWO_SIDED_QUOTE,5PAISA_READ_ONLY_FEED
  2026-10-26T11:05:33.872494+05:30,2026-10-26T11:05:32+05:30,1872.5,NIFTY 27 OCT 2026 PE 22600.00,OPTION_PE,99.15,98.9,99.15,910,1560,0.25,0.25,False,True,VALID_TWO_SIDED_QUOTE,5PAISA_READ_ONLY_FEED
  ```
  These genuine quotes reference the **October 27, 2026 expiry** (`NIFTY 27 OCT 2026`).

---

## 3. Provenance & Root Cause Tracing

A search across the repository reveals the exact origin of the erroneous token `NIFTY 24OCT...`:
1. **Mock Test Fixtures in 2024:**
   - In [`tests/options/test_5paisa_read_only.py`](file:///Users/syedaskari/project/5paisa-options/tests/options/test_5paisa_read_only.py#L140):
     ```python
     symbol="NIFTY 24OCT24800CE"
     ```
   - In [`tests/options/test_options_risk_manager.py`](file:///Users/syedaskari/project/5paisa-options/tests/options/test_options_risk_manager.py#L84):
     ```python
     contract_id="NIFTY_24OCT_25000CE"
     ```
   - In [`docs/data_contract.md`](file:///Users/syedaskari/project/5paisa-options/docs/data_contract.md#L14):
     ```markdown
     e.g. NIFTY 50, NIFTY24OCTFUT, NIFTY24OCT25000CE
     ```
2. **Accidental Conflation:**
   During the compilation of preliminary readiness reports, an illustrative test fixture referencing `24OCT` was inadvertently blended with the October 26 session date, generating the hybrid string `NIFTY 24OCT26 22400 CE`.
3. **Absence in Live Ingestion Stream:**
   This string does **not** exist in the DuckDB tick database, nor in the live collector socket log. It only appeared as descriptive narrative text in earlier markdown drafts.

---

## 4. Rejection and Corrective Actions

| Item | Status | Action Taken |
|---|---|---|
| `NIFTY 24OCT26 22400 CE` | **REJECTED (INVALID)** | Purged from all quantitative readiness evidence and classified as an erroneous mock token. |
| Observation Timestamp | **INVALIDATED** | Any quote attributed to `24OCT26` on October 26 is declared void. |
| Genuine Live Quotes | **RATIFIED** | Only quotes adhering to `NIFTY 27 OCT 2026 CE/PE 22600.00` from `LIVE_MARKET_DATA_QUALITY.csv` are accepted as authentic exchange observations. |
| Instrument Master Validation | **ENFORCED** | Enforce pre-trade validation that verifies contract expiry date against exchange holiday calendar before any quote is accepted by the paper engine. |

---

## 5. Audit Conclusion

The quote under `24OCT26` is an invalid artifact. Its rejection has zero impact on the mathematical backtests or signal engines, but it reinforces the critical mandate: **No synthetic or unverified metadata may be presented as genuine live market evidence**.
