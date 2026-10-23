"""Opening Range Breakout (ORB) Strategy Implementation.

Source Rulebook: ORB_Strategy_Rulebook-2.pdf (13 pages)
Classification: Intraday Trend & Breakout / Breakdown Playbook
Instrument: NIFTY 50 Index (5-min chart)
Decision Time: Exactly 09:20 AM IST (after first 5-minute candle closes)

Documented Entry Methods:
- Method 1: Previous-Day Level Breakout (Pages 3-4)
  Long: Uptrend (price > 200 EMA), breaks & closes > PDH.
  Short: Downtrend (price < 200 EMA), breaks & closes < PDL.
- Method 2: Trendline & Inner Resistance / Support Breakout (Pages 4-7)
  Long: Uptrend, rising trendline, breaks & closes > inner resistance inside prior range.
  Short: Downtrend, falling trendline, breaks & closes < inner support inside prior range.
- Method 3 / Special Setup: Gap Above / Below 200 DMA (Pages 8-9)
  *CRITICAL DISTINCTION*: Uses 200 DMA (Daily Moving Average), NOT 200 EMA.
  Long: Gaps up above 200 DMA from below, candle closes > 200 DMA.
  Short: Gaps down below 200 DMA from above, candle closes < 200 DMA.

Risk Parameters:
- Stop-Loss: Fixed 30 points (Long: Entry - 30, Short: Entry + 30)
- Profit Target: Fixed 60 points (Long: Entry + 60, Short: Entry - 60)
- Risk-Reward: 1:2
- Forced EOD Square-Off: 15:15 IST
"""

from datetime import time
from typing import Any

from trading_platform.core.enums import SignalDirection, StrategyStatus
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext
from trading_platform.strategies.price_action import (
    get_lower_wick_ratio,
    get_upper_wick_ratio,
    is_hpr_pin_bar,
    is_lpr_pin_bar,
    is_strong_body_candle,
)


class ORBStrategy(BaseStrategy):
    """Canonical Opening Range Breakout (ORB) Strategy implementing all 3 rulebook methods."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(name="ORB", config=config, status=StrategyStatus.RESEARCH)
        self.target_points = float(self.config.get("target_points", 60.0))
        self.stop_points = float(self.config.get("stop_points", 30.0))
        self.decision_time = time(9, 20)
        self.eod_time = time(15, 15)
        self.min_body_ratio = float(self.config.get("min_body_ratio", 0.50))
        self.max_opposing_wick_ratio = float(self.config.get("max_opposing_wick_ratio", 0.35))
        self._daily_evaluated: dict[str, bool] = {}

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        """Initialize parameters."""
        self.config.update(config)
        self.target_points = float(self.config.get("target_points", 60.0))
        self.stop_points = float(self.config.get("stop_points", 30.0))

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        """Process incoming 5-minute bar and evaluate ORB entry."""
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        """Evaluate ORB rules strictly after the 09:15-09:20 bar completes (at 09:20 IST)."""
        latest_bar = context.latest_bar
        if latest_bar is None:
            return None

        bar_time = latest_bar.exchange_time.time()
        # Decision must be made at 09:20 AM after first candle completes (Page 1)
        is_first_candle = (bar_time == time(9, 15)) or (bar_time == time(9, 20))
        day_key = latest_bar.exchange_time.date().isoformat()

        if not is_first_candle or self._daily_evaluated.get(day_key, False):
            return None

        # Point-in-time context levels
        pdh = context.state.get("pdh")
        pdl = context.state.get("pdl")
        prev_close = context.state.get("prev_close")
        ema_200 = context.state.get("ema_200")  # 5-min intraday 200 EMA
        dma_200 = context.state.get("dma_200")  # Daily 200 DMA
        inner_res = context.state.get("inner_resistance")
        inner_supp = context.state.get("inner_support")
        method = self.config.get("method", "METHOD_1")

        direction = SignalDirection.HOLD
        entry_reason = "NO_SETUP"
        ref_price = latest_bar.close

        # Candle quality filters (Pages 9-10)
        is_solid_body = is_strong_body_candle(latest_bar, self.min_body_ratio)
        upper_wick = get_upper_wick_ratio(latest_bar)
        lower_wick = get_lower_wick_ratio(latest_bar)
        is_bullish_candle = (
            latest_bar.close > latest_bar.open
            and is_solid_body
            and upper_wick <= self.max_opposing_wick_ratio
        ) or is_lpr_pin_bar(latest_bar, 0.45)
        is_bearish_candle = (
            latest_bar.close < latest_bar.open
            and is_solid_body
            and lower_wick <= self.max_opposing_wick_ratio
        ) or is_hpr_pin_bar(latest_bar, 0.45)

        # -------------------------------------------------------------
        # Method 1: Previous-Day Major Level Breakout (Pages 3-4)
        # -------------------------------------------------------------
        if method == "METHOD_1":
            if pdh is not None and latest_bar.close > pdh and is_bullish_candle:
                # Long: Uptrend filter (price > 200 EMA)
                if ema_200 is not None and latest_bar.close >= ema_200:
                    direction = SignalDirection.BUY
                    entry_reason = "ORB_METHOD_1_PDH_BREAKOUT"
            elif pdl is not None and latest_bar.close < pdl and is_bearish_candle:
                # Short: Downtrend filter (price < 200 EMA)
                if ema_200 is not None and latest_bar.close <= ema_200:
                    direction = SignalDirection.SELL
                    entry_reason = "ORB_METHOD_1_PDL_BREAKDOWN"

        # -------------------------------------------------------------
        # Method 2: Trendline & Inner Resistance / Support Breakout (Pages 4-7)
        # -------------------------------------------------------------
        elif method == "METHOD_2":
            # Inner level inside previous day range
            if inner_res is not None and latest_bar.close > inner_res and is_bullish_candle:
                # Long: price above 200 EMA
                if ema_200 is not None and latest_bar.close >= ema_200:
                    direction = SignalDirection.BUY
                    entry_reason = "ORB_METHOD_2_INNER_RESISTANCE_BREAKOUT"
            elif inner_supp is not None and latest_bar.close < inner_supp and is_bearish_candle:
                # Short: price below 200 EMA
                if ema_200 is not None and latest_bar.close <= ema_200:
                    direction = SignalDirection.SELL
                    entry_reason = "ORB_METHOD_2_INNER_SUPPORT_BREAKDOWN"

        # -------------------------------------------------------------
        # Method 3: Gap Above / Below 200 DMA (Pages 8-9)
        # -------------------------------------------------------------
        elif method in ("METHOD_3", "GAP_200_DMA"):
            # Requires daily 200 DMA
            if dma_200 is not None and prev_close is not None:
                # Long: Gaps up through 200 DMA from below; candle closes above it
                if (
                    prev_close < dma_200
                    and latest_bar.open >= dma_200
                    and latest_bar.close > dma_200
                    and is_bullish_candle
                ):
                    direction = SignalDirection.BUY
                    entry_reason = "ORB_METHOD_3_GAP_ABOVE_200DMA"
                # Short: Gaps down through 200 DMA from above; candle closes below it
                elif (
                    prev_close > dma_200
                    and latest_bar.open <= dma_200
                    and latest_bar.close < dma_200
                    and is_bearish_candle
                ):
                    direction = SignalDirection.SELL
                    entry_reason = "ORB_METHOD_3_GAP_BELOW_200DMA"
            else:
                entry_reason = "REJECTED_INSUFFICIENT_DMA_WARMUP"

        self._daily_evaluated[day_key] = True

        if direction == SignalDirection.BUY:
            target = round(ref_price + self.target_points, 2)
            stop_loss = round(ref_price - self.stop_points, 2)
            sig = Signal(
                strategy_name=self.name,
                method_id=entry_reason,
                symbol=latest_bar.symbol,
                direction=direction,
                timestamp=latest_bar.exchange_time,
                reference_price=ref_price,
                stop_loss=stop_loss,
                target=target,
                reason=entry_reason,
                metadata={
                    "method": entry_reason,
                    "target_points": self.target_points,
                    "stop_points": self.stop_points,
                },
            )
            return sig if self.validate_signal(sig, context) else None

        elif direction == SignalDirection.SELL:
            target = round(ref_price - self.target_points, 2)
            stop_loss = round(ref_price + self.stop_points, 2)
            sig = Signal(
                strategy_name=self.name,
                method_id=entry_reason,
                symbol=latest_bar.symbol,
                direction=direction,
                timestamp=latest_bar.exchange_time,
                reference_price=ref_price,
                stop_loss=stop_loss,
                target=target,
                reason=entry_reason,
                metadata={
                    "method": entry_reason,
                    "target_points": self.target_points,
                    "stop_points": self.stop_points,
                },
            )
            return sig if self.validate_signal(sig, context) else None

        return None
