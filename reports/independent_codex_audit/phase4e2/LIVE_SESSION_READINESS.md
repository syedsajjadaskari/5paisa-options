# Live Session Readiness

## Status

**PENDING — no complete normal NSE live session was captured.**

The bounded collector remains read-only and records spot, near-ATM CE/PE quotes, provider timestamps, receipt timestamps, freshness, contract metadata, duplicates, gaps and reconnection telemetry. Sampled spot-derived candles remain ineligible for breakout-dependent decisions until reconciled against authoritative completed candles.

Run during NSE hours:

```bash
.venv/bin/python scripts/run_phase4e_collector.py observe
```

Stop with `Ctrl-C`; then run:

```bash
.venv/bin/python scripts/run_phase4e_collector.py status
.venv/bin/python scripts/generate_phase4e2_reports.py
```

Paper fills, live orders and account mutations remain disabled.
