"""Independent regression tests for backtest timing and fixed-distance exits."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading_platform.backtesting.engine import UnderlyingBacktestEngine
from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


def _bar(at: datetime, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        timestamp=at.astimezone(TZ_UTC),
        exchange_time=at,
        symbol="NIFTY 50",
        instrument_id="999920000",
        open=open_,
        high=high,
        low=low,
        close=close,
    )


class _OneShotLongStrategy(BaseStrategy):
    def __init__(self) -> None:
        super().__init__("TEST")
        self.emitted = False

    def prepare(self, data, config):
        return None

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        if self.emitted:
            return None
        self.emitted = True
        bar = context.latest_bar
        assert bar is not None
        return Signal(
            strategy_name="TEST",
            method_id="FIXED_DISTANCE",
            symbol=bar.symbol,
            direction=SignalDirection.BUY,
            timestamp=bar.exchange_time,
            reference_price=bar.close,
            stop_loss=bar.close - 30.0,
            target=bar.close + 60.0,
            reason="fixed distance",
            metadata={"stop_points": 30.0, "target_points": 60.0},
        )


def test_fixed_target_and_stop_are_rebased_to_actual_next_bar_fill():
    """A gap at entry must not silently shorten the rulebook's fixed target distance."""
    start = datetime(2026, 1, 5, 9, 15, tzinfo=TZ_IST)
    bars = [
        _bar(start, 95.0, 101.0, 94.0, 100.0),
        # Next-bar fill is 110.5 after slippage. The old 160 target is touched,
        # but the correct target is 170.5 measured from the actual fill.
        _bar(start + timedelta(minutes=5), 110.0, 165.0, 109.0, 160.0),
        _bar(start + timedelta(minutes=10), 160.0, 171.0, 159.0, 170.0),
    ]

    report = UnderlyingBacktestEngine(slippage_points=0.5).run(
        _OneShotLongStrategy(),
        bars,
    )

    assert report.total_trades == 1
    trade = report.trades[0]
    assert trade.entry_price == 110.5
    assert trade.target == 170.5
    assert trade.stop_loss == 80.5
    assert trade.exit_time == start + timedelta(minutes=10)
    assert trade.points_pnl == pytest.approx(60.0)


def test_pending_signal_cannot_enter_at_or_after_1430_cutoff():
    """A 14:25 close signal must not become a new position at the 14:30 open."""
    start = datetime(2026, 1, 5, 14, 25, tzinfo=TZ_IST)
    bars = [
        _bar(start, 95.0, 101.0, 94.0, 100.0),
        _bar(start + timedelta(minutes=5), 100.0, 180.0, 90.0, 170.0),
        _bar(start + timedelta(minutes=10), 170.0, 180.0, 160.0, 175.0),
    ]

    report = UnderlyingBacktestEngine().run(_OneShotLongStrategy(), bars)

    assert report.total_trades == 0
