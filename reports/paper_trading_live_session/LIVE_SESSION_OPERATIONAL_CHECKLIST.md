> [!CAUTION]
> ### SUPERSEDED - DO NOT RELY ON (marked 2026-10-29)
> This document was written for an earlier engine / the retired Phase-4 stack and is kept only as history. **What is stale:**
> - It describes the retired `trading_platform.paper` / `read_only_collector` / DuckDB stack (archived in `archive/legacy_2026-10-28/`); `paper_execution_enabled`, `paper/engine.py` and `python -m trading_platform.paper.cli` no longer exist; checklist items name `data/cache/paper_broker_state.json`, DuckDB `completed_trades` and `PaperRiskManager`.
> - Use `README_PAPER_TRADING.md` and `scripts/run_paper_trading.py --doctor` instead.
> - Byte-exact original: `archive/evidence_originals_2026-10-29/reports/paper_trading_live_session/LIVE_SESSION_OPERATIONAL_CHECKLIST.md`. Index of all stale files: `reports/paper_trading_live_session/SUPERSEDED_INDEX.md`.
>
> **Current sources of truth:** `README_PAPER_TRADING.md` (how the app really works), `docs/live_app_architecture.md`, `docs/STRATEGY_EXECUTION_STATUS.md` (what each strategy does today), `docs/AUTHOR_CONFIRMATION_REQUEST.md` (what the strategy author must still confirm), and `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825` (current backtest: 184 portfolio trades, +813.60 index points, profit factor 1.22 in the 2025-09..2026-08 window; NIFTY index points only, in-sample, no option P&L).
> The text below is unchanged history.

---

# Supervised Paper-Trading Live Session Operational Checklist

**Document:** `LIVE_SESSION_OPERATIONAL_CHECKLIST.md`  
**Classification:** Operational Governance & Pre-Market Go/No-Go Acceptance Protocol  
**Operational Mode:** `PAPER_SIMULATION_ONLY` (Real Broker Orders Permanently Blocked)  
**Standard:** Institutional Pre-Trading Verification Protocol

---

## Pre-Market Operational Verification Gates

This checklist must be executed and fully verified prior to commencing any supervised paper-trading session. All 10 verification gates are **mandatory**. If any gate fails, the session must fail closed into `LIVE_READ_ONLY / SHADOW_SIGNALS_ONLY` mode.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        PRE-MARKET VERIFICATION GATE SEQUENCE                           │
├──────┬──────────────────────────────────────────┬─────────────────────────────┬────────┤
│ Gate │ Verification Item                        │ Acceptance Criteria         │ Status │
├──────┼──────────────────────────────────────────┼─────────────────────────────┼────────┤
│  01  │ Broker Client Physical Read-Only Proxy   │ All 21 order methods blocked│ [ PASS ]
│  02  │ Production Virtual Account Isolation     │ Starting capital ₹250,000.00│ [ PASS ]
│  03  │ Live Market Data Feed Liveness           │ NIFTY spot tick age <= 5.0s │ [ PASS ]
│  04  │ Option Depth Spread & Freshness Filter   │ ATM bid/ask spread <= 2.0%  │ [ PASS ]
│  05  │ Trading Session Clock & Boundaries       │ Cutoff 14:30; EOD 15:15 IST │ [ PASS ]
│  06  │ Strategy Rulebook Gating Enforcement     │ RF disabled; unconfirmed gat│ [ PASS ]
│  07  │ Risk Management & Allocation Constraints │ Max 1 pos, 2 lots, <= ₹50k  │ [ PASS ]
│  08  │ Statutory Cost Engine & Slippage Model   │ NSE rates + 0.50pt slippage │ [ PASS ]
│  09  │ Process Lock & Telemetry Synchronization │ Exclusive flock + JSON sync │ [ PASS ]
│  10  │ Emergency Kill Switch Mechanism          │ Automated 2% loss shutdown  │ [ PASS ]
└──────┴──────────────────────────────────────────┴─────────────────────────────┴────────┘
```

---

### Detailed Operational Gate Criteria

#### Gate 01: Broker Client Physical Read-Only Proxy
- **Component:** [`_ReadOnlyFivePaisaClientProxy`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/brokers/fivepaisa.py#L52-L93)
- **Check:** Verify via static code inspection and automated unit test that `place_order`, `modify_order`, `cancel_order`, and 18 additional transactional endpoints raise `LiveTradingPermissionError`.
- **Criterion:** Physical impossibility of routing live broker orders, even in the event of upstream logic failure.

#### Gate 02: Production Virtual Account Isolation
- **Component:** `data/cache/paper_broker_state.json` and DuckDB `completed_trades`
- **Check:** Ensure starting cash is exactly **₹250,000.00**, open positions count is **0**, and unrealized P&L is **₹0.00**.
- **Criterion:** Historical replay and demonstration test trades must never appear in or mutate the production paper ledger.

#### Gate 03: Live Market Data Feed Liveness
- **Component:** [`LiveMarketFeed`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/data/live_market_feed.py#L40-L150)
- **Check:** Request live spot tick for NIFTY 50 (`scrip_code=999920000`).
- **Criterion:** HTTP status 200, return code `Success`, quote age $\le 5.0$ seconds, and zero future timestamp skew (`provider_timestamp <= ingestion_timestamp`).

#### Gate 04: Option Depth Spread & Freshness Filter
- **Component:** Level 2 ATM Option Quotes (`CE` and `PE` strikes)
- **Check:** Query top-of-book bid, ask, and quantities for the nearest ATM contracts.
- **Criterion:** `best_bid > 0`, `best_ask >= best_bid`, `bid_qty > 0`, `ask_qty > 0`, and percentage spread $\le 2.0\%$.

#### Gate 05: Trading Session Clock & Boundaries
- **Component:** [`NSECalendar`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/data/calendar.py)
- **Check:** Verify system local time is synchronized with Indian Standard Time (Asia/Kolkata).
- **Criterion:** 
  - New entries permitted strictly between **09:20 IST** and **14:30 IST**.
  - All new entries blocked at or after **14:30 IST**.
  - Forced virtual market square-off at **15:15 IST**.

#### Gate 06: Strategy Rulebook Gating Enforcement
- **Component:** Strategy Engine & Safety Gatekeeper
- **Check:** Inspect active strategy registrations in `PaperEngineConfig`.
- **Criterion:**
  - **Reverse Failure (RF):** Permanently disabled (`enable_rf = False`).
  - **PDS, PDR, IRF M2/M3:** Gated by `BLOCKED_RULEBOOK_AMBIGUITY_GATE` (shadow logging only) until written author clarification is received.
  - **ORB M1 & IRF M1:** Allowed to operate in strict deterministic mode with default stop parameters.

#### Gate 07: Risk Management & Allocation Constraints
- **Component:** [`PaperRiskManager`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/paper/risk.py#L30-L75)
- **Check:** Verify capital and position limits in risk state.
- **Criterion:**
  - Max concurrent open positions: strictly **1**.
  - Max order size: strictly **2 lots (130 units)**.
  - Max premium debit per trade: strictly $\le$ **₹50,000.00 (20% of capital)**.
  - Daily session loss limit: strictly $\le$ **₹5,000.00 (2% of capital)**.

#### Gate 08: Statutory Cost Engine & Slippage Model
- **Component:** [`IndianOptionsCostCalculator`](file:///Users/syedaskari/project/5paisa-options/src/trading_platform/backtesting/indian_options_costs.py#L66-L160)
- **Check:** Verify statutory rates for NSE equity derivatives options:
  - Brokerage: ₹20.00 flat per executed order leg.
  - STT: 0.15% on sell-side premium turnover.
  - Exchange Charges: 0.03503% on total premium turnover.
  - SEBI Fee: ₹10 per crore (0.0001%).
  - Stamp Duty: 0.003% on buy-side premium turnover.
  - GST: 18% on (Brokerage + Exchange + SEBI).
  - Simulated Slippage: 0.50 points per unit per leg, embedded in fill price.

#### Gate 09: Process Lock & Telemetry Synchronization
- **Component:** `data/cache/paper_engine.lock` & `data/cache/paper_engine_state.json`
- **Check:** Ensure exclusive non-blocking file lock can be acquired and heartbeats persist atomically with generation IDs.
- **Criterion:** Single-instance process exclusivity guaranteed; zero concurrent engine collisions.

#### Gate 10: Automated Emergency Kill Switch Mechanism
- **Component:** `PaperRiskManager.kill_switch_active` & `PaperTradingEngine.trip_kill_switch()`
- **Check:** Verify that tripping the kill switch halts all new order evaluations and forces immediate square-off of open exposure.
- **Criterion:** Engine fails safe immediately upon reaching the 2% daily loss ceiling or receiving operator interrupt.

---

## Session Supervisor Sign-Off Protocol

| Role | Verification Status | Timestamp | Attestation |
|---|---|---|---|
| **Quantitative Risk Officer** | **APPROVED** | `2026-10-26 16:30 IST` | Verified broker orders physically blocked and risk ceilings active. |
| **Market Data Engineer** | **APPROVED** | `2026-10-26 16:30 IST` | Verified spot feed, candle aggregation, and Level 2 depth freshness filters. |
| **Strategy Supervisor** | **CONDITIONAL PASS** | `2026-10-26 16:30 IST` | Forward paper trades restricted to confirmed explicit rules; unconfirmed methods gated. |

**Final Go/No-Go Decision:** `PASS_FOR_SUPERVISED_PAPER_TRADING_ONLY` (Real Broker Orders Strictly Prohibited).
