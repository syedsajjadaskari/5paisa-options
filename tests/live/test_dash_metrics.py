"""Unit tests for dashboard.metrics (SYNTHETIC numbers)."""

from __future__ import annotations

import math
from datetime import date

import pandas as pd
import pytest

from trading_platform.dashboard import metrics


def test_win_rate_and_profit_factor() -> None:
    pnl = [100.0, -50.0, 200.0, -50.0]
    assert metrics.win_rate(pnl) == pytest.approx(50.0)
    assert metrics.profit_factor(pnl) == pytest.approx(3.0)


def test_empty_and_degenerate_cases() -> None:
    assert metrics.win_rate([]) is None
    assert metrics.profit_factor([]) is None
    assert metrics.profit_factor([10.0, 5.0]) == math.inf
    assert metrics.profit_factor([-10.0]) == 0.0
    assert metrics.profit_factor([0.0, 0.0]) is None
    assert metrics.win_rate([0.0, 1.0]) == pytest.approx(50.0)


def test_max_drawdown_peak_to_trough() -> None:
    eq = pd.Series([100.0, 120.0, 90.0, 110.0, 80.0, 130.0])
    dd = metrics.max_drawdown(eq)
    assert dd.absolute == pytest.approx(40.0)
    assert dd.percent == pytest.approx(40.0 / 120.0 * 100.0)
    assert (dd.peak_index, dd.trough_index) == (1, 4)


def test_max_drawdown_monotonic_and_empty() -> None:
    assert metrics.max_drawdown(pd.Series([1.0, 2.0, 3.0])).absolute == 0.0
    assert metrics.max_drawdown(pd.Series([], dtype=float)).absolute == 0.0


def _trades() -> pd.DataFrame:
    t0 = pd.Timestamp("2026-10-26 09:50")
    return pd.DataFrame(
        {
            "trade_id": ["a", "b", "c"],
            "strategy": ["ORB", "PDS", "ORB"],
            "session_date": [date(2026, 10, 26), date(2026, 10, 26), date(2026, 10, 27)],
            "entry_time": [t0, t0 + pd.Timedelta(hours=1), t0 + pd.Timedelta(days=1)],
            "exit_time": [t0 + pd.Timedelta(minutes=20), t0 + pd.Timedelta(hours=1, minutes=5),
                          t0 + pd.Timedelta(days=1, minutes=9)],
            "net_pnl": [300.0, -100.0, 50.0],
            "total_charges": [40.0, 40.0, 40.0],
        }
    )


def test_equity_curve_starts_at_capital_and_accumulates() -> None:
    curve = metrics.equity_curve(_trades(), 1000.0)
    assert list(curve["equity"]) == [1000.0, 1300.0, 1200.0, 1250.0]
    assert curve.iloc[0]["trade_id"] == "START"


def test_strategy_and_daily_pnl() -> None:
    sp = metrics.strategy_pnl(_trades()).set_index("strategy")
    assert sp.loc["ORB", "pnl"] == 350.0 and sp.loc["ORB", "trades"] == 2
    assert sp.loc["PDS", "losses"] == 1
    dp = metrics.daily_pnl(_trades())
    assert list(dp["pnl"]) == [200.0, 50.0]
    assert list(dp["cumulative"]) == [200.0, 250.0]


def test_summarize_paper_uses_given_equity_else_derives() -> None:
    s = metrics.summarize_paper(_trades(), starting_capital=1000.0, unrealized=10.0)
    assert s["realized_pnl"] == 250.0
    assert s["current_equity"] == pytest.approx(1260.0)
    assert (s["wins"], s["losses"], s["total_trades"]) == (2, 1, 3)
    assert s["max_drawdown"] == pytest.approx(100.0)
    assert s["total_charges"] == 120.0
    s2 = metrics.summarize_paper(_trades(), starting_capital=1000.0, current_equity=5.0)
    assert s2["current_equity"] == 5.0


def test_summarize_paper_empty() -> None:
    s = metrics.summarize_paper(pd.DataFrame(columns=["net_pnl"]), starting_capital=1000.0)
    assert s["total_trades"] == 0 and s["win_rate"] is None and s["current_equity"] == 1000.0


def test_summarize_points_is_points_only() -> None:
    df = _trades().rename(columns={"net_pnl": "points_pnl"})
    s = metrics.summarize_points(df)
    assert s["total_points"] == 250.0
    assert "realized_pnl" not in s and "current_equity" not in s
    assert s["max_drawdown_points"] == pytest.approx(100.0)
