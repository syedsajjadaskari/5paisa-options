# Phase 4E Collector Operational Audit - 2026-10-23

## Executive result

The audited collector run is **not currently running**. Persisted state is
`STOPPED`, `pid=null`, the recorded heartbeat is `2026-10-23T09:22:18.548756+05:30`,
and its age at audit time was `438535.1` seconds. No collector PID was
found (`0 matching collector processes`). The lock file contains PID `12536`, but that PID is not alive (`False`); the
file is stale lock metadata, not evidence of a running process.

The terminal word `Success` is not accepted as ingestion evidence. Storage and
timestamp evidence below is the basis for this report.

## Observed run: `READONLY_20261023_091904_498edc`

| Check | Evidence |
|---|---:|
| Valid NIFTY spot rows in DuckDB | 34 |
| Spot rows rejected by persisted state | 0 |
| Latest verified spot exchange timestamp | 2026-10-23T09:22:15+05:30 |
| Spot receipt span | 2026-10-23T09:19:06.501579+05:30 to 2026-10-23T09:22:15.370714+05:30 |
| Spot maximum receipt gap | 21.649s |
| Spot receipt gaps over 1s | 33 |
| Valid option rows in DuckDB | 328 |
| Option rows rejected by persisted state | 4 |
| Option attempts represented by persisted counters | 330 |
| Latest verified option exchange timestamp | 2026-10-23T09:22:16+05:30 |
| CE / PE rows | 164 / 164 |
| Option contracts | 12 |
| Option expiry / lot metadata | 2026-10-27/65, 2026-10-27/65 |
| Local scrip-master contract matches | 12/12; metadata mismatches 0 |
| Option strike range | 22600.0 to 22850.0 |
| DuckDB option rows | 328 |
| Parquet option rows after reconciliation | 328 across 7 files |
| Option receipt minimum interval | 0.230s; configured floor 0.200s |
| Option maximum receipt gap | 7.484s |
| Option intervals under configured floor | 0 |
| Duplicate spot identities | 0 |
| Duplicate option identities | 0 |
| Provider future timestamp rejects observed in stored valid rows | 0 |
| Persisted feed interruptions | none |
| Collector cycles / heartbeat spot counter | 33 / 34 |
| Heartbeat option counter vs DuckDB | 326 vs 328 |

The heartbeat counter mismatch is a telemetry defect: DuckDB is authoritative
for accepted records, while the heartbeat reports two fewer option observations.
The shutdown Parquet-buffer defect was reproduced and corrected; the 28 rows
that were present in DuckDB but absent from Parquet were written to a separate
reconciliation Parquet file without overwriting existing data.

## Five-minute candles

The run contains `1` completed candle. Stored source values are
`UNKNOWN` and validation statuses are
`LEGACY_UNVALIDATED`. Authoritative independently validated
candles: `0`.

The observed live candle was created by sampled spot aggregation. It is not an
exchange/provider OHLC bar and cannot independently validate breakout highs or
lows. The recorder now persists `source`, `validation_status`, and
`intrabar_exact=false`; future sampled candles are explicitly marked
`SAMPLED_LIVE_OHLC_NOT_BREAKOUT_AUTHORITY`.

## Shadow and execution safety

- Shadow evaluations recorded for this collector run: `0`.
- Paper virtual orders: `0`.
- Paper virtual fills: `0`.
- Completed trades: `0`.
- The read-only broker proxy blocks transactional SDK methods, and this audit
  made no broker requests. Existing tests cover the transaction boundary.

## Credential handling

Future authentication stdout/stderr is now captured, logging records are
redacted, and persisted provider payloads recursively redact token,
authorization, password, PIN, TOTP, and key fields. The existing token-cache
file is present (`True`); its value was not read into this
report. Because authentication output was previously exposed, invalidate the
5paisa session immediately, revoke any exposed API/session token, rotate the
relevant application credentials if required by 5paisa, and remove the local
token cache only after revocation. Do not paste the old token into tickets or
audit logs.

## Status

`COLLECTOR_RUNNING = FALSE`

`HEARTBEAT_FRESH = FALSE`

`VALID_SPOT_INGESTION = TRUE FOR THE SHORT OBSERVED WINDOW`

`VALID_CE_PE_INGESTION = TRUE FOR THE SHORT OBSERVED WINDOW`

`FULL_SESSION_VALIDATION = NOT ESTABLISHED`

`PAPER_EXECUTION = DISABLED`

`BROKER_TRANSACTIONS = BLOCKED`

This run covered approximately three minutes, not the full NSE session. It is
therefore not evidence of full-session live acceptance.
