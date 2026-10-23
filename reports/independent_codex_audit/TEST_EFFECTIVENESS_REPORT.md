# Test Effectiveness Report

## Final test result

Using the repository `.venv`:

```text
182 passed
compileall: PASS
ruff check .: PASS
git diff --check: PASS
```

The earlier project claim of 136 tests was not used as evidence. The current count includes the additional audit/regression tests and a corrected historical expectation for the corrected engine (89 timing matches; ORB PDH/PDL metric 45).

## Test classes

| Class | Evidence | Confidence |
|---|---|---|
| Pure unit | timestamp parser, calendar, cost formulas, reference-level calculations, strategy predicates | Good for stated examples; not proof of rulebook completeness |
| Mock-only broker/feed | mocked py5paisa responses, Level2 quotes, stale/fresh ticks | Good for negative paths; no exchange authenticity |
| Synthetic lifecycle | paper entry, exit, EOD, pending exit, restart, lock, accounting | Good for deterministic invariants; fixtures are not market data |
| Historical-data integration | 1m/5m/daily/manual reconciliation pipeline | Reproducible against local files; source files are not independent holdout data |
| Real API read-only | `doctor` response and source inspection | Partial: one response, no live-session soak, no option-fill E2E |
| Real order integration | place/modify/cancel | Intentionally not run; interface blocked |
| Optimization validation | artifact inspection | No valid independent WFER confirmation; no new search run |
| Dashboard/CLI E2E | status/replay/start refusal and process observation | Partial; report/stop were not run against corrupt production state |

## Independent/adversarial tests added or strengthened

- `/Date(1729571156000+0530)/` conversion to 2024, not displayed 2026.
- Missing/malformed provider timestamp rejection.
- Freshness boundaries at 4.9/5.0/5.1 seconds.
- Missing and malformed paper YAML fail closed.
- Mixed state-generation restart rejection.
- Duplicate process lock and same-process lock rejection.
- Transactional broker endpoint blocking, including generic `order_request`.
- Entry-charge/exit-charge reconciliation and broker cash equality.
- Next-bar fill and fixed-distance rebasing regression.
- No option-bar fill at the signal timestamp; no double slippage.
- 14:30 cutoff for both generated and pending entries.
- Official daily levels override intraday proxies and do not leak future data.
- Corrected 2026 holiday dates and removed incorrect dates.
- Research chart generator refuses hard-coded overlays.
- IRF M3 requires an evidence-derived swing low; PDR/PDS require actual prior body levels; PDS fallback requires a weak first red candle; RF canonical path remains disabled.

## Weaknesses

The tests still do not establish: broker rate limits/reconnect, authentication expiry, a fresh option-chain archive, real market-clock behavior on special sessions, exchange-supported order semantics, crash-window durability under power loss, DuckDB migration compatibility, or a complete rulebook adjudication against independent human annotations. Several expectations are deterministic values copied from the fixed local dataset; they are regression checks, not independent market truth.

## Reproduction commands

```bash
cd /Users/syedaskari/project/5paisa-options
.venv/bin/python -m compileall -q src tests scripts
.venv/bin/pytest -q
.venv/bin/ruff check .
git diff --check
```

