"""Unit tests for Underlying-based, Premium-based, and Hybrid option exit evaluators."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from trading_platform.core.enums import (
    DataSource,
    Exchange,
    InstrumentType,
    SignalDirection,
    Timeframe,
)
from trading_platform.core.models import Bar
from trading_platform.data.option_chain import OptionBar
from trading_platform.options.premium_exits import ExitEvaluator, ExitMode

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


def make_test_bars(
    t_hour: int,
    t_min: int,
    spot_o: float,
    spot_h: float,
    spot_l: float,
    spot_c: float,
    opt_o: float,
    opt_h: float,
    opt_l: float,
    opt_c: float,
) -> tuple[Bar, OptionBar]:
    dt = datetime(2024, 10, 18, t_hour, t_min, tzinfo=TZ_IST)
    u_bar = Bar(
        timestamp=dt.astimezone(TZ_UTC),
        exchange_time=dt,
        symbol="NIFTY 50",
        instrument_id="999920000",
        exchange=Exchange.NSE,
        instrument_type=InstrumentType.INDEX,
        interval=Timeframe.M5,
        open=spot_o,
        high=spot_h,
        low=spot_l,
        close=spot_c,
        volume=10000,
        source=DataSource.SYNTHETIC,
    )
    o_bar = OptionBar(
        timestamp=dt.astimezone(TZ_UTC),
        exchange_time=dt,
        trading_date=dt.date(),
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
        interval=Timeframe.M5,
        open=opt_o,
        high=opt_h,
        low=opt_l,
        close=opt_c,
        volume=2500,
    )
    return u_bar, o_bar


def test_underlying_based_target_exit():
    evaluator = ExitEvaluator(mode=ExitMode.UNDERLYING_BASED)
    # Entry at 24800, target 24860 (+60 pts), stop 24770 (-30 pts)
    u_bar, o_bar = make_test_bars(10, 0, 24840, 24865, 24835, 24862, 140, 165, 138, 162)

    decision = evaluator.evaluate_exit(
        underlying_bar=u_bar,
        option_bar=o_bar,
        entry_premium=120.0,
        signal_direction=SignalDirection.BUY,
        underlying_target=24860.0,
        underlying_stop=24770.0,
    )
    assert decision.should_exit is True
    assert decision.exit_reason == "UNDERLYING_TARGET"
    assert decision.exit_price == 162.0


def test_underlying_based_same_bar_conflict_sl_priority():
    evaluator = ExitEvaluator(mode=ExitMode.UNDERLYING_BASED)
    # Volatile bar touches both TP (24860) and SL (24770): High=24870, Low=24760
    u_bar, o_bar = make_test_bars(10, 0, 24800, 24870, 24760, 24810, 120, 170, 80, 125)

    decision = evaluator.evaluate_exit(
        underlying_bar=u_bar,
        option_bar=o_bar,
        entry_premium=120.0,
        signal_direction=SignalDirection.BUY,
        underlying_target=24860.0,
        underlying_stop=24770.0,
    )
    # Worst-case Stop-Loss assumed hit first
    assert decision.should_exit is True
    assert decision.exit_reason == "UNDERLYING_SL_CONFLICT"
    assert decision.is_uncertain_fill is True
    assert decision.exit_price == o_bar.low


def test_premium_based_target_and_stop():
    evaluator = ExitEvaluator(
        mode=ExitMode.PREMIUM_BASED,
        premium_target_pct=0.50,  # +50% -> 180.0
        premium_stop_pct=0.25,  # -25% -> 90.0
    )
    # Target reached
    u_bar, o_bar = make_test_bars(10, 0, 24820, 24830, 24815, 24825, 150, 185, 145, 182)
    decision = evaluator.evaluate_exit(
        u_bar,
        o_bar,
        entry_premium=120.0,
        signal_direction=SignalDirection.BUY,
        underlying_target=25000.0,
        underlying_stop=24000.0,
    )
    assert decision.should_exit is True
    assert decision.exit_reason == "PREMIUM_TARGET"
    assert decision.exit_price == 180.0  # Exit at planned target price

    # Stop reached intrabar
    u_bar2, o_bar2 = make_test_bars(10, 5, 24780, 24790, 24770, 24775, 100, 105, 85, 88)
    decision_sl = evaluator.evaluate_exit(
        u_bar2,
        o_bar2,
        entry_premium=120.0,
        signal_direction=SignalDirection.BUY,
        underlying_target=25000.0,
        underlying_stop=24000.0,
    )
    assert decision_sl.should_exit is True
    assert decision_sl.exit_reason == "PREMIUM_SL"
    assert decision_sl.exit_price == 90.0


def test_premium_based_gap_through_stop():
    """Verify gap-through-stop fills at Open, NOT at nominal stop level."""
    evaluator = ExitEvaluator(
        mode=ExitMode.PREMIUM_BASED,
        premium_stop_pct=0.25,  # Stop = 120 * 0.75 = 90.0
    )
    # Severe gap down on option bar: Open = 75.0 (below nominal stop of 90.0)
    u_bar, o_bar = make_test_bars(10, 10, 24700, 24710, 24680, 24690, 75.0, 78.0, 70.0, 72.0)
    decision = evaluator.evaluate_exit(
        u_bar,
        o_bar,
        entry_premium=120.0,
        signal_direction=SignalDirection.BUY,
        underlying_target=25000.0,
        underlying_stop=24000.0,
    )
    assert decision.should_exit is True
    assert decision.exit_reason == "PREMIUM_SL"
    # Filled at actual bar Open (75.0), preserving realistic execution penalty
    assert decision.exit_price == 75.0


def test_timestamp_alignment_enforcement():
    """Verify evaluator strictly rejects misaligned bar timestamps."""
    evaluator = ExitEvaluator()
    dt1 = datetime(2024, 10, 18, 10, 0, tzinfo=TZ_IST)
    dt2 = datetime(2024, 10, 18, 10, 5, tzinfo=TZ_IST)
    u_bar = Bar(
        timestamp=dt1.astimezone(TZ_UTC),
        exchange_time=dt1,
        symbol="NIFTY 50",
        instrument_id="999920000",
        exchange=Exchange.NSE,
        instrument_type=InstrumentType.INDEX,
        interval=Timeframe.M5,
        open=24800,
        high=24810,
        low=24790,
        close=24800,
        volume=1000,
        source=DataSource.SYNTHETIC,
    )
    o_bar = OptionBar(
        timestamp=dt2.astimezone(TZ_UTC),
        exchange_time=dt2,  # Misaligned!
        trading_date=dt2.date(),
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
        interval=Timeframe.M5,
        open=100,
        high=110,
        low=90,
        close=105,
        volume=1000,
    )
    with pytest.raises(ValueError, match="Timestamp mismatch"):
        evaluator.evaluate_exit(u_bar, o_bar, 100.0, SignalDirection.BUY, 25000.0, 24000.0)


def test_forced_eod_exit():
    evaluator = ExitEvaluator(eod_exit_time=time(15, 15, 0))
    # Bar at 15:15 IST
    u_bar, o_bar = make_test_bars(15, 15, 24800, 24810, 24790, 24805, 120, 125, 115, 122)
    decision = evaluator.evaluate_exit(
        u_bar,
        o_bar,
        entry_premium=120.0,
        signal_direction=SignalDirection.BUY,
        underlying_target=25000.0,
        underlying_stop=24000.0,
    )
    assert decision.should_exit is True
    assert decision.exit_reason == "EOD_EXIT"
    assert decision.exit_price == 120.0  # Fills at start of 15:15 bar (open)
