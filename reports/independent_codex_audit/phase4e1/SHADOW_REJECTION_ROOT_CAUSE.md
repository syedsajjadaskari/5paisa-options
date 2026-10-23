# Phase 4E.1 Shadow Rejection Root Cause

The existing audit contains 375 rows: IRF=75, ORB=75, PDR=75, PDS=75, RF=75.

## Exact primary reasons

Category totals are MISSING_REFERENCE_DATA=300, RULEBOOK_AMBIGUITY=75. Every row has a single primary category in the CSV.

- 300 rows (PDS, ORB, IRF and PDR; 75 each) are `MISSING_REFERENCE_DATA`. Their rule evaluation says `completed_candle=true`, `aligned_to_5m=true`, `future_observation=false`, but `previous_day_levels_available=false`. The replay caller passed `{}` to the shadow evaluator, so these rows were not strategy-condition false; they were not eligible for a point-in-time rule evaluation.
- 75 rows (RF; all 75 candles) are `RULEBOOK_AMBIGUITY` because the canonical RF exit/target is explicitly unresolved. This is an intentional safety gate, not a blanket data failure.

## Blanket-gating investigation

There is no evidence that candle alignment or future-data gating caused the 375 rejections: all 375 rows report aligned, completed, non-future candles. There is also no `STRATEGY_CONDITION_FALSE` row because the conservative shadow collector stops before evaluating strategy conditions when required inputs or rulebook semantics are unavailable. The diagnostic change records those gates separately so a future run cannot confuse missing evidence with a false setup.

The next safe diagnostic step is to provide verified prior-day levels and indicator history to the shadow evaluator. PDS/PDR/ORB/IRF must still remain rejected as `RULEBOOK_AMBIGUITY` until the author confirms the unresolved definitions; RF remains non-executable until its canonical exit is specified.
