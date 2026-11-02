> [!CAUTION]
> ### SUPERSEDED - DO NOT RELY ON (marked 2026-10-29)
> This document was written for an earlier engine / the retired Phase-4 stack and is kept only as history. **What is stale:**
> - It describes the retired `trading_platform.paper` / `read_only_collector` / DuckDB stack (archived in `archive/legacy_2026-10-28/`); `paper_execution_enabled`, `paper/engine.py` and `python -m trading_platform.paper.cli` no longer exist; the 'blockers' and the GO steps (run `read_only_collector.py`, toggle `paper_execution_enabled`) cannot be followed.
> - Rule ambiguities listed are those of the pre-V3 engine; see `docs/AUTHOR_CONFIRMATION_REQUEST.md`.
> - Byte-exact original: `archive/evidence_originals_2026-10-29/reports/paper_trading_live_session/PAPER_EXECUTION_BLOCKERS.md`. Index of all stale files: `reports/paper_trading_live_session/SUPERSEDED_INDEX.md`.
>
> **Current sources of truth:** `README_PAPER_TRADING.md` (how the app really works), `docs/live_app_architecture.md`, `docs/STRATEGY_EXECUTION_STATUS.md` (what each strategy does today), `docs/AUTHOR_CONFIRMATION_REQUEST.md` (what the strategy author must still confirm), and `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825` (current backtest: 184 portfolio trades, +813.60 index points, profit factor 1.22 in the 2025-09..2026-08 window; NIFTY index points only, in-sample, no option P&L).
> The text below is unchanged history.

---

# Prospective Paper Execution Blockers & Data Readiness Audit

**Document:** `PAPER_EXECUTION_BLOCKERS.md`  
**Classification:** Operational Readiness, Data Pipeline Audit & Risk Governance  
**Applicability:** NIFTY 50 Options Algorithmic Paper Trading Platform  
**Status:** Mandatory Pre-Execution Review Gate  
**Current Paper Execution Authorization:** **BLOCKED (0 of 5 Strategies Eligible)**

---

## 1. Executive Summary

Prospective paper execution (virtual fills against live market quotes) is **strictly blocked** across the entire platform. While the system operates safely in `LIVE_READ_ONLY` and `SHADOW_ONLY` modes, paper execution cannot proceed until specific quantitative rule ambiguities and operational infrastructure limits are formally resolved.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                         FORWARD EXECUTION READINESS SUMMARY                            │
├──────────────────────────────────────────┬──────────────────────┬──────────────────────┤
│ Component                                │ Readiness Status     │ Execution Gate       │
├──────────────────────────────────────────┼──────────────────────┼──────────────────────┤
│ Read-Only Broker Guardrails              │ 100% OPERATIONAL     │ PASSED (Enforced)    │
│ Unit & Integration Test Suite (219 tests)│ 100% PASSING         │ PASSED               │
│ Strategy Rulebook Specification          │ UNCONFIRMED PROXIES  │ BLOCKED (Author P1-3)│
│ Continuous Live Ingestion (09:15–15:30)  │ PARTIALLY OBSERVED   │ BLOCKED (Live Session)│
│ High-Frequency Options Depth Streaming   │ REST POLLING ONLY    │ BLOCKED (Rate Limits)│
│ Point-in-Time Reference Level Provider   │ STATIC CACHE         │ BLOCKED (Live Feed)  │
│ Virtual Fill Execution Gate              │ FAILS CLOSED         │ BLOCKED (Disabled)   │
└──────────────────────────────────────────┴──────────────────────┴──────────────────────┘
```

---

## 2. Category 1: Quantitative Strategy Rule Blockers

The primary blocker preventing algorithmic execution is that **all active portfolio strategies rely on heuristic proxies rather than author-ratified mathematical rules**. 

Under institutional quant standards, generating backtest trades against unconfirmed parameters does **not** grant paper-trading eligibility.

### Blocker 1.1: PDS Method 1 — Heuristic Zone and Stop Boundaries
- **Unresolved Issue:** The author PDF requires entering short when price retests "yesterday's support zone." The code (`src/trading_platform/strategies/pds.py`) implements a 10-point buffer around the lowest 5-minute candle.
- **Risk:** Without author confirmation of whether the zone is derived from the single lowest bar, a multi-bar consolidation, or the daily low, the algorithm risks generating false entries or missing authentic setups.
- **Required Resolution:** Author must confirm Priority 1 questions regarding zone source bars, proximity tolerance, and the boundary condition between 35 and 40 pt stops.

### Blocker 1.2: ORB Method 1 — Heuristic Trend Filter
- **Unresolved Issue:** The author specifies trading only in the direction of the "established trend." The automated engine implements a 5-minute 200 EMA slope proxy.
- **Risk:** On chop days or during early-session volatility, a 5-minute EMA can contradict daily market structure or higher-timeframe DMA, generating counter-trend whipsaws.
- **Required Resolution:** Author must ratify whether 200 EMA on 5m is the authoritative trend definition or if a daily structure rule applies.

### Blocker 1.3: IRF Method 2 — Undefined Pullback Support Lookback
- **Unresolved Issue:** The author diagrams an extended move above IRH followed by a breakdown below "recent pullback support." The engine uses an internal 5-bar swing pivot proxy.
- **Risk:** Varying the lookback window between 3, 5, or 8 bars drastically shifts the support price level and timing of the breakdown signal.
- **Required Resolution:** Author must specify the exact swing lookback window or structural pivot definition.

### Blocker 1.4: PDR Retest Bounce — Undefined Resistance Zone
- **Unresolved Issue:** Requires a retest of yesterday's resistance zone after a gap-up open. The engine applies an unconfirmed 10-point buffer.
- **Risk:** Asymmetry with PDS zone definitions creates unmodeled tracking error against manual expectations.
- **Required Resolution:** Author must specify the exact resistance zone construction and retest tolerance.

### Blocker 1.5: RF Strategy — Complete Absence of Canonical Exits
- **Status:** **PERMANENTLY DISABLED**.
- **Reason:** The author PDF contains no stop-loss, profit target, or trailing stop specifications. Execution is prohibited.

---

## 3. Category 2: Live Market Data Pipeline Readiness Audit

The table below contrasts what has been **empirically observed during live market operation** versus what has been **demonstrated solely through automated mock tests**:

| Data Pipeline Capability | Verified Live in Real Market | Demonstrated Only in Automated Tests | Production Blocker Assessment |
|---|---|---|---|
| **Full Normal NSE Session**<br>*(09:15 to 15:30 IST continuous)* | **No.** Observed live for a 23-minute morning window (08:58 to 09:22 IST on Oct 26, 2026). Continuous 6.25-hour session unproven. | **Yes.** Simulated via full-day mock feed in integration test suite. | **BLOCKER:** Must record a continuous 375-minute live trading session without network dropout, socket reset, or buffer overrun. |
| **Verified Completed 5m Candles**<br>*(BarBuilder synthetic aggregation)* | **Partial.** Formed initial morning bars during live premarket/market transition; verified event-time boundary alignment. | **Yes.** Unit tests verify boundary rollover, timestamp tagging (`09:20:00` for `09:15–09:20`), and volume accumulation. | **PASSED FOR INGESTION:** BarBuilder logic is deterministic and verified against DuckDB storage. |
| **Current Reference Data**<br>*(PDH, PDL, PDC, Daily EMA)* | **No.** Engine relied on pre-calculated historical daily parquet cache (`data/processed/nifty/5m`). Live automated EOD sync unverified. | **Yes.** Tested with synthetic prior-day OHLC fixtures. | **BLOCKER:** Automated live morning retrieval of previous-day settlement levels from 5paisa REST API must be validated pre-09:15. |
| **Genuine CE/PE Bid/Ask Quotes**<br>*(Option chain depth & spread)* | **Yes.** Live quotes retrieved on Oct 26: `NIFTY 24OCT26 22400 CE` (Bid: ₹142.10, Ask: ₹143.50, Spread: ₹1.40). | **Yes.** Simulated option chain fixtures tested across ITM/ATM/OTM strikes. | **OPERATIONAL WARNING:** Polling depth via REST is rate-limited to 5 req/sec. High-frequency option execution requires WebSocket depth streaming. |
| **Fresh Contract Metadata**<br>*(Expiry calendar, strike step, lot size)* | **Yes.** Successfully parsed active weekly options (`NIFTY26OCT...`) and verified 50-point strike spacing. | **Yes.** Metadata parser validated against 5paisa master scrip file. | **PASSED:** Contract resolution maps spot price to correct ATM strike within 2ms. |
| **Event-Time Ordering & Clock Skew**<br>*(Monotonic ordering)* | **Yes.** Live collector rejected 2 out-of-order packets with future timestamps on Oct 26, logging timestamp skew alerts. | **Yes.** Comprehensive unit tests enforce packet discard on negative delta time. | **PASSED:** Event-time ordering is verified in live market conditions. |
| **Duplicate Prevention**<br>*(Deduplication cache)* | **Yes.** DuckDB primary key constraints and in-memory hash set prevented duplicate tick writes during live capture. | **Yes.** Stress tests verified zero duplicates across 50,000 synthetic ticks. | **PASSED:** Ingestion engine guarantees idempotent records. |
| **Read-Only Broker Interface**<br>*(Security proxy)* | **Yes.** Active `_ReadOnlyFivePaisaClientProxy` blocked order placement attempts during live session runs. | **Yes.** Unit test `test_phase4e_read_only.py` verifies all 21 order methods raise `PermissionError`. | **PASSED (CRITICAL GUARD):** Absolutely no real capital at risk. |

---

## 4. Category 3: Execution Engine & Safeguard Blockers

### Blocker 4.1: Virtual Fill Disablement
- The configuration setting `paper_execution_enabled` is hardcoded to `False` in `src/trading_platform/paper/engine.py`.
- Any signal generated by an authorized strategy is logged as a `SHADOW_SIGNAL` but creates **no virtual position, fills zero contracts, and generates ₹0.00 virtual P&L**.
- Fails closed by design.

### Blocker 4.2: 15:15 IST Mandatory Intraday Square-Off
- The platform enforces automatic position liquidation at 15:15:00 IST to eliminate overnight carry risk.
- While verified in unit test suites (`tests/unit/test_paper_engine.py`), it has **never been observed in a live market session** because past live runs were terminated prior to the afternoon close.

### Blocker 4.3: Daily Loss Limit & Consecutive Loss Kill-Switch
- The risk manager trips an emergency circuit breaker if cumulative session loss exceeds ₹5,000 or if 3 consecutive losing trades occur.
- Verified in synthetic replay tests, but requires prospective session observation under live quote feeds.

---

## 5. Comprehensive Resolution Roadmap

To lift the execution blockers and authorize genuine prospective paper trading, the following sequence of milestones must be completed:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        PAPER EXECUTION READINESS ROADMAP                               │
├──────┬─────────────────────────┬───────────────────────────────────────────────────────┤
│ Step │ Milestone               │ Success Criteria                                      │
├──────┼─────────────────────────┼───────────────────────────────────────────────────────┤
│ 1    │ Author Rulebook Signoff │ Author provides deterministic answers to Priority 1   │
│      │                         │ and Priority 2 questions (Zone, SL selection, Trend). │
├──────┼─────────────────────────┼───────────────────────────────────────────────────────┤
│ 2    │ Algorithmic Alignment   │ Update `pds.py`, `orb.py`, and `pdr.py` to match the   │
│      │                         │ author's signed-off rules exactly; remove all proxies.│
├──────┼─────────────────────────┼───────────────────────────────────────────────────────┤
│ 3    │ Continuous Live Session │ Run `read_only_collector.py` for one full 09:15–15:30  │
│      │ Data Capture            │ NSE session; verify 75 consecutive 5m completed bars  │
│      │                         │ with zero timestamp anomalies or socket resets.       │
├──────┼─────────────────────────┼───────────────────────────────────────────────────────┤
│ 4    │ Reference Level Sync    │ Validate automated pre-market sync of Prior Day High, │
│      │                         │ Low, Close from 5paisa REST API prior to 09:00 IST.   │
├──────┼─────────────────────────┼───────────────────────────────────────────────────────┤
│ 5    │ Shadow Strategy Dry Run │ Run platform in `SHADOW_ONLY` mode during live session│
│      │                         │ with zero fills; verify signal alignment in real time.│
├──────┼─────────────────────────┼───────────────────────────────────────────────────────┤
│ 6    │ Executive Authorization │ Explicit written user approval to toggle              │
│      │                         │ `paper_execution_enabled = True` for ratified methods.│
└──────┴─────────────────────────┴───────────────────────────────────────────────────────┘
```

---

## 6. Audit Verdict

1. **Eligible Strategies for Prospective Paper Fills:** **0**
2. **Current Safe Mode:** `LIVE_READ_ONLY` data capture and `SHADOW_ONLY` signal logging.
3. **Broker Safety State:** Read-only client proxy enforced; 0 real broker orders possible.
4. **Virtual Execution State:** Disabled; fails closed.
