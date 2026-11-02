"""Shared SYNTHETIC helpers for the test_bt_* backtest tests (no real market data).

Everything built here is labelled SYNTHETIC: bars use ``DataSource.SYNTHETIC`` and the
strategies are scripted test doubles, never the real PDS/ORB/IRF/PDR rules.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from trading_platform.core.enums import (
    DataSource,
    Exchange,
    InstrumentType,
    SignalDirection,
    Timeframe,
)
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")

DAY = date(2026, 1, 5)


def at(hh: int, mm: int, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ_IST)


def make_bar(when: datetime, o: float, h: float, low: float, c: float) -> Bar:
    """One SYNTHETIC 5-minute bar stamped with its OPEN time."""
    return Bar(
        timestamp=when.astimezone(TZ_UTC),
        exchange_time=when,
        symbol="NIFTY 50",
        instrument_id="999920000",
        exchange=Exchange.NSE,
        instrument_type=InstrumentType.INDEX,
        interval=Timeframe.M5,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=0,
        source=DataSource.SYNTHETIC,
    )


def flat_bars(start: datetime, count: int, price: float = 100.0) -> list[Bar]:
    """``count`` flat bars (o=h=l=c=price) every 5 minutes from ``start``."""
    return [
        make_bar(start + timedelta(minutes=5 * i), price, price, price, price)
        for i in range(count)
    ]


def replace_bar(bars: list[Bar], when: datetime, o: float, h: float, low: float, c: float) -> None:
    """Overwrite the bar opening at ``when`` in place."""
    for i, bar in enumerate(bars):
        if bar.exchange_time == when:
            bars[i] = make_bar(when, o, h, low, c)
            return
    raise AssertionError(f"no bar at {when}")


class ScriptedStrategy(BaseStrategy):
    """SYNTHETIC test double: emits a fixed-distance signal on scheduled candle-open times.

    ``schedule`` maps candle OPEN time -> direction.  The signal is produced on that candle's
    close, exactly like a real strategy.  It also counts every bar it is fed.
    """

    def __init__(
        self,
        name: str,
        schedule: dict[datetime, SignalDirection],
        target_points: float = 60.0,
        stop_points: float = 30.0,
        method: str | None = None,
    ) -> None:
        super().__init__(name)
        self.schedule = dict(schedule)
        self.target_points = target_points
        self.stop_points = stop_points
        self.method = method or f"{name}_SCRIPTED"
        self.bars_seen = 0

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        return None

    def reset_state(self) -> None:
        super().reset_state()
        self.bars_seen = 0

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        context.add_bar(bar)
        self.bars_seen += 1
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        bar = context.latest_bar
        assert bar is not None
        direction = self.schedule.get(bar.exchange_time)
        if direction is None:
            return None
        ref = bar.close
        if direction == SignalDirection.BUY:
            stop, target = ref - self.stop_points, ref + self.target_points
        else:
            stop, target = ref + self.stop_points, ref - self.target_points
        return Signal(
            strategy_name=self.name,
            method_id=self.method,
            symbol=bar.symbol,
            direction=direction,
            timestamp=bar.exchange_time,
            reference_price=ref,
            stop_loss=stop,
            target=target,
            reason="synthetic scripted signal",
            metadata={
                "method": self.method,
                "target_points": self.target_points,
                "stop_points": self.stop_points,
            },
        )


class BreakoutStrategy(BaseStrategy):
    """SYNTHETIC history-dependent strategy for look-ahead tests.

    Once per day, BUY when the bar's close exceeds the highest close of the previous three
    bars.  It reads only ``context.historical_bars`` (past bars + the bar just closed).
    """

    def __init__(self, name: str = "BRK") -> None:
        super().__init__(name)
        self._fired: set[date] = set()

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        return None

    def reset_state(self) -> None:
        super().reset_state()
        self._fired = set()

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        bars = [b for b in context.historical_bars if b.exchange_time.date() == DAY_OF(context)]
        if len(bars) < 4:
            return None
        latest = bars[-1]
        day = latest.exchange_time.date()
        if day in self._fired:
            return None
        if latest.close > max(b.close for b in bars[-4:-1]):
            self._fired.add(day)
            return Signal(
                strategy_name=self.name,
                method_id="BRK_SYNTH",
                symbol=latest.symbol,
                direction=SignalDirection.BUY,
                timestamp=latest.exchange_time,
                reference_price=latest.close,
                stop_loss=latest.close - 20.0,
                target=latest.close + 40.0,
                reason="synthetic breakout",
                metadata={"method": "BRK_SYNTH", "target_points": 40.0, "stop_points": 20.0},
            )
        return None


def DAY_OF(context: StrategyContext) -> date:  # noqa: N802 - small helper, kept next to its user
    latest = context.latest_bar
    assert latest is not None
    return latest.exchange_time.date()


def test_support_module_has_no_tests() -> None:
    """Placeholder so pytest does not warn about an empty module; helpers live above."""
    assert flat_bars(at(9, 15), 2)[1].exchange_time == at(9, 20)
