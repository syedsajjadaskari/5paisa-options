# Point-in-Time Reference Data Provenance

Primary replay session: `2025-08-06`. Contexts are immutable snapshots built by `PointInTimeReferenceProvider` and shared by the Phase 4E.2 shadow replay.

Official previous-day PDH/PDL/PDO/PDC values come from the processed 1-day 5paisa historical dataset. The prior-day zone body values come from the intraday candle containing the corresponding official high/low. EMA uses only intraday closes at or before the evaluated candle; DMA uses only completed daily closes strictly before the decision date.

Historical publication timestamps are not present in the stored Parquet files. The conservative retrospective assumption is that a completed prior-day dataset was available by the next session open. This is recorded as `HISTORICAL_AVAILABILITY_ASSUMED` and is not claimed as prospective collector evidence.

Complete strategy-reference evaluations: `300` of `300`.

| Field | Value | Source dataset | Source date | Source timestamp | Availability timestamp | Calculation | Validation |
|---|---:|---|---|---|---|---|---|
| `pdh` | `24733.1` | `data/processed/nifty/1d/nifty50_1d.parquet` | `2025-08-05` | `2025-08-05 00:00:00+05:30` | `2025-08-06 09:15:00+05:30` | official completed daily OHLC; prior date strictly less than decision date | `HISTORICAL_AVAILABILITY_ASSUMED` |
| `pdl` | `24590.3` | `data/processed/nifty/1d/nifty50_1d.parquet` | `2025-08-05` | `2025-08-05 00:00:00+05:30` | `2025-08-06 09:15:00+05:30` | official completed daily OHLC; prior date strictly less than decision date | `HISTORICAL_AVAILABILITY_ASSUMED` |
| `pdc` | `24649.55` | `data/processed/nifty/1d/nifty50_1d.parquet` | `2025-08-05` | `2025-08-05 00:00:00+05:30` | `2025-08-06 09:15:00+05:30` | official completed daily OHLC; prior date strictly less than decision date | `HISTORICAL_AVAILABILITY_ASSUMED` |
| `pdo` | `24720.25` | `data/processed/nifty/1d/nifty50_1d.parquet` | `2025-08-05` | `2025-08-05 00:00:00+05:30` | `2025-08-06 09:15:00+05:30` | official completed daily OHLC; prior date strictly less than decision date | `HISTORICAL_AVAILABILITY_ASSUMED` |
| `pd_res_body` | `24720.25` | `data/processed/nifty/5m/nifty50_5m.parquet` | `2025-08-05` | `2025-08-05 09:15:00+05:30` | `2025-08-06 09:15:00+05:30` | body of intraday candle containing prior official high | `HISTORICAL_AVAILABILITY_ASSUMED` |
| `pd_supp_body` | `24601.6` | `data/processed/nifty/5m/nifty50_5m.parquet` | `2025-08-05` | `2025-08-05 14:05:00+05:30` | `2025-08-06 09:15:00+05:30` | body of intraday candle containing prior official low | `HISTORICAL_AVAILABILITY_ASSUMED` |
| `ema_200` | `24657.66` | `data/processed/nifty/5m/nifty50_5m.parquet` | `2025-08-06` | `2025-08-06 09:20:00+05:30` | `2025-08-06 09:20:00+05:30` | recursive EMA over 200 completed 5-minute closes through this candle | `VALIDATED` |
| `dma_200` | `24047.18` | `data/processed/nifty/1d/nifty50_1d.parquet` | `2025-08-05` | `2025-08-05 00:00:00+05:30` | `2025-08-06 09:15:00+05:30` | simple mean of the previous 200 official daily closes | `HISTORICAL_AVAILABILITY_ASSUMED` |
| `session_open` | `24652.0` | `data/processed/nifty/5m/nifty50_5m.parquet` | `2025-08-06` | `2025-08-06 09:15:00+05:30` | `2025-08-06 09:20:00+05:30` | first completed 09:15 session candle open | `VALIDATED` |
| `irh` | `24664.65` | `data/processed/nifty/5m/nifty50_5m.parquet` | `2025-08-06` | `2025-08-06 09:15:00+05:30` | `2025-08-06 09:20:00+05:30` | high of the completed first 09:15 five-minute candle | `VALIDATED` |
| `irl` | `24634.55` | `data/processed/nifty/5m/nifty50_5m.parquet` | `2025-08-06` | `2025-08-06 09:15:00+05:30` | `2025-08-06 09:20:00+05:30` | low of the completed first 09:15 five-minute candle | `VALIDATED` |
| `gap_points` | `2.45` | `data/processed/nifty/5m/nifty50_5m.parquet+data/processed/nifty/1d/nifty50_1d.parquet` | `2025-08-06` | `2025-08-06 09:15:00+05:30` | `2025-08-06 09:20:00+05:30` | current session open minus official prior daily close | `VALIDATED` |
