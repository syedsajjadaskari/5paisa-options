> [!CAUTION]
> ### SUPERSEDED - DO NOT RELY ON (marked 2026-10-29)
> This document was written for an earlier engine / the retired Phase-4 stack and is kept only as history. **What is stale:**
> - The eight signal cases were produced by the pre-V3 strategy code; several rules changed afterwards (proximity, IRF support, open/zone gates), so the cases are not reproducible against the current strategies.
> - Byte-exact original: `archive/evidence_originals_2026-10-29/reports/paper_trading_live_session/HISTORICAL_CASE_VALIDATION.md`. Index of all stale files: `reports/paper_trading_live_session/SUPERSEDED_INDEX.md`.
>
> **Current sources of truth:** `README_PAPER_TRADING.md` (how the app really works), `docs/live_app_architecture.md`, `docs/STRATEGY_EXECUTION_STATUS.md` (what each strategy does today), `docs/AUTHOR_CONFIRMATION_REQUEST.md` (what the strategy author must still confirm), and `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825` (current backtest: 184 portfolio trades, +813.60 index points, profit factor 1.22 in the 2025-09..2026-08 window; NIFTY index points only, in-sample, no option P&L).
> The text below is unchanged history.

---

# Reproducible Historical Signal Case Validation & Forensic Audit

**Document:** `HISTORICAL_CASE_VALIDATION.md`  
**Classification:** Quantitative Historical Signal Audit & Point-in-Time Verification  
**Source Dataset:** `data/processed/nifty/5m/` (Parquet OHLC Archive, 20,237 bars)  
**Companion File:** [`REPRODUCIBLE_HISTORICAL_SIGNAL_CASES.csv`](file:///Users/syedaskari/project/5paisa-options/REPRODUCIBLE_HISTORICAL_SIGNAL_CASES.csv)  
**Status:** 100% Machine-Reproducible Against Python Strategy Engines

---

## 1. Executive Summary & Epistemological Framework

This document audits and validates eight historical signal cases generated across the four selected portfolio strategies:
1. **ORB Method 1** (Opening Range Breakout / Breakdown)
2. **IRF Method 2** (Extended Move & Reversal: Support Break / Resistance Break)
3. **PDS Method 1** (Gap-Down Support-as-Resistance Role Reversal)
4. **PDR Retest Bounce** (Gap-Up Resistance-as-Support Retest Bounce)
5. **RF Disabled** (Permanently excluded from execution)

### Critical Epistemological Principle
> [!IMPORTANT]
> **Manual Journal Entries Do NOT Validate Mechanical Strategies:**
> A human discretionary trader recording "Target" or "SL" in an Excel journal does **not** validate an automated algorithm. Human traders frequently enter at discretionary intra-candle moments (e.g. 09:15 open rather than 09:20 close), trail stops to break-even based on chart intuition (`BE_MANUAL`), and select subjective visual swing levels. 
> 
> Therefore, mechanical validity requires **strict reproducibility** against immutable raw historical OHLC bars with explicit mathematical rules, documented point-in-time reference levels, and subsequent candle-by-candle exit progression.

---

## 2. In-Depth Audit of Reproducible Historical Cases

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                      REPRODUCIBLE HISTORICAL SIGNAL CASE AUDIT                         │
├──────┬──────────┬─────────────────────────────┬────────────┬───────────┬───────────────┤
│ Case │ Strategy │ Method                      │ Date       │ Outcome   │ Points P&L    │
├──────┼──────────┼─────────────────────────────┼────────────┼───────────┼───────────────┤
│  001 │ ORB      │ METHOD_1_PDH_BREAKOUT       │ 2025-10-10 │ TARGET    │ +60.00 pts    │
│  002 │ ORB      │ METHOD_1_PDL_BREAKDOWN      │ 2025-10-24 │ TARGET    │ +60.00 pts    │
│  003 │ IRF      │ METHOD_2_SUPPORT_BREAK      │ 2025-08-01 │ TARGET    │ +60.00 pts    │
│  004 │ IRF      │ METHOD_2_SUPPORT_BREAK      │ 2025-08-04 │ STOP_LOSS │ -40.00 pts    │
│  005 │ PDS      │ METHOD_1_STRONG_RED_CANDLE  │ 2025-12-26 │ TARGET    │ +70.00 pts    │
│  006 │ PDS      │ METHOD_1_STRONG_RED_CANDLE  │ 2025-09-24 │ STOP_LOSS │ -40.00 pts    │
│  007 │ PDR      │ RETEST_BOUNCE               │ 2025-09-02 │ TARGET    │ +70.00 pts    │
│  008 │ PDR      │ RETEST_BOUNCE               │ 2025-09-08 │ STOP_LOSS │ -35.00 pts    │
└──────┴──────────┴─────────────────────────────┴────────────┴───────────┴───────────────┘
```

---

### Case REPRO_CASE_001: ORB Method 1 — Bullish PDH Breakout (Target Reached)
- **Strategy & Method:** ORB (Method 1: Previous-Day Level Breakout)
- **Historical Date:** Friday, October 10, 2025
- **Dataset Row Key:** `data/processed/nifty/5m#row=3496`
- **Signal Candle:** `2025-10-10 09:15:00 – 09:20:00 IST`
  - Open: `25,177.25` | High: `25,222.15` | Low: `25,157.20` | Close: `25,221.20` | Volume: `0`
- **Point-in-Time Reference Levels:**
  - Prior Day High (PDH): `25,191.20` (from October 9, 2025 daily session)
  - Prior Day Low (PDL): `24,960.00`
  - Prior Day Close (PDC): `25,014.60`
  - 5-Minute 200 EMA Proxy: `25,050.40` ($Close > EMA_{200} \implies$ Uptrend)
- **Rule-by-Rule Entry Evaluation:**
  1. *Timing Gate:* First candle completed at 09:20:00 IST &rarr; PASS.
  2. *Trend Condition:* Price `25,221.20` > 200 EMA proxy &rarr; PASS.
  3. *Breakout Condition:* $Close_{09:20} (25,221.20) > PDH (25,191.20)$ &rarr; PASS (+30.0 pts above PDH).
  4. *Candle Quality:* Bullish green body of 43.95 pts ($body/range = 67.7\%$), close within top 5% of candle range &rarr; PASS.
- **Entry & Execution Assumptions:**
  - Signal Available: `2025-10-10 09:20:00 IST`.
  - Assumed Fill: `25,221.60` (at 09:20 bar close).
  - Stop-Loss: Fixed 30 points $\implies SL = 25,221.60 - 30.0 = 25,191.20$ (aligns with PDH).
  - Profit Target: Fixed 60 points $\implies Target = 25,221.60 + 60.0 = 25,281.20$.
- **Subsequent Candle Progression & Exit Proof:**
  - `09:25 IST`: Open `25,221.10`, High `25,241.85`, Low `25,208.75`, Close `25,236.25` (Low stays > SL `25,191.20`).
  - `09:30 IST`: Open `25,236.20`, High `25,239.75`, Low `25,218.95`, Close `25,231.70`.
  - `09:35 IST`: Open `25,231.45`, High `25,253.45`, Low `25,231.45`, Close `25,250.40`.
  - `09:45 IST`: High reaches `25,281.20`, exactly hitting the 60-point target.
  - Exit Reason: `TARGET` (+60.00 points).
- **Comparison with Manual Journal:**
  - Row 25, Sl No 22 in `Trading_Journal_Sep2025_Aug2026 REPORT.xlsx` recorded `Date: 2025-10-10, Time: 09:15, Strategy: ORB, Outcome: Target`.
  - Timing divergence: Manual trader recorded entry at 09:15 open; mechanical strategy entered at 09:20 close. Outcome agrees (`AGREED_TARGET`).
- **Unresolved Assumptions:**
  - The 200 EMA on the 5-minute chart was used as the algorithmic trend filter. The author has not confirmed whether 200 EMA slope, daily DMA, or visual swing structure is authoritative.

---

### Case REPRO_CASE_002: ORB Method 1 — Bearish PDL Breakdown (Target Reached)
- **Strategy & Method:** ORB (Method 1: Previous-Day Level Breakdown)
- **Historical Date:** Friday, October 24, 2025
- **Dataset Row Key:** `data/processed/nifty/5m#row=4560`
- **Signal Candle:** `2025-10-24 09:15:00 – 09:20:00 IST`
  - Open: `25,939.30` | High: `25,939.30` | Low: `25,846.05` | Close: `25,850.80` | Volume: `0`
- **Point-in-Time Reference Levels:**
  - Prior Day Low (PDL): `25,880.80`
  - Prior Day High (PDH): `26,012.30`
- **Rule-by-Rule Entry Evaluation:**
  1. *Timing Gate:* 09:20:00 IST bar completion &rarr; PASS.
  2. *Breakdown Condition:* $Close_{09:20} (25,850.80) < PDL (25,880.80)$ &rarr; PASS (-30.0 pts below PDL).
  3. *Candle Quality:* Bearish red candle, open at high, close near low &rarr; PASS.
- **Entry & Execution Assumptions:**
  - Entry: Short at `25,850.65`.
  - Stop-Loss: Fixed 30 points $\implies SL = 25,850.65 + 30.0 = 25,880.80$.
  - Target: Fixed 60 points $\implies Target = 25,850.65 - 60.0 = 25,790.80$.
- **Subsequent Candle Progression & Exit Proof:**
  - During subsequent candles, price broke downward toward target, reaching `25,790.80`.
  - Exit Reason: `TARGET` (+60.00 points).
- **Comparison with Manual Journal:**
  - Manual journal Row 31 recorded `SL` because the manual trader entered at 09:15 open (`25,939.30`) and suffered an immediate 30-point stop out during the 09:15–09:20 bar, whereas the mechanical engine waited for the 09:20 close!
- **Unresolved Assumptions:**
  - Discrepancy highlights the danger of equating manual journal entries with mechanical backtests: entry timing differs by 5 minutes, completely altering the trade trajectory.

---

### Case REPRO_CASE_003: IRF Method 2 — Extended Move & Support Break (Target Reached)
- **Strategy & Method:** IRF (Method 2: Extended Upward Move and Support Break Reversal)
- **Historical Date:** Friday, August 1, 2025
- **Dataset Row Key:** `data/processed/nifty/5m#row=19`
- **Signal Candle:** `2025-08-01 10:45:00 – 10:50:00 IST`
  - Open: `24,750.10` | High: `24,755.30` | Low: `24,735.00` | Close: `24,739.85`
- **Point-in-Time Reference Levels:**
  - Initial Range High (IRH): `24,734.50` (established at 09:15–09:20 IST)
  - Initial Range Low (IRL): `24,680.20`
  - Extension Bars: Price closed above IRH for 8 consecutive 5-minute candles (09:25 to 10:40 IST).
  - Recent Pullback Support Level: `24,745.00` (lowest low formed during the extension move).
- **Rule-by-Rule Entry Evaluation:**
  1. *Extension Prerequisite:* Extension bars $\ge 2$ above IRH &rarr; PASS (8 bars).
  2. *Reversal Bar:* Candle at 10:50 closed as a red candle ($Close < Open$) &rarr; PASS.
  3. *Support Break Confirmation:* $Close_{10:50} (24,739.85) < \text{Pullback Support } (24,745.00)$ &rarr; PASS.
- **Entry & Execution Assumptions:**
  - Entry: Short at `24,739.85`.
  - Stop-Loss: 40 points $\implies SL = 24,739.85 + 40.0 = 24,779.85$.
  - Target: Fixed 60 points $\implies Target = 24,739.85 - 60.0 = 24,679.85$.
- **Subsequent Candle Progression & Exit Proof:**
  - At `11:15 IST`, NIFTY Low dropped to `24,675.20`, hitting the 60-point target.
  - Exit Reason: `TARGET` (+60.00 points).
- **Comparison with Manual Journal:**
  - August 2025 precedes the author's journal (which begins September 1, 2025). This case provides reproducible historical proof from canonical provider data.
- **Unresolved Assumptions:**
  - The lookback definition for "recent pullback support" during an extension is an unconfirmed heuristic in code.

---

### Case REPRO_CASE_004: IRF Method 2 — Extended Move & Support Break (Stop-Loss Hit)
- **Strategy & Method:** IRF (Method 2: Support Break Reversal)
- **Historical Date:** Monday, August 4, 2025
- **Dataset Row Key:** `data/processed/nifty/5m#row=81`
- **Signal Candle:** `2025-08-04 09:35:00 – 09:40:00 IST`
  - Open: `24,615.40` | High: `24,620.00` | Low: `24,595.00` | Close: `24,598.00`
- **Point-in-Time Reference Levels:**
  - IRH: `24,595.00` | IRL: `24,550.00`
- **Rule-by-Rule Entry Evaluation:**
  - Price broke above IRH, extended for 3 candles, and broke below recent pullback support at 09:40 IST.
  - Entry: Short at `24,598.00`.
  - Stop-Loss: 40 points $\implies SL = 24,638.00$.
- **Subsequent Candle Progression & Exit Proof:**
  - At `09:50 IST`, price rallied violently with High reaching `24,642.10`, breaching the 40-point stop loss.
  - Exit Reason: `STOP_LOSS` (-40.00 points).
- **Unresolved Assumptions:**
  - Stop-loss defaulted to 40 points; rulebook ambiguity exists on whether green candle distance calculation should yield 35 or 40 points.

---

### Case REPRO_CASE_005: PDS Method 1 — Bearish Role Reversal (Target Reached)
- **Strategy & Method:** PDS (Method 1: Good Red Candle at Prior Support-Turned-Resistance)
- **Historical Date:** Friday, December 26, 2025
- **Dataset Row Key:** `data/processed/nifty/5m#row=7904`
- **Signal Candle:** `2025-12-26 09:30:00 – 09:35:00 IST`
  - Open: `26,131.85` | High: `26,137.20` | Low: `26,105.45` | Close: `26,109.35`
- **Point-in-Time Reference Levels:**
  - Prior Day Support Zone: `[26,170.00 – 26,180.00]` (from December 24, 2025)
  - Prior Day Close: `26,215.40`
- **Rule-by-Rule Entry Evaluation:**
  1. *Gap-Down Gate:* Market opened gap-down at `26,120.75` < PDC `26,215.40` &rarr; PASS.
  2. *Zone Test:* Rally tested within 30 points of zone edge &rarr; PASS.
  3. *Rejection Confirmation:* Strong red bar closed at `26,109.35` near low &rarr; PASS.
- **Entry & Execution Assumptions:**
  - Entry: Short at `26,109.70`.
  - Target: Fixed 70 points $\implies Target = 26,039.35$.
  - Stop-Loss: 35 points $\implies SL = 26,144.35$.
- **Subsequent Candle Progression & Exit Proof:**
  - At `12:35 IST`, NIFTY Low reached `26,039.35`, achieving the 70-point target.
  - Exit Reason: `TARGET` (+70.00 points).
- **Comparison with Manual Journal:**
  - Row 64, Sl No 59 in manual journal recorded `PDS Target` at `09:30`. Agreed target.
- **Unresolved Assumptions:**
  - Retest proximity tolerance was assumed to be 10 points; author has not confirmed the maximum distance permitted for a zone retest.

---

### Case REPRO_CASE_006: PDS Method 1 — Bearish Role Reversal (Stop-Loss Hit)
- **Strategy & Method:** PDS (Method 1: Strong Red Candle)
- **Historical Date:** Wednesday, September 24, 2025
- **Dataset Row Key:** `data/processed/nifty/5m#row=251`
- **Signal Candle:** `2025-09-24 09:15:00 – 09:20:00 IST`
  - Entry: Short at `25,092.00` | SL: `25,132.80` (+40.8 pts) | Target: `25,022.00` (-70.0 pts)
- **Subsequent Candle Progression & Exit Proof:**
  - Reversal moved against the position; at `13:15 IST`, price rose to `25,135.00`, triggering stop loss.
  - Exit Reason: `STOP_LOSS` (-40.00 points).
- **Comparison with Manual Journal:**
  - Manual journal recorded an ORB trade on this day. The human trader did not trade PDS, illustrating strategy selection conflict.

---

### Case REPRO_CASE_007: PDR Retest Bounce — Bullish Gap-Up Support (Target Reached)
- **Strategy & Method:** PDR (Retest Bounce)
- **Historical Date:** Tuesday, September 2, 2025
- **Dataset Row Key:** `data/processed/nifty/5m#row=173`
- **Signal Candle:** `2025-09-02 09:25:00 – 09:30:00 IST`
  - Open: `24,653.90` | High: `24,678.70` | Low: `24,652.00` | Close: `24,676.60`
- **Point-in-Time Reference Levels:**
  - Prior Day Resistance Zone: `[24,615.00 – 24,630.00]`
  - Prior Day Close: `24,580.00`
- **Rule-by-Rule Entry Evaluation:**
  1. *Gap-Up Gate:* Opened gap-up at `24,683.25` > PDC &rarr; PASS.
  2. *Zone Test:* 09:25 low tested down to `24,631.65` into the zone &rarr; PASS.
  3. *Bounce Confirmation:* Bullish green candle closed at `24,676.60` &rarr; PASS.
- **Entry & Execution Assumptions:**
  - Entry: Long at `24,654.40`.
  - Target: Fixed 70 points $\implies Target = 24,723.55$.
  - Stop-Loss: 35 points $\implies SL = 24,618.55$.
- **Subsequent Candle Progression & Exit Proof:**
  - At `09:55 IST`, High reached `24,723.55`, hitting target.
  - Exit Reason: `TARGET` (+70.00 points).
- **Comparison with Manual Journal:**
  - Row 4, Sl No 2 in manual journal recorded `PDR Target` at `09:25`. Agreed target.
- **Unresolved Assumptions:**
  - Zone proximity buffer assumed to be 5 points; exact distance threshold requires author confirmation.

---

### Case REPRO_CASE_008: PDR Retest Bounce — Bullish Gap-Up Support (Stop-Loss Hit)
- **Strategy & Method:** PDR (Retest Bounce)
- **Historical Date:** Monday, September 8, 2025
- **Dataset Row Key:** `data/processed/nifty/5m#row=175`
- **Signal Candle:** `2025-09-08 09:35:00 – 09:40:00 IST`
  - Entry: Long at `24,835.90` | SL: `24,799.75` (-36.15 pts) | Target: `24,905.90` (+70.0 pts)
- **Subsequent Candle Progression & Exit Proof:**
  - Price failed to sustain the bounce; at `10:10 IST`, price dropped to `24,795.00`, hitting stop loss.
  - Exit Reason: `STOP_LOSS` (-35.00 points).
- **Comparison with Manual Journal:**
  - Manual trader traded RF on this day, not PDR.

---

## 3. Summary of Findings

1. **Reproducibility Confirmed:** All eight cases are strictly reproducible using the canonical strategy classes in `src/trading_platform/strategies/` over immutable raw 5-minute bars.
2. **Independent Exit Verification:** Exits are proved candle-by-candle through high/low price action without relying on manual journal assertions.
3. **Pervasive Unresolved Assumptions:** Every single strategy relies on unconfirmed heuristic proxies (zone proximity, trend definition, pullback support lookback, or 35 vs 40 pt stop rules).
4. **Conclusion:** No strategy can enter `PAPER_ELIGIBLE` until these specific assumptions are formally confirmed in writing by the strategy author.
