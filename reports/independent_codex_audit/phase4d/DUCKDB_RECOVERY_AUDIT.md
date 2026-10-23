# DuckDB Recovery Audit

- Schema initialization and migrations run in an explicit transaction.
- `market_events` now migrates `ingestion_timestamp`; recorder inserts use named columns.
- Spot, option and candle IDs are deterministic per run/provider observation; duplicate replays are ignored.
- Account metadata remains isolated under `PAPER_VIRTUAL`.
- Successful capture `PHASE4D_20261022_122136` has one market row, two option rows, and zero virtual fills.

Residual risk: DuckDB remains single-process and must remain protected by the engine lock.
