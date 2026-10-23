# Lookahead and Availability Audit

- Primary session: `2025-08-06`; comparison sessions: `2025-08-06, 2025-08-07, 2025-08-08, 2025-08-11, 2025-08-12`.
- Prior-day daily references are selected only from dates strictly less than the evaluated session date.
- EMA includes only bars at or before the evaluated candle timestamp; missing warm-up remains missing.
- DMA uses only the previous 200 completed official daily closes.
- Zone-body source candles are from the prior session only.
- A five-minute candle is eligible only at or after its close timestamp; no opening-timestamp backdating is used.
- Historical bars have no original publication timestamp, so availability is explicitly labelled assumed, never prospective.
- The four strategies receive the same frozen context object for each bar; no later-bar values are injected.
