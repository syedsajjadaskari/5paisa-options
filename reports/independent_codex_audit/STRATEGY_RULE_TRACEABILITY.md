# Strategy Rule Traceability

Source PDFs were rendered and read page-by-page: IRF (17 pages), ORB (13), PDR (4), PDS (7), and RF (9). The PDFs contain qualitative language and examples, not a complete machine-executable specification. Missing details are not inferred from the manual journal.

Classification: `EXACT_RULEBOOK`, `REASONABLE_INTERPRETATION`, `UNVERIFIED_ASSUMPTION`, `EXPERIMENTAL_VARIANT`, `INCORRECT_IMPLEMENTATION`, or `UNIMPLEMENTED_RULE`.

## Initial Range Failure (IRF)

| Rulebook rule | Code location | Audit classification | Evidence/limitation |
|---|---|---|---|
| Initial range is the first 5-minute candle | `strategies/irf.py`; `backtesting/engine.py` | EXACT_RULEBOOK | Evaluated on closed 5-minute bars. |
| M1: close above IR high, then next candle closes back inside | `strategies/irf.py` | EXACT_RULEBOOK | Sequence and direction match. |
| M2: extension/reversal followed by support failure, or resistance reversal | `strategies/irf.py` | REASONABLE_INTERPRETATION | Mechanical proxy is defensible; “support/resistance” construction is not fully specified in PDF. |
| M3: small inside candles, red sweep with roughly 60–70% wick outside, then compulsory support break | `strategies/irf.py:190`; `data/reference_levels.py:211-300` | REASONABLE_INTERPRETATION | Invented `IRH-10` support was removed; evidence-derived swing low is now required. Exact wick tolerance remains interpretation. |
| Stop 35/40, target 60 points | `strategies/irf.py` | EXACT_RULEBOOK | Fixed-distance mechanics retained. |

## Opening Range Breakout (ORB)

| Rulebook rule | Code location | Audit classification | Evidence/limitation |
|---|---|---|---|
| Decision uses first candle and a prior major/inner trendline level | `strategies/orb.py` | REASONABLE_INTERPRETATION | PDH/PDL and supplied inner levels are mechanical substitutes. |
| Established trend required; EMA alone is insufficient | `strategies/orb.py` | UNVERIFIED_ASSUMPTION | Missing EMA now blocks instead of allowing a first-candle-only signal, but EMA is still only a proxy for “established trend.” |
| Strong body with limited opposing wick | `strategies/orb.py` | REASONABLE_INTERPRETATION | Filter is now actually applied; numerical threshold is not stated in PDF. |
| Gap through 200-DMA special case | `strategies/orb.py`; `data/reference_levels.py` | REASONABLE_INTERPRETATION | Prior-only DMA is supplied. Exact “through” tolerance remains assumed. |
| Stop 30, target 60 | `strategies/orb.py` | EXACT_RULEBOOK | No parameter tuning performed. |

## Previous Day Resistance (PDR)

| Rulebook rule | Code location | Audit classification | Evidence/limitation |
|---|---|---|---|
| Prior resistance zone spans candle body to upper wick | `data/reference_levels.py:88-172`; `strategies/pdr.py:73-95` | REASONABLE_INTERPRETATION | Fabricated fixed ±15 zone removed; actual prior intraday body boundary required. Which prior candle defines the zone is partly discretionary. |
| Gap up and retest/very close to zone | `strategies/pdr.py` | UNVERIFIED_ASSUMPTION | “Very close” has no rulebook distance. Existing numeric tolerance is not canonical. |
| Green confirmation candle | `strategies/pdr.py` | EXACT_RULEBOOK | Direction is explicit. |
| Target 70, stop 35/40 | `strategies/pdr.py` | REASONABLE_INTERPRETATION | Distances are explicit; selecting 35 versus 40 is not. |

## Previous Day Support (PDS)

| Rulebook rule | Code location | Audit classification | Evidence/limitation |
|---|---|---|---|
| Prior support zone uses real prior candle body/wick | `data/reference_levels.py:88-172`; `strategies/pds.py:78-99` | REASONABLE_INTERPRETATION | Fabricated ±15 zone removed. Zone candle selection remains partially discretionary. |
| Gap down and retest from below | `strategies/pds.py` | UNVERIFIED_ASSUMPTION | Exact touch/proximity tolerance is not specified. |
| M1 good red candle | `strategies/pds.py` | REASONABLE_INTERPRETATION | Numeric body/wick definition is an implementation choice. |
| M2 HPR/red pin | `strategies/pds.py` | REASONABLE_INTERPRETATION | Pin-bar ratio is qualitative in source. |
| M3 support break only after weak first red candle | `strategies/pds.py` | REASONABLE_INTERPRETATION | Missing prerequisite was added; “weak” remains a numeric interpretation. |
| Target 70, stop 35/40 | `strategies/pds.py` | REASONABLE_INTERPRETATION | Stop-choice rule unresolved. |

## Reverse Failure (RF)

| Rulebook rule | Code location | Audit classification | Evidence/limitation |
|---|---|---|---|
| Established uptrend; gap up within prior range | `strategies/rf.py` | UNVERIFIED_ASSUMPTION | EMA proxy cannot prove the discretionary trend requirement. |
| First candle closes below a reference low, then reclaims | `strategies/rf.py` | REASONABLE_INTERPRETATION | Mechanical sequence implemented. |
| Exact entry | `strategies/rf.py` | UNIMPLEMENTED_RULE | PDF is not precise enough to establish one canonical trigger. |
| Exit/target | `strategies/rf.py:121` | UNIMPLEMENTED_RULE | PDF explicitly leaves exit unresolved. Canonical mode emits no trade. |
| 60-point target variant | `strategies/rf.py:121`; strategy test | EXPERIMENTAL_VARIANT | Renamed `RF_EXPERIMENTAL_60_POINT_RECLAIM_LONG`; must not be mixed into canonical metrics. |

## Cross-cutting timing/session rules

- Signal timestamps represent candle close (`core/models.py:114`).
- Entry must occur on a strictly later bar; same-time option bars are rejected (`backtesting/option_execution.py:75`).
- No new signal or pending fill is accepted at or after 14:30 (`backtesting/engine.py:79,162,242`; `paper/risk.py:115`).
- Open paper positions are scheduled for virtual square-off at 15:15, but an executable bid is still required; therefore the time is a control target, not a guaranteed fill.

## Conclusion

IRF/ORB are closest to mechanically auditable, PDR/PDS remain materially interpretation-dependent, and RF cannot be called canonical. The extra automated PDR/PDS signals cannot be proven equivalent to the discretionary PDF setups. Strategy-rule completeness is therefore a deployment blocker.

