# Backtest Reconciliation

## Scope and method

The audit did not accept generated summaries as evidence. It read the saved trade parquet, independently sorted trades by `(entry_time, strategy, method, trade_id)`, and applied one-position FIFO acceptance. It separately inspected fill timing, reference-level construction, costs, slippage, cutoff behavior, and strategy semantics. Results are NIFTY index points, not option premiums or rupee returns.

## Preserved `BASELINE_V1` ledger

Source SHA-256: `2083689b897e6039625fd6083c43edf4ac8ab7806c00c247b145520855997e14`.

### Unarbitrated saved ledger

| Strategy | Trades | Wins | Losses | Net points | Profit factor | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| IRF | 112 | — | — | -74.05 | 0.9691 | 408.00 |
| ORB | 61 | — | — | +558.10 | 1.5336 | 250.45 |
| PDR | 77 | — | — | +116.10 | 1.0699 | 335.15 |
| PDS | 74 | — | — | +1,259.80 | 1.9371 | 136.80 |
| RF | 29 | — | — | -166.30 | 0.7354 | 418.70 |
| **Total** | **353** | **149** | **204** | **+1,693.65** | **1.2393** | **666.55** |

Win rate is 42.21%; expectancy is +4.7979 points/trade.

### Independent FIFO reconstruction

Accepted 302, rejected 51; wins 128, losses 174; net +1,608.90; win rate 42.38%; expectancy +5.3275; profit factor 1.2686; max drawdown 635.25. The audit's accept/reject decisions have zero mismatches against the saved prior FIFO CSV.

This verifies the historical arithmetic and arbitration of the saved ledger. It does not verify that the 353 trades were generated without rule, data, or fill defects.

## Defects that invalidate current-code reproduction

1. Next-bar entries could retain target/stop distances anchored to the signal reference rather than actual fill (`backtesting/engine.py`). Example: signal reference 100, next fill 110.5, 60-point target: old target 160; corrected target 170.5.
2. Option execution required only non-earlier bars and could double-apply slippage (`backtesting/option_execution.py`, `indian_options_costs.py`). It now requires `bar_time > signal_time` and separates execution price from charges.
3. Backtest signals/pending fills could enter after the intended 14:30 cutoff. The old saved ledger contains 20 entries at or after 14:30, including two at 15:15.
4. Prior-day levels could be derived from inconsistent intraday 15:30 records. Official daily bars are now preferred and future dates are excluded.
5. ORB accepted missing trend context; PDR/PDS invented body-zone boundaries; IRF M3 invented support; RF's unresolved exit was presented as canonical.

## `CORRECTED_V2` mechanical candidate

This is a separately named audit result produced after correctness changes, with no parameter search.

| Strategy | Trades | W/L | Net points | Profit factor | Max drawdown | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| IRF | 104 | 39/65 | -110.30 | 0.9532 | 353.50 | 37.50% |
| ORB | 58 | 25/33 | +507.15 | 1.5108 | 245.00 | 43.10% |
| PDR | 75 | 33/42 | +471.45 | 1.3117 | 267.50 | 44.00% |
| PDS | 70 | 36/34 | +1,124.80 | 1.8639 | 127.50 | 51.43% |
| RF canonical | 0 | 0/0 | 0.00 | n/a | 0.00 | n/a |
| **Unarbitrated** | **307** | **133/174** | **+1,993.10** | **1.3233** | **433.65** | **43.32%** |
| **FIFO** | **271** | **120/151** | **+2,016.20** | **1.3812** | **568.50** | **44.28%** |

FIFO rejected 36 overlapping candidates. The higher net is incidental and must not be interpreted as optimization or evidence of future profitability. PDR/PDS qualitative definitions and ORB trend semantics remain unresolved; there is no historical option premium series behind these point outcomes.

## Manual journal reconciliation

Workbook SHA-256: `97a9ad4dfb5520619bb979770115d7b6f4e3e37e649af255c543f5fce9694781`.

- 190 unique numbered rows; no formulas.
- Outcomes: 81 Target, 65 SL, 44 BE.
- Labels: ORB 89, IRF 73, RF 17, PDR 8, PDS 3.
- Five Saturday records; no Sunday records.
- Against the preserved legacy ledger, independent chronological one-to-one same-strategy/date matching gives 91 within ten minutes: 3 exact, 80 nonzero up to five minutes, 8 from over five through ten minutes. Existing match decisions are identical.
- 25 candidates beyond ten minutes are ambiguous and must not be counted; 69 are manual-only; the five weekends are non-trading records.
- Against the corrected current auditor, the pinned result is 89 timing matches (3 exact, 79 up to five, 7 up to ten), with 23 beyond ten, 73 without an automated signal, and five non-trading days.

Timing proximity does not prove setup equivalence or exact fills.

## Optimization audit

Artifacts show 600 Optuna trials (4 strategies × 3 folds × 50) and 12 fold rows, so optimization was executed historically. The objective used seeded TPE and a net-points/sample/profit-factor construction. Saved expanding-fold totals are:

| Strategy | IS sum | OOS sum | Simple OOS/IS ratio |
|---|---:|---:|---:|
| IRF | 1,509.10 | 384.25 | 0.2546 |
| ORB | 1,066.90 | 292.35 | 0.2740 |
| PDR | 1,725.55 | -552.50 | -0.3202 |
| PDS | 1,837.55 | 959.35 | 0.5221 |

The reported ORB WFER 0.81–0.82 uses figures that do not reconcile to this saved fold ledger. Summing expanding in-sample folds double-counts observations and is not a valid aggregate WFER. All optimization artifacts are also invalidated by the engine corrections and hard-coded research graphics. No new search was run.

## Reproduction

```bash
cd /Users/syedaskari/project/5paisa-options
.venv/bin/pytest -q tests/unit/test_backtest_execution_integrity.py \
  tests/unit/test_reference_levels.py tests/unit/test_historical_reconciliation_audit.py
.venv/bin/python -m trading_platform.paper.cli replay --bars 2
```

