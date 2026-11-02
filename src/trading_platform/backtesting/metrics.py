"""Points-based performance metrics for historical NIFTY index backtests.

Everything here is measured in NIFTY INDEX POINTS.  There are no verified historical option
premiums, so option-rupee P&L is deliberately never computed from history: a points result
says nothing about what a bought ATM option would have earned or lost.

Conventions:

* ``win`` = ``points_pnl > 0``; ``loss`` = ``points_pnl < 0``; exactly zero is ``breakeven``
  (not a win, not a loss).
* Profit factor = gross profit points / gross loss points; ``inf`` when there are no losing
  trades but some profit, ``0.0`` when there are no trades or no profit.
* Max drawdown is the largest peak-to-trough fall of the cumulative points curve, which
  starts at 0 (so an opening losing streak counts), computed over the trades in the order
  given after sorting by (entry_time, exit_time, trade_id).  For the combined portfolio pass
  ONLY the accepted (sequenced, non-overlapping) trades.
* Duration is ``exit_time - entry_time`` in minutes (bar-open stamps; a trade stopped on its
  own entry bar has duration 0).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

from trading_platform.backtesting.constants import PNL_UNIT_LABEL


class _TradeLike(Protocol):
    trade_id: str
    entry_time: datetime
    exit_time: datetime
    points_pnl: float


@dataclass(frozen=True)
class PointsMetrics:
    """Summary of a trade sequence in NIFTY index points."""

    trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate_pct: float
    net_points: float
    gross_profit_points: float
    gross_loss_points: float
    profit_factor: float
    max_drawdown_points: float
    avg_points_per_trade: float
    avg_duration_minutes: float
    max_consecutive_losses: int
    unit: str = PNL_UNIT_LABEL

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (an infinite profit factor becomes ``None``)."""
        data = asdict(self)
        if math.isinf(self.profit_factor):
            data["profit_factor"] = None
            data["profit_factor_note"] = "undefined (no losing trades)"
        return data


def _duration_minutes(trade: _TradeLike) -> float:
    return (trade.exit_time - trade.entry_time).total_seconds() / 60.0


def compute_points_metrics(trades: Iterable[_TradeLike]) -> PointsMetrics:
    """Compute points metrics for a trade sequence (see module docstring for conventions)."""
    ordered = sorted(trades, key=lambda t: (t.entry_time, t.exit_time, t.trade_id))
    n = len(ordered)
    if n == 0:
        return PointsMetrics(
            trades=0,
            wins=0,
            losses=0,
            breakeven=0,
            win_rate_pct=0.0,
            net_points=0.0,
            gross_profit_points=0.0,
            gross_loss_points=0.0,
            profit_factor=0.0,
            max_drawdown_points=0.0,
            avg_points_per_trade=0.0,
            avg_duration_minutes=0.0,
            max_consecutive_losses=0,
        )

    wins = losses = breakeven = 0
    gross_profit = gross_loss = 0.0
    cumulative = peak = max_dd = 0.0
    run = max_run = 0
    for t in ordered:
        pts = float(t.points_pnl)
        cumulative += pts
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
        if pts > 0:
            wins += 1
            gross_profit += pts
            run = 0
        elif pts < 0:
            losses += 1
            gross_loss += -pts
            run += 1
            max_run = max(max_run, run)
        else:
            breakeven += 1
            run = 0

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        profit_factor = math.inf if gross_profit > 0 else 0.0

    net = gross_profit - gross_loss
    return PointsMetrics(
        trades=n,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        win_rate_pct=round(wins / n * 100.0, 2),
        net_points=round(net, 2),
        gross_profit_points=round(gross_profit, 2),
        gross_loss_points=round(gross_loss, 2),
        profit_factor=profit_factor if math.isinf(profit_factor) else round(profit_factor, 3),
        max_drawdown_points=round(max_dd, 2),
        avg_points_per_trade=round(net / n, 2),
        avg_duration_minutes=round(sum(_duration_minutes(t) for t in ordered) / n, 1),
        max_consecutive_losses=max_run,
    )


def format_profit_factor(value: float) -> str:
    """Human-readable profit factor (``inf`` for no-loss sequences)."""
    return "inf" if math.isinf(value) else f"{value:.2f}"


def metrics_table_row(label: str, m: PointsMetrics) -> str:
    """One fixed-width table row: strategy | trades | win rate | net pts | PF | max DD | avg dur."""
    return (
        f"{label:<14} | {m.trades:>6} | {m.win_rate_pct:>7.2f}% | {m.net_points:>10.2f} | "
        f"{format_profit_factor(m.profit_factor):>6} | {m.max_drawdown_points:>8.2f} | "
        f"{m.avg_duration_minutes:>8.1f}"
    )


METRICS_TABLE_HEADER = (
    f"{'strategy':<14} | {'trades':>6} | {'win rate':>8} | {'net pts':>10} | "
    f"{'PF':>6} | {'max DD':>8} | {'avg min':>8}"
)
