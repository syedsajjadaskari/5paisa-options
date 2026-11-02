# Strategy Authorization & Ambiguity Matrix

**Document:** `STRATEGY_AUTHORIZATION_MATRIX.md`  
**Classification:** Quantitative Governance & Algorithmic Rule Traceability  
**Status:** Authoritative Pre-Paper-Trading Gatekeeper  
**Applicability:** NIFTY 50 Options Algorithmic Framework

---

## 1. Executive Strategy Authorization Matrix

The table below provides a rule-by-rule evaluation of all five trading strategies across their respective execution methods. Each method is evaluated against the original strategy PDFs to determine whether it can be executed using **strictly explicit rules** without introducing unsupported mechanical assumptions.

| Strategy & Method | Source PDF & Pages | Trading Concept & Direction | Explicit Mathematical Rules | Ambiguities & Discretionary Dependencies | Authorization Status | Author Action Required for Live Paper Trading |
|---|---|---|---|---|---|---|
| **ORB Method 1**<br>*(PDH/PDL Breakout)* | `ORB_Strategy_Rulebook-2.pdf`<br>(pp. 1–3, 11) | Intraday breakout of prior day high (Long) or breakdown of prior day low (Short) | • First candle: 09:15–09:20 IST.<br>• Long: $Close_{09:20} > PDH$.<br>• Short: $Close_{09:20} < PDL$.<br>• Stop-Loss: Fixed 30 points.<br>• Target: Fixed 60 points. | • Definition of "established trend" (200-EMA slope vs daily structure).<br>• "Strong body / minimal opposing wick" quantitative threshold. | **AUTHORIZATION_READY_WITH_GATES**<br>*(Executable as strict PDH/PDL close)* | Author confirmation of whether 200-EMA on 5m chart is the sole trend filter. |
| **ORB Method 2**<br>*(Trendline / Inner Level)* | `ORB_Strategy_Rulebook-2.pdf`<br>(pp. 4–8) | Breakout of consolidation or inner levels inside yesterday's range | • First candle: 09:15–09:20 IST.<br>• SL: 30 pts, Target: 60 pts. | • Mathematical definition of trendline anchor points.<br>• Identification of "inner resistance/support". | **BLOCKED_AMBIGUITY**<br>*(High Discretionary Bias)* | Numerical definition of swing anchor lookback and trendline slope. |
| **ORB Method 3**<br>*(200 DMA Gap)* | `ORB_Strategy_Rulebook-2.pdf`<br>(pp. 9–10) | Gap-open across 200-day moving average | • 200-day daily SMA calculation.<br>• Gap across moving average. | • Gap tolerance and confirmation candle body ratio. | **EXPERIMENTAL_RESEARCH_ONLY** | Exact gap distance threshold and bar close confirmation. |
| **IRF Method 1**<br>*(Immediate Failure)* | `IRF_Strategy_Guide Updated.pdf`<br>(pp. 2–5, 14) | First bar breakout above IRH followed by immediate next-bar close back inside | • Initial Range: High/Low of 09:15–09:20 bar ($IRH, IRL$).<br>• $C_1$: $Close > IRH$.<br>• $C_2$: $Close < IRH$.<br>• Entry Short on $C_2$ close.<br>• Target: Fixed 60 points. | • Stop-loss selection between 35 and 40 points based on distance to recent green candle. | **AUTHORIZATION_READY_WITH_GATES**<br>*(Executable if 40pt stop is fixed)* | Confirm whether 40 points is acceptable as the default stop when green candle distance is ambiguous. |
| **IRF Method 2**<br>*(Extended Move & Reversal)* | `IRF_Strategy_Guide Updated.pdf`<br>(pp. 6–10) | Multi-candle extension above IRH followed by pullback support breakdown | • Price extends $> 1$ bar above IRH.<br>• Red reversal bar forms.<br>• Target: Fixed 60 points. | • Lookback period and formula for "recent pullback support".<br>• Confirmation A vs Confirmation B priority. | **BLOCKED_AMBIGUITY** | Algorithmic definition of swing support lookback during intraday extensions. |
| **IRF Method 3**<br>*(Liquidity Sweep)* | `IRF_Strategy_Guide Updated.pdf`<br>(pp. 11–13) | Upper wick spike above IRH with close below recent support | • Compression inside initial range.<br>• Red candle wicks above IRH.<br>• Target: Fixed 60 points. | • Wick ratio threshold ("approximately 60–70% outside").<br>• Compulsory support break timing. | **BLOCKED_AMBIGUITY** | Confirm exact upper wick percentage ($\ge 60\%$) and whether sweep bar must close below support. |
| **PDS Method 1**<br>*(Good Red Candle)* | `PDS strategy.pdf`<br>(pp. 1–3, 5) | Gap-down open; price rallies to test yesterday's support-turned-resistance | • Next-day open strictly gap-down ($Open < Close_{prev}$).<br>• Short on red candle close.<br>• Target: Fixed 70 points. | • Source candle for previous day's support zone (lowest bar vs daily low).<br>• Proximity tolerance for zone test.<br>• Quantitative definition of "good red candle".<br>• Stop-loss selection: 35 vs 40 points. | **BLOCKED_AMBIGUITY** | 1. Support zone source candle.<br>2. Proximity tolerance (in index points).<br>3. Exact 35 vs 40 pt stop rule. |
| **PDS Method 2**<br>*(Red Pin Bar / HPR)* | `PDS strategy.pdf`<br>(pp. 3–4) | Higher Price Rejection pin bar into prior support zone | • Gap-down open.<br>• Upper wick probes zone.<br>• Candle closes red ($Close < Open$).<br>• Target: 70 points. | • Upper wick ratio to total range.<br>• Zone proximity tolerance.<br>• Stop-loss selection: 35 vs 40 points. | **BLOCKED_AMBIGUITY** | Numeric pin bar wick ratio ($\ge 50\%$) and stop-loss selection formula. |
| **PDS Method 3**<br>*(Small Support Break)* | `PDS strategy.pdf`<br>(pp. 4–5) | Break of minor intraday support after weak first touch | • Gap-down open.<br>• Break below minor support level. | • Definition of "weak first touch".<br>• Swing lookback for minor support level. | **BLOCKED_AMBIGUITY** | Mechanical definition of weak candle and intraday support lookback. |
| **PDR Retest Bounce** | `PDR_Strategy_Guide.pdf`<br>(pp. 1–4) | Gap-up open; pullback tests yesterday's resistance-turned-support | • Next-day open gap-up ($Open > Close_{prev}$).<br>• Retest into prior resistance zone.<br>• Bullish green bounce candle.<br>• Long entry on green close.<br>• Target: Fixed 70 points. | • Source candle for prior resistance zone.<br>• Proximity tolerance for retest.<br>• Quantitative threshold for green candle.<br>• Stop-loss selection: 35 vs 40 points. | **BLOCKED_AMBIGUITY** | 1. Resistance zone boundaries.<br>2. Retest proximity tolerance.<br>3. Stop-loss rule (35 vs 40 pts). |
| **RF**<br>*(Reverse Failure)* | `RF strategy.pdf`<br>(pp. 1–6) | Failed breakdown in established uptrend with subsequent reclaim | • Market in uptrend.<br>• Breakdown below level followed by reclaim. | • **Canonical exit is completely absent in author PDF**.<br>• Target, stop-loss, and exit policies unspecified. | **PERMANENTLY_DISABLED** | Complete exit rulebook specification (stop-loss, target, and trailing rules). |

---

## 2. Concise, Prioritized List of Required Author Confirmations

To enable prospective paper-trading fills without introducing subjective assumptions, the strategy author must provide deterministic answers to the following prioritized questions:

### Priority 1: Zone Definition & Retest Proximity Tolerance (PDS & PDR)
1. **Zone Construction:** Does yesterday's reference zone span the candle body and wick of:
   - The single lowest/highest 5-minute bar of the previous session?
   - The multi-hour consolidation zone around the daily extreme?
2. **Proximity Distance:** When price approaches the zone, what numerical threshold (in NIFTY index points) constitutes a valid test if the candle wick does not physically intersect the zone rectangle? *(e.g. within 5 points, 10 points, or 15 points?)*
3. **Stop-Loss Selection:** The rulebook specifies "35 or 40 points based on the recent green/red candle." Please confirm the exact formula:
   $$\text{If } |Entry - CandleExtreme| \le 35.0 \implies SL = 35.0 \text{ pts, else } SL = 40.0 \text{ pts}$$

### Priority 2: Trend Filter & Entry Boundaries (ORB Method 1)
1. **Established Trend Proxy:** Confirm whether an established trend requires:
   - Price above/below the 200 EMA on the 5-minute chart at 09:15 IST, or
   - Daily 200 DMA slope, or
   - A sequential higher-high/lower-low market structure.
2. **Candle Body Ratio:** Confirm the quantitative threshold for "strong body and close near extreme":
   - Body $\ge 50\%$ of candle range ($|Close - Open| / (High - Low) \ge 0.50$)?
   - Close in top 20% (Long) or bottom 20% (Short) of the 09:15–09:20 range?

### Priority 3: Support Lookback & Wick Threshold (IRF Method 2 & Method 3)
1. **Method 2 Support Lookback:** Define the exact bar lookback (e.g. 3 bars, 5 bars, or swing low) used to identify the "recent pullback support" formed during an extension above IRH.
2. **Method 3 Wick Sweep Threshold:** Confirm whether the upper wick share above IRH must strictly equal or exceed $60.0\%$ ($\frac{High - IRH}{High - Open} \ge 0.60$).
3. **Sweep Bar Close:** Must the sweep candle itself close below recent support, or can a subsequent confirmation candle trigger the short entry?

### Priority 4: Canonical Exit Specification (RF Strategy)
1. **Canonical Exit Policy:** Because the author PDF lacks an exit definition, provide the exact rule for target (e.g. 60 points), stop-loss (e.g. 35/40 points), and trailing/EOD square-off conditions.

---

## 3. Deployment Feasibility Summary

```
┌───────────────────────────────┬──────────────────────┬──────────────────────────────────────────────────────┐
│ Strategy Deployment Status    │ Methods Included     │ Operational Preconditions                            │
├───────────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────┤
│ READY FOR FORWARD PAPER TRADE │ ORB Method 1         │ Restricted to strict PDH/PDL close + 30pt SL / 60pt  │
│ (Subject to Safety Gate)      │ IRF Method 1         │ TP + 40pt default SL. Fails closed on ambiguity.     │
├───────────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────┤
│ GATED / SHADOW LOGGING ONLY   │ PDS (Methods 1, 2, 3)│ Awaiting Priority 1 & 2 author confirmations.        │
│                               │ PDR (Retest Bounce)  │ Evaluated incrementally on closed candles, but fills │
│                               │ IRF (Methods 2, 3)   │ strictly blocked.                                    │
├───────────────────────────────┼──────────────────────┼──────────────────────────────────────────────────────┤
│ PERMANENTLY DISABLED          │ RF (All Methods)     │ Excluded until author provides complete exit rules.  │
└───────────────────────────────┴──────────────────────┴──────────────────────────────────────────────────────┘
```
