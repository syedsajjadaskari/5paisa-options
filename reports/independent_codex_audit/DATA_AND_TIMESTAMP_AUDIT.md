# Data and Timestamp Audit

## Source inventory and hashes

| Source | Rows/shape | Coverage | SHA-256 |
|---|---:|---|---|
| NIFTY 1-minute parquet | 92,981 | 2025-09-01 to 2026-08-31; 248 dates | `a0fea90ac0e7469e150fc732dba6f61f5984bd4a75a614ec5497e206a22db524` |
| NIFTY 5-minute parquet | 20,237 | 2025-08-01 to 2026-08-31; 267 dates | `02984db58b9697e1bb8862b2a1a0284e94648d1536b8d9a5c2e61306d1ed684b` |
| NIFTY daily parquet | 661 | 2024-01-01 to 2026-08-31 | `8ed0deb2722cabc82e0528bd120a1947bf8e2668910f1a0ea97589a78c73bc4e` |
| Market DuckDB | inspected | table counts agree with parquet inventory | `b77f46d52766a4ff306ef8c34b265698ed92c1cd1dc1abece5d134e10f54a87d` |
| Manual workbook | 190 rows | journal window | `97a9ad4dfb5520619bb979770115d7b6f4e3e37e649af255c543f5fce9694781` |
| Instrument master | 178,229 rows | NIFTY options: 4,062 | `8991a989bdf3bf33b546cb51f7593ec420517494083183cc0014dbe9f5ae4286` |

## Candle integrity

- 1-minute data has no duplicate timestamps, out-of-order rows, invalid/null OHLC, or null timestamps. Regular days mostly have 375 bars; one Muhurat day has 61. 295 opens fall outside the regular-hours pattern.
- 5-minute data has no duplicate timestamps, out-of-order rows, invalid/null OHLC, or null timestamps. Regular days mostly have 75 bars; Muhurat has 13. It includes 15:30 (265 rows), 15:35 (3), 15:40 (3), 15:45/15:50/15:55 (one each).
- Daily bars are clean at the basic OHLC/date level. 413 daily sessions precede the evaluation window, satisfying indicator warm-up availability.
- Raw maximum consecutive-bar move is approximately 3.3%; 22 moves exceed one percent. These require market/session-boundary adjudication before being treated as errors.

## The 15:30 record

All 265 observed 15:30 5-minute rows have zero volume. Only 63 equal the official daily close; 192 differ by more than one point, with a maximum difference of 88.4 points. The 15:30 record is therefore not accepted as an official settlement or daily close. Corrected reference-level code uses official daily bars for PDH/PDL/PDC/PDO/DMA and only uses intraday bars for the explicit body-zone context, restricted to 09:15–15:25.

## Calendar coverage

For the evaluation period, the corrected static calendar expects 246 regular sessions and finds no missing regular session. Two special sessions are present in the data: 2025-10-21 Muhurat (13:45–14:45) and 2026-02-01 Sunday Budget session (regular hours). The static calendar does not model those special sessions dynamically. That is a deployment blocker: a calendar lookup cannot by itself prove that a given minute is executable.

The 2026 F&O holiday list was checked against [NSE circular FAOP71777](https://nsearchives.nseindia.com/content/circulars/FAOP71777.pdf) and the January update referencing FAOP72262 ([NSE circular CMPT72276](https://nsearchives.nseindia.com/content/circulars/CMPT72276.pdf)). Incorrect dates formerly listed in code (including Feb 17, Mar 20, Mar 27, May 27, Jun 25, Nov 9) were removed; authoritative dates such as Jan 15, Mar 26, Mar 31, May 28, Jun 26, Sep 14, and Nov 10 were added.

## Provider timestamp verification

`parse_fivepaisa_timestamp` now requires a complete `/Date(milliseconds+offset)/` or supported ISO form and returns timezone-aware IST. The historical example `/Date(1729571156000+0530)/` converts to **2024-10-22 09:55:56 IST**. It cannot be a displayed October 2026 quote. Missing/malformed/out-of-range values raise; they are never replaced with local receipt time.

The live feed independently recomputes age against the provider timestamp. Boundary tests verify 4.9 seconds fresh, 5.0 seconds fresh under the configured inclusive threshold, and 5.1 seconds stale.

## Options archive finding

The DuckDB archive contained 8,257 rows / unique IDs; the parquet archive contained 8,140 rows. Every inspected row had `provider_timestamp == receipt_timestamp`; DuckDB had 270 nonpositive prices and parquet 250. Stale-count logic consequently reported zero, but that is not proof of freshness: a receipt timestamp can be fresh while the underlying quote timestamp is fabricated. The archive is quarantined/unusable for backtesting.

The recorder now requires raw `TimeStamp`/`TickDt`, parses it strictly, and skips missing or invalid records. A new live soak must establish a nonzero provider-to-receipt time delta distribution before fills are enabled.

## Instrument evidence

The NIFTY option master has no duplicate scrip IDs, expiry dates from 2026-10-27 through 2031-06-24, and lot size 65 for the inspected current contracts. Lot-size behavior was checked against [NSE circular FAOP70616](https://nsearchives.nseindia.com/content/circulars/FAOP70616.pdf). This validates metadata shape, not live-chain freshness or liquidity.

The transaction-cost regime was checked against [NSE circular FA64232](https://nsearchives.nseindia.com/content/circulars/FA64232.pdf) and the [Union Budget memorandum](https://www.indiabudget.gov.in/doc/memo.pdf). Current weekly-expiry and contract metadata references were checked against [NSE contract specifications](https://www.nseindia.com/static/products-services/equity-derivatives-contract-specifications).
