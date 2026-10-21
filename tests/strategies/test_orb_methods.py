"""Unit tests for ORB (Opening Range Breakout) Methods 1, 2, 3 and 200 DMA warmup gate."""

from __future__ import annotations

from datetime import date, datetime

from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar
from trading_platform.strategies.base import StrategyContext
from trading_platform.strategies.orb import ORBStrategy


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


def test_orb_method1_pdh_breakout() -> None:
    """Method 1: First candle breaks above PDH with strong bullish body -> BUY."""
    strat = ORBStrategy(config={"method": "METHOD_1"})
    ctx = StrategyContext()
    ctx.state["pdh"] = 24800.0
    ctx.state["pdl"] = 24700.0
    ctx.state["ema_200"] = 24750.0

    d = date(2024, 10, 18)
    # 09:15 candle: Open 24790, High 24840, Low 24785, Close 24830 (> PDH 24800)
    # Solid body = (24830 - 24790) / (24840 - 24785) = 40 / 55 = 72% > 50%
    b = _make_bar(d, 9, 15, 24790.0, 24840.0, 24785.0, 24830.0)
    sig = strat.on_bar(ctx, b)

    assert sig is not None
    assert sig.direction == SignalDirection.BUY
    assert sig.method_id == "ORB_METHOD_1_PDH_BREAKOUT"
    assert sig.target == 24830.0 + 60.0
    assert sig.stop_loss == 24830.0 - 30.0


def test_orb_method2_inner_resistance_breakout() -> None:
    """Method 2: First candle breaks above inner resistance level inside yesterday range -> BUY."""
    strat = ORBStrategy(config={"method": "METHOD_2"})
    ctx = StrategyContext()
    ctx.state["pdh"] = 24900.0
    ctx.state["pdl"] = 24650.0
    ctx.state["inner_resistance"] = 24780.0
    ctx.state["ema_200"] = 24720.0

    d = date(2024, 10, 18)
    # Candle opens 24760, closes 24810 (> inner_res 24780)
    b = _make_bar(d, 9, 15, 24760.0, 24820.0, 24755.0, 24810.0)
    sig = strat.on_bar(ctx, b)

    assert sig is not None
    assert sig.direction == SignalDirection.BUY
    assert sig.method_id == "ORB_METHOD_2_INNER_RESISTANCE_BREAKOUT"


def test_orb_method3_requires_200_dma_warmup() -> None:
    """Method 3: Strictly requires daily 200 DMA; if missing, rejects with no signal."""
    strat = ORBStrategy(config={"method": "METHOD_3"})
    ctx = StrategyContext()
    # Missing dma_200 in context
    ctx.state["prev_close"] = 24600.0

    d = date(2024, 10, 18)
    b = _make_bar(d, 9, 15, 24760.0, 24820.0, 24755.0, 24810.0)
    sig = strat.on_bar(ctx, b)

    assert sig is None  # Rejection enforced due to insufficient warmup


def test_orb_method3_gap_above_200_dma_trigger() -> None:
    """Method 3: Prev close below 200 DMA, opens and closes above 200 DMA -> BUY."""
    strat = ORBStrategy(config={"method": "METHOD_3"})
    ctx = StrategyContext()
    ctx.state["dma_200"] = 24700.0
    ctx.state["prev_close"] = 24680.0  # Yesterday closed below 200 DMA

    d = date(2024, 10, 18)
    # Opens 24720 (> 24700), closes 24760 (> 24700)
    b = _make_bar(d, 9, 15, 24720.0, 24770.0, 24710.0, 24760.0)
    sig = strat.on_bar(ctx, b)

    assert sig is not None
    assert sig.direction == SignalDirection.BUY
    assert sig.method_id == "ORB_METHOD_3_GAP_ABOVE_200DMA"
