# Phase 4E.1 Full-Session Read-Only Acceptance

## Decision

**CONDITIONAL PASS — diagnostics and collector safeguards are ready; full-session live validation is PENDING.**

A genuine full normal NSE session was not captured in Phase 4E.1. The current environment was outside 09:15–15:30 IST when validation was run, and the collector stopped at the calendar gate without making API calls. The Phase 4D sample remains a verified short read-only sample, not full-session evidence.

The acceptance gate requires one run with 09:15–15:30 IST coverage, actual provider timestamps, accepted spot and near-ATM CE/PE quotes, contract metadata, freshness, gaps, reconnection telemetry, and 75 completed five-minute bars. Sampled-tick OHLC must remain non-breakout-authoritative until reconciled with the authenticated 5paisa historical candle endpoint.

## Safe commands

Start during a normal NSE session:

```bash
.venv/bin/python scripts/run_phase4e_collector.py observe
```

Stop with `Ctrl-C`; shutdown is persisted as `STOPPED` and the lock is released. Inspect status with:

```bash
.venv/bin/python scripts/run_phase4e_collector.py status
.venv/bin/python scripts/generate_phase4e1_reports.py
```

Paper execution, simulated fills, account mutations, live orders, optimization and BASELINE_V1 changes remain disabled.
