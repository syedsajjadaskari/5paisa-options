# Phase 4E.3 Offline Shadow Rejection Root Cause

## Scope

This report is based on the Phase 4E.2 audit and a fresh deterministic replay
of the genuine stored provider-labelled 5-minute file. It does not represent a
live observation and no broker authentication or market-data request was made.

## Primary replay

- Session: `2025-08-06`
- Source: `data/processed/nifty/5m/nifty50_5m.parquet` (`FIVEPAISA_HISTORICAL`)
- Completed candles: `75`; aligned: `75`
- Missing intervals: `0`; future rows: `0`
- Duplicate rows: `0`; OHLC-invalid rows: `0`
- No execution records were created.

## Explanation of the original 375 rejections

The original Phase 4E.1 file contains 375 rows: 75 per canonical method.
300 rows (PDS, PDR, ORB and IRF) were rejected as `MISSING_REFERENCE_DATA`
because the caller passed an empty prior-day context. They were not strategy
condition failures. The 75 RF rows were rejected as `RULEBOOK_AMBIGUITY` because
the original guide does not define a canonical exit/target.

Phase 4E.2 corrected the context path. Its primary replay had valid completed
candles and valid point-in-time contexts; outcomes then separated genuine
`STRATEGY_CONDITION_FALSE` from `RULEBOOK_AMBIGUITY` rather than blanket-gating
all rows. The fresh Phase 4E.3 run preserves that distinction in
`SHADOW_EVALUATIONS.csv`.

## Safety conclusion

No rejection was overridden. Ambiguous rules remain blocked and do not create
signals, virtual trades, paper positions, or P&L. Full live-session validation
remains pending.
