# Five-Minute Candle Integrity Audit

Historical replay session selected: `2025-08-01` with `75` rows.

- Aligned rows: `75`
- Future rows relative to replay sequence: `0`
- OHLC-invalid rows: `0`
- Missing 5-minute intervals: `0`
- Maximum interval gap: `5.0` minutes
- Complete replay sequence: `True`

The source is a provider candle file, not a complete raw tick stream. This validates timestamp alignment and OHLC consistency but does not prove that polling-based live OHLC would capture intrabar highs or lows. Live candle acceptance remains pending until a full session of authoritative candles or sufficiently dense ticks is captured.
