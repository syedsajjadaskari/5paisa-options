# NIFTY index-point backtest - V3_PDF_CORRECTED

Run: `V3_PDF_CORRECTED_20261028T174123Z_523f2825`

> NIFTY INDEX-POINT backtest of the ORIGINAL strategy configuration, run WITHOUT any optimization or parameter tuning.  Points are index points only: no historical option prices exist, so no option-rupee P&L is computed and nothing here validates option profitability.  The 2025-09..2026-08 window overlaps the data used when strategies and methods were selected (e.g. IRF Method 2 over Method 1), so treat results as a research candidate, not an out-of-sample estimate.

## Execution model

- Entry at the NEXT bar open after the signal candle close, plus 0.5 pt adverse slippage; exit slippage of the same size on EVERY exit type.
- No entry at/after 14:30 (signal candles closing at/after the cutoff are dropped); square-off at 15:15; forced close if no square-off bar arrives.
- Same-bar stop/target ambiguity resolves to the stop (conservative).
- One joint chronological simulation, max 1 concurrent position, tie-break priority PDS > ORB > IRF > PDR.

## Evaluation window 2025-09-01 to 2026-08-31 (in-sample, see note above)

| strategy | trades | win rate | net pts | PF | max DD | avg duration (min) |
|---|---:|---:|---:|---:|---:|---:|
| PDS (alone) | 16 | 50.00% | 224.75 | 1.78 | 108.00 | 57.5 |
| ORB (alone) | 49 | 48.98% | 679.15 | 1.91 | 215.00 | 55.3 |
| IRF (alone) | 110 | 37.27% | -183.75 | 0.93 | 598.65 | 54.3 |
| PDR (alone) | 34 | 35.29% | 19.05 | 1.02 | 257.10 | 83.4 |
| PORTFOLIO | 184 | 41.30% | 813.60 | 1.22 | 554.00 | 58.0 |

## Whole simulated dataset (includes warm-up months)

| strategy | trades | win rate | net pts | PF | max DD | avg duration (min) |
|---|---:|---:|---:|---:|---:|---:|
| PDS (alone) | 16 | 50.00% | 224.75 | 1.78 | 108.00 | 57.5 |
| ORB (alone) | 53 | 52.83% | 917.15 | 2.23 | 215.00 | 52.2 |
| IRF (alone) | 115 | 38.26% | -81.25 | 0.97 | 598.65 | 54.6 |
| PDR (alone) | 35 | 34.29% | 4.35 | 1.01 | 257.10 | 90.1 |
| PORTFOLIO | 194 | 42.78% | 1139.40 | 1.30 | 554.00 | 58.5 |

## Portfolio detail (window)

- Raw engine signals: 218
- Exit mix (accepted): {"STOP_LOSS": 103, "TARGET": 72, "EOD_SQUAREOFF": 9}
- Rejected signals by reason: {"AFTER_ENTRY_CUTOFF": 9, "OVERLAP_POSITION_OPEN": 24, "LOST_TIE_BREAK": 1}
- Pooled standalone trades overlap in time; their drawdown is order-dependent and not an achievable single-account curve: {"trades": 209, "wins": 85, "losses": 124, "breakeven": 0, "win_rate_pct": 40.67, "net_points": 739.2, "gross_profit_points": 5065.05, "gross_loss_points": 4325.85, "profit_factor": 1.171, "max_drawdown_points": 625.0, "avg_points_per_trade": 3.54, "avg_duration_minutes": 59.5, "max_consecutive_losses": 13, "unit": "NIFTY_INDEX_POINTS"}

Max drawdown for the PORTFOLIO row uses only the accepted, sequenced trades.
