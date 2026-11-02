> [!CAUTION]
> ### SUPERSEDED - DO NOT RELY ON (marked 2026-10-29)
> This document was written for an earlier engine / the retired Phase-4 stack and is kept only as history. **What is stale:**
> - Same pre-V3 numbers as `BACKTEST_VERSION_RECONCILIATION.md` (274 trades, +2,400.90 pts, PF 1.46); not representative of the current rules. Current: 184 trades, +813.60 pts, PF 1.22 (index points only, in-sample, no option P&L).
> - Its companion ledger `COMBINED_PORTFOLIO_TRADES.csv` (repo root) is the same pre-V3 data.
> - Byte-exact original: `archive/evidence_originals_2026-10-29/reports/paper_trading_live_session/PORTFOLIO_PERFORMANCE.md`. Index of all stale files: `reports/paper_trading_live_session/SUPERSEDED_INDEX.md`.
>
> **Current sources of truth:** `README_PAPER_TRADING.md` (how the app really works), `docs/live_app_architecture.md`, `docs/STRATEGY_EXECUTION_STATUS.md` (what each strategy does today), `docs/AUTHOR_CONFIRMATION_REQUEST.md` (what the strategy author must still confirm), and `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825` (current backtest: 184 portfolio trades, +813.60 index points, profit factor 1.22 in the 2025-09..2026-08 window; NIFTY index points only, in-sample, no option P&L).
> The text below is unchanged history.

---

# Combined Portfolio Performance Report (FIFO Single-Position Execution)

**Document:** `PORTFOLIO_PERFORMANCE.md`  
**Classification:** Quantitative Portfolio Backtest & Capacity Management Audit  
**Applicability:** NIFTY 50 Options Algorithmic Framework  
**Strategy Selection:** PDS Method 1, ORB Method 1, IRF Method 2, PDR Retest Bounce (RF Disabled)  
**Execution Policy:** Strict FIFO Single-Position Execution (Capacity Limit = 1)  
**Companion Trade Ledger:** [`COMBINED_PORTFOLIO_TRADES.csv`](file:///Users/syedaskari/project/5paisa-options/COMBINED_PORTFOLIO_TRADES.csv)  

---

## 1. Executive Summary & Policy Specification

This document presents the institutional performance metrics of the **reproduced Combined Portfolio** executing across verified historical 5-minute candles from `data/processed/nifty/5m/nifty50_5m.parquet` (20,237 bars).

### Core Execution Safeguards
1. **Underlying Index Points Only:** All performance metrics report **pure NIFTY 50 underlying index points**. Zero rupee option leverage, premium decay, or delta-weighted option returns are modeled or claimed.
2. **Next-Available-Bar Execution:** Fills occur strictly at `bar.open` of the subsequent 5-minute candle following signal confirmation, with **0.50 index points slippage** deducted on both entry and exit legs (1.00 pt round-trip).
3. **Single-Position Capacity Constraint:** Exactly one open position is permitted across the entire four-strategy portfolio at any moment. If Strategy A is in an active trade, any subsequent signal from Strategy B, C, or D is **rejected due to capacity limits** until Strategy A exits.
4. **Deterministic Tie-Breaking:** If multiple strategies signal on the exact same bar close, signals are arbitrated by canonical priority (`ORB` &rarr; `PDS` &rarr; `PDR` &rarr; `IRF`).
5. **Conservative Intrabar Resolution:** If a single 5-minute candle spans both the Stop-Loss and Target prices, the backtest engine assumes the Stop-Loss hits first.

---

## 2. Combined Portfolio Performance Summary

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        COMBINED PORTFOLIO PERFORMANCE METRICS                          │
├──────────────────────────────────────────┬──────────────────────┬──────────────────────┤
│ Metric Dimension                         │ Full Dataset (20,237)│ Evaluation Window    │
│                                          │ (Aug 2025–Aug 2026)  │ (Sep 2025–Aug 2026)  │
├──────────────────────────────────────────┼──────────────────────┼──────────────────────┤
│ Total Standalone Candidate Signals       │                  332 │                  315 │
│ Accepted Portfolio Trades (Capacity = 1) │                  288 │                  274 │
│ Rejected Overlapping Signals             │                   44 │                   41 │
│ Portfolio Acceptance Rate                │               86.75% │               86.98% │
├──────────────────────────────────────────┼──────────────────────┼──────────────────────┤
│ Winning Trades Count                     │                  134 │                  126 │
│ Losing Trades Count                      │                  154 │                  148 │
│ Portfolio Win Rate                       │               46.53% │               45.99% │
├──────────────────────────────────────────┼──────────────────────┼──────────────────────┤
│ Gross Underlying Index Points            │          +2,979.50 pt│          +2,674.90 pt│
│ Slippage Points Deducted (1.0 pt / trade)│            -288.00 pt│            -274.00 pt│
│ Net Underlying Index Points              │          +2,691.50 pt│          +2,400.90 pt│
├──────────────────────────────────────────┼──────────────────────┼──────────────────────┤
│ Portfolio Profit Factor                  │                 1.49 │                 1.46 │
│ Expectancy per Trade                     │            +9.35 pts │            +8.76 pts │
│ Maximum Drawdown                         │           -613.00 pts│           -613.00 pts│
│ Maximum Consecutive Losses               │             9 trades │             9 trades │
│ Average Trade Duration                   │            63.2 mins │            63.0 mins │
└──────────────────────────────────────────┴──────────────────────┴──────────────────────┘
```

---

## 3. Strategy Contribution Breakdown

The table below breaks down the accepted portfolio trades, net points contribution, and arbitration rejections across each individual strategy within the Combined Portfolio:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                       PORTFOLIO CONTRIBUTION BY STRATEGY (FULL DATASET)                │
├──────────┬─────────────────────────────┬──────────┬──────────┬────────────┬────────────┤
│ Strategy │ Method Description          │ Accepted │ Rejected │ Net Points │ Win Rate   │
├──────────┼─────────────────────────────┼──────────┼──────────┼────────────┼────────────┤
│ **ORB**  │ Method 1: PDH / PDL Breakout│       62 │        0 │ +747.15 pt │ 46.77%     │
│ **IRF**  │ Method 2: Support Breakdown │       98 │       21 │ +719.40 pt │ 44.90%     │
│ **PDS**  │ Method 1: Red Candle Rej.   │       63 │       12 │ +639.80 pt │ 50.79%     │
│ **PDR**  │ Retest Bounce from Res.     │       65 │       11 │ +585.15 pt │ 44.62%     │
│ **RF**   │ Reverse Failure             │        0 │        0 │    0.00 pt │ DISABLED   │
├──────────┼─────────────────────────────┼──────────┼──────────┼────────────┼────────────┤
│ TOTAL    │ Combined Single-Position    │      288 │       44 │+2,691.50 pt│ 46.53%     │
└──────────┴─────────────────────────────┴──────────┴──────────┴────────────┴────────────┘
```

### Key Analytical Takeaways:
1. **ORB Efficiency:** ORB Method 1 experienced **0 rejections** because its signals trigger strictly at 09:20 IST on the opening candle, giving it first-in priority before subsequent intraday setups develop. It generated +747.15 net index points.
2. **IRF Rejections:** IRF Method 2 experienced the largest number of capacity rejections (**21 rejected trades**), because extended-move pullback breakdowns frequently occur between 10:00 and 12:00 IST while an earlier ORB or PDS trade is still being held.
3. **PDS & PDR Stability:** Both PDS and PDR maintained win rates between 44.6% and 50.8%, with 1:2 risk-reward structures delivering +639.80 pts and +585.15 pts respectively.

---

## 4. Signal Arbitration & Overlap Analysis

Under institutional risk constraints, capital capacity limits prevent executing multiple concurrent positions:
- **Total Overlapping Signals Rejected:** **44 candidate trades** were blocked by an existing open trade.
- **Audit Verification:** Every rejected signal in [`COMBINED_PORTFOLIO_TRADES.csv`](file:///Users/syedaskari/project/5paisa-options/COMBINED_PORTFOLIO_TRADES.csv) explicitly logs the `blocking_trade_id` and the holding timestamp of the active position.
  * *Example:* On 2025-08-22 at 09:20 IST, ORB entered a short position (`ORB_TR_0002`). A simultaneous PDS short setup (`PDS_TR_0003`) was logged as `REJECTED_SIGNAL` with reason: `PORTFOLIO_CAPACITY_LIMIT_REACHED: Active position ORB_TR_0002 held until 09:30 IST.`

---

## 5. Critical Caveat: Ambiguous Rule Exposure

> [!WARNING]
> **Points Performance Does NOT Validate Production Readiness:**
> While the Combined Portfolio demonstrates positive mathematical expectancy (+2,691.50 net points, Profit Factor 1.49), **100% of these trades were produced using heuristic proxies** for author rules that lack numerical definitions (e.g. 5m 200 EMA trend proxy for ORB; 5-bar swing pivot for IRF M2; 10pt zone proximity buffer for PDS/PDR).
> 
> As quantified in [`AMBIGUOUS_RULE_EXPOSURE.csv`](file:///Users/syedaskari/project/5paisa-options/AMBIGUOUS_RULE_EXPOSURE.csv), these results represent research candidates and cannot be treated as author-ratified trading systems until formal developer sign-off is completed.
