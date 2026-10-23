# Strategy Clarification Questions for Original Developer

These questions preserve the ambiguity register. No alternative below is an implementation decision.

| PDF reference | Ambiguous rule | Current implementation | Why clarification matters | Alternatives to confirm |
|---|---|---|---|---|
| `docs/strategy_rulebooks/ORB_Strategy_Rulebook-2.pdf` | “Established trend”, “major/inner level”, and strong-body/opposing-wick confirmation are qualitative. | Prior-only levels plus EMA/body and wick proxies. | Different lookbacks change signal count and entry timing. | Define trend lookback/indicator; level source and lookback; minimum body ratio; maximum opposing wick; close-only versus intrabar confirmation. |
| `docs/strategy_rulebooks/PDR_Strategy_Guide.pdf` | Prior resistance zone, gap/retest proximity, green confirmation, and stop distance are not uniquely numeric. | Prior intraday body zone with numeric proximity; stop interpretation is parameterized. | Zone width and confirmation timing change both eligibility and risk. | Identify source candle; define zone boundaries; define gap/retest tolerance; specify 35 versus 40 point stop and close-only versus intrabar break. |
| `docs/strategy_rulebooks/PDS strategy.pdf` | “Good red candle”, HPR confirmation, weak first red candle, and support-zone origin are subjective. | Support zone and body/pin proxies with a weak-first-red prerequisite. | Subjective confirmation can create materially different trades. | Define body-to-range and wick thresholds; HPR pattern; zone source; weak-candle threshold; break confirmation timing. |
| `docs/strategy_rulebooks/RF strategy.pdf` | Complete exit/target/stop policy is not specified. | RF is excluded from executable research; the 60-point form remains experimental. | Without an exit policy, P&L and risk cannot be audited. | Provide exact entry trigger, stop, target, time exit, invalidation and pending-exit behavior. |

Until written confirmation is received, affected methods remain interpretation-dependent and ineligible for executable paper signals. No strategy parameter was changed during Phase 4D.
