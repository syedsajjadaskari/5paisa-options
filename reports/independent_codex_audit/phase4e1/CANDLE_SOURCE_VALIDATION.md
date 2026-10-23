# Candle Source Validation

## Live path

`FiveMinuteCandleAggregator` consumes `SpotTick.last_rate` and reconstructs each bar from the sampled ticks observed by the poller. Its high and low are the maximum and minimum sampled prices; the provider's session high/low fields are not an intrabar OHLC source. Therefore a sparse poll can miss the true exchange high or low. Live aggregated candles are marked `FIVEPAISA_WS` and are `INSUFFICIENT_FOR_BREAKOUT_VALIDATION` until reconciled to an authoritative provider candle.

A candle is emitted only after a provider-timestamped tick crosses the next five-minute boundary. The current incomplete bar is not sent to shadow evaluation. A gap between completed buckets is recorded as missing and the affected sampled bar is not accepted as a complete continuous session.

## Verified reconciliation source

The broker adapter exposes authenticated 5paisa historical candles through `FivePaisaBroker.fetch_historical_candles()` using `/V2/historical/N/C/999920000/5m`. That endpoint is suitable for completed-bar reconciliation, subject to authentication and provider availability. It was not called during this out-of-hours diagnostic, so no live reconciliation is claimed.

## Stored historical sample

source,observed_full_session_days,sample_replay_day,sample_replay_candles,source_kind,breakout_validation_status
data/processed/nifty/5m/nifty50_5m.parquet,266,2025-08-01,75,provider historical 5m OHLC file,REPLAY_ONLY_NOT_LIVE

The historical file can validate a replay session's timestamps and OHLC invariants, but it does not convert sampled live ticks into authoritative live candles.
