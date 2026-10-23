# Live Session Acceptance Checklist

## Decision

Current status: **read-only observation only**. Paper fills are disabled. Unattended operation is prohibited.

## A. Synthetic infrastructure evidence

- [x] Read-only broker proxy blocks order/modify/cancel/generic transport.
- [x] Process lock, run ID, heartbeat, atomic state writes, and restart mismatch tests pass.
- [x] Account namespaces prevent live/broker cash import.
- [x] Decimal reference accounting reconciles to paise.
- [x] Missing/future/stale/crossed quotes are rejected.
- [x] Unsupported special sessions block entries.

## B. Historical market-data evidence

- [x] NIFTY 1m/5m/daily integrity and hashes documented.
- [x] Official daily references separated from 15:30 intraday records.
- [x] BASELINE_V1 preserved.
- [x] CORRECTED_V2 regenerated deterministically: 307 unarbitrated, 271 FIFO, +2,016.20 FIFO points.
- [x] RF excluded from corrected executable set.
- [ ] Genuine historical option premium archive with exchange timestamps.
- [ ] Complete rulebook ambiguity confirmation.

## C. Genuine market-hours read-only observation

Before any paper fill gate is reconsidered, capture for multiple cycles:

- [ ] Redacted raw spot and option responses, response hashes, and receipt times.
- [ ] Provider timestamp, timezone conversion, age, clock-skew result, and session classification.
- [ ] NIFTY scrip identity and option scrip/expiry/strike/type/lot metadata.
- [ ] Positive non-crossed bid/ask, quantities, spread, and quote age.
- [ ] Reconnect, SSL failure, API status, token expiry, and rate-limit behavior.
- [ ] At least one complete valid quote sequence after a confirmed underlying candle close.
- [ ] No paper fill until all prior checks are green.

## Stop conditions

Stop observation and keep the gate false on missing provider time, future time, stale data, unexpected scrip, unsupported session, missing bid/ask, crossed book, contract mismatch, duplicate run, mixed state, pending exit without quote, or any API response that is merely `Success` without validated data.

