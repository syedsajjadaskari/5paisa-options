# Strategy Developer Clarification Questions

These questions use the original IRF, ORB, PDR, PDS and RF PDFs and the prior Codex audit. No answer has been inferred into the executable path.

## Priority questions

1. **PDS:** For the support-turned-resistance zone, which candle supplies the support body, what are the exact good-red/HPR thresholds, and what precise event arms Method 3's small-support fallback? These choices can materially change PDS counts.
2. **PDR:** Which prior-day candle defines the resistance body, what is the retest tolerance, and is the confirmation required to close above the zone body or only to be green inside the zone? These choices can materially change PDR counts.
3. **ORB:** Define established trend, the exact lookback/source for inner resistance/support, body and opposing-wick thresholds, and whether confirmation is close-only or intrabar. These choices can materially change ORB counts.

## Remaining questions

4. **IRF:** Confirm support/resistance lookback, the exact 60–70% sweep-wick calculation, and whether the break is evaluated on close only.
5. **RF:** Provide the canonical entry, stop, target, and forced-exit policy. The PDF's open target/exit language means RF must remain `RULEBOOK_AMBIGUITY`.
6. **All strategies:** Confirm whether a 5-minute bar timestamp denotes bar open, and whether a signal may be evaluated only after the next provider timestamp confirms that bar is closed.

## Genuine historical examples

The following are unlabelled candles from the verified historical file; they are examples for clarification, not claimed signals:

- `2025-08-01 09:15:00` O=24736.4 H=24759.2 L=24701.1 C=24726.0
- `2025-08-01 09:20:00` O=24725.9 H=24732.6 L=24712.2 C=24712.7
- `2025-08-01 09:25:00` O=24712.6 H=24714.15 L=24679.7 C=24686.9

Until answered, no shadow evaluation may create a signal, virtual trade, or P&L.
