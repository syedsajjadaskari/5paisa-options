# Paper Account Reconciliation

## Independent Decimal calculation

For 65 units, entry 101.00, exit 109.50, entry charges 26.53, and exit charges 37.23:

```text
entry premium       6,565.00
entry cash debit    6,591.53
exit gross credit   7,117.50
exit cash credit    7,080.27
gross P&L             552.50
net P&L               488.74
ending cash      250,488.74
```

The independent `ReferenceLedger` uses `Decimal` and asserts the flat identity `ending cash = starting cash + net P&L`. Its MTM method adds current market value to cash without treating that value as available cash.

## Runtime controls

- Entry deducts premium plus entry-leg charges.
- Open premium remains invested capital, not available cash.
- MTM is unrealized and does not increase available cash.
- Exit credits bid-based proceeds less exit-leg charges.
- Entry charges remain attached to the position and are included once in realized P&L.
- Broker and portfolio state must share namespace, generation, cash, and position identity.
- Pending exits remain pending when no valid executable bid exists.

## Legacy state result

The old broker snapshot and current portfolio snapshot cannot be reconciled because they are different namespaces/implementations. The new engine rejects that restart state. No automatic reset or deletion was performed.

## Evidence categories

- Category A: independent Decimal reference and deterministic synthetic invariants.
- Category B: historical/local data and persisted state inspection.
- Category C: genuine market-hours observation. **Not demonstrated for options.**

## Remaining accounting risk

The daily loss control is a gate, not a guaranteed maximum loss under gaps or missing quotes. DuckDB multi-table writes still need explicit transaction/migration hardening before a full paper-testing PASS.

