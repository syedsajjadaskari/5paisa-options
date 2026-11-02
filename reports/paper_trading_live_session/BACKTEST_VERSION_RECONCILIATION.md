> [!CAUTION]
> ### SUPERSEDED - DO NOT RELY ON (marked 2026-10-29)
> This document was written for an earlier engine / the retired Phase-4 stack and is kept only as history. **What is stale:**
> - Headline numbers are pre-V3: LATEST_REPRODUCTION FIFO portfolio 274 trades, +2,400.90 pts, PF 1.46 (288 trades / +2,691.50 on the full dataset). The current engine gives 184 trades, +813.60 pts, PF 1.22 in the window (194 / +1,139.40 / PF 1.30 on the full dataset); IRF M2 is -183.75 pts (PF 0.93) and PDR about flat (+19.05 pts, PF 1.02) standalone.
> - 'Status: 100% Reconciled' only means the historical versions were reconciled with each other; the strategy code has changed since, so these numbers do not represent the current rules.
> - Byte-exact original: `archive/evidence_originals_2026-10-29/reports/paper_trading_live_session/BACKTEST_VERSION_RECONCILIATION.md`. Index of all stale files: `reports/paper_trading_live_session/SUPERSEDED_INDEX.md`.
>
> **Current sources of truth:** `README_PAPER_TRADING.md` (how the app really works), `docs/live_app_architecture.md`, `docs/STRATEGY_EXECUTION_STATUS.md` (what each strategy does today), `docs/AUTHOR_CONFIRMATION_REQUEST.md` (what the strategy author must still confirm), and `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825` (current backtest: 184 portfolio trades, +813.60 index points, profit factor 1.22 in the 2025-09..2026-08 window; NIFTY index points only, in-sample, no option P&L).
> The text below is unchanged history.

---

# Comprehensive Backtest Version Reconciliation & Architectural Audit

**Document:** `BACKTEST_VERSION_RECONCILIATION.md`  
**Classification:** Quantitative Governance, Backtest Lineage & Engine Reconciliation  
**Applicability:** NIFTY 50 Options Algorithmic Framework  
**Status:** 100% Reconciled Across All Historical Versions  
**Preserved Baseline V1 Hash:** `2083689b897e6039625fd6083c43edf4ac8ab7806c00c247b145520855997e14`  

---

## 1. Executive Summary & Version Lineage

This audit reconciles the three historical backtest generations developed in this repository:
1. **`BASELINE_V1` (Legacy Initial Backtest):** The original baseline produced in Phase 2D across five canonical strategies (including an ad-hoc 40pt stop proxy for RF and IRF Method 1).
2. **`CORRECTED_V2` (Codex Remediation Candidate):** Corrected execution defects (rebased next-bar fills, removed double slippage, 14:30 entry cutoff, separated daily reference levels), disabled RF due to unspecified exits, and evaluated IRF Method 1.
3. **`LATEST_ENGINE_REPRODUCTION` (Current Reconciled Portfolio):** Executes the mandated portfolio selection: **PDS Method 1, ORB Method 1, IRF Method 2, PDR Retest Bounce, and RF Disabled**, with chronological replay, next-available-bar open fills, and deterministic FIFO single-position capacity arbitration.

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       HISTORICAL BACKTEST VERSION COMPARISON MATRIX                                    │
├──────────────────────┬─────────────┬──────────────┬──────────────┬─────────────┬──────────────┬───────────┬────────────┤
│ Version Identifier   │ Scope / Type│ Strategy Set │ Date Window  │ Trade Count │ Net Points   │ Profit Fac│ Max DD     │
├──────────────────────┼─────────────┼──────────────┼──────────────┼─────────────┼──────────────┼───────────┼────────────┤
│ BASELINE_V1          │ Unarbitrated│ 5 Strategies │ 2025-09–2026 │ 353 trades  │ +1,693.65 pts│ 1.24      │ 666.55 pts │
│ BASELINE_V1          │ FIFO Port.  │ 5 Strategies │ 2025-09–2026 │ 302 trades  │ +1,608.90 pts│ 1.27      │ 635.25 pts │
├──────────────────────┼─────────────┼──────────────┼──────────────┼─────────────┼──────────────┼───────────┼────────────┤
│ CORRECTED_V2         │ Unarbitrated│ 4 Strategies │ 2025-09–2026 │ 307 trades  │ +1,993.10 pts│ 1.32      │ 649.50 pts │
│ CORRECTED_V2         │ FIFO Port.  │ 4 Strategies │ 2025-09–2026 │ 271 trades  │ +2,016.20 pts│ 1.38      │ 568.50 pts │
├──────────────────────┼─────────────┼──────────────┼──────────────┼─────────────┼──────────────┼───────────┼────────────┤
│ LATEST_REPRODUCTION  │ Raw Signals │ 4 Strategies │ 2025-09–2026 │ 340 signals │ N/A (Signals)│ N/A       │ N/A        │
│ LATEST_REPRODUCTION  │ Raw Signals │ 4 Strategies │ Full Dataset │ 360 signals │ N/A (Signals)│ N/A       │ N/A        │
│ LATEST_REPRODUCTION  │ Standalone  │ 4 Strategies │ 2025-09–2026 │ 315 trades  │ +2,408.85 pts│ 1.41      │ 649.50 pts │
│ LATEST_REPRODUCTION  │ Standalone  │ 4 Strategies │ Full Dataset │ 332 trades  │ +3,010.05 pts│ 1.52      │ 649.50 pts │
│ LATEST_REPRODUCTION  │ FIFO Port.  │ 4 Strategies │ 2025-09–2026 │ 274 trades  │ +2,400.90 pts│ 1.46      │ 613.00 pts │
│ LATEST_REPRODUCTION  │ FIFO Port.  │ 4 Strategies │ Full Dataset │ 288 trades  │ +2,691.50 pts│ 1.49      │ 613.00 pts │
└──────────────────────┴─────────────┴──────────────┴──────────────┴─────────────┴──────────────┴───────────┴────────────┘
```

> [!IMPORTANT]
> **Distinction Between Standalone and Portfolio Totals:**
> - **Standalone Unarbitrated Trades:** Each strategy is executed in parallel in its own isolated simulation. If ORB and PDS both signal at 09:20, *both trades enter and are counted*.
> - **FIFO Single-Position Portfolio Trades:** A single unified portfolio executes with a strict capacity constraint of at most one active position across all strategies. If ORB enters at 09:20, a concurrent PDS signal is *rejected due to capacity limits*.
> - Comparing standalone unarbitrated totals with portfolio-arbitrated totals without identifying this distinction produces invalid tracking error.

---

## 2. Forensic Reconciliation of Core Trade Counts

The table below explains the exact mathematical cause behind every number cited in the historical audits:

| Metric Cited | Count | Version & Scope | Exact Underlying Cause & Attribution |
|---|---|---|---|
| **Original Unarbitrated Signals** | **353** | `BASELINE_V1`<br>(Standalone) | Sum of all trades emitted across the 5 canonical strategies in the Phase 2D baseline (2025-09-01 to 2026-08-31): ORB M1 (38), IRF M1 (56), PDS M1 (31), PDR (182), and RF (46). RF used an unconfirmed 40pt SL proxy. |
| **Corrected Unarbitrated Signals** | **307** | `CORRECTED_V2`<br>(Standalone) | In the Codex remediation, RF was **disabled** because the author PDF omitted exit rules ($353 - 46 = 307$). Strategy methods remained identical (IRF M1, ORB M1, PDS M1, PDR), but execution mechanics were corrected (fill-price rebasing, double-slippage removal). |
| **Current In-Window Raw Signals** | **340** | `LATEST_REPRODUCTION`<br>(Raw Signals) | Generated by the 4 authorized portfolio strategies in the 2025-09-01 to 2026-08-31 window. Uses **IRF Method 2** instead of Method 1: ORB M1 (58), IRF M2 (127), PDS M1 (74), PDR Retest (81). IRF M2 generates 71 more signals than Method 1 because extension pullback breakdowns occur frequently throughout the trading day. |
| **Original FIFO Trades** | **302** | `BASELINE_V1`<br>(Portfolio Capacity = 1) | Replaying the 353 Baseline V1 trades through the single-position FIFO arbitrator rejected **51 overlapping trades**, accepting exactly 302 trades (+1,608.90 net index points). |
| **Corrected FIFO Trades** | **271** | `CORRECTED_V2`<br>(Portfolio Capacity = 1) | Replaying the 307 Corrected V2 trades through the single-position FIFO arbitrator rejected **36 overlapping trades**, accepting exactly 271 trades (+2,016.20 net index points). |
| **Latest Engine-Replayed Trades** | **332** | `LATEST_REPRODUCTION`<br>(Standalone Full Dataset) | Sum of standalone engine-executed trades across the **entire historical dataset (20,237 bars, spanning 2025-08-01 to 2026-08-31)**: ORB M1 (62) + IRF M2 (119) + PDS M1 (75) + PDR Retest (76) = 332 standalone trades. |

---

## 3. Structural Drivers of Variation

### Driver 1: Strategy Method Selection (IRF Method 2 vs Method 1)
- In `BASELINE_V1` and `CORRECTED_V2`, the backtest engine evaluated **IRF Method 1** (Immediate Two-Bar Failure). Method 1 requires price to close above IRH on candle 1 and immediately close back below IRH on candle 2. This strict pattern produced only 56 trades.
- In `LATEST_REPRODUCTION`, the engine strictly implements the user-mandated portfolio selection: **IRF Method 2** (Extended Move & Pullback Support Break). Method 2 monitors extended multi-bar rallies above IRH and fires when price breaks below intraday pullback support. This rule fires across a wider intraday window, generating **135 raw signals across the full dataset (127 in-window)**.

### Driver 2: Eligibility Gates (RF Disabled)
- In `BASELINE_V1`, Reverse Failure (RF) was included using an ad-hoc 40pt stop-loss and 80pt profit target, contributing 46 trades.
- In `CORRECTED_V2` and `LATEST_REPRODUCTION`, RF is **strictly disabled** because the author's PDF (`RF strategy.pdf`) completely lacks canonical stop-loss, profit target, and trailing rules. This permanently removed 46 trades from the portfolio.

### Driver 3: Date Windows (Evaluation Window vs Full Historical Archive)
- The manual journal and Phase 2D evaluation window covers **September 1, 2025 to August 31, 2026** (248 trading days).
- The full processed 5-minute dataset (`data/processed/nifty/5m/nifty50_5m.parquet`) contains **20,237 bars**, including August 2025 pre-warmup sessions.
- In-window signals = 340; full dataset signals = 360 (+20 signals in August 2025).
- In-window standalone trades = 315; full dataset standalone trades = 332 (+17 trades in August 2025).

### Driver 4: Portfolio Capacity Arbitration
- Standalone execution assumes infinite capacity (parallel execution).
- FIFO single-position arbitration enforces real-world capacity (maximum 1 active position across all strategies).
- Under FIFO single-position arbitration across the full dataset, **44 candidate trades are rejected** due to overlapping position duration, yielding **288 accepted portfolio trades** ([`COMBINED_PORTFOLIO_TRADES.csv`](file:///Users/syedaskari/project/5paisa-options/COMBINED_PORTFOLIO_TRADES.csv)).

---

## 4. Preservation of Source Artifacts & Hashes

All previous version outputs and data files remain immutably preserved in the repository:

- `BASELINE_V1` Parquet: [`reports/phase2d/backtest_trades.parquet`](file:///Users/syedaskari/project/5paisa-options/reports/phase2d/backtest_trades.parquet)  
  SHA-256: `2083689b897e6039625fd6083c43edf4ac8ab7806c00c247b145520855997e14`
- `CORRECTED_V2` Reconciliation: [`reports/independent_codex_audit/remediation/BACKTEST_TRADE_RECONCILIATION.csv`](file:///Users/syedaskari/project/5paisa-options/reports/independent_codex_audit/remediation/BACKTEST_TRADE_RECONCILIATION.csv)
- Verified 5M Market Data: [`data/processed/nifty/5m/nifty50_5m.parquet`](file:///Users/syedaskari/project/5paisa-options/data/processed/nifty/5m/nifty50_5m.parquet)  
  SHA-256: `02984db58b9697e1bb8862b2a1a0284e94648d1536b8d9a5c2e61306d1ed684b`
- Audited Manual Journal: [`reports/manual_journal/manual_trades.parquet`](file:///Users/syedaskari/project/5paisa-options/reports/manual_journal/manual_trades.parquet)  
  SHA-256: `f05ac38ddf450b2f96f361c57c8fb0ea15d288232d64db11825eb13c7749ae61`
- Current Git Revision: `a5773b136f1c2f702f3479a10a8c4d966d3149eb`
