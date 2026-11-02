> [!CAUTION]
> ### SUPERSEDED - DO NOT RELY ON (marked 2026-10-29)
> This document was written for an earlier engine / the retired Phase-4 stack and is kept only as history. **What is stale:**
> - It reports the pre-V3 strategy engine (5-bar swing pivot, 10 pt zone buffer, no first-red-candle / open-vs-zone rules, 360 signals / 288 FIFO trades); the code now uses 5 pt proximity, first-red-candle IRF support and the gates listed in `docs/AUTHOR_CONFIRMATION_REQUEST.md`.
> - 'Status: 100% Reconciled Against Author PDFs, Historical Data, and Source Code' is NOT true for today's code: it cites '10pt zone buffer', '5-bar swing pivot', 'ORB 38 signals', 'PDR 38 signals', 'IRF 135 signals'; current in-window raw signals are PDS 16, ORB 49, IRF 118, PDR 35 (218 total).
> - Byte-exact original: `archive/evidence_originals_2026-10-29/reports/paper_trading_live_session/CORRECTED_STRATEGY_AUTHORIZATION_MATRIX.md`. Index of all stale files: `reports/paper_trading_live_session/SUPERSEDED_INDEX.md`.
>
> **Current sources of truth:** `README_PAPER_TRADING.md` (how the app really works), `docs/live_app_architecture.md`, `docs/STRATEGY_EXECUTION_STATUS.md` (what each strategy does today), `docs/AUTHOR_CONFIRMATION_REQUEST.md` (what the strategy author must still confirm), and `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825` (current backtest: 184 portfolio trades, +813.60 index points, profit factor 1.22 in the 2025-09..2026-08 window; NIFTY index points only, in-sample, no option P&L).
> The text below is unchanged history.

---

# Corrected Strategy Authorization Matrix

**Document:** `CORRECTED_STRATEGY_AUTHORIZATION_MATRIX.md`  
**Classification:** Definitive Algorithmic Strategy Governance & Pre-Execution Gate  
**Applicability:** NIFTY 50 Options Algorithmic Paper Trading Platform  
**Status:** 100% Reconciled Against Author PDFs, Historical Data, and Source Code  
**Authorized Paper Eligible Strategies:** **0 (ZERO)**

---

## 1. Executive Authorization Summary

This document establishes the definitive authorization matrix for the five designated portfolio strategies in accordance with strict operational, mathematical, and legal safeguards.

### Core Policy Mandates
1. **Preservation of Portfolio Selection:** The designated portfolio strategies are strictly:
   - **PDS Method 1** (Good Red Candle into Prior Day Support-as-Resistance)
   - **ORB Method 1** (Previous Day High Breakout / Previous Day Low Breakdown)
   - **IRF Method 2** (Extended Move Above IRH & Pullback Support Break / Extended Move Below IRL & Pullback Resistance Break)
   - **PDR Retest Bounce** (Bullish Green Bounce at Prior Day Resistance-as-Support)
   - **RF Disabled** (Permanently excluded from live or paper execution)
   *Note: IRF Method 1 is evaluated strictly as research and is NOT an authorized portfolio replacement.*
2. **Zero Paper Eligibility Without Author Rule Confirmation:** A strategy cannot enter `PAPER_ELIGIBLE` status merely because a mechanical backtest baseline generates trade signals. Under the quantitative risk gate, any strategy with unresolved rule ambiguities, discretionary lookbacks, or unverified heuristic proxies MUST be classified as `SHADOW_ONLY`.
3. **RF Permanently Disabled:** Due to the complete absence of canonical stop-loss, target, and trailing exit rules in the author's reference PDF (`RF strategy.pdf`), Reverse Failure remains permanently disabled.
4. **Read-Only Operational State:** All broker order submission endpoints remain blocked by `_ReadOnlyFivePaisaClientProxy`, and prospective virtual paper execution remains disabled (`paper_execution_enabled = False`).

---

## 2. Definitive Strategy Authorization Matrix

| Strategy & Method | Entry Requirements | Stop-Loss Requirements | Target Requirements | Author-Confirmed Rules (PDF) | Unresolved Assumptions & Ambiguities | Authorization Decision | Exact Evidence Supporting Decision |
|---|---|---|---|---|---|---|---|
| **PDS Method 1**<br>*(Strong Red Candle at Prior Support)* | • Gap-down open ($Open < PDC$).<br>• Intraday rally retests yesterday's support-turned-resistance zone.<br>• Strong red bearish candle closes inside or immediately rejecting zone.<br>• Sell/PE Buy on candle close. | Fixed 35 or 40 index points based on recent candle high. | Fixed 70 index points. | • Gap-down open is compulsory.<br>• Retest of prior day support zone is mandatory.<br>• Fixed 70 pt target is documented. | 1. **Zone Construction:** Author has not defined whether the prior support zone is the body/wick of the single lowest 5m bar or a multi-bar consolidation cluster.<br>2. **Proximity Tolerance:** Missing quantitative threshold for when a candle "approaches" the zone without intersecting it.<br>3. **Stop Selection Formula:** Missing deterministic rule governing whether 35 or 40 pts is selected.<br>4. **Candle Threshold:** "Good red candle" is subjective without a minimum body percentage. | `SHADOW_ONLY` | • Python engine `src/trading_platform/strategies/pds.py` implements proxy rules (10pt zone buffer, 35pt default SL).<br>• Baseline produces 31 historical trades, but heuristic proxy parameters diverge from pure mechanical intent.<br>• Fails paper eligibility gate until Priority 1 author confirmations are signed off. |
| **ORB Method 1**<br>*(PDH/PDL Breakout/Breakdown)* | • Completed 09:15–09:20 5m opening bar.<br>• Long: $Close_{09:20} > PDH$ in established uptrend.<br>• Short: $Close_{09:20} < PDL$ in established downtrend.<br>• Strong candle body closing near extreme. | Fixed 30 index points (or placed at opposite side of opening bar). | Fixed 60 index points (1:2 Risk/Reward). | • First 5m candle close determines entry.<br>• Long on PDH breakout; Short on PDL breakdown.<br>• 30pt SL and 60pt TP explicitly stated in rulebook (pp. 1–3, 11). | 1. **Established Trend Proxy:** The author specifies "trade only in the direction of the established trend" without a mechanical formula. The engine substitutes a 5m 200 EMA filter.<br>2. **Candle Body Threshold:** Author states "strong body and close near high/low" without numeric ratio. Engine uses 50% body ratio proxy. | `SHADOW_ONLY` | • Strategy engine `src/trading_platform/strategies/orb.py` produces 38 valid historical signals in 2025–2026.<br>• Backtest confirms mechanical viability, but 200 EMA slope proxy is not author-confirmed.<br>• Cannot enter `PAPER_ELIGIBLE` until trend definition is author-ratified. |
| **IRF Method 2**<br>*(Extended Move & Pullback Support Break)* | • 09:15–09:20 Initial Range established ($IRH, IRL$).<br>• Multi-candle extension above IRH ($n > 1$ bars).<br>• Pullback forms higher low support above IRH.<br>• Reversal red candle breaks below pullback support.<br>• Short/PE Buy on breakdown close. | Fixed 35 or 40 index points (or above recent swing high). | Fixed 60 index points. | • Initial range defined by 09:15–09:20 bar.<br>• Reversal triggered after extended move fails.<br>• Breakdown below pullback support.<br>• Fixed 60 pt target. | 1. **Pullback Support Lookback:** Author PDF (pp. 6–10) visually diagrams pullback support but provides no numerical lookback window (e.g. 3 bars vs 5 bars vs swing low pivot).<br>2. **Extension Threshold:** Missing explicit minimum extension distance beyond IRH before a reversal qualifies.<br>3. **Stop Selection:** 35 vs 40 pt ambiguity based on prior green candle size. | `SHADOW_ONLY` | • Strategy engine `src/trading_platform/strategies/irf.py` implements Method 2 (`IRF_METHOD_2_SUPPORT_BREAK` and `IRF_METHOD_2_RESISTANCE_BREAK`), yielding 135 historical signals.<br>• Mechanically functional but relies on an internal 5-bar swing pivot proxy.<br>• Must remain `SHADOW_ONLY` until pullback support lookback is confirmed. |
| **PDR Retest Bounce**<br>*(Prior Resistance as Support)* | • Gap-up open ($Open > PDC$).<br>• Intraday pullback retests yesterday's resistance-turned-support zone.<br>• Bullish green bounce candle rejects zone.<br>• Long/CE Buy on candle close. | Fixed 35 or 40 index points based on recent candle low. | Fixed 70 index points. | • Gap-up open is compulsory.<br>• Retest of prior resistance zone is mandatory.<br>• Bullish green candle confirmation.<br>• Fixed 70 pt target is documented. | 1. **Zone Construction:** Author has not defined whether resistance zone is derived from the highest 5m bar or day's closing consolidation.<br>2. **Retest Proximity Tolerance:** Undefined point tolerance for near-miss retests.<br>3. **Stop Selection Formula:** 35 vs 40 pt selection rule unverified.<br>4. **Bounce Confirmation:** Heuristic body ratio proxy applied in code. | `SHADOW_ONLY` | • Strategy engine `src/trading_platform/strategies/pdr.py` produces 38 historical signals.<br>• Requires heuristic 10pt zone proximity buffer and 35pt baseline stop.<br>• Cannot enter `PAPER_ELIGIBLE` until author defines exact zone boundaries. |
| **RF**<br>*(Reverse Failure)* | • Failed breakdown in established uptrend with subsequent reclaim of broken level.<br>• Buy on reclaim candle close. | **Completely Unspecified** in Author PDF. | **Completely Unspecified** in Author PDF. | • Concept of failed breakdown and reclaim in an uptrend (pp. 1–6). | • **Zero mechanical exit rules:** Author PDF does not define stop-loss distance, target points, trailing rules, or time stop.<br>• Backtest baseline used an ad-hoc 40pt SL / 80pt TP proxy. | `PERMANENTLY_DISABLED` | • `RF strategy.pdf` contains no actionable exit specifications.<br>• Baseline generated 11 journal matches, but all used invented exit assumptions.<br>• Excluded from portfolio execution by mandate. |

---

## 3. Research Strategy Evaluation: IRF Method 1 (Non-Portfolio Research Only)

> [!NOTE]
> **Separation of Research from Portfolio Authorization:**  
> As directed, IRF Method 1 was evaluated separately as research. It is **NOT** authorized to replace IRF Method 2 in the portfolio.

| Metric / Dimension | IRF Method 1 (Research Only) | IRF Method 2 (Mandated Portfolio Strategy) |
|---|---|---|
| **Core Concept** | Immediate two-bar failure: Bar 1 closes above IRH, Bar 2 immediately closes back inside IRH. | Extended multi-bar rally above IRH, formation of pullback support, followed by breakdown. |
| **Rule Determinism** | High: Strict two-bar sequence ($C_1 > IRH$, $C_2 < IRH$). Requires no swing lookback. | Moderate: Requires dynamic tracking of intraday pullback support levels above IRH. |
| **Baseline Backtest Signal Count** | 56 trades in baseline research backtest. | 135 reproducible signals generated across 2025–2026 dataset. |
| **Primary Ambiguity** | Stop-loss distance selection (35 vs 40 pts based on $C_1$ height). | Support lookback window (bars $k$) and extension distance threshold. |
| **Portfolio Status** | **NON-PORTFOLIO RESEARCH ONLY** (Cannot replace Method 2 without explicit written authorization). | **MANDATED PORTFOLIO STRATEGY** (Classified as `SHADOW_ONLY` pending lookback confirmation). |

---

## 4. Prioritized Author Confirmations Required for Paper Trading

To upgrade any strategy from `SHADOW_ONLY` to `PAPER_ELIGIBLE`, the strategy author must confirm the following explicit parameters:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                      PRIORITIZED AUTHOR CONFIRMATION ROADMAP                           │
├──────────┬─────────────────────────────┬───────────────────────────────────────────────┤
│ Priority │ Target Strategies           │ Required Author Clarification                 │
├──────────┼─────────────────────────────┼───────────────────────────────────────────────┤
│ P1       │ PDS Method 1 & PDR Retest   │ 1. Prior Day Reference Zone Definition:       │
│          │                             │    - Exact high/low of extreme 5m candle, or  │
│          │                             │    - Multi-bar cluster around extreme?        │
│          │                             │ 2. Proximity Tolerance:                       │
│          │                             │    - Within 5 pts, 10 pts, or exact touch?    │
│          │                             │ 3. SL Selection Rule:                         │
│          │                             │    - Exact boundary between 35 and 40 pts?   │
├──────────┼─────────────────────────────┼───────────────────────────────────────────────┤
│ P2       │ ORB Method 1                │ 1. Established Trend Definition:              │
│          │                             │    - 5m 200 EMA slope, Daily 200 DMA, or     │
│          │                             │      swing structure?                         │
│          │                             │ 2. Candle Body Ratio:                         │
│          │                             │    - Is 50% body ratio acceptable?            │
├──────────┼─────────────────────────────┼───────────────────────────────────────────────┤
│ P3       │ IRF Method 2                │ 1. Pullback Support Lookback:                 │
│          │                             │    - Bar count (e.g. 3, 5) or pivot formula?  │
│          │                             │ 2. Minimum Extension:                         │
│          │                             │    - Minimum points above IRH before reversal?│
├──────────┼─────────────────────────────┼───────────────────────────────────────────────┤
│ P4       │ RF (Reverse Failure)        │ 1. Complete Exit Specification:               │
│          │                             │    - Canonical SL, Target, and Trailing rules.│
└──────────┴─────────────────────────────┴───────────────────────────────────────────────┘
```

---

## 5. Audit Conclusion & Gatekeeper Verdict

- **Total Authorized Strategies for Prospective Paper Fills:** **0**
- **Total Strategies Authorized for Shadow Logging:** **4** (PDS M1, ORB M1, IRF M2, PDR Retest Bounce)
- **Total Strategies Permanently Disabled:** **1** (RF)
- **Broker Safety Gate:** `_ReadOnlyFivePaisaClientProxy` active; 21 order methods raise `PermissionError`.
- **Virtual Paper Engine Status:** `paper_execution_enabled: bool = False` (Fails closed).
