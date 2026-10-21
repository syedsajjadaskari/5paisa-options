"""Unit tests for PDS, PDR, and RF strategies across their documented rulebook methods."""

from __future__ import annotations

from datetime import date, datetime

from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar
from trading_platform.strategies.base import StrategyContext
from trading_platform.strategies.pdr import PDRStrategy
from trading_platform.strategies.pds import PDSStrategy
from trading_platform.strategies.rf import RFStrategy


def _make_bar(d: date, hour: int, minute: int, o: float, h: float, low: float, c: float) -> Bar:
    t = datetime(d.year, d.month, d.day, hour, minute)
    return Bar(
        symbol="NIFTY",
        instrument_id="NIFTY50",
        timestamp=t,
        exchange_time=t,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1000,
    )


def test_pds_method1_gap_down_strong_red_candle() -> None:
    """PDS Method 1: Session opens gap down, tests PDL zone, strong red candle triggers short."""
    strat = PDSStrategy(config={"method": "METHOD_1"})
    ctx = StrategyContext()
    ctx.state["pdl"] = 24800.0
    ctx.state["prev_close"] = 24850.0  # Gap down open (< 24850)

    d = date(2024, 10, 18)
    # 09:15 - Open 24780 (< 24850 = gap-down verified)
    strat.on_bar(ctx, _make_bar(d, 9, 15, 24780.0, 24795.0, 24760.0, 24775.0))

    # 09:20 - Rallies up to test PDL zone (High 24805 touches PDL 24800), closes decisive red (Open 24800, Close 24770)
    # Range = 24805 - 24765 = 40. Body = 30. Body ratio = 75% > 45%
    b_test = _make_bar(d, 9, 20, 24800.0, 24805.0, 24765.0, 24770.0)
    sig = strat.on_bar(ctx, b_test)

    assert sig is not None
    assert sig.direction == SignalDirection.SELL
    assert sig.method_id == "PDS_METHOD_1_STRONG_RED_CANDLE"
    assert sig.target == 24770.0 - 70.0


def test_pds_method2_hpr_pin_bar() -> None:
    """PDS Method 2: Zone retest forms red HPR pin bar (long upper wick rejection)."""
    strat = PDSStrategy(config={"method": "METHOD_2"})
    ctx = StrategyContext()
    ctx.state["pdl"] = 24800.0
    ctx.state["prev_close"] = 24850.0

    d = date(2024, 10, 18)
    strat.on_bar(ctx, _make_bar(d, 9, 15, 24780.0, 24790.0, 24760.0, 24770.0))

    # 09:20 - Tests 24810, long upper wick: Open 24780, High 24815, Low 24765, Close 24770
    # Upper wick = 24815 - 24780 = 35. Total range = 50. Upper wick ratio = 70% >= 45%
    b_pin = _make_bar(d, 9, 20, 24780.0, 24815.0, 24765.0, 24770.0)
    sig = strat.on_bar(ctx, b_pin)

    assert sig is not None
    assert sig.direction == SignalDirection.SELL
    assert sig.method_id == "PDS_METHOD_2_HPR_PIN_BAR"


def test_pdr_gap_up_retest_bounce() -> None:
    """PDR: Gap-up open, pulls back to PDH resistance zone, green candle bounces -> BUY."""
    strat = PDRStrategy()
    ctx = StrategyContext()
    ctx.state["pdh"] = 24800.0
    ctx.state["prev_close"] = 24750.0  # Gap up open (> 24750)

    d = date(2024, 10, 18)
    # 09:15 - Open 24830 (> 24750 gap up verified)
    strat.on_bar(ctx, _make_bar(d, 9, 15, 24830.0, 24850.0, 24820.0, 24840.0))

    # 09:20 - Pulls back into PDH zone (Low 24798 touches PDH 24800), closes strong green 24825
    b_bounce = _make_bar(d, 9, 20, 24805.0, 24830.0, 24798.0, 24825.0)
    sig = strat.on_bar(ctx, b_bounce)

    assert sig is not None
    assert sig.direction == SignalDirection.BUY
    assert sig.method_id == "PDR_RETEST_BOUNCE"
    assert sig.target == 24825.0 + 70.0


def test_rf_uptrend_gap_up_reclaim() -> None:
    """RF: Established uptrend, gap up within range, breakdown below RFL, reclaim -> BUY."""
    strat = RFStrategy(config={"strict_rulebook_mode": False})
    ctx = StrategyContext()
    ctx.state["pdh"] = 24850.0
    ctx.state["pdl"] = 24700.0
    ctx.state["prev_close"] = 24780.0
    ctx.state["ema_200"] = 24720.0  # Uptrend filter satisfied (price > 24720)

    d = date(2024, 10, 18)
    # 09:15 - Open 24800 (gap-up inside range: 24780 < 24800 <= 24850), High 24820, Low 24790, Close 24810
    # RFL is set to 24790
    strat.on_bar(ctx, _make_bar(d, 9, 15, 24800.0, 24820.0, 24790.0, 24810.0))

    # 09:20 - Breakdown candle closing strictly below RFL 24790: Close 24775
    strat.on_bar(ctx, _make_bar(d, 9, 20, 24810.0, 24812.0, 24770.0, 24775.0))

    # 09:25 - Reclaim candle closing back inside range > 24790: Close 24805
    b_rec = _make_bar(d, 9, 25, 24775.0, 24810.0, 24770.0, 24805.0)
    sig = strat.on_bar(ctx, b_rec)

    assert sig is not None
    assert sig.direction == SignalDirection.BUY
    assert sig.method_id == "RF_CANONICAL_RECLAIM_LONG"
    assert sig.target == 24805.0 + 60.0
