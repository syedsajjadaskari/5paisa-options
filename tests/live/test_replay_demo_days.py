"""find_demo_days: dates with accepted portfolio trades (uses the verified historical parquet
files READ-ONLY; nothing is written; strategies/backtest code are only called)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trading_platform.live.providers.replay import (
    DEFAULT_1D_PATH,
    DEFAULT_5M_PATH,
    DemoDay,
    DemoScan,
    ReplayProvider,
    find_demo_days,
    first_demo_day,
)

ROOT = Path(__file__).resolve().parents[2]
P5, P1 = ROOT / DEFAULT_5M_PATH, ROOT / DEFAULT_1D_PATH
pytestmark = pytest.mark.skipif(
    not (P5.exists() and P1.exists()), reason="verified historical NIFTY parquet not present"
)


@pytest.fixture(scope="module")
def scan() -> DemoScan:
    res = find_demo_days(P5, P1, return_scan=True)
    assert isinstance(res, DemoScan)
    return res


def test_returns_sorted_days_with_authorized_strategies_only(scan):
    days = scan.days
    assert days and all(isinstance(d, DemoDay) for d in days)
    assert [d.session_date for d in days] == sorted({d.session_date for d in days})
    names = {n for d in days for n in d.strategies}
    assert names <= {"PDS", "ORB", "IRF", "PDR"}  # RF never appears
    for d in days:
        assert d.trades == len(d.strategies) == len(d.exit_reasons) == len(d.directions) >= 1
        assert d.first_entry_time > d.first_signal_candle_open
        assert d.first_entry_time.date() == d.session_date
        assert d.raw_signals >= d.trades


def test_matches_the_official_portfolio_backtest(scan):
    """Same accepted-trade dates as run_portfolio_backtest over the full dataset."""
    from trading_platform.backtesting.portfolio_runner import (
        BacktestParams,
        load_nifty_bars,
        run_portfolio_backtest,
    )

    bars_1d, bars_5m = load_nifty_bars(ROOT)
    result = run_portfolio_backtest(
        bars_5m, bars_1d, BacktestParams(window_start=None, window_end=None)
    )
    official = {t.date for t in result.portfolio_trades}
    listed = {d.session_date for d in scan.days}
    assert listed <= official
    # everything dropped from the official set is explained (unreplayable date), never silent
    assert official - listed == set(scan.skipped)
    for d in scan.days:
        mine = [t for t in result.portfolio_trades if t.date == d.session_date]
        assert tuple(t.strategy_name for t in sorted(mine, key=lambda t: t.entry_time)) == (
            d.strategies
        )


def test_every_listed_day_is_actually_replayable(scan):
    first = scan.days[0]
    p = ReplayProvider(first.session_date, P5, P1, scrip_master_path=ROOT / "data/reference/scrip_master.parquet")
    assert all(c.ok for c in p.startup_checks(first.session_date))
    assert len(p.completed_candles(first.session_date, p.session_end)) == 75
    # unreplayable dates carry a reason and are not listed
    assert all(isinstance(v, str) and v for v in scan.skipped.values())
    assert not set(scan.skipped) & {d.session_date for d in scan.days}


def test_window_filter_and_first_demo_day(scan):
    start = scan.days[3].session_date
    end = scan.days[6].session_date
    sub = find_demo_days(P5, P1, start=start, end=end)
    assert [d.session_date for d in sub] == [d.session_date for d in scan.days[3:7]]
    assert first_demo_day(P5, P1, start=start) == scan.days[3]
    assert first_demo_day(P5, P1, start=date(2100, 1, 1)) is None


def test_no_signals_means_no_demo_days():
    from trading_platform.core.enums import StrategyStatus
    from trading_platform.strategies.base import BaseStrategy

    class Silent(BaseStrategy):
        def __init__(self):
            super().__init__("PDS", {}, StrategyStatus.RESEARCH)

        def prepare(self, data, config):
            return None

        def on_bar(self, context, bar):
            return None

        def generate_signal(self, context):
            return None

    assert find_demo_days(P5, P1, strategy_factory=lambda: {"PDS": Silent()}) == []
