# Candle Source Validation

The replay used `data/processed/nifty/5m/nifty50_5m.parquet` and `data/processed/nifty/1d/nifty50_1d.parquet`.
The stored 5-minute rows are labelled `FIVEPAISA_HISTORICAL` and contain
provider OHLC. The primary session has `75` standard
completed bars, with `0` missing intervals,
`0` duplicate timestamps, and
`0` invalid OHLC rows.

This is not equivalent to a live full-session capture. A sampled spot stream
aggregated into 5-minute buckets can preserve neither the true intrabar high
nor the true intrabar low; such sampled bars remain diagnostic-only and must
not validate breakout rules. The collector therefore records sampled candles
as non-authoritative, while this offline replay uses only the stored provider
OHLC and excludes the 15:30 boundary row from the 75-bar count.

No completed-bar trigger was evaluated before the bar's 5-minute close. No
current incomplete bar was used. A verified broker historical source may be
used for future reconciliation only when credentials and market-data access
are intentionally available; no such request was made in this offline run.
