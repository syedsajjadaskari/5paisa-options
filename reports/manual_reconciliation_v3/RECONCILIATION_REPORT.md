# Manual journal vs V3 automated backtest - reconciliation report

NIFTY **index points only**. The manual journal records `Sl No., Date, Entry Time, Strategy, Outcome` and nothing else:
**direction, method, entry price, stop and target are UNKNOWN for every record** and were never inferred.
All times are IST. No parameter was tuned, no strategy code was changed for this report, no paper fill or broker call is involved.

## 1. Answer

**V3 PARTIALLY reproduces the manual journal.** It reproduces 91 of 168 eligible manual trades
(54.2%) at the signal level and gets the outcome right for 62/91
matched trades (68.1%, or 92.5% when the 44 manual break-even outcomes,
which the PDFs and the engine cannot express, are set aside). It does not reproduce the other 77 eligible entries.
The timing of the reproduced trades is exact for 74 of 91 (81.3%).

| Metric | Result |
|---|---|
| Manual trades | 190 (Sl No. 1-190, 2025-09-01 to 2026-08-31) |
| Eligible manual trades | 168 = 190 - 17 RF (unsupported) - 5 dated on a non-trading day - 0 ambiguous |
| Exact matches (same candle) | 74 / 168 = 44.0% |
| Matches within 5 minutes (CLOSE) | 10 (cumulative exact+close = 84) |
| Matches within 10 minutes (NEAR) | 7 (cumulative valid = 91) |
| Same date + strategy but > 10 min (DIFFERENT) | 17 |
| Unmatched (no same-date same-strategy signal) | 60 |
| **Overall signal match rate** | **91 / 168 = 54.2%** (valid = EXACT + CLOSE + NEAR) |
| **Outcome agreement rate** | **62/91 = 68.1%** of valid matches (both Target 34, both Stop 28); 62/67 = 92.5% excluding manual BE |
| **Portfolio execution match rate** | matched signals accepted by the one-position FIFO portfolio: **86 / 91 = 94.5%**; independent match against accepted portfolio trades: 86 / 168 = 51.2% |

"Match" means: same date, same strategy, V3 signal candle within 10 minutes of the journal time, one-to-one. It does **not** prove the
manual trade used the same level, candle or rule, because the journal has no such detail.

## 2. Sources and integrity

- Journal: `data/manual/Trading_Journal_Sep2025_Aug2026 REPORT.xlsx` (sha256 `97a9ad4dfb5520619bb979770115d7b6f4e3e37e649af255c543f5fce9694781`), sheet *Trading Journal*, **190 rows**.
  The parsed copy `reports/manual_journal/manual_trades.csv` is identical to the workbook.
  The workbook's own *Summary* sheet says **189 total trades** (Target 80), but the data sheet has 190 rows (Target 81): the last row (Sl No. 190, 2026-08-31 ORB Target) is not in the Summary. This report uses the 190 data rows.
- Automated: run `V3_PDF_CORRECTED_20261030T045143Z_523f2825` (engine `V3_PDF_CORRECTED`, git `a5773b136f1c`), produced by `scripts/run_backtest.py`; reproduced twice (28 Oct and 30 Oct 2026) with identical headline results (218 raw signals and 184 portfolio trades in the evaluation window). Datasets verified against the pinned hashes:

- `data/processed/nifty/5m/nifty50_5m.parquet`: MATCH (`02984db58b9697e1...`)
- `data/processed/nifty/1d/nifty50_1d.parquet`: MATCH (`8ed0deb2722cabc8...`)
- `data/processed/nifty/1m/nifty50_1m.parquet`: MATCH (`a0fea90ac0e7469e...`)
- Frozen baselines (V1/V2) were not modified; V1 is used below as a reference only.
- Journal composition: ORB 89, IRF 73, RF 17, PDR 8, PDS 3.
  Outcomes: Target 81, SL 65, BE 44.
- Eligibility: RF (17) is **preserved as unsupported**, not dropped (the RF PDF defines no stop/target/exit and RF is permanently disabled). 5 records are dated on weekends (no NSE session and no bars exist): 2025-10-11, 2026-02-14, 2026-08-15, 2026-08-22, 2026-08-29. They stay in every table as UNMATCHED / unspecified.

## 3. Timestamp convention (checked, not assumed)

The journal time is compared with the V3 **signal candle OPEN label**, the dataset's bar-open convention. Evidence:

- The 85 eligible manual ORB records all carry the single entry time `09:15` (so do all 89 ORB journal rows), and every V3 ORB signal candle opens at `09:15`. The ORB rule cannot be evaluated until that candle **closes** at 09:20, so a person cannot have entered at 09:15: the journal records the trigger candle's label.
- Signed offsets (manual minus V3 signal-candle open, minutes) are centred on 0 and symmetric, not piled at +5 (which a fill-time convention would produce): `{"IRF": {"-210": 1, "-120": 1, "-60": 1, "-55": 1, "-40": 1, "-20": 2, "-15": 1, "-10": 3, "-5": 5, "0": 23, "5": 5, "10": 4, "15": 2, "25": 1, "35": 1, "50": 1, "80": 1, "100": 1, "135": 1}, "PDR": {"0": 4, "15": 1}, "ORB": {"0": 45}, "PDS": {"0": 2}}`.
- Every 5-minute offset is flagged in `convention_note` in the all-trades CSV together with the offset against the actual V3 fill time, so it is never silently counted as an exact match. Under the label convention a 5-minute offset is CLOSE, not EXACT.

## 4. Results

### A. Strategy signal matching

| strategy | manual_trades | eligible_manual_trades | exact | close_le_5min | near_le_10min | different_gt_10min | unmatched | signal_match_rate_fraction | signal_match_rate_pct |
|---|---|---|---|---|---|---|---|---|---|
| PDS | 3 | 3 | 2 | 0 | 0 | 0 | 1 | 2/3 | 66.7 |
| ORB | 89 | 85 | 45 | 0 | 0 | 0 | 40 | 45/85 | 52.9 |
| IRF | 73 | 72 | 23 | 10 | 7 | 16 | 16 | 40/72 | 55.6 |
| PDR | 8 | 8 | 4 | 0 | 0 | 1 | 3 | 4/8 | 50.0 |
| RF | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 0/0 |  |
| TOTAL | 190 | 168 | 74 | 10 | 7 | 17 | 60 | 91/168 | 54.2 |


Signal Match Rate = valid matched manual records / eligible manual records = **91 / 168 = 54.2%**.
Unsupported RF records: **17**. Ambiguous manual records (off-grid time / unknown outcome): **0**.
Matching was also run independently against the standalone trades (91 / 168 = 54.2%) and against the accepted FIFO portfolio trades (86 / 168 = 51.2%).

### B. Outcome agreement (valid matches only)

| manual_outcome | TARGET | STOP | EOD | NO_AUTOMATED_EXIT |
|---|---|---|---|---|
| Target | 34 | 3 | 0 | 0 |
| SL | 2 | 28 | 0 | 0 |
| BE | 7 | 15 | 2 | 0 |


- Agreement (Target/Target or SL/Stop): **62/91 = 68.1%**; excluding manual BE: **62/67 = 92.5%**.
- Manual BE vs automated: Target 7, Stop 15, EOD 2. **Both-break-even can never occur**: no author PDF contains a break-even / trailing / stop-move rule (searched all five), and the engine's exits are target, stop and 15:15 square-off only. 44 of 190 manual outcomes (23.2%) are BE, so a large part of the journal's P&L profile reflects discretionary trade management that is not in the written rules.
- Manual Target vs automated Stop: 3; manual SL vs automated Target: 2.
- Matching entry time and Win/Loss does **not** establish that the strategy was replicated: direction, method, level and stop/target distances are unknown for the manual side.

### C. Portfolio execution agreement

Of 91 matched signals, **86 were accepted** by the FIFO combined portfolio and **5 were rejected** (IRF OVERLAP_POSITION_OPEN x5). See `UNMATCHED_TRADE_REASONS.csv` scope `PORTFOLIO_LEVEL`.

### D. Strategy-wise comparison

| strategy | manual_trades | valid_matched_le_10min | signal_match_rate_pct | outcome_agreement_fraction | matched_signals_accepted_by_portfolio | main_mismatch_reasons |
|---|---|---|---|---|---|---|
| PDS | 3 | 2 | 66.7 | 2/2 | 2 | Strategy-method mismatch (1) |
| ORB | 89 | 45 | 52.9 | 34/45 | 45 | Strategy-method mismatch (17); Manual discretionary entry (16); Rulebook ambiguity (7) |
| IRF | 73 | 40 | 55.6 | 23/40 | 35 | Strategy-method mismatch (16); Different candle timing (13); Portfolio position conflict (5) |
| PDR | 8 | 4 | 50.0 | 3/4 | 4 | Rulebook ambiguity (3); Different candle timing (1) |
| RF | 17 | 0 |  | 0/0 | 0 | Unsupported strategy (RF disabled) (17) |


Reverse view (V3 signals in the journal's date range that have **no** manual counterpart; the journal may simply not contain every trade the friend could have taken):

| strategy | v3_signals | matched_to_a_manual_trade | no_manual_counterpart |
|---|---|---|---|
| PDS | 16 | 2 | 14 |
| ORB | 49 | 45 | 4 |
| IRF | 118 | 40 | 78 |
| PDR | 35 | 4 | 31 |


Reading: V3's ORB is highly consistent with the journal (45 of 49 V3 ORB signals are journal trades) but the journal contains many more ORB entries than Method 1 produces (recall 45/85). For IRF the opposite holds: V3 Method 2 emits many signals (118) that the journal does not record (78 with no counterpart).

## 5. Why manual trades are not reproduced (root causes)

| root_cause | IRF | ORB | PDR | PDS | RF | TOTAL |
|---|---|---|---|---|---|---|
| Strategy-method mismatch | 16 | 17 | 0 | 1 | 0 | 34 |
| Manual discretionary entry | 3 | 16 | 0 | 0 | 0 | 19 |
| Unsupported strategy (RF disabled) | 0 | 0 | 0 | 0 | 17 | 17 |
| Different candle timing | 13 | 0 | 1 | 0 | 0 | 14 |
| Rulebook ambiguity | 0 | 7 | 3 | 0 | 0 | 10 |
| Portfolio position conflict | 5 | 0 | 0 | 0 | 0 | 5 |
| Unspecified or missing manual information | 1 | 4 | 0 | 0 | 0 | 5 |


Classification is evidence-based from a day-by-day replay through the real strategy classes with the backtest's own point-in-time context
(`UNMATCHED_TRADE_REASONS.csv` has the conditions satisfied/failed, levels, the candle at the manual time, same-day V3 signals and diagnostic-only alternate-method signals for every record).
Alternate methods (ORB Method 2/3, IRF Method 1/3, PDS all-methods) and alternate level definitions are **diagnostics only, never applied**.

### The five most important reasons

1. **ORB trades without a broken previous-day level (40 of the 85 eligible ORB records are unmatched).** V3 implements only ORB Method 1 (09:15 candle closes beyond the previous-day high/low, with trend and candle-quality filters). On the unmatched days the 09:15 candle usually closed inside the previous day's range (median ~35 points short). The ORB PDF has two further setups the journal cannot distinguish: Method 2 (inner level + trendline) and Gap + 200 DMA. 17 unmatched ORB records have a strong in-trend 09:15 candle that fits an inner-level Method 2 entry (consistent with, not proof of it); 16 fail even the candle/trend tests (no ORB setup can be reconstructed); 7 are rulebook-ambiguity cases (level read as a zone edge, or provisional body/wick/EMA thresholds).
2. **Break-even outcomes.** 44 manual BE outcomes (23.2%) cannot be reproduced because no PDF defines a break-even rule. This is why outcome agreement is 68.1% overall but 92.5% when BE is set aside.
3. **IRF method not recorded.** The journal does not say which IRF method was used; V3 runs Method 2 only. 16 unmatched IRF records coincide with an IRF Method 1 or 3 pattern within 10 minutes (e.g. a breakout candle immediately closing back inside, which the PDF defines as Method 1), 13 have a V3 Method 2 signal the same day but more than 10 minutes away, and 3 have no initial-range break at all by the manual time.
4. **Provisional rules deliberately left untuned.** 3 PDR records fail only the provisional "open above the resistance-zone top" gate; ORB zone/body/wick/EMA thresholds and the PDS zone definition are likewise PROVISIONAL. Changing them to chase the journal would be curve-fitting, so they were not changed (`docs/AUTHOR_CONFIRMATION_REQUEST.md` lists the author questions).
5. **Scope and portfolio effects.** RF (17 records, 8.9%) is disabled by design; 5 entries are dated on weekends; and 5 matched IRF signals lost to the one-position FIFO rule.

## 6. Implementation-defect hunt (section 7 of the brief)

No implementation defect was demonstrated, so **no strategy or engine code was changed**; before and after results are identical.

- ORB previous-day levels were independently recomputed from raw bars for 265 sessions: **264 match, 1 differ**; unexplained differences: **0**. 2025-10-23: previous session 2025-10-21 has only 13 regular candles (special session), so the level builder deliberately uses the official daily high/low (OFFICIAL_DAILY_FALLBACK) (That session's only journal entry is an IRF trade, which does not use previous-day levels.)
- Timestamps: signal-candle labels, fill times (label + 5 min) and the 14:30 / 15:15 rules were checked against the dataset; the 09:15-only first-candle rule is consistent with the PDF ("evaluate only after the candle has completed").
- IRF is short-only per the PDF ("Short trades only"), so the absence of a mirrored bullish setup is correct.
- The matching code is covered by tests (`tests/backtesting/test_manual_reconciliation.py`).

## 7. Reference baselines (read-only, not the comparison target)

- Frozen V1 ledger `reports/phase2d/backtest_trades.parquet (353 trades; IRF Method 1; frozen; read-only)`, same matcher (signal label = fill time - 5 min): valid matches **77 / 168 = 45.8%**, exact 71; by strategy {"PDS": "3/3", "ORB": "46/85", "IRF": "24/72", "PDR": "4/8"}. V1 used IRF Method 1 and legacy rules.
- V2 (the earlier corrected engine) is documented in the frozen reconciliation test as 89 timing matches under the older auditor's tolerance tiers; that is a different procedure and is quoted for context only.

## 8. Conclusion

V3 **partially reproduces** the manual strategy:

- Where the journal's entry is an ORB Method 1 / IRF Method 2 / PDR / PDS setup that the written rules can express, V3 finds the same candle (exact timing for 81.3% of matches) and the same win/loss for 92.5% of non-BE matches.
- It does **not** reproduce 77 of 168 eligible entries (45.8%), mostly ORB entries without a previous-day-level break, IRF entries of another method or at another time, and manual trade management (break-even) that no document specifies; it does not claim to verify what rule or level the manual trader used.
- It therefore **should not be described as a replica of the manual journal**. Closing the gap needs author answers on the provisional rules, the method used per journal entry (especially ORB and IRF), and the break-even handling.

## 9. Verification

{
  "all_records_present": true,
  "records_in_table": 190,
  "serials_unique": true,
  "every_record_explained_or_fully_matched": true,
  "duplicate_automated_signal_matches": 0,
  "duplicate_portfolio_trade_matches": 0,
  "denominator_eligible_equals_sum_categories": 1,
  "rf_records_identified": 17,
  "generated_at": "2026-10-30T11:33:32"
}

Backtest summary used: `reports/backtest/V3_PDF_CORRECTED_20261030T045143Z_523f2825/summary.md`.
