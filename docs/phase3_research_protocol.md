# PHASE 3 — QUANTITATIVE RESEARCH & AUDIT PROTOCOL

**Document Title**: Quantitative Strategy Research, Optimization Safeguards & Statistical Testing Protocol  
**Audit Scope**: Algorithmic Options Trading Pipeline (`5paisa-options`)  
**Standard**: Institutional Quantitative Research Standards (No Overfitting, Anti-Snooping, Out-of-Sample Discipline)  
**Status**: APPROVED RESEARCH GOVERNANCE  

---

## 1. Research Governance & Anti-Overfitting Safeguards

### 1.1 The Cardinal Rule of Data Partitioning

In quantitative finance, optimizing parameters on a historical dataset and then reporting the backtest performance on that same dataset as "expected returns" is fraudulent and guarantees live execution failure.

The 12-month historical window (**September 1, 2025 – August 31, 2026**) in this repository has already been thoroughly examined during Phase 2 to verify canonical rulebook alignment and reconcile manual trading journals. Therefore:
1. **No Retrospective Claim of Pure Out-of-Sample Status**:
   - The Sep 2025 – Aug 2026 dataset CANNOT be presented as an untouched, pristine out-of-sample test.
   - It is formally designated as **Historical In-Sample Development & Walk-Forward Partitioning Data**.
2. **Separation of Partitions**:
   - **Partition A: Rule Validation Data**: Sub-periods used strictly to check if coded state machines match PDF logic.
   - **Partition B: In-Sample Development Data (Train Folds)**: Folds used by optimization engines to propose parameter candidates.
   - **Partition C: Walk-Forward Validation Data (Test Folds)**: Temporally sequential forward segments evaluated out-of-fold.
   - **Partition D: Untouched Prospective Test Data**: Genuinely unseen live/paper market data collected forward in time from current date (`October 2026+`).

---

## 2. Strategy Ambiguity Resolution & Experimental Variants

### 2.1 Range Failure (RF) Policy

- **Current Status**: `ENTRY_VALIDATION_ONLY`.
- **Reasoning**:
  - The canonical PDF rulebook specifies entry conditions (level pierce and reclaim within 2 bars) but explicitly leaves the exit rule as "OPEN / Discretionary".
  - The 60-point target tested in baseline research is an arbitrary heuristic borrowed from ORB/PDS.
  - In baseline testing, canonical RF produced 29 trades, -166.30 net points, and a 0.74 profit factor.
- **Mandate**: RF MUST NOT enter automated parameter optimization until an authenticated, exchange-verified exit rulebook is formally established.

### 2.2 Trailing-Stop Mechanics: Empirical Findings

A common retail trader intuition is to "move stop-loss to break-even" once a trade moves into profit to eliminate risk. An experimental study was performed on all 353 baseline trades comparing:
1. `CANONICAL_FIXED_TP_SL`: Zero trailing stop (Target 60 pts, SL 30-35 pts).
2. `MOVE_TO_BE_AT_30`: Move SL to entry price once favorable excursion reaches +30 points.
3. `MOVE_TO_BE_AT_40`: Move SL to entry price once favorable excursion reaches +40 points.

#### Empirical Trailing Stop Comparison Table

| Trailing Policy | Total Trades | Break-Even Exits Triggered | Win Rate (%) | Gross Points | Slippage Incurred | Net Index Points |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **CANONICAL_FIXED_TP_SL** | **353** | **0** | **42.21%** | **+2,046.65** | **-353.00** | **+1,693.65** |
| `MOVE_TO_BE_AT_30` | 353 | 112 | 22.95% | -157.30 | -353.00 | **-510.30** |
| `MOVE_TO_BE_AT_40` | 353 | 63 | 30.03% | +447.40 | -353.00 | **+94.40** |

#### Critical Mathematical Takeaways

1. **Alpha Destruction through Premature Choking**:
   - Intraday NIFTY 50 oscillates with high intraday variance ($\pm 25$ to $35$ points of standard noise).
   - Moving the stop to break-even at +30 points prematurely stopped out **112 trades** that subsequently reversed to hit full +60 point targets. This converted a **+1,693.65 point edge into a -510.30 point net loss**.
2. **The Break-Even Illusion**:
   - Moving an underlying stop order to entry price **DOES NOT GUARANTEE FINANCIAL BREAK-EVEN**.
   - With exchange fees, broker commissions, and 1.00 point round-trip slippage, an exit at "entry price" yields a guaranteed net loss of **-1.00 index points** (-₹75 per lot) per trade.
3. **Policy Determination**:
   - Canonical fixed TP/SL remains the primary production standard.
   - Any trailing stop variant tested in Phase 3B must be segregated and clearly labeled as an experimental modified strategy.

---

## 3. Formal Parameter Optimization Safeguards (Optuna Framework)

### 3.1 Permitted Parameter Spaces

Optimization trials in Phase 3B must be bounded within economically justifiable ranges:

| Strategy | Parameter Name | Type | Canonical Baseline | Permitted Search Range | Step | Economic Rationale |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **ORB** | `min_body_ratio` | float | 0.50 | `[0.40, 0.65]` | 0.05 | Filters indecision dojis |
| **ORB** | `max_opposing_wick_ratio` | float | 0.35 | `[0.20, 0.45]` | 0.05 | Prevents rejection wicks |
| **ORB** | `stop_points` | float | 30.0 | `[25.0, 40.0]` | 5.0 | Adapts to opening volatility |
| **PDS** | `min_body_ratio` | float | 0.60 | `[0.50, 0.75]` | 0.05 | Validates institutional momentum |
| **PDS** | `stop_points` | float | 35.0 | `[25.0, 45.0]` | 5.0 | Protects against false pierce |
| **PDR** | `zone_buffer_points` | float | 10.0 | `[5.0, 15.0]` | 2.5 | Tightens level proximity |
| **IRF** | `extension_bars` | int | 3 | `[2, 5]` | 1 | Method 2 exhaustion depth |

### 3.2 Anti-Overfitting Penalty Function

To prevent Optuna's Tree-structured Parzen Estimator (TPE) sampler from converging on parameters that produce 2 abnormally large wins over a tiny sample size, the objective function enforces an explicit sample-size penalty:

$$\text{Objective}(\theta) = \left( \sum_{i=1}^{N} \text{PointsPnL}_i \right) \times \min\left(1.0, \frac{N}{N_{\text{threshold}}}\right)$$

- Where $N_{\text{threshold}} = 15$ trades per quarterly fold.
- Trials with $N < 15$ trades are penalized proportionally.
- Trials with zero trades return a score of $-9999.0$.

### 3.3 Walk-Forward Efficiency Ratio (WFER)

A strategy configuration is considered robust if and only if:

$$\text{WFER} = \frac{\text{Annualized Out-of-Sample Return}}{\text{Annualized In-Sample Return}} \ge 0.50$$

A WFER below 0.50 indicates that over 50% of the in-sample performance was curve-fitted noise.

---

## 4. Independent Quantitative Robustness Test Battery

Before any strategy can be approved for prospective paper trading, it must pass five independent statistical tests:

### 4.1 Bootstrap Confidence Intervals (1,000 Resamples)
- Resample historical trades with replacement $B = 1000$ times.
- Derive 95% empirical confidence intervals for:
  - Win Rate: $[W_{\text{lower}}, W_{\text{upper}}]$
  - Net Points: $[P_{\text{lower}}, P_{\text{upper}}]$
  - Profit Factor: $[PF_{\text{lower}}, PF_{\text{upper}}]$
  - Expectancy: $[E_{\text{lower}}, E_{\text{upper}}]$
- **Pass Criterion**: Lower bound of 95% CI for Profit Factor must satisfy $PF_{\text{lower}} \ge 1.00$.

### 4.2 Monte Carlo Drawdown Reshuffling (1,000 Permutations)
- Permute the chronological sequence of executed trades without replacement 1,000 times.
- Compute maximum peak-to-valley drawdown across all 1,000 synthetic paths.
- **Reporting**: Report Median (P50), 95th Percentile (P95), 99th Percentile (P99), and Worst-Case Drawdown.
- **Distinction**: Clarify that trade reshuffling isolates sequence risk for a given set of realized trades; it does not model non-stationary future market regimes.

### 4.3 Slippage Decay Curve
- Re-evaluate performance under progressive slippage burdens:
  $$\text{Slippage per side} \in [0.50, 0.75, 1.00, 1.50, 2.00, 2.50]\text{ points}$$
- Determine the **Break-Even Slippage Threshold** (the slippage level where net expectancy decays to zero).

### 4.4 Market Regime Segregation
- Classify market conditions using Daily 50-period and 200-period Simple Moving Averages:
  - **Bullish Trend**: Price > 50 DMA and 50 DMA > 200 DMA.
  - **Bearish Trend**: Price < 50 DMA and 50 DMA < 200 DMA.
  - **Sideways / Chop**: Non-trending or converging DMAs.
- Evaluate strategy expectancy across each regime. A viable multi-strategy portfolio must maintain positive expectancy in at least two of the three regimes.

### 4.5 Strategy Correlation Matrix
- Compute Pearson correlation of daily strategy PnL series:
  $$\rho(S_i, S_j) = \frac{\text{Cov}(R_i, R_j)}{\sigma_i \sigma_j}$$
- Low or negative correlation ($\rho < 0.20$) demonstrates authentic diversification.

---

## 5. Options Execution Boundary: Status & Guardrails

1. **Physical Derivative Reality**:
   - The end execution vehicle is NIFTY Weekly European Call/Put Options (`NIFTY CE/PE`) traded on NSE via 5paisa.
   - Under no circumstances may index points be directly converted to options points by multiplying by a fixed delta (e.g. 0.50), because options suffer from nonlinear gamma expansion, vega crush, and theta decay.
2. **Options Backtesting Status**:
   - **BLOCKED**: Expired intraday options 1-minute/5-minute candle data is currently unavailable from 5paisa historical endpoints.
   - Synthetic Black-Scholes substitution is strictly forbidden for historical validation because it cannot capture real-world bid-ask spreads, order book depth, and implied volatility skews during high-momentum breakouts.
3. **Prospective Paper Trading Preparation**:
   - The platform maintains an options contract selector (`OptionsContractSelector`), strike selector, and cost engine.
   - When prospective paper trading begins, options prices will be recorded directly from the live websocket feed.
   - **Live and Paper order submission remains disabled.**
