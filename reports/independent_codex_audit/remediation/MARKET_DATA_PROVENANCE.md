# Market-Data Provenance Remediation

## Accepted pipeline

```text
5paisa read-only response
  -> response status/data shape check
  -> NIFTY/option scrip identity check
  -> strict provider timestamp parse
  -> future/stale/clock-skew rejection
  -> positive, non-crossed bid/ask validation
  -> contract metadata and session validation
  -> verified archive only
  -> paper-fill eligibility
```

`/Date(1729571156000+0530)/` converts to **2024-10-22 09:55:56 IST**. The epoch milliseconds are absolute; the `+0530` suffix is not allowed to turn it into 2026. Tests cover the conversion and malformed values.

## Code changes

- `parse_fivepaisa_timestamp` rejects missing/malformed/out-of-range values.
- `fetch_spot_tick` rejects missing provider timestamps, future timestamps, and unexpected response scrip codes when supplied.
- `fetch_option_depth` rejects missing/future timestamps and unexpected response scrip codes when supplied.
- `LiveContractSelector` independently recomputes quote age, rejects future age, and requires `provenance_verified`.
- `MarketDataRecorder` stores raw provider timestamp, validation status, validation reason, and redacted raw response JSON for prospective captures.
- New verified data writes to `data/processed/options_archive_verified`.

## Raw response evidence status

A genuine redacted raw API payload was **not available in this remediation run**. A safe read-only connection attempt returned `connected=False`, so no current raw payload was captured. The prior development report states a payload shape containing `Status`, `Message`, `Data[].LastRate`, `Data[].TickDt`, `High`, `Low`, and `PClose`, and option depth containing `MarketDepthData[].Asks/Bids/TimeStamp`; those statements are treated as development-report claims, not current evidence.

No secret, token, or credential value is included. The next read-only soak must persist a redacted payload containing only the fields above plus response receipt time, with raw response hash and validation result.

## Eligibility policy

API `Status=0` or `Message=Success` only proves response-level success. It does not prove quote freshness, contract identity, executable bid/ask, or current session. Without authoritative provider timestamp and valid two-sided depth, the observation is rejected and cannot produce a paper fill.

