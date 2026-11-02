# Author confirmation request: provisional strategy rules

**Status: OPEN - nothing below is confirmed, nothing is author-approved.** Written 2026-10-29 from the code that runs today.
This document **replaces** `reports/paper_trading_live_session/STRATEGY_AUTHOR_FINAL_SIGNOFF.pdf`, which described a
retired engine (see `reports/paper_trading_live_session/SUPERSEDED_INDEX.md`). Do not sign the old PDF.

## What is being asked and what the answers do

The four strategy PDFs leave many numbers and definitions open. The code fills each gap with a **PROVISIONAL**
reading (listed verbatim below, as `PROVISIONAL_RULES` in each strategy class and in `configs/strategy_provisional/*.yaml`).
Because of that, every strategy runs as `SHADOW_ONLY` in the live app (signals are journaled, **no virtual fill**).

* For each rule please tick **Confirm** (the reading is what you mean) or **Change** (write the correct rule).
* An answer does **not** switch anything on by itself. After your answers, an engineer must change the code/config
  and re-run the backtest; only then can a human decide whether a strategy may be treated as author-approved.
  This build never grants `AUTHOR_APPROVED`; the only way to get simulated fills today is the explicit research opt-in
  (see `docs/STRATEGY_EXECUTION_STATUS.md`), which labels every fill `RESEARCH_PAPER_PROVISIONAL`.
* The app is a **virtual** paper trader on read-only data. No real order is ever sent.

## Evidence base (read this before judging effect sizes)

Current engine `V3_PDF_CORRECTED`, run `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825`. NIFTY **index points only** (no historical option prices exist, so no
option P&L), one joint simulation, max 1 open position, entry at next-bar open + 0.5 pt slippage per side, 14:30 cutoff,
15:15 square-off. **In-sample**: the window overlaps the data used when strategies/methods were chosen, so these are
research candidates, not out-of-sample estimates.

| strategy (standalone) | window 2025-09-01..2026-08-31 | full dataset |
|---|---|---|
| PDS M1 | 16 trades, 50.00% win, +224.75 pts, PF 1.78 | same (16) |
| ORB M1 | 49 trades, 48.98% win, +679.15 pts, PF 1.91 | 53 trades, +917.15 pts, PF 2.23 |
| IRF M2 | 110 trades, 37.27% win, -183.75 pts, PF 0.93 | 115 trades, -81.25 pts, PF 0.97 |
| PDR retest | 34 trades, 35.29% win, +19.05 pts, PF 1.02 | 35 trades, +4.35 pts, PF 1.01 |
| **PORTFOLIO (FIFO, 1 position)** | **184 trades, 41.30% win, +813.60 pts, PF 1.22, max DD 554 pts** | 194 trades, +1,139.40 pts, PF 1.30 |

In the window the engine produced 218 raw signals (PDS 16, ORB 49, IRF 118, PDR 35); the portfolio rejected 34 of them
(24 overlapping an open position, 9 after the 14:30 cutoff, 1 lost a tie-break). The old figures (274 trades, +2,400.90 pts,
PF 1.46; '360 signals / 288 trades') belong to a retired engine and are superseded.

Manual journal comparison (`reports/manual_journal/manual_trades.csv`, same strategy and same date only; direction and time are
NOT compared): PDS: 3 manual PDS trades; engine signalled on 2 of those 3 days (all methods enabled: 3 of 3); ORB: 89 manual ORB trades; engine signalled on 45 of those 89 days (and 45 of the engine's 49 signal days are journal days); IRF: 73 manual IRF trades; engine signalled on 56 of those 73 days (engine signals on 118 days); PDR: 8 manual PDR trades; engine signalled on 5 of those 8 days (relaxing the open-above-zone gate: 8 of 8).
Gate sensitivities quoted below (PDR/PDS) were re-run on 2026-10-29 with the review scripts and match the review notes.

RF (reverse failure) is **permanently disabled**: the author's RF PDF contains no stop, target or trailing rule, so no
mechanical exit exists to simulate. It is never evaluated, not even in shadow. It stays disabled until you provide complete
exit rules.

## PDS: PDS Method 1 (bearish, buy PE)

Tier today: `SHADOW_ONLY` (config `method: METHOD_1`). Backtest V3 window (standalone): 16 trades, 50.00% win, +224.75 pts, PF 1.78.

**Stated by the PDF (not in question):** NIFTY 50, 5-minute chart; bearish role reversal (buy PE on the options layer); previous-day support zone = candle body + lower wick of the day's low; next session opens gap down; price rises from below and tests the zone; the FIRST red candle at the zone decides - Method 1 good-body red candle, Method 2 red pin bar with higher-price rejection, a weak first candle arms Method 3 (small support break); target 70 points; stop 35 or 40 points by distance to the recent green candle. (Source: `docs/strategy_rulebooks/PDS strategy.pdf`.)

**Provisional rules (13).** For each: the rule exactly as the code states it, its current value, where it is
implemented, the question, and what is known about its effect.

### PDS-1

- **Rule as implemented:** Good red candle = body/range >= min_body_ratio (PDF gives no numeric definition)
- **Current value:** 0.45 (`min_body_ratio`)
- **Code location:** `src/trading_platform/strategies/pds.py:496` (def _judge_first_red); `configs/strategy_provisional/pds.yaml:10` (min_body_ratio:)
- **Question to the author:** What body/range ratio makes a red candle a 'good red candle' (Method 1)?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-2

- **Rule as implemented:** HPR pin bar = red close with upper wick/range >= pin_bar_wick_ratio (PDF gives no wick:body ratio)
- **Current value:** 0.45 (`pin_bar_wick_ratio`)
- **Code location:** `src/trading_platform/strategies/pds.py:496` (def _judge_first_red); `configs/strategy_provisional/pds.yaml:15` (pin_bar_wick_ratio:)
- **Question to the author:** What upper-wick/range (or wick:body) ratio makes the Method 2 HPR pin bar? Not used in live/backtest (METHOD_1 only).
- **Effect / evidence:** Method 2 is not enabled in the default configuration.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-3

- **Rule as implemented:** 'Very close' zone tolerance zone_proximity_pts (PDF gives no number)
- **Current value:** 5.0 index points (`zone_proximity_pts`)
- **Code location:** `src/trading_platform/strategies/pds.py:452` (zone_low, zone_top, prox); `configs/strategy_provisional/pds.yaml:20` (zone_proximity_pts:)
- **Question to the author:** How close (index points) may a candle stay to the zone and still count as a test? (0 = must touch.)
- **Effect / evidence:** Not measured separately. The retired sign-off PDF offered 10.0 as 'current'; the code has used 5.0 since the V3 rewrite.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-4

- **Rule as implemented:** Zone = [pdl, top of the body of the first 5m candle that made the previous session's low]: lower wick plus candle body, all taken from the previous session's regular 5m candles (single dataset; the official daily low is only a labelled fallback that carries no body). Strategy-level fallback when no body top is supplied: [pdl, pd_supp_body]
- **Current value:** zone = [previous-session 5m low, body top of the first 5m candle that made that low]; official daily low only as labelled fallback
- **Code location:** `src/trading_platform/data/reference_levels.py:261` (def summarize_regular_session); `src/trading_platform/data/reference_levels.py:348` (def levels_for)
- **Question to the author:** Is the support zone the lower wick plus the body of the candle that made the day's low? Is that candle the FIRST such 5m candle? Is the 5m low (not the official daily low) the right wick extreme?
- **Effect / evidence:** The 5m-based low differs from the official daily low on almost every day (median about 0.5 pt, p90 about 5 pt, three days 37-51 pts off for PDH/PDL).
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-5

- **Rule as implemented:** Open must be below the zone low (reading of 'after the open price is trading below the zone'); a gap down that opens inside/above the zone is skipped (68 of the 116 gap-down days in the history are skipped by this gate; see docs/AUTHOR_CONFIRMATION_REQUEST.md)
- **Current value:** `require_open_below_zone: true`
- **Code location:** `src/trading_platform/strategies/pds.py:437` (below = bar.open < levels); `configs/strategy_provisional/pds.yaml:45` (require_open_below_zone:)
- **Question to the author:** Must the 09:15 open be BELOW the zone low, or is a gap-down that opens inside/above the zone also tradable?
- **Effect / evidence:** Gate funnel over the history (267 days): NO_GAP_DOWN 150, OPEN_NOT_BELOW_ZONE 68, ZONE_FAILED 9, still alive 39 (16 signalled), NO_FIRST_BAR 1. So this gate alone skips 68 of the 116 gap-down days. It is NOT the reason the 2025-12-26 journal day is missed: relaxing it still misses that day (22 signals, 2 of 3 journal days).
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-6

- **Rule as implemented:** The first red candle at the zone is the only one judged for Methods 1 and 2: if it is weak (fails the body / pin-bar test) it consumes the approach and a later strong red candle at the same approach is not taken unless Method 3 is enabled (this alone costs the 2025-12-26 manual-journal day under METHOD_1)
- **Current value:** hard-coded: only the FIRST red candle at the zone is judged (no yaml key)
- **Code location:** `src/trading_platform/strategies/pds.py:476` (if st.first_red is None and is_red); `src/trading_platform/strategies/pds.py:496` (def _judge_first_red)
- **Question to the author:** If the first red candle at the zone is weak, may a LATER strong red candle at the same approach be taken (the PDF says use Method 3 after a weak first candle)?
- **Effect / evidence:** Journal day 2025-12-26 (one of the 3 manual PDS trades): the 09:25 red candle overlaps the zone, fails both M1 (body) and M2 (pin bar), consumes the approach, and the strong 09:30 red candle is never judged under METHOD_1. Enabling all methods (`method: ALL`) gives 22 signals and matches 3 of 3 journal days; METHOD_1 gives 16 signals and matches 2 of 3.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-7

- **Rule as implemented:** Opening 09:15 candle can never be the test/decision candle
- **Current value:** `allow_opening_bar_entry: false`
- **Code location:** `src/trading_platform/strategies/pds.py:453` (first_bar and not self.allow_opening_bar_entry); `configs/strategy_provisional/pds.yaml:40` (allow_opening_bar_entry:)
- **Question to the author:** May the 09:15 candle itself be the test/decision candle?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-8

- **Rule as implemented:** Method 3 small support = low of the weak first red candle (one candle); break = candle close below it (support_break_mode); armed only after a weak first red candle
- **Current value:** `support_break_mode: close`
- **Code location:** `src/trading_platform/strategies/pds.py:465` (self.support_break_mode == "close"); `configs/strategy_provisional/pds.yaml:25` (support_break_mode:)
- **Question to the author:** Method 3 only: must a candle CLOSE below the small support, or is a touch enough? Method 3 is not enabled with the default `method: METHOD_1`.
- **Effect / evidence:** Not active in the default configuration.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-9

- **Rule as implemented:** A close above the zone (+ proximity) invalidates the setup for the day
- **Current value:** `invalidate_on_close_above_zone: true`
- **Code location:** `src/trading_platform/strategies/pds.py:520` (def _zone_failure_check); `configs/strategy_provisional/pds.yaml:50` (invalidate_on_close_above_zone:)
- **Question to the author:** Does a candle that closes above the zone (+ proximity) end the setup for the day?
- **Effect / evidence:** 9 of the 116 gap-down days end here (ZONE_FAILED). With all methods enabled and no open-below-zone gate the history gives 31 signals; also dropping this invalidation gives 72, so it is a large lever.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-10

- **Rule as implemented:** After price leaves the zone downward a new rise to the zone starts a new approach (first red candle is judged again)
- **Current value:** hard-coded: re-approach after leaving the zone downward starts a new approach
- **Code location:** `src/trading_platform/strategies/pds.py:488` (Price left the zone downward)
- **Question to the author:** After price leaves the zone downward and rises back, is that a fresh approach (first red candle judged again)?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-11

- **Rule as implemented:** Stop distance measure = recent green candle HIGH minus signal-candle close, searched in today's earlier bars; no green candle => 35 points; exactly 35 => 35
- **Current value:** `green_candle_distance_threshold_pts: 35.0`; stops 35 / 40
- **Code location:** `src/trading_platform/strategies/pds.py:552` (def _stop_points); `configs/strategy_provisional/pds.yaml:30` (green_candle_distance_threshold_pts:)
- **Question to the author:** How exactly is the 35 vs 40 stop chosen: distance to which price of the recent green candle (high/close), measured from which price, searched over which bars? What if the distance is exactly 35?
- **Effect / evidence:** Stop width changes risk per trade and which trades are stopped; effect not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-12

- **Rule as implemented:** At most max_signals_per_day (1) signal per day
- **Current value:** `max_signals_per_day: 1`
- **Code location:** `src/trading_platform/strategies/pds.py:449` (if st.signals_emitted >= self.max_signals_per_day); `configs/strategy_provisional/pds.yaml:35` (max_signals_per_day:)
- **Question to the author:** May PDS trade more than once per day?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDS-13

- **Rule as implemented:** Entry on the completed signal candle (PDF entry timing open); 14:30 entry cutoff and 15:15 square-off are platform conventions, not PDF rules
- **Current value:** entry cutoff 14:30, square-off 15:15, entry at the completed signal candle (next-bar open fill)
- **Code location:** `src/trading_platform/backtesting/constants.py:25` (ENTRY_CUTOFF_TIME:); `src/trading_platform/backtesting/constants.py:26` (SQUARE_OFF_TIME:)
- **Question to the author:** Confirm the 14:30 entry cutoff and 15:15 square-off (platform conventions, not in the PDF) and the next-bar-open / fresh-ask entry timing.
- **Effect / evidence:** Applies to all four strategies.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

## ORB: ORB Method 1 (PDH/PDL break)

Tier today: `SHADOW_ONLY` (config `method: METHOD_1`). Backtest V3 window (standalone): 49 trades, 48.98% win, +679.15 pts, PF 1.91.

**Stated by the PDF (not in question):** NIFTY 50, 5-minute chart; Method 1 = previous-day level breakout decided at 09:20 on the FIRST 5-minute candle: long when price is above the 200 EMA and the candle breaks and closes above previous-day resistance with a strong green body and small upper wick; mirrored short; a pin bar is only a conditional candle; stop 30, target 60 (unit left open); no end-of-day rule. (Source: `docs/strategy_rulebooks/ORB_Strategy_Rulebook-2.pdf`, pp. 3-4, 9-11, 13.)

**Provisional rules (7).** For each: the rule exactly as the code states it, its current value, where it is
implemented, the question, and what is known about its effect.

### ORB-1

- **Rule as implemented:** Trend filter: 'price above/below 200 EMA' read as the 5-minute close-based 200-period EMA (PDF gives no timeframe); 'bullish/bearish price structure' is not tested (no definition)
- **Current value:** `trend_ema_basis: EMA200_5M_CLOSE`
- **Code location:** `src/trading_platform/strategies/orb.py:324` (TREND_CLOSE_); `src/trading_platform/data/reference_levels.py:442` (class IntradayContextTracker); `configs/strategy_provisional/orb.yaml:34` (trend_ema_basis:)
- **Question to the author:** Which series/timeframe is 'the 200 EMA' (5-minute close? daily?), and how is 'bullish/bearish price structure' defined (it is not tested at all today)?
- **Effect / evidence:** Effect of dropping the trend filter or using another EMA not measured.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### ORB-2

- **Rule as implemented:** Candle quality: strong body = body/range >= 0.50 (PDF gives no number)
- **Current value:** 0.50 (`min_body_ratio`)
- **Code location:** `src/trading_platform/strategies/orb.py:293` (body_ok = body >= self.min_body_ratio); `configs/strategy_provisional/orb.yaml:9` (min_body_ratio:)
- **Question to the author:** Numeric threshold for a 'strong body' (body/range)?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### ORB-3

- **Rule as implemented:** Candle quality: small opposing wick = opposing wick/range <= 0.35 (PDF gives no number)
- **Current value:** 0.35 (`max_opposing_wick_ratio`)
- **Code location:** `src/trading_platform/strategies/orb.py:292` (wick_ok = opposing <= self.max_opposing_wick_ratio); `configs/strategy_provisional/orb.yaml:14` (max_opposing_wick_ratio:)
- **Question to the author:** Numeric threshold for a 'small' opposing wick? (Two older files say 0.20 / 20%; the code has always used 0.35.)
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### ORB-4

- **Rule as implemented:** Pin bar (conditional candle): lower/upper wick >= 0.45 of range and must close in the trade direction (PDF gives no ratio and no exact close test)
- **Current value:** 0.45 wick/range + directional close (`pin_bar_min_wick_ratio`, `pin_bar_requires_directional_close`)
- **Code location:** `src/trading_platform/strategies/orb.py:294` (pin_ok = rejection >= self.pin_bar_min_wick_ratio); `configs/strategy_provisional/orb.yaml:21` (pin_bar_min_wick_ratio:)
- **Question to the author:** Wick ratio of a pin bar and the exact close test ('position and close must support the breakout')?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### ORB-5

- **Rule as implemented:** Previous-day resistance/support = highest high / lowest low of the previous session's regular 5-minute candles (wick extreme, single dataset; the official daily high/low is only a labelled fallback and can differ by 0.5 pt typically and 37-51 pts on a few days); the PDF screenshots draw a zone, zone edges are not used
- **Current value:** `level_basis: PDH_PDL_WICK_EXTREME` from the previous session's regular 5m candles
- **Code location:** `src/trading_platform/strategies/orb.py:346` (pdh, pdl = levels); `src/trading_platform/data/reference_levels.py:261` (def summarize_regular_session); `configs/strategy_provisional/orb.yaml:41` (level_basis:)
- **Question to the author:** Is the 'previous-day resistance/support' the wick extreme (high/low) or the zone drawn in the PDF screenshots? Is the 5m-candle extreme the right source, or the official daily high/low?
- **Effect / evidence:** 5m-based PDH/PDL differs from the official daily file on 262 of 266 days (median about 0.5 pt, p90 5 pts; 2025-11-27 PDL +46.8, 2026-02-04 PDH -51.0, 2026-08-05 PDH -37.25). A 40-50 pt shift on those few days changes breakout eligibility.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### ORB-6

- **Rule as implemented:** SL 30 / TP 60 read as NIFTY index points (PDF p11 leaves the unit to the trader)
- **Current value:** `risk_unit: NIFTY_INDEX_POINTS` (SL 30 / TP 60)
- **Code location:** `src/trading_platform/strategies/orb.py:55` (PDF_STOP_POINTS =); `configs/strategy_provisional/orb.yaml:51` (risk_unit:)
- **Question to the author:** Are SL 30 / TP 60 index points, option-premium points, or rupees?
- **Effect / evidence:** Option-premium or rupee readings would change every trade; the whole backtest is in index points.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### ORB-7

- **Rule as implemented:** 15:15 square-off and 14:30 entry cutoff are operational rules, not in the PDF
- **Current value:** entry cutoff 14:30, square-off 15:15
- **Code location:** `src/trading_platform/backtesting/constants.py:25` (ENTRY_CUTOFF_TIME:); `src/trading_platform/backtesting/constants.py:26` (SQUARE_OFF_TIME:)
- **Question to the author:** Confirm the cutoff and square-off (not in the PDF).
- **Effect / evidence:** Applies to all four strategies.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

## IRF: IRF Method 2 (extended move reversal, short)

Tier today: `SHADOW_ONLY` (config `method: METHOD_2`). Backtest V3 window (standalone): 110 trades, 37.27% win, -183.75 pts, PF 0.93 (net negative).

**Stated by the PDF (not in question):** NIFTY 50, 5-minute chart, short only; the initial range (IRH/IRL) is the 09:15 candle; Method 2 = a candle closes above IRH, price extends upward, a red candle forms, then EITHER a candle closes below recent support (confirmation A) OR price pushes back above recent resistance and a red candle closes back below it (confirmation B); if the candle right after the breakout closes back inside the range it is Method 1, not Method 2; target 60 points; stop 35 (distance <= 35) or 40 (> 35) by distance to the recent green candle. (Source: `docs/strategy_rulebooks/IRF_Strategy_Guide Updated.pdf`, pp. 2, 7-10, 13-14.)

**Provisional rules (12).** For each: the rule exactly as the code states it, its current value, where it is
implemented, the question, and what is known about its effect.

### IRF-1

- **Rule as implemented:** Extension length: PDF says only 'extends further upward'; at least 2 closes above IRH including the breakout candle is used (min_extension_bars)
- **Current value:** `min_extension_bars: 2`
- **Code location:** `src/trading_platform/strategies/irf.py:403` (ext_ok = n_ext >= self.min_extension_bars); `configs/strategy_provisional/irf.yaml:11` (min_extension_bars:)
- **Question to the author:** How many closes above IRH (including the breakout candle) make a valid extension, or how many points?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-2

- **Rule as implemented:** Extension bar = a bar that CLOSES above IRH (PDF gives no definition)
- **Current value:** `extension_bar_definition: CLOSE_ABOVE_IRH`
- **Code location:** `src/trading_platform/strategies/irf.py:343` (def _step_method2); `configs/strategy_provisional/irf.yaml:18` (extension_bar_definition:)
- **Question to the author:** Does an extension bar need a CLOSE above IRH, or is a high above IRH enough?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-3

- **Rule as implemented:** Recent support = lowest low from the first red candle after the breakout candle (PDF: 'a low formed by recent candles during the extension, such as a pullback low'); no lookback length is given and none is applied
- **Current value:** `support_definition: LOWEST_LOW_FROM_FIRST_RED_AFTER_BREAKOUT`, no lookback
- **Code location:** `src/trading_platform/strategies/irf.py:438` (def _absorb_method2); `configs/strategy_provisional/irf.yaml:24` (support_definition:)
- **Question to the author:** Which low is 'recent support'? Lowest low from the first red candle after the breakout (current), the last pullback low, a fixed lookback of N bars, or the lowest low of the whole extension?
- **Effect / evidence:** IRF M2 is the weakest strategy in the backtest: 110 standalone trades, 37.3% win, -183.75 pts, PF 0.93 in the window (118 raw signals; 90 trades / -133.75 pts after portfolio arbitration). The earlier all-bars definition was changed because the PDF's Figure 3 contradicts it. Sensitivity to the choice has not been measured.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-4

- **Rule as implemented:** Recent resistance = highest high of the first push (breakout candle up to the candle before the first red candle); fixed once that red candle forms
- **Current value:** `resistance_definition: HIGHEST_HIGH_OF_FIRST_PUSH`, fixed when the first red candle forms
- **Code location:** `src/trading_platform/strategies/irf.py:448` (state["resistance"] = state["push_high"]); `configs/strategy_provisional/irf.yaml:37` (resistance_definition:)
- **Question to the author:** Is recent resistance the top of the FIRST push (fixed once the first red candle forms), as the current code reads Figure 4?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-5

- **Rule as implemented:** 'Price trades back above resistance' = a bar after the first red candle with high above resistance, on a bar before the trigger bar (strict sequencing; single-bar wick-through rejection is not accepted)
- **Current value:** `resistance_push_above_rule: PRIOR_BAR_HIGH_ABOVE_RESISTANCE`
- **Code location:** `src/trading_platform/strategies/irf.py:220` (pushed_above_resistance); `configs/strategy_provisional/irf.yaml:45` (resistance_push_above_rule:)
- **Question to the author:** Confirmation B: must the 'push back above resistance' be a LATER bar than the red candle that closes below it, or may one candle wick above and close below?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-6

- **Rule as implemented:** Stop-loss basis: distance from entry to the HIGH of the most recent same-session green candle (PDF does not say which price); 35 when none exists
- **Current value:** `stop_distance_basis: GREEN_CANDLE_HIGH`; 35 when no green candle
- **Code location:** `src/trading_platform/strategies/irf.py:528` (def _recent_green_reference); `configs/strategy_provisional/irf.yaml:53` (stop_distance_basis:)
- **Question to the author:** 35 vs 40 stop: distance to the green candle's HIGH or CLOSE? What if there is no earlier green candle today?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-7

- **Rule as implemented:** Only one Method 2 signal is emitted per session (PDF has no one-trade-per-day rule)
- **Current value:** hard-coded: one Method 2 signal per session
- **Code location:** `src/trading_platform/strategies/irf.py:208` (has_signalled)
- **Question to the author:** May IRF Method 2 signal more than once per day?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-8

- **Rule as implemented:** A bar after the breakout that does not close above IRH disables Method 2 for the day only when it is the candle immediately after the breakout (Method 1 per PDF p9); later closes inside the range do not end the sequence
- **Current value:** hard-coded: only the candle immediately after the breakout can disable Method 2
- **Code location:** `src/trading_platform/strategies/irf.py:358` (state["post_breakout_bars"] == 0 and bar.close <= irh)
- **Question to the author:** If a later candle closes back inside the range, is Method 2 still live?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-9

- **Rule as implemented:** Method 2 edge case: a candle right after the breakout that closes EXACTLY at IRH counts as 'not above IRH' and disables Method 2 for the day (close <= IRH), whereas Method 1 treats only a close strictly below IRH as 'back inside the range'; a close exactly at IRH therefore leaves neither method live (0 occurrences in 189 historical breakouts)
- **Current value:** hard-coded: `bar.close <= irh` (Method 2) vs `bar.close < irh` (Method 1)
- **Code location:** `src/trading_platform/strategies/irf.py:358` (state["post_breakout_bars"] == 0 and bar.close <= irh); `src/trading_platform/strategies/irf.py:294` (if bar.close < irh:)
- **Question to the author:** A candle right after the breakout closing EXACTLY at IRH: is that 'not above IRH' (Method 2 off) or 'back inside' (Method 1)? The code currently makes neither method live (an inconsistency).
- **Effect / evidence:** Occurred 0 times in 189 historical breakout sequences, so there is no P&L impact today.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-10

- **Rule as implemented:** Data-gap rule: any gap other than 5 minutes between consecutive bars (status DATA_GAP) or a non-5-minute bar disables IRF for the rest of the day (fail closed; not a PDF rule)
- **Current value:** hard-coded: a gap other than 5 minutes between bars disables IRF for the day (`DATA_GAP`)
- **Code location:** `src/trading_platform/strategies/irf.py:247` ("DATA_GAP")
- **Question to the author:** Not an author question - a fail-closed data-integrity rule listed for completeness.
- **Effect / evidence:** Safe by design; may cost a setup after a data outage.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-11

- **Rule as implemented:** Entry cutoff 14:30 and square-off 15:15 are engine conventions, not in the PDF
- **Current value:** entry cutoff 14:30, square-off 15:15
- **Code location:** `src/trading_platform/backtesting/constants.py:25` (ENTRY_CUTOFF_TIME:); `src/trading_platform/backtesting/constants.py:26` (SQUARE_OFF_TIME:)
- **Question to the author:** Confirm the cutoff and square-off (not in the PDF).
- **Effect / evidence:** Applies to all four strategies.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### IRF-12

- **Rule as implemented:** Entry fill is next-bar open (backtest) / fresh ask (live), not the PDF's 'at that close'
- **Current value:** entry fill: next-bar open (backtest) / fresh observed ask (live) instead of 'at that close'
- **Code location:** `src/trading_platform/backtesting/engine.py:197` (def simulate)
- **Question to the author:** Is a next-bar entry acceptable, since 'at that close' cannot be filled?
- **Effect / evidence:** Applies to all four strategies.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

## PDR: PDR retest bounce (long, buy CE)

Tier today: `SHADOW_ONLY` (config `method: RETEST_BOUNCE`). Backtest V3 window (standalone): 34 trades, 35.29% win, +19.05 pts, PF 1.02 (about flat).

**Stated by the PDF (not in question):** NIFTY 50, 5-minute chart, long only; previous day's high/reversal defines a zone (candle body to upper wick, a 'small box'); the market opens gap up; price returns toward the zone (wick / body / inside / very close); after the zone is tested wait for a green candle, never enter at the open; target 70 points fixed; stop 35 or 40 points. (Source: `docs/strategy_rulebooks/PDR_Strategy_Guide.pdf`.)

**Provisional rules (11).** For each: the rule exactly as the code states it, its current value, where it is
implemented, the question, and what is known about its effect.

### PDR-1

- **Rule as implemented:** Zone test proximity ('very close approach') is a point value the PDF does not give
- **Current value:** 5.0 (`zone_proximity_points`)
- **Code location:** `src/trading_platform/strategies/pdr.py:205` (proximity = float(rules["zone_proximity_points"])); `configs/strategy_provisional/pdr.yaml:12` (zone_proximity_points:)
- **Question to the author:** How close above the zone top (index points) counts as a 'very close approach'?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDR-2

- **Rule as implemented:** Stop-loss selection between 35 and 40 points is not defined by the PDF (35 default, 40 when the close is >35 points above the zone bottom)
- **Current value:** 35 default, 40 when the confirmation close is more than 35 points above the zone bottom
- **Code location:** `src/trading_platform/strategies/pdr.py:45` (wide_stop_distance_threshold_points); `configs/strategy_provisional/pdr.yaml:32` (wide_stop_distance_threshold_points:)
- **Question to the author:** What is the exact rule that chooses the 35 or the 40 stop?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDR-3

- **Rule as implemented:** Day open must be above the zone top: read from the PDF charts, not stated as a rule (on the manual journal this gate roughly halves signals and costs 3 of 8 journal days, 2025-10-07 / 2025-12-31 / 2026-05-19, which open inside the zone)
- **Current value:** `require_open_above_zone_top: true`
- **Code location:** `src/trading_platform/strategies/pdr.py:403` (def _evaluate_gates); `configs/strategy_provisional/pdr.yaml:39` (require_open_above_zone_top:)
- **Question to the author:** Must the day open ABOVE the zone top, or may a gap-up that opens inside the zone also be traded (the manual journal contains such trades)?
- **Effect / evidence:** Reproduced here: with the gate 37 signals and 5 of 8 manual-journal PDR days match; without it 79 signals and 8 of 8 match. The three missed days (2025-10-07, 2025-12-31, 2026-05-19) fail with OPEN_NOT_ABOVE_ZONE: their opens (25087.55, 25975.45, 23675.30) sit inside the zones [25077.25-25095.70], [25970.55-25976.75], [23662.70-23695.40]. The PDF text ('opens gap up and then pulls back into the marked zone', 'if price keeps rising without returning to the zone there is no setup') makes the strict reading defensible but does not forbid an open inside the zone. This is the single biggest PDR lever.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDR-4

- **Rule as implemented:** The zone test must happen on an earlier candle than the confirming green candle: literal reading of 'green candle after the zone is tested'
- **Current value:** `allow_same_bar_touch_and_confirm: false`
- **Code location:** `src/trading_platform/strategies/pdr.py:219` (same_bar = bool(rules["allow_same_bar_touch_and_confirm"])); `configs/strategy_provisional/pdr.yaml:50` (allow_same_bar_touch_and_confirm:)
- **Question to the author:** Must the zone test happen on an EARLIER candle than the confirming green candle, or may one candle both touch and confirm?
- **Effect / evidence:** Allowing it: 38 signals, the same 5 of 8 journal days (small effect).
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDR-5

- **Rule as implemented:** Confirming candle must close at or above the zone bottom: not in the PDF
- **Current value:** `require_confirm_close_at_or_above_zone_bottom: true`
- **Code location:** `src/trading_platform/strategies/pdr.py:235` (def _confirm); `configs/strategy_provisional/pdr.yaml:60` (require_confirm_close_at_or_above_zone_bottom:)
- **Question to the author:** Must the confirming green candle close at or above the zone bottom?
- **Effect / evidence:** Dropping it: 37 signals, the same 5 of 8 (no effect on the journal match).
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDR-6

- **Rule as implemented:** A close below the zone bottom discards the earlier zone test: not in the PDF
- **Current value:** `reset_touch_on_close_below_zone_bottom: true`
- **Code location:** `src/trading_platform/strategies/pdr.py:209` (discard = bool(rules["reset_touch_on_close_below_zone_bottom"])); `configs/strategy_provisional/pdr.yaml:67` (reset_touch_on_close_below_zone_bottom:)
- **Question to the author:** Does a close below the zone bottom cancel an earlier zone test?
- **Effect / evidence:** Relaxing it together with the open-above-zone, same-bar and close-floor rules gives 92 signals and 8 of 8 journal days (the open-above-zone gate alone explains the three misses).
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDR-7

- **Rule as implemented:** No limit on the time or distance between the zone test and the green candle (PDF silent)
- **Current value:** `max_bars_between_touch_and_confirm: null`, `max_confirm_distance_above_zone_top_points: null` (no limit)
- **Code location:** `src/trading_platform/strategies/pdr.py:249` (max_bars = rules); `configs/strategy_provisional/pdr.yaml:75` (max_bars_between_touch_and_confirm:)
- **Question to the author:** Is there a maximum time or distance between the zone test and the green candle?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDR-8

- **Rule as implemented:** Zone height ('small box') has no numeric cap (PDF gives none)
- **Current value:** `max_zone_height_points: null` (no cap)
- **Code location:** `src/trading_platform/strategies/pdr.py:382` (cap = self.rules["max_zone_height_points"]); `configs/strategy_provisional/pdr.yaml:89` (max_zone_height_points:)
- **Question to the author:** How tall may the 'small box' zone be?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDR-9

- **Rule as implemented:** Zone geometry is derived outside this class from the previous session's regular 5m candles: zone = [body top of the first candle that made the day high, day high] (single dataset; the official daily high is only a labelled fallback that carries no body); swing-peak zones are not modelled
- **Current value:** zone = [body top of the first 5m candle that made the previous session's high, that high]; official daily high only as labelled fallback
- **Code location:** `src/trading_platform/data/reference_levels.py:261` (def summarize_regular_session); `src/trading_platform/strategies/pdr.py:363` (def _resolve_levels)
- **Question to the author:** Is the resistance-turned-support zone the upper wick above the body of the previous day's high candle? Swing-peak zones are not modelled - should they be?
- **Effect / evidence:** Same data-source question as PDS-4 / ORB-5.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDR-10

- **Rule as implemented:** At most one signal per day (PDF silent on trades per day)
- **Current value:** `max_signals_per_day: 1`
- **Code location:** `src/trading_platform/strategies/pdr.py:53` (max_signals_per_day); `configs/strategy_provisional/pdr.yaml:99` (max_signals_per_day:)
- **Question to the author:** May PDR trade more than once per day?
- **Effect / evidence:** Not measured separately.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

### PDR-11

- **Rule as implemented:** Entry cutoff 14:30 and 15:15 square-off are platform risk policy, not PDF rules
- **Current value:** entry cutoff 14:30, square-off 15:15
- **Code location:** `src/trading_platform/backtesting/constants.py:25` (ENTRY_CUTOFF_TIME:); `src/trading_platform/backtesting/constants.py:26` (SQUARE_OFF_TIME:)
- **Question to the author:** Confirm the cutoff and square-off (not in the PDF).
- **Effect / evidence:** Applies to all four strategies.
- **Your answer:** [ ] Confirm as implemented &nbsp;&nbsp; [ ] Change to: ______________________________

## Sign-off

| | |
|---|---|
| Strategy author name | ______________________ |
| Signature | ______________________ |
| Date | ______________________ |

Verdict (tick one):

* [ ] The confirmed rules (with my changes above) may be used for supervised **research paper** trading with simulated fills.
* [ ] Keep every strategy shadow-only until I answer further.

The engineering side records the answers only. No statement here verifies any trading outcome; all figures are in-sample
index-point backtests.

## How this document is kept honest

`tests/live/test_fix_docs.py` fails if any `PROVISIONAL_RULES` entry of PDS/ORB/IRF/PDR is missing from this document, so a
rule cannot be added or reworded in code without this document being updated. `scripts/generate_final_paper_decision_artifacts.py`
regenerates a PDF sheet (one confirm/change line per provisional rule) plus per-strategy exposure from the same attributes, into
`reports/paper_trading_live_session/regenerated/` (it never overwrites the archived originals).
