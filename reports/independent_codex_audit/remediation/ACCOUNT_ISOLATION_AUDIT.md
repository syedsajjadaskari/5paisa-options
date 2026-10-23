# Account Isolation Audit

## Origin of the two balances

The forensic files establish different origins:

| Value | File/evidence | Meaning |
|---:|---|---|
| ₹102,429.65 | `data/cache/paper_broker_state.json`: `initial_balance=100000`, `current_cash=102429.65`, two fills, flat position | Legacy **virtual** broker result; not verified live cash |
| ₹250,000.00 | `data/cache/paper_portfolio_state.json`, `paper_engine_state.json`, production YAML | Current configured virtual starting capital |

The legacy balance is mathematically consistent with the stored synthetic ORB trade: ₹2,502.50 gross less ₹72.85 costs added to ₹100,000. The actual `FivePaisaBroker.get_margins()` path is read-only and now reports `available_cash=None` with namespace `BROKER_READ_ONLY_OBSERVATION`; it is not a paper funding source. No live account-balance endpoint was invoked during Phase 4C.

## Enforced namespaces

- `PaperBroker.ACCOUNT_NAMESPACE = PAPER_VIRTUAL`.
- `VirtualPortfolio.ACCOUNT_NAMESPACE = PAPER_VIRTUAL`.
- Engine state records the same namespace and refuses a mismatch.
- Paper broker and portfolio JSON loads refuse missing/wrong namespace metadata.
- DuckDB creates `account_metadata` with `account_namespace=PAPER_VIRTUAL`, `account_type=SIMULATED_OPTIONS_ACCOUNT`, and `actual_broker_cash_imported=false`.
- Verified options data is stored in a separate archive path from the legacy archive.

The old state is not silently reset. It remains preserved as forensic evidence and is rejected for restart recovery because it lacks the new namespace/generation agreement.

## Cross-contamination tests

`tests/unit/test_phase4c_remediation.py` verifies that a broker-namespace JSON cannot load into `PaperBroker`, that a virtual portfolio restarts with its own cash, and that the read-only broker namespace has no cash value. Existing restart tests verify generation, cash, and position agreement.

## Residual risk

The runtime portfolio continues to use binary floats with explicit two-decimal rounding. `paper/accounting.py` provides an independent Decimal reference calculation, but a future PASS should either migrate all runtime money arithmetic to Decimal or formally freeze and test the current rounding contract across every fee regime.

