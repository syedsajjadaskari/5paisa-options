"""Walk-Forward Validation and Parameter Optimization Framework.

Implements institutional walk-forward time-series fold generation,
Optuna hyperparameter study architecture, objective evaluation,
and anti-overfitting sample-size penalties without lookahead bias.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import optuna

from trading_platform.backtesting.engine import UnderlyingBacktestEngine
from trading_platform.core.models import Bar
from trading_platform.strategies.base import BaseStrategy

# Configure Optuna logging level to WARNING to avoid verbose output during studies
optuna.logging.set_verbosity(optuna.logging.WARNING)


@dataclass(frozen=True)
class WalkForwardFold:
    """Date boundaries for an individual walk-forward train/test partition."""

    fold_index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    train_bars_count: int
    test_bars_count: int


@dataclass
class WalkForwardOptimizationResult:
    """Summary of walk-forward optimization study across folds."""

    strategy_name: str
    total_folds: int
    best_parameters_by_fold: list[dict[str, Any]]
    in_sample_metrics: list[dict[str, Any]]
    out_of_sample_metrics: list[dict[str, Any]]
    overall_out_of_sample_net_points: float
    walk_forward_efficiency_ratio: float  # OOS Annualized Return / IS Annualized Return
    summary_notes: str = ""


class WalkForwardSplitter:
    """Generates rolling or expanding walk-forward temporal splits."""

    @staticmethod
    def generate_expanding_folds(
        bars: list[Bar],
        train_start: date = date(2025, 9, 1),
        eval_end: date = date(2026, 8, 31),
        fold_step_months: int = 3,
        initial_train_months: int = 3,
    ) -> list[WalkForwardFold]:
        """Generate expanding walk-forward train and test intervals."""
        # 4 quarters across Sep 2025 - Aug 2026:
        # Q1: 2025-09-01 to 2025-11-30
        # Q2: 2025-12-01 to 2026-02-28
        # Q3: 2026-03-01 to 2026-05-31
        # Q4: 2026-06-01 to 2026-08-31
        quarter_bounds = [
            (date(2025, 9, 1), date(2025, 11, 30)),
            (date(2025, 12, 1), date(2026, 2, 28)),
            (date(2026, 3, 1), date(2026, 5, 31)),
            (date(2026, 6, 1), date(2026, 8, 31)),
        ]

        folds: list[WalkForwardFold] = []
        for i in range(1, len(quarter_bounds)):
            t_start = quarter_bounds[0][0]  # Expanding start
            t_end = quarter_bounds[i - 1][1]
            o_start = quarter_bounds[i][0]
            o_end = quarter_bounds[i][1]

            train_bars = [b for b in bars if t_start <= b.exchange_time.date() <= t_end]
            test_bars = [b for b in bars if o_start <= b.exchange_time.date() <= o_end]

            folds.append(
                WalkForwardFold(
                    fold_index=i,
                    train_start=t_start,
                    train_end=t_end,
                    test_start=o_start,
                    test_end=o_end,
                    train_bars_count=len(train_bars),
                    test_bars_count=len(test_bars),
                )
            )

        return folds


class WalkForwardOptimizer:
    """Executes parameter optimization on training folds and validates on out-of-sample folds."""

    def __init__(
        self,
        engine: UnderlyingBacktestEngine | None = None,
        min_trades_threshold: int = 8,
        slippage_points: float = 0.50,
    ) -> None:
        self.engine = engine or UnderlyingBacktestEngine(slippage_points=slippage_points)
        self.min_trades_threshold = min_trades_threshold

    def get_candidate_parameter_space(self, strategy_name: str) -> dict[str, Any]:
        """Define approved candidate parameter search spaces for experimental optimization."""
        spaces = {
            "ORB": {
                "min_body_ratio": {"type": "float", "low": 0.40, "high": 0.65, "step": 0.05},
                "max_opposing_wick_ratio": {
                    "type": "float",
                    "low": 0.20,
                    "high": 0.45,
                    "step": 0.05,
                },
                "stop_points": {"type": "float", "low": 25.0, "high": 40.0, "step": 5.0},
                "target_points": {"type": "float", "low": 50.0, "high": 80.0, "step": 10.0},
            },
            "IRF": {
                "min_extension_bars": {"type": "int", "low": 2, "high": 4},
                "default_stop_points": {"type": "float", "low": 30.0, "high": 45.0, "step": 5.0},
                "target_points": {"type": "float", "low": 50.0, "high": 70.0, "step": 10.0},
            },
            "PDS": {
                "min_body_ratio": {"type": "float", "low": 0.40, "high": 0.65, "step": 0.05},
                "zone_proximity_pts": {"type": "float", "low": 0.0, "high": 10.0, "step": 2.5},
                "default_stop_points": {"type": "float", "low": 25.0, "high": 45.0, "step": 5.0},
                "target_points": {"type": "float", "low": 50.0, "high": 80.0, "step": 10.0},
            },
            "PDR": {
                "zone_proximity_pts": {"type": "float", "low": 0.0, "high": 10.0, "step": 2.5},
                "default_stop_points": {"type": "float", "low": 30.0, "high": 45.0, "step": 5.0},
                "target_points": {"type": "float", "low": 60.0, "high": 80.0, "step": 10.0},
            },
        }
        return spaces.get(strategy_name.upper(), {})

    def evaluate_fold_study(
        self,
        strategy_cls: type[BaseStrategy],
        bars: list[Bar],
        fold: WalkForwardFold,
        daily_history: list[Bar],
        strategy_name: str | None = None,
        base_config: dict[str, Any] | None = None,
        n_trials: int = 50,
        random_seed: int = 42,
    ) -> dict[str, Any]:
        """Run an Optuna study on a single train fold and evaluate the best params out-of-sample."""
        train_bars = [
            b for b in bars if fold.train_start <= b.exchange_time.date() <= fold.train_end
        ]
        test_bars = [b for b in bars if fold.test_start <= b.exchange_time.date() <= fold.test_end]

        strat_name = strategy_name or getattr(strategy_cls, "name", strategy_cls.__name__).replace("Strategy", "").upper()
        space = self.get_candidate_parameter_space(strat_name)
        base_cfg = dict(base_config or {})

        trial_records: list[dict[str, Any]] = []

        def objective(trial: optuna.Trial) -> float:
            config = dict(base_cfg)
            for param_name, spec in space.items():
                if spec["type"] == "float":
                    config[param_name] = trial.suggest_float(
                        param_name, spec["low"], spec["high"], step=spec.get("step")
                    )
                elif spec["type"] == "int":
                    config[param_name] = trial.suggest_int(param_name, spec["low"], spec["high"])

            strat = strategy_cls(config)
            rep = self.engine.run(strat, train_bars, daily_history_bars=daily_history)

            eval_t = [t for t in rep.trades if fold.train_start <= t.date <= fold.train_end]
            if not eval_t:
                trial_records.append({
                    "trial_number": trial.number,
                    "params": trial.params,
                    "trades": 0,
                    "net_points": -999.0,
                    "score": -999.0,
                })
                return -999.0

            n_trades = len(eval_t)
            net_pts = sum(t.points_pnl for t in eval_t)
            wins = [t for t in eval_t if t.is_win]
            losses = [t for t in eval_t if not t.is_win]

            gw = sum(t.points_pnl for t in wins)
            gl = abs(sum(t.points_pnl for t in losses))
            pf = (gw / gl) if gl > 0 else 2.0

            # Penalize trials with insufficient observations
            sample_penalty = min(1.0, n_trades / self.min_trades_threshold)

            # Objective: Net Points * Sample Penalty * sqrt(Profit Factor)
            score = float((net_pts * sample_penalty) * (pf**0.5))
            trial_records.append({
                "trial_number": trial.number,
                "params": trial.params,
                "trades": n_trades,
                "net_points": round(net_pts, 2),
                "profit_factor": round(pf, 2),
                "score": round(score, 2),
            })
            return score

        sampler = optuna.samplers.TPESampler(seed=random_seed)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        study.optimize(objective, n_trials=n_trials)

        best_params = study.best_params
        merged_best_cfg = {**base_cfg, **best_params}

        # Evaluate best params In-Sample (Train)
        best_strat_is = strategy_cls(merged_best_cfg)
        rep_is = self.engine.run(best_strat_is, train_bars, daily_history_bars=daily_history)
        is_trades = [t for t in rep_is.trades if fold.train_start <= t.date <= fold.train_end]
        is_net = sum(t.points_pnl for t in is_trades)
        is_wr = (
            (sum(1 for t in is_trades if t.is_win) / len(is_trades) * 100.0) if is_trades else 0.0
        )

        # Evaluate best params Out-Of-Sample (Test)
        best_strat_oos = strategy_cls(merged_best_cfg)
        rep_oos = self.engine.run(best_strat_oos, test_bars, daily_history_bars=daily_history)
        oos_trades = [t for t in rep_oos.trades if fold.test_start <= t.date <= fold.test_end]
        oos_net = sum(t.points_pnl for t in oos_trades)
        oos_wr = (
            (sum(1 for t in oos_trades if t.is_win) / len(oos_trades) * 100.0)
            if oos_trades
            else 0.0
        )

        return {
            "strategy_name": strat_name,
            "fold_index": fold.fold_index,
            "train_window": f"{fold.train_start} to {fold.train_end}",
            "test_window": f"{fold.test_start} to {fold.test_end}",
            "best_params": best_params,
            "merged_best_cfg": merged_best_cfg,
            "in_sample_trades": len(is_trades),
            "in_sample_net_points": round(is_net, 2),
            "in_sample_win_rate_pct": round(is_wr, 2),
            "out_of_sample_trades": len(oos_trades),
            "out_of_sample_net_points": round(oos_net, 2),
            "out_of_sample_win_rate_pct": round(oos_wr, 2),
            "is_trades_records": is_trades,
            "oos_trades_records": oos_trades,
            "trial_records": trial_records,
        }
