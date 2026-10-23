# Raw Payload Provenance

Successful capture `PHASE4D_20261022_122136` persisted sanitized provider items/envelopes in `RAW_CAPTURE_SANITIZED.json`. Provider timestamps and local receipt timestamps are separate, and response hashes are recorded.

The persisted spot row contains the exact provider `Data[0]` item, including `TickDt`, `Token`, `Exch`, `ExchType`, `LastRate`, `High`, `Low` and `PClose`. The option rows contain full `Status`, `Message`, `MarketDepthData`, `Asks`, `Bids`, `ScripCode`, `ExchType` and `TimeStamp` envelopes. The feed has since been corrected to persist full spot envelopes prospectively as well.

`/Date(1729571156000+0530)/` independently parses to `2024-10-22 09:55:56 IST`. The captured 2026 values remain unchanged. A bounded 2-second provider whole-second clock-skew tolerance is documented; larger future skew is rejected.

Virtual fills for this run: `0`. Paper execution remained disabled.
