"""Unit tests for IRF (Initial Range Failure) Methods 1, 2, 3 and dynamic SL."""

from __future__ import annotations

from datetime import date, datetime

from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar
from trading_platform.strategies.base import StrategyContext
from trading_platform.strategies.irf import IRFStrategy


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


def test_irf_method1_breakout_immediate_failure() -> None:
    """Method 1: C1 closes > IRH, C2 immediately closes < IRH -> SELL with dynamic SL."""
    strat = IRFStrategy(config={"method": "METHOD_1"})
    ctx = StrategyContext()

    d = date(2024, 10, 18)
    # 09:15 - IR formation bar (IRH = 24900.0)
    b1 = _make_bar(d, 9, 15, 24820.0, 24900.0, 24800.0, 24880.0)
    sig1 = strat.on_bar(ctx, b1)
    assert sig1 is None

    # 09:20 - C1: Breakout above IRH (Close = 24910 > 24900)
    b2 = _make_bar(d, 9, 20, 24880.0, 24915.0, 24875.0, 24910.0)
    sig2 = strat.on_bar(ctx, b2)
    assert sig2 is None

    # 09:25 - C2: Immediate failure back inside range (Close = 24890 < 24900)
    # Distance to prior green candle high (24915 - 24890 = 25 pts <= 35 -> SL = 35 pts)
    b3 = _make_bar(d, 9, 25, 24910.0, 24912.0, 24885.0, 24890.0)
    sig3 = strat.on_bar(ctx, b3)

    assert sig3 is not None
    assert sig3.direction == SignalDirection.SELL
    assert sig3.method_id == "IRF_METHOD_1_IMMEDIATE_FAILURE"
    assert sig3.stop_loss == 24890.0 + 35.0  # 35 pt SL
    assert sig3.target == 24890.0 - 60.0  # Fixed 60 pt target


def test_irf_method2_support_break() -> None:
    """Method 2: Extension above IRH for >= 2 candles, then breakdown below support -> SELL."""
    strat = IRFStrategy(config={"method": "METHOD_2_SUPPORT_BREAK"})
    ctx = StrategyContext()

    d = date(2024, 10, 18)
    # 09:15 - IR (IRH = 24900)
    strat.on_bar(ctx, _make_bar(d, 9, 15, 24850.0, 24900.0, 24800.0, 24850.0))

    # 09:20 - Extension 1 (Close = 24920 > 24900, Low = 24905)
    strat.on_bar(ctx, _make_bar(d, 9, 20, 24910.0, 24925.0, 24905.0, 24920.0))

    # 09:25 - Extension 2 (Close = 24930 > 24900, Low = 24910)
    strat.on_bar(ctx, _make_bar(d, 9, 25, 24920.0, 24935.0, 24910.0, 24930.0))

    # 09:30 - Red candle closes below recent support (24905): Close = 24895 < 24905
    b_rev = _make_bar(d, 9, 30, 24920.0, 24922.0, 24890.0, 24895.0)
    sig = strat.on_bar(ctx, b_rev)

    assert sig is not None
    assert sig.direction == SignalDirection.SELL
    assert sig.method_id == "IRF_METHOD_2_SUPPORT_BREAK"
    assert sig.target == 24895.0 - 60.0


def test_irf_method3_liquidity_sweep() -> None:
    """Method 3: Pierces beyond IRH with 60-70% wick above IRH, red candle closes below IRH."""
    strat = IRFStrategy(config={"method": "METHOD_3"})
    ctx = StrategyContext()

    d = date(2024, 10, 18)
    # 09:15 - IR (IRH = 24900)
    strat.on_bar(ctx, _make_bar(d, 9, 15, 24850.0, 24900.0, 24800.0, 24850.0))

    # 09:20 - Small bar inside IR
    strat.on_bar(ctx, _make_bar(d, 9, 20, 24850.0, 24870.0, 24840.0, 24860.0))

    # 09:25 - Sweep candle: Open = 24880, High = 24950, Low = 24840, Close = 24860
    # Upper wick = 24950 - 24880 = 70 pts.
    # Portion above IRH (24900) = 24950 - 24900 = 50 pts.
    # Outside share = 50 / 70 = ~71.4% (matches ~60-75% wick condition)
    b_sweep = _make_bar(d, 9, 25, 24880.0, 24950.0, 24840.0, 24860.0)
    sig = strat.on_bar(ctx, b_sweep)

    assert sig is not None
    assert sig.direction == SignalDirection.SELL
    assert sig.method_id == "IRF_METHOD_3_LIQUIDITY_SWEEP"
