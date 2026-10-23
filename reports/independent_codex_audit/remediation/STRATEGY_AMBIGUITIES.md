# Strategy Ambiguities and Required Confirmation

No parameter was changed to improve performance. The following items remain outside an exact machine-verifiable rule set.

| Strategy/method | Explicit source rule | Current interpretation | Confirmation required |
|---|---|---|---|
| IRF M3 | Red sweep wick approximately 60–70% outside and compulsory support break | Evidence-derived recent swing low; numeric wick test retained as an interpretation | Exact wick percentage, support lookback, and whether break is close-only or intrabar |
| ORB M1/M2 | Established trend, major/inner level, strong body/limited opposing wick | Prior-only levels plus EMA/body proxy | Definition of established trend, level lookback, body/wick thresholds |
| ORB M3 | Gap through 200-DMA special case | Prior-only DMA and mechanical gap proxy | Gap size, DMA touch/through tolerance, confirmation candle |
| PDR | Prior resistance body-to-upper-wick zone, gap/retest/green confirmation | Real prior intraday body zone; numeric proximity | Which prior candle defines zone, exact proximity, stop choice 35 vs 40 |
| PDS M1/M2 | Prior support zone, gap/retest, red/HPR confirmation | Real support zone and qualitative body/pin proxies | Zone source candle, proximity, “good red”/HPR thresholds, stop choice |
| PDS M3 | Support break only after weak first red candle | Weak-first-red prerequisite added | Exact weak candle definition and break confirmation |
| RF | Established uptrend, reclaim; exit is open in PDF | Canonical path disabled; 60-point form experimental only | Exact entry trigger and complete exit/target/stop policy |

RF remains excluded from executable research. PDR/PDS/ORB methods should remain disabled or explicitly marked interpretation-dependent until the strategy developer supplies deterministic confirmations.

