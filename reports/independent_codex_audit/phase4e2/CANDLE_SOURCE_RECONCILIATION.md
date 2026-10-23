# Authoritative Five-Minute Candle Reconciliation

The repository's authenticated adapter exposes the official 5paisa V2 historical endpoint through `FivePaisaBroker.fetch_historical_candles()` for `N/C/999920000/5m`. The stored processed 5-minute file is labelled `FIVEPAISA_HISTORICAL` and is the authoritative historical source used in this replay.

The endpoint returns completed OHLC rows; the adapter parses the provider timestamp and rejects empty or unauthenticated responses. This phase did not make a new network call because it was run outside market hours. The existing stored source was independently validated for session alignment and OHLC invariants.

Primary session `2025-08-06`: `75` rows, aligned `75`, future `0`, duplicates `0`, OHLC-invalid `0`, missing intervals `0`, complete `True`.

A stored historical bar proves that the bar exists today (A), not that its original publication time was observed prospectively by the collector (B/C). Shadow evaluation therefore uses the bar-close timestamp as a conservative retrospective availability assumption and records that assumption in the reference provenance.

Sampled live spot aggregation remains diagnostic-only and cannot silently replace provider OHLC for breakout decisions.
