# Strategy Clarification Package

Please answer the following against the cited original PDFs. Alternatives are questions, not implementation choices.

## Historical candle examples

The following real historical candles are supplied as context; they are not labeled as signals:
- `2025-08-01 09:15:00` O=24736.40, H=24759.20, L=24701.10, C=24726.00
- `2025-08-01 09:20:00` O=24725.90, H=24732.60, L=24712.20, C=24712.70
- `2025-08-01 09:25:00` O=24712.60, H=24714.15, L=24679.70, C=24686.90

## Questions

1. ORB (`docs/strategy_rulebooks/ORB_Strategy_Rulebook-2.pdf`): define established trend, level lookback/source, strong-body ratio, opposing-wick limit, and close-only versus intrabar confirmation.
2. PDR (`docs/strategy_rulebooks/PDR_Strategy_Guide.pdf`): identify the resistance source candle, zone boundaries, retest tolerance, and whether stop is 35 or 40 points.
3. PDS (`docs/strategy_rulebooks/PDS strategy.pdf`): define good-red and HPR ratios, support source candle, retest tolerance, weak-first-red threshold and stop choice.
4. IRF (`docs/strategy_rulebooks/IRF_Strategy_Guide Updated.pdf`): define support lookback, exact 60–70% sweep-wick tolerance and close-only versus intrabar break.
5. RF (`docs/strategy_rulebooks/RF strategy.pdf`): provide complete canonical entry, stop, target, time exit and pending-exit policy.

Until answered, shadow rows are recorded as interpretation-dependent or unexecutable and cannot create paper signals.
