# Broker and Paper-Trading Safety Audit

## Verdict

Live order safety is **verified at the audited broker boundary**, but paper execution is **not approved** because valid live option data and the persisted account state are not established. The system is left read-only.

## Call-graph review

`trading_platform.brokers.fivepaisa.FivePaisaBroker` now exposes a `_ReadOnlyFivePaisaClientProxy` (`brokers/fivepaisa.py:40`) around the installed py5paisa client. The proxy blocks public transactional methods and `order_request`, including the SDK's generic transport route. Eleven transport-level tests attempt place/modify/cancel/order-request paths and expect `LiveTradingPermissionError`; no broker transaction was sent.

The installed SDK was inspected directly. Its `logging.py` prints response messages, and `py5paisa.py` logs `Message` values such as `Success`; this is an API response message, not proof of a fresh quote or an accepted order. Market-depth and market-feed requests use read-only request types (`MD`/`MF1`); transactional paths use `OP`, `OM`, and `OC`.

The public launcher previously selected the older `trading_platform.execution.paper_trading_engine` duplicate. It now routes to `trading_platform.paper.cli`; the old module remains as dormant compatibility debt and must not be used operationally.

## Paper controls verified in code/tests

- Starting capital: ₹250,000.
- Maximum two lots; current NIFTY lot lookup is 65 for the audited contract dates.
- One concurrent position.
- 20% premium allocation cap.
- Long CE for bullish signals and long PE for bearish signals.
- Entry at observed ask plus configured slippage; exit at observed bid less configured slippage.
- Five-second quote-age and two-percent spread gates.
- 14:30 entry cutoff; 15:15 virtual square-off target.
- Atomic broker/portfolio/engine state writes and generation checks.
- Duplicate process lock with exclusive creation and run-ID ownership.
- Missing or malformed provider timestamp rejects a quote; archive recorder skips it.
- Missing live strategy context no longer receives fabricated PDH/PDL/previous-close/DMA values.
- `PaperEngineConfig.paper_execution_enabled` defaults false. Missing or malformed YAML forces `LIVE_READ_ONLY`; production YAMLs are false.

## Accounting verification

The corrected ledger records entry and exit leg charges separately, rounds each leg independently, and carries entry charges in the active position. For qty 65, entry 101.00 and exit 109.50 in the April-2026 cost regime:

```text
buy-leg charges   ₹26.53
sell-leg charges  ₹37.23
total charges     ₹63.76
gross P&L         ₹552.50
net P&L           ₹488.74
```

The old path deducted entry charges at entry and again in the round-trip calculation. Recovery now asserts `starting capital + net P&L == portfolio equity == broker cash` within cents.

## Actual persisted-state evidence

The production files are not a clean account snapshot. The broker state reports current cash ₹102,429.65, while the portfolio state reports ₹250,000 and no position. The DuckDB contains one synthetic-looking ORB trade with logical entry 09:20 but order timestamps around 15:53, qty 65, entry 136.00, exit 174.50, net ₹2,429.65. A former session-validation report called this `PASSED`; the reporting path now returns evidence/status rather than hard-coded success and scopes queries by run ID.

The old paper process was flat and was stopped cleanly. The new generation checks reject the mixed state and disable paper execution. The state and historical archive are retained for forensic review; they are not valid execution evidence.

## Live-data observations

`doctor` completed a read-only authenticated request and returned an NIFTY spot response (approximately 22,713.20 at audit time). This verifies only response parsing/authentication. `observe --cycles 1` encountered an SSL handshake timeout and accepted no tick. No option quote was promoted to a fill. No order endpoint was called.

## Safety gaps still open

- The option archive has no independently valid provider-time history, so a genuine quote-to-fill-to-exit lifecycle is unverified.
- The daily loss limit is a risk gate, not a guaranteed ceiling under gaps, missing bids, pending exits, or asynchronous state recovery.
- A static calendar does not model every special session; the engine must not infer market-open eligibility from a weekday alone.
- The dormant duplicate engine has different defaults and remains a maintenance hazard even though the public launcher no longer selects it.
- SDK rate limits, reconnect behavior, authentication expiry, and full live-chain parsing lack a real market-session soak test.

## Safe commands

```bash
.venv/bin/python -m trading_platform.paper.cli doctor
.venv/bin/python -m trading_platform.paper.cli replay --bars 2
.venv/bin/python scripts/run_paper_trading.py start --confirm-paper-only --cycles 0
```

The first two are read-only/replay checks. The last command must refuse while the gate is false; it did so during this audit.

