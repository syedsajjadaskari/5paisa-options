# Deterministic Collector Replay Acceptance

The collector's lock, heartbeat, restart, closed-session gating, persistence
schema, market-data validation, and safe-shutdown boundaries were exercised
through deterministic local checks. The replay consumed stored historical
OHLC only; it did not invoke the broker or attempt authentication.

The persisted state stayed `READ_ONLY`, the closed-session path made zero spot
requests, and the temporary DuckDB schema contained zero virtual orders and
zero virtual fills. The normal-session historical file validated at 75 aligned
completed bars with no gaps or future rows.

This establishes offline readiness of the collection path, not live-session
acceptance. `FULL_LIVE_SESSION_VALIDATION = PENDING`.
