"""Independent Quantitative Robustness and Statistical Testing Suite.

Provides institutional verification tools:
- Bootstrap confidence intervals (Win Rate, Net Points, Profit Factor, Expectancy)
- Monte Carlo trade-order reshuffling (Permutation distribution of Drawdown)
- Slippage sensitivity and cost decay analysis
- Market regime performance segregation (Bullish, Bearish, Sideways)
- Multi-strategy correlation matrix
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from trading_platform.backtesting.engine import UnderlyingTradeRecord
from trading_platform.core.models import Bar


@dataclass(frozen=True)
class BootstrapConfidenceInterval:
    """Parametric or empirical confidence interval bounds."""

    metric_name: str
    point_estimate: float
    ci_lower: float
    ci_upper: float
    confidence_level: float = 0.95


@dataclass(frozen=True)
class MonteCarloDrawdownDistribution:
    """Empirical distribution of maximum drawdown across trade permutations."""

    actual_max_drawdown: float
    p50_drawdown: float
    p95_drawdown: float
    p99_drawdown: float
    worst_case_drawdown: float
    iterations: int


@dataclass(frozen=True)
class SlippageSensitivityStep:
    """Performance metrics under a specific slippage stress level."""

    slippage_per_side: float
    round_trip_slippage: float
    net_index_points: float
    profit_factor: float
    expectancy: float
    is_profitable: bool


class RobustnessTester:
    """Executes non-parametric statistical tests, sensitivity audits, and regime segregation."""

    @staticmethod
    def bootstrap_confidence_intervals(
        trades: list[UnderlyingTradeRecord],
        n_resamples: int = 1000,
        ci: float = 0.95,
        random_seed: int = 42,
    ) -> list[BootstrapConfidenceInterval]:
        """Compute empirical bootstrap confidence intervals via sampling with replacement."""
        if not trades:
            return []

        rng = np.random.default_rng(random_seed)
        pnl_array = np.array([t.points_pnl for t in trades])
        n_trades = len(pnl_array)

        boot_win_rates = []
        boot_net_points = []
        boot_profit_factors = []
        boot_expectancies = []

        alpha = (1.0 - ci) / 2.0
        q_lower = alpha * 100.0
        q_upper = (1.0 - alpha) * 100.0

        for _ in range(n_resamples):
            sample = rng.choice(pnl_array, size=n_trades, replace=True)
            wins = sample[sample > 0]
            losses = sample[sample <= 0]

            wr = (len(wins) / n_trades) * 100.0
            net_pts = float(np.sum(sample))
            gross_win = float(np.sum(wins)) if len(wins) > 0 else 0.0
            gross_loss = float(np.abs(np.sum(losses))) if len(losses) > 0 else 0.0
            pf = (gross_win / gross_loss) if gross_loss > 0 else 99.0
            exp = (
                ((wr / 100.0) * (gross_win / len(wins)))
                - (((100.0 - wr) / 100.0) * (gross_loss / len(losses)))
                if (len(wins) > 0 and len(losses) > 0)
                else 0.0
            )

            boot_win_rates.append(wr)
            boot_net_points.append(net_pts)
            boot_profit_factors.append(pf)
            boot_expectancies.append(exp)

        # Actual point estimates
        actual_wins = pnl_array[pnl_array > 0]
        actual_losses = pnl_array[pnl_array <= 0]
        actual_wr = (len(actual_wins) / n_trades) * 100.0
        actual_net = float(np.sum(pnl_array))
        actual_gw = float(np.sum(actual_wins)) if len(actual_wins) > 0 else 0.0
        actual_gl = float(np.abs(np.sum(actual_losses))) if len(actual_losses) > 0 else 0.0
        actual_pf = (actual_gw / actual_gl) if actual_gl > 0 else 99.0
        actual_exp = (
            ((actual_wr / 100.0) * (actual_gw / len(actual_wins)))
            - (((100.0 - actual_wr) / 100.0) * (actual_gl / len(actual_losses)))
            if (len(actual_wins) > 0 and len(actual_losses) > 0)
            else 0.0
        )

        return [
            BootstrapConfidenceInterval(
                metric_name="Win Rate (%)",
                point_estimate=round(actual_wr, 2),
                ci_lower=round(float(np.percentile(boot_win_rates, q_lower)), 2),
                ci_upper=round(float(np.percentile(boot_win_rates, q_upper)), 2),
                confidence_level=ci,
            ),
            BootstrapConfidenceInterval(
                metric_name="Net Index Points",
                point_estimate=round(actual_net, 2),
                ci_lower=round(float(np.percentile(boot_net_points, q_lower)), 2),
                ci_upper=round(float(np.percentile(boot_net_points, q_upper)), 2),
                confidence_level=ci,
            ),
            BootstrapConfidenceInterval(
                metric_name="Profit Factor",
                point_estimate=round(actual_pf, 2),
                ci_lower=round(float(np.percentile(boot_profit_factors, q_lower)), 2),
                ci_upper=round(float(np.percentile(boot_profit_factors, q_upper)), 2),
                confidence_level=ci,
            ),
            BootstrapConfidenceInterval(
                metric_name="Expectancy (Points)",
                point_estimate=round(actual_exp, 2),
                ci_lower=round(float(np.percentile(boot_expectancies, q_lower)), 2),
                ci_upper=round(float(np.percentile(boot_expectancies, q_upper)), 2),
                confidence_level=ci,
            ),
        ]

    @staticmethod
    def session_block_bootstrap(
        trades: list[UnderlyingTradeRecord],
        all_trading_dates: list[date] | None = None,
        block_size: int = 5,
        n_resamples: int = 1000,
        ci: float = 0.95,
        random_seed: int = 42,
    ) -> list[BootstrapConfidenceInterval]:
        """Dependence-aware contiguous block bootstrap across trading sessions.

        Preserves intra-day trade clustering, autocorrelation, and multi-day volatility
        regimes by resampling contiguous blocks of trading sessions.
        """
        if not trades:
            return []

        rng = np.random.default_rng(random_seed)
        trades_by_date: dict[date, list[UnderlyingTradeRecord]] = {}
        for t in trades:
            trades_by_date.setdefault(t.date, []).append(t)

        dates = sorted(all_trading_dates or list(trades_by_date.keys()))
        n_days = len(dates)
        if n_days < block_size:
            block_size = max(1, n_days)

        blocks = [dates[i : i + block_size] for i in range(0, n_days - block_size + 1)]
        if not blocks:
            blocks = [dates]
        n_blocks_needed = int(np.ceil(n_days / block_size))

        boot_win_rates = []
        boot_net_points = []
        boot_profit_factors = []
        boot_expectancies = []

        alpha = (1.0 - ci) / 2.0
        q_lower = alpha * 100.0
        q_upper = (1.0 - alpha) * 100.0

        for _ in range(n_resamples):
            sampled_block_indices = rng.choice(len(blocks), size=n_blocks_needed, replace=True)
            sampled_pnls: list[float] = []
            for b_idx in sampled_block_indices:
                for d in blocks[b_idx]:
                    if d in trades_by_date:
                        for t in trades_by_date[d]:
                            sampled_pnls.append(t.points_pnl)

            if not sampled_pnls:
                continue

            arr = np.array(sampled_pnls)
            wins = arr[arr > 0]
            losses = arr[arr <= 0]
            n_t = len(arr)

            wr = (len(wins) / n_t) * 100.0
            net = float(np.sum(arr))
            gw = float(np.sum(wins)) if len(wins) > 0 else 0.0
            gl = float(np.abs(np.sum(losses))) if len(losses) > 0 else 0.0
            pf = (gw / gl) if gl > 0 else 99.0
            exp = net / n_t

            boot_win_rates.append(wr)
            boot_net_points.append(net)
            boot_profit_factors.append(pf)
            boot_expectancies.append(exp)

        # Point estimates from original trade sequence
        actual_pnls = np.array([t.points_pnl for t in trades])
        actual_wins = actual_pnls[actual_pnls > 0]
        actual_losses = actual_pnls[actual_pnls <= 0]
        actual_wr = (len(actual_wins) / len(actual_pnls)) * 100.0
        actual_net = float(np.sum(actual_pnls))
        actual_gw = float(np.sum(actual_wins)) if len(actual_wins) > 0 else 0.0
        actual_gl = float(np.abs(np.sum(actual_losses))) if len(actual_losses) > 0 else 0.0
        actual_pf = (actual_gw / actual_gl) if actual_gl > 0 else 99.0
        actual_exp = actual_net / len(actual_pnls)

        return [
            BootstrapConfidenceInterval(
                metric_name="Win Rate (%)",
                point_estimate=round(actual_wr, 2),
                ci_lower=round(float(np.percentile(boot_win_rates, q_lower)), 2),
                ci_upper=round(float(np.percentile(boot_win_rates, q_upper)), 2),
                confidence_level=ci,
            ),
            BootstrapConfidenceInterval(
                metric_name="Net Index Points",
                point_estimate=round(actual_net, 2),
                ci_lower=round(float(np.percentile(boot_net_points, q_lower)), 2),
                ci_upper=round(float(np.percentile(boot_net_points, q_upper)), 2),
                confidence_level=ci,
            ),
            BootstrapConfidenceInterval(
                metric_name="Profit Factor",
                point_estimate=round(actual_pf, 2),
                ci_lower=round(float(np.percentile(boot_profit_factors, q_lower)), 2),
                ci_upper=round(float(np.percentile(boot_profit_factors, q_upper)), 2),
                confidence_level=ci,
            ),
            BootstrapConfidenceInterval(
                metric_name="Expectancy (Points)",
                point_estimate=round(actual_exp, 2),
                ci_lower=round(float(np.percentile(boot_expectancies, q_lower)), 2),
                ci_upper=round(float(np.percentile(boot_expectancies, q_upper)), 2),
                confidence_level=ci,
            ),
        ]

    @staticmethod
    def monte_carlo_drawdown_reshuffle(
        trades: list[UnderlyingTradeRecord],
        n_permutations: int = 1000,
        random_seed: int = 42,
    ) -> MonteCarloDrawdownDistribution:
        """Permute trade sequence without replacement to evaluate path-dependent drawdown risk."""
        if not trades:
            return MonteCarloDrawdownDistribution(0.0, 0.0, 0.0, 0.0, 0.0, 0)

        rng = np.random.default_rng(random_seed)
        pnl_array = np.array([t.points_pnl for t in trades])

        # Actual drawdown
        actual_cum = np.cumsum(pnl_array)
        actual_peak = np.maximum.accumulate(actual_cum)
        actual_max_dd = float(np.max(actual_peak - actual_cum))

        perm_drawdowns = []
        for _ in range(n_permutations):
            shuffled = rng.permutation(pnl_array)
            cum = np.cumsum(shuffled)
            peak = np.maximum.accumulate(cum)
            dd = np.max(peak - cum)
            perm_drawdowns.append(float(dd))

        return MonteCarloDrawdownDistribution(
            actual_max_drawdown=round(actual_max_dd, 2),
            p50_drawdown=round(float(np.percentile(perm_drawdowns, 50.0)), 2),
            p95_drawdown=round(float(np.percentile(perm_drawdowns, 95.0)), 2),
            p99_drawdown=round(float(np.percentile(perm_drawdowns, 99.0)), 2),
            worst_case_drawdown=round(float(np.max(perm_drawdowns)), 2),
            iterations=n_permutations,
        )

    @staticmethod
    def slippage_sensitivity_analysis(
        trades: list[UnderlyingTradeRecord],
        base_slippage: float = 0.50,
        steps: list[float] | None = None,
    ) -> list[SlippageSensitivityStep]:
        """Evaluate net PnL decay as execution slippage increases."""
        if not trades:
            return []

        step_levels = steps or [0.50, 0.75, 1.00, 1.50, 2.00, 2.50]
        results: list[SlippageSensitivityStep] = []

        for s in step_levels:
            # Recompute net points adjusting from base slippage
            # Each trade has 2 sides (entry + exit). Delta slippage = 2 * (s - base_slippage)
            delta_slip = 2.0 * (s - base_slippage)
            adjusted_pnls = [t.points_pnl - delta_slip for t in trades]

            net_pts = float(sum(adjusted_pnls))
            wins = [p for p in adjusted_pnls if p > 0]
            losses = [p for p in adjusted_pnls if p <= 0]
            gw = sum(wins) if wins else 0.0
            gl = abs(sum(losses)) if losses else 0.0
            pf = (gw / gl) if gl > 0 else (99.0 if gw > 0 else 0.0)
            wr = (len(wins) / len(adjusted_pnls)) * 100.0
            exp = (
                ((wr / 100.0) * (gw / len(wins))) - (((100.0 - wr) / 100.0) * (gl / len(losses)))
                if (wins and losses)
                else 0.0
            )

            results.append(
                SlippageSensitivityStep(
                    slippage_per_side=s,
                    round_trip_slippage=s * 2.0,
                    net_index_points=round(net_pts, 2),
                    profit_factor=round(pf, 2),
                    expectancy=round(exp, 2),
                    is_profitable=net_pts > 0,
                )
            )

        return results

    @staticmethod
    def market_regime_analysis(
        trades: list[UnderlyingTradeRecord],
        daily_bars: list[Bar],
    ) -> dict[str, dict[str, Any]]:
        """Segregate trade performance by market regime (Bullish, Bearish, Sideways/Chop)."""
        if not trades or not daily_bars:
            return {}

        # Build daily trend classification: 50 DMA vs 200 DMA
        daily_df = pd.DataFrame(
            [
                {
                    "date": b.exchange_time.date(),
                    "close": b.close,
                }
                for b in sorted(daily_bars, key=lambda x: x.exchange_time)
            ]
        )
        daily_df["dma_50"] = daily_df["close"].rolling(50).mean()
        daily_df["dma_200"] = daily_df["close"].rolling(200).mean()

        regime_map: dict[date, str] = {}
        for _, row in daily_df.iterrows():
            d = row["date"]
            c = row["close"]
            d50 = row["dma_50"]
            d200 = row["dma_200"]

            if pd.isna(d200) or pd.isna(d50):
                regime_map[d] = "UNCLASSIFIED_WARMUP"
            elif c > d50 and c > d200:
                regime_map[d] = "BULLISH_TREND"
            elif c < d50 and c < d200:
                regime_map[d] = "BEARISH_TREND"
            else:
                regime_map[d] = "SIDEWAYS_CHOP"

        regime_trades: dict[str, list[UnderlyingTradeRecord]] = {
            "BULLISH_TREND": [],
            "BEARISH_TREND": [],
            "SIDEWAYS_CHOP": [],
        }

        for t in trades:
            reg = regime_map.get(t.date, "SIDEWAYS_CHOP")
            if reg in regime_trades:
                regime_trades[reg].append(t)

        out = {}
        for reg_name, ts in regime_trades.items():
            wins = [t for t in ts if t.is_win]
            losses = [t for t in ts if not t.is_win]
            net_pts = sum(t.points_pnl for t in ts)
            wr = (len(wins) / len(ts) * 100.0) if ts else 0.0
            gw = sum(t.points_pnl for t in wins)
            gl = abs(sum(t.points_pnl for t in losses))
            pf = (gw / gl) if gl > 0 else (99.0 if gw > 0 else 0.0)

            out[reg_name] = {
                "trade_count": len(ts),
                "net_index_points": round(net_pts, 2),
                "win_rate_pct": round(wr, 2),
                "profit_factor": round(pf, 2),
            }

        return out

    @staticmethod
    def strategy_correlation_matrix(
        trades: list[UnderlyingTradeRecord],
    ) -> pd.DataFrame:
        """Compute pairwise daily PnL Pearson cross-correlation across strategy families."""
        if not trades:
            return pd.DataFrame()

        df = pd.DataFrame(
            [
                {
                    "date": t.date,
                    "strategy": t.strategy_name,
                    "points": t.points_pnl,
                }
                for t in trades
            ]
        )
        daily_pivot = df.pivot_table(
            index="date", columns="strategy", values="points", aggfunc="sum", fill_value=0.0
        )
        return daily_pivot.corr().round(2)
