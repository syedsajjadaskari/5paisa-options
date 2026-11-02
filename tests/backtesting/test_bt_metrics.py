"""Points-metrics tests (SYNTHETIC trade sequences)."""

from __future__ import annotations

import math
from datetime import timedelta

import pytest
from test_bt_support import at

from trading_platform.backtesting.engine import UnderlyingTradeRecord
from trading_platform.backtesting.metrics import (
    compute_points_metrics,
    format_profit_factor,
)
from trading_platform.core.enums import SignalDirection


def _t(i: int, minute: int, pnl: float, dur: int = 10):
    entry = at(9, 30) + timedelta(minutes=minute)
    return UnderlyingTradeRecord(
        trade_id=f"T{i}",
        date=entry.date(),
        strategy_name="X",
        direction=SignalDirection.BUY,
        method="M",
        entry_time=entry,
        entry_price=100.0,
        exit_time=entry + timedelta(minutes=dur),
        exit_price=100.0 + pnl,
        exit_reason="TARGET",
        points_pnl=pnl,
        is_win=pnl > 0,
        target=160.0,
        stop_loss=70.0,
    )


def test_known_sequence():
    trades = [_t(1, 0, 30), _t(2, 20, -20), _t(3, 40, -10), _t(4, 60, 50, dur=30), _t(5, 90, 0)]
    m = compute_points_metrics(trades)
    assert (m.trades, m.wins, m.losses, m.breakeven) == (5, 2, 2, 1)
    assert m.win_rate_pct == 40.0
    assert m.net_points == 50.0
    assert m.gross_profit_points == 80.0 and m.gross_loss_points == 30.0
    assert m.profit_factor == pytest.approx(80 / 30, abs=1e-3)
    # curve: 30, 10, 0, 50, 50 -> peak 30 then trough 0 => max DD 30
    assert m.max_drawdown_points == 30.0
    assert m.avg_duration_minutes == pytest.approx((10 + 10 + 10 + 30 + 10) / 5)
    assert m.max_consecutive_losses == 2
    assert m.unit == "NIFTY_INDEX_POINTS"


def test_drawdown_counts_an_opening_losing_streak_from_zero():
    m = compute_points_metrics([_t(1, 0, -25), _t(2, 20, -15), _t(3, 40, 10)])
    assert m.max_drawdown_points == 40.0


def test_order_is_by_entry_time_regardless_of_input_order():
    trades = [_t(2, 20, -20), _t(1, 0, 30)]
    assert compute_points_metrics(trades) == compute_points_metrics(list(reversed(trades)))


def test_empty_and_no_loss_cases():
    empty = compute_points_metrics([])
    assert empty.trades == 0 and empty.profit_factor == 0.0 and empty.max_drawdown_points == 0.0
    all_wins = compute_points_metrics([_t(1, 0, 10), _t(2, 20, 5)])
    assert math.isinf(all_wins.profit_factor)
    assert all_wins.to_dict()["profit_factor"] is None
    assert format_profit_factor(all_wins.profit_factor) == "inf"


def test_zero_duration_trade_counts_as_zero_minutes():
    m = compute_points_metrics([_t(1, 0, -30.5, dur=0)])
    assert m.avg_duration_minutes == 0.0
