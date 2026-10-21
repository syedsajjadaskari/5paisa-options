# Market Data Contract & Canonical Schema

## 1. Overview
The platform enforces a standardized, normalized data contract across all historical data providers (CSV, Parquet, 5paisa historical API) and real-time feeds (5paisa WebSocket). Every candle ingested into the platform must conform to this schema before storage and strategy processing.

---

## 2. Canonical Column Schema

| Column Name | Data Type (PyArrow / DuckDB) | Nullable | Description / Standard |
| :--- | :--- | :--- | :--- |
| `timestamp` | `TIMESTAMP_MS` (or `TIMESTAMP_NS`) | No | Bar open timestamp in UTC |
| `exchange_time` | `TIMESTAMP_MS` | No | Bar open timestamp in `Asia/Kolkata` (IST) |
| `symbol` | `VARCHAR` | No | Trading symbol (e.g. `NIFTY 50`, `NIFTY24OCTFUT`, `NIFTY24OCT25000CE`) |
| `instrument_id` | `VARCHAR` | No | Unique broker/exchange token (e.g., 5paisa ScripCode or NSE token) |
| `exchange` | `VARCHAR` | No | `NSE`, `NFO`, `BSE`, `MCX` |
| `instrument_type` | `VARCHAR` | No | `INDEX`, `EQUITY`, `FUTURES`, `OPTCE`, `OPTPE` |
| `interval` | `VARCHAR` | No | Candle resolution: `1m`, `5m`, `15m`, `1d` |
| `open` | `DOUBLE` | No | Open price of the bar |
| `high` | `DOUBLE` | No | High price of the bar |
| `low` | `DOUBLE` | No | Low price of the bar |
| `close` | `DOUBLE` | No | Close price of the bar |
| `volume` | `INT64` | No | Traded volume during the bar (0 for cash indices) |
| `open_interest` | `INT64` | Yes | Open interest at bar close (derivatives only, null for index) |
| `source` | `VARCHAR` | No | Data source tag: `CSV`, `FIVEPAISA_HISTORICAL`, `FIVEPAISA_WS` |
| `ingested_at` | `TIMESTAMP_MS` | No | Wall-clock timestamp when record was ingested (UTC) |

---

## 3. Data Invariants & Quality Rules

Before any bar record is committed to storage or consumed by a strategy, it must pass through the `DataValidator`:

1. **OHLC Integrity:**
   - $High \ge \max(Open, Close)$
   - $Low \le \min(Open, Close)$
   - $High \ge Low$
   - $Open > 0, High > 0, Low > 0, Close > 0$
2. **Volume Integrity:**
   - $Volume \ge 0$
3. **Chronological Monotonicity:**
   - Timestamps must be strictly monotonically increasing per `(symbol, interval)`. No duplicate timestamps are permitted.
4. **Session Boundaries:**
   - NSE regular cash/index trading hours: `09:15:00` to `15:30:00` IST.
   - For 5-minute candles, standard open timestamps are `09:15:00, 09:20:00, ..., 15:25:00` (75 candles per normal trading day).
5. **Session Continuity:**
   - Any gap within trading hours $\Delta t > \text{interval}$ during a normal session is flagged as a `DATA_GAP_WARNING` or rejected based on configuration.

---

## 4. Storage Architecture

- **Partitioning Scheme:**
  `data/processed/{instrument_type}/{symbol}/{year}/data_{interval}.parquet`
- **Compression:** Parquet format compressed using `ZSTD` (compression level 3) for optimal balance of compression ratio and decompressive read speed.
- **Query Engine:** DuckDB queries the Parquet partitions directly via zero-copy PyArrow integration without loading unneeded columns into memory.
