"""Unit tests for StrategyRegistry and BaseStrategy interface."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading_platform.core.enums import SignalDirection
from trading_platform.core.exceptions import StrategyRegistrationError
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext
from trading_platform.strategies.registry import StrategyRegistry

TZ_IST = ZoneInfo("Asia/Kolkata")


class SampleStrategy(BaseStrategy):
    """Test strategy implementation."""

    def prepare(self, data, config):
        self.original_parameters = {"target": 60, "stop_loss": 35}

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        bar = context.latest_bar
        if not bar:
            return None
        return Signal(
            strategy_name=self.name,
            method_id="SAMPLE_METHOD_1",
            symbol=bar.symbol,
            direction=SignalDirection.BUY,
            timestamp=bar.exchange_time,
            reference_price=bar.close,
            stop_loss=bar.close - 35.0,
            target=bar.close + 60.0,
            reason="Sample buy signal",
        )


def test_registry_register_and_retrieve():
    reg = StrategyRegistry()
    reg.register("SAMPLE", SampleStrategy)
    assert "SAMPLE" in reg.list_strategies()
    retrieved = reg.get("SAMPLE")
    assert retrieved is SampleStrategy


def test_registry_duplicate_registration_fails():
    reg = StrategyRegistry()
    reg.register("SAMPLE", SampleStrategy)
    with pytest.raises(StrategyRegistrationError):
        reg.register("SAMPLE", SampleStrategy)


def test_registry_unregistered_lookup_fails():
    reg = StrategyRegistry()
    with pytest.raises(StrategyRegistrationError):
        reg.get("UNKNOWN_STRATEGY")


def test_strategy_lifecycle_and_signal_generation():
    strat = SampleStrategy(name="SAMPLE")
    strat.prepare(None, {})
    assert strat.get_parameters()["original"]["target"] == 60

    ctx = StrategyContext()
    from trading_platform.core.enums import DataSource, Exchange, InstrumentType, Timeframe

    bar = Bar(
        timestamp=datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST),
        exchange_time=datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST),
        symbol="NIFTY 50",
        instrument_id="999920000",
        exchange=Exchange.NSE,
        instrument_type=InstrumentType.INDEX,
        interval=Timeframe.M5,
        open=24800.0,
        high=24830.0,
        low=24795.0,
        close=24825.0,
        volume=50000,
        source=DataSource.SYNTHETIC,
    )
    sig = strat.on_bar(ctx, bar)
    assert sig is not None
    assert sig.direction == SignalDirection.BUY
    assert sig.target == 24825.0 + 60.0
    assert sig.stop_loss == 24825.0 - 35.0
    assert strat.validate_signal(sig, ctx) is True
