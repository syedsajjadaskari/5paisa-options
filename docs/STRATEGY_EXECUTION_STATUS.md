# Strategy execution status (what the app really does today)

Hand-assembled and verified on 2026-10-29 against the code: tiers from `configs/live_paper.yaml` and
`src/trading_platform/live/authorization.py`; provisional rules printed from each strategy's `PROVISIONAL_RULES`;
backtest numbers from `reports/backtest/V3_PDF_CORRECTED_20261028T174123Z_523f2825/summary.json`.
`tests/live/test_fix_docs.py` fails if this table or the rule lists drift from the code.

**No strategy is author-approved, and none can become so in this build** (`AUTHOR_APPROVED` is never granted).
The app is a manually launched, **virtual** paper trader on read-only 5paisa data; no broker order of any kind is sent.

## Status table

| strategy | tier today (default live run) | author-approved | executable in replay | executable for real prospective virtual fills | backtest V3 (window, standalone, index points, in-sample) |
|---|---|---|---|---|---|
| PDS Method 1 (bearish, buy PE) | `SHADOW_ONLY` (signals journaled, NO fills) | No (none) | Yes - SIMULATED, labelled `REPLAY_SIMULATED`, isolated from the live ledger | Only through the research-paper opt-in below; never by default; fills labelled `RESEARCH_PAPER_PROVISIONAL` | 16 trades, 50.00% win, +224.75 pts, PF 1.78 |
| ORB Method 1 (PDH/PDL break) | `SHADOW_ONLY` (signals journaled, NO fills) | No (none) | Yes - SIMULATED, labelled `REPLAY_SIMULATED`, isolated from the live ledger | Only through the research-paper opt-in below; never by default; fills labelled `RESEARCH_PAPER_PROVISIONAL` | 49 trades, 48.98% win, +679.15 pts, PF 1.91 |
| IRF Method 2 (extended move reversal, short) | `SHADOW_ONLY` (signals journaled, NO fills) | No (none) | Yes - SIMULATED, labelled `REPLAY_SIMULATED`, isolated from the live ledger | Only through the research-paper opt-in below; never by default; fills labelled `RESEARCH_PAPER_PROVISIONAL` | 110 trades, 37.27% win, -183.75 pts, PF 0.93 (net negative) |
| PDR retest bounce (long, buy CE) | `SHADOW_ONLY` (signals journaled, NO fills) | No (none) | Yes - SIMULATED, labelled `REPLAY_SIMULATED`, isolated from the live ledger | Only through the research-paper opt-in below; never by default; fills labelled `RESEARCH_PAPER_PROVISIONAL` | 34 trades, 35.29% win, +19.05 pts, PF 1.02 (about flat) |
| RF (reverse failure) | `DISABLED` (never evaluated) | No | No | No (cannot be enabled: the author PDF has no exit rules) | excluded |

Portfolio (all four, FIFO, one position at a time): 184 trades, +813.60 pts, PF 1.22, max drawdown 554 pts in the 2025-09-01..2026-08-31 window.
These are index points; **option rupee profitability has not been tested** and cannot be from this data.

## What must happen for a strategy to reach `RESEARCH_PAPER` (simulated fills)

All four must hold (checked by `authorize()` in `src/trading_platform/live/authorization.py`; the startup banner prints which one is missing):

1. `configs/live_paper.yaml`: `research_paper.enabled: true`
2. `research_paper.acknowledgement` equal to the exact phrase (character for character):

   `I UNDERSTAND THESE RULES ARE PROVISIONAL AND NOT AUTHOR-APPROVED; SIMULATED FILLS ONLY`

3. the strategy listed in `research_paper.strategies` (e.g. `[PDS, ORB]`)
4. the `--research-paper` flag on the command line (`./.venv/bin/python scripts/run_paper_trading.py --research-paper`)

The `strategies:` block can only hold `SHADOW_ONLY` or `DISABLED`; fills are never granted from it. Even then the fill is
simulated (entry = observed ask + 0.5, exit = observed bid - 0.5, charges applied), carries the `RESEARCH_PAPER_PROVISIONAL`
label, and the evidence is **not** author-approved. Recommended first step: several `SHADOW_ONLY` sessions.

## Provisional rules per strategy (printed from `PROVISIONAL_RULES`)

These are the readings the code uses where the author's PDF gives no number or definition. The questions, current values, code
locations and effect sizes are in `docs/AUTHOR_CONFIRMATION_REQUEST.md`.

### PDS Method 1 (bearish, buy PE)

1. Good red candle = body/range >= min_body_ratio (PDF gives no numeric definition)
2. HPR pin bar = red close with upper wick/range >= pin_bar_wick_ratio (PDF gives no wick:body ratio)
3. 'Very close' zone tolerance zone_proximity_pts (PDF gives no number)
4. Zone = [pdl, top of the body of the first 5m candle that made the previous session's low]: lower wick plus candle body, all taken from the previous session's regular 5m candles (single dataset; the official daily low is only a labelled fallback that carries no body). Strategy-level fallback when no body top is supplied: [pdl, pd_supp_body]
5. Open must be below the zone low (reading of 'after the open price is trading below the zone'); a gap down that opens inside/above the zone is skipped (68 of the 116 gap-down days in the history are skipped by this gate; see docs/AUTHOR_CONFIRMATION_REQUEST.md)
6. The first red candle at the zone is the only one judged for Methods 1 and 2: if it is weak (fails the body / pin-bar test) it consumes the approach and a later strong red candle at the same approach is not taken unless Method 3 is enabled (this alone costs the 2025-12-26 manual-journal day under METHOD_1)
7. Opening 09:15 candle can never be the test/decision candle
8. Method 3 small support = low of the weak first red candle (one candle); break = candle close below it (support_break_mode); armed only after a weak first red candle
9. A close above the zone (+ proximity) invalidates the setup for the day
10. After price leaves the zone downward a new rise to the zone starts a new approach (first red candle is judged again)
11. Stop distance measure = recent green candle HIGH minus signal-candle close, searched in today's earlier bars; no green candle => 35 points; exactly 35 => 35
12. At most max_signals_per_day (1) signal per day
13. Entry on the completed signal candle (PDF entry timing open); 14:30 entry cutoff and 15:15 square-off are platform conventions, not PDF rules

### ORB Method 1 (PDH/PDL break)

1. Trend filter: 'price above/below 200 EMA' read as the 5-minute close-based 200-period EMA (PDF gives no timeframe); 'bullish/bearish price structure' is not tested (no definition)
2. Candle quality: strong body = body/range >= 0.50 (PDF gives no number)
3. Candle quality: small opposing wick = opposing wick/range <= 0.35 (PDF gives no number)
4. Pin bar (conditional candle): lower/upper wick >= 0.45 of range and must close in the trade direction (PDF gives no ratio and no exact close test)
5. Previous-day resistance/support = highest high / lowest low of the previous session's regular 5-minute candles (wick extreme, single dataset; the official daily high/low is only a labelled fallback and can differ by 0.5 pt typically and 37-51 pts on a few days); the PDF screenshots draw a zone, zone edges are not used
6. SL 30 / TP 60 read as NIFTY index points (PDF p11 leaves the unit to the trader)
7. 15:15 square-off and 14:30 entry cutoff are operational rules, not in the PDF

### IRF Method 2 (extended move reversal, short)

1. Extension length: PDF says only 'extends further upward'; at least 2 closes above IRH including the breakout candle is used (min_extension_bars)
2. Extension bar = a bar that CLOSES above IRH (PDF gives no definition)
3. Recent support = lowest low from the first red candle after the breakout candle (PDF: 'a low formed by recent candles during the extension, such as a pullback low'); no lookback length is given and none is applied
4. Recent resistance = highest high of the first push (breakout candle up to the candle before the first red candle); fixed once that red candle forms
5. 'Price trades back above resistance' = a bar after the first red candle with high above resistance, on a bar before the trigger bar (strict sequencing; single-bar wick-through rejection is not accepted)
6. Stop-loss basis: distance from entry to the HIGH of the most recent same-session green candle (PDF does not say which price); 35 when none exists
7. Only one Method 2 signal is emitted per session (PDF has no one-trade-per-day rule)
8. A bar after the breakout that does not close above IRH disables Method 2 for the day only when it is the candle immediately after the breakout (Method 1 per PDF p9); later closes inside the range do not end the sequence
9. Method 2 edge case: a candle right after the breakout that closes EXACTLY at IRH counts as 'not above IRH' and disables Method 2 for the day (close <= IRH), whereas Method 1 treats only a close strictly below IRH as 'back inside the range'; a close exactly at IRH therefore leaves neither method live (0 occurrences in 189 historical breakouts)
10. Data-gap rule: any gap other than 5 minutes between consecutive bars (status DATA_GAP) or a non-5-minute bar disables IRF for the rest of the day (fail closed; not a PDF rule)
11. Entry cutoff 14:30 and square-off 15:15 are engine conventions, not in the PDF
12. Entry fill is next-bar open (backtest) / fresh ask (live), not the PDF's 'at that close'

### PDR retest bounce (long, buy CE)

1. Zone test proximity ('very close approach') is a point value the PDF does not give
2. Stop-loss selection between 35 and 40 points is not defined by the PDF (35 default, 40 when the close is >35 points above the zone bottom)
3. Day open must be above the zone top: read from the PDF charts, not stated as a rule (on the manual journal this gate roughly halves signals and costs 3 of 8 journal days, 2025-10-07 / 2025-12-31 / 2026-05-19, which open inside the zone)
4. The zone test must happen on an earlier candle than the confirming green candle: literal reading of 'green candle after the zone is tested'
5. Confirming candle must close at or above the zone bottom: not in the PDF
6. A close below the zone bottom discards the earlier zone test: not in the PDF
7. No limit on the time or distance between the zone test and the green candle (PDF silent)
8. Zone height ('small box') has no numeric cap (PDF gives none)
9. Zone geometry is derived outside this class from the previous session's regular 5m candles: zone = [body top of the first candle that made the day high, day high] (single dataset; the official daily high is only a labelled fallback that carries no body); swing-peak zones are not modelled
10. At most one signal per day (PDF silent on trades per day)
11. Entry cutoff 14:30 and 15:15 square-off are platform risk policy, not PDF rules

### RF

No rules are evaluated. The author PDF has no stop, target or trailing rule; RF is permanently `DISABLED`.

## Other facts that bound what the app can do today

* The live path has only been exercised against fakes and replay, never against the live 5paisa feed (run `--doctor` with real credentials during market hours first).
* Replay is SIMULATED: no network, labelled `REPLAY_SIMULATED`, never written to the live ledger.
* Default execution policy allows contracts expiring today (`allow_expiry_day: true`, `expiry_policy: NEAREST`), i.e. 0-DTE options on weekly expiry days until the 14:30 cutoff.
* Limits (`configs/live_paper.yaml`): 1 position, 2 lots, 20% premium allocation, 10% daily loss of session-opening equity. There is no kill switch; stop with Ctrl+C.
