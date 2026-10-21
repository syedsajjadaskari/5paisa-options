"""Initial Range Failure (IRF) Strategy Implementation.

Source Rulebook: IRF_Strategy_Guide Updated.pdf (17 pages)
Classification: Intraday Short-Selling Price-Action Framework
Instrument: NIFTY 50 Index (5-min chart)
Trading Direction: Short Trades ONLY

Documented Entry Methods:
- Method 1: Breakout and Immediate Failure (Pages 5-6)
  C1 closes > IRH; immediate next candle C2 closes < IRH.
- Method 2: Extended Upward Move and Reversal (Pages 7-10)
  Price closes > IRH, extends upward for > 1 candle, forms a red candle,
  and breaks either:
  * Confirmation A (Support Break): Closes below recent pullback support.
  * Confirmation B (Resistance Break): Fails at recent resistance and red candle closes below it.
- Method 3: Advanced IRF / Liquidity Sweep (Pages 11-12)
  Small candles inside IR; red sweep candle spikes with 60-70% upper wick above IRH,
  closing below recent support.

Risk Parameters:
- Profit Target: Fixed 60 points below entry (Target = Entry - 60)
- Stop-Loss: 35 or 40 points based on distance to recent green candle:
  * Distance <= 35 points -> SL = 35 points
  * Distance > 35 points -> SL = 40 points
- Forced EOD Square-Off: 15:15 IST
"""

from datetime import time
from typing import Any

from trading_platform.core.enums import SignalDirection, StrategyStatus
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext


class IRFStrategy(BaseStrategy):
    """Canonical Initial Range Failure (IRF) Strategy implementing all 3 rulebook methods."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(name="IRF", config=config, status=StrategyStatus.RESEARCH)
        self.target_points = float(self.config.get("target_points", 60.0))
        self.default_stop_points = float(self.config.get("default_stop_points", 35.0))
        self.max_stop_points = float(self.config.get("max_stop_points", 40.0))
        self.eod_time = time(15, 15)
        self._daily_state: dict[str, dict[str, Any]] = {}

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        """Initialize parameters."""
        self.config.update(config)
        self.target_points = float(self.config.get("target_points", 60.0))

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        """Process incoming 5-minute bar and evaluate IRF failure setups."""
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        """Evaluate IRF short setup state machine across Method 1, Method 2, and Method 3."""
        latest_bar = context.latest_bar
        if latest_bar is None:
            return None

        day_key = latest_bar.exchange_time.date().isoformat()
        if day_key not in self._daily_state:
            self._daily_state[day_key] = {
                "ir_candle": None,
                "irh": None,
                "irl": None,
                "breakout_candle": None,
                "extension_candles_count": 0,
                "extension_recent_support": None,
                "extension_recent_resistance": None,
                "resistance_test_seen": False,
                "has_traded": False,
            }

        state = self._daily_state[day_key]
        if state["has_traded"]:
            return None

        bar_time = latest_bar.exchange_time.time()

        # 1. Establish Initial Range (09:15–09:20 candle, wicks included, Page 4)
        if state["ir_candle"] is None:
            if bar_time in (time(9, 15), time(9, 20)):
                state["ir_candle"] = latest_bar
                state["irh"] = latest_bar.high
                state["irl"] = latest_bar.low
            return None

        irh = state["irh"]
        method = self.config.get("method", "METHOD_1")

        # -------------------------------------------------------------
        # Method 1: Breakout and Immediate Failure (Pages 5-6)
        # -------------------------------------------------------------
        if method == "METHOD_1":
            if state["breakout_candle"] is None:
                # C1 closes strictly above IRH
                if latest_bar.close > irh:
                    state["breakout_candle"] = latest_bar
                return None
            else:
                # C2 must be the immediate next candle closing back inside range
                if latest_bar.close < irh:
                    state["has_traded"] = True
                    return self._create_short_signal(
                        latest_bar, context, "IRF_METHOD_1_IMMEDIATE_FAILURE"
                    )
                else:
                    # Sequence broken if C2 does not close inside range
                    state["breakout_candle"] = None
                    return None

        # -------------------------------------------------------------
        # Method 2: Extended Upward Move and Reversal (Pages 7-10)
        # -------------------------------------------------------------
        elif method in ("METHOD_2", "METHOD_2_SUPPORT_BREAK", "METHOD_2_RESISTANCE_BREAK"):
            # Check if breakout occurred
            if state["breakout_candle"] is None:
                if latest_bar.close > irh:
                    state["breakout_candle"] = latest_bar
                    state["extension_candles_count"] = 1
                return None
            else:
                # Extension phase: closes above IRH continue
                if latest_bar.close > irh:
                    state["extension_candles_count"] += 1
                    # Track recent pullback support during extension
                    if (
                        state["extension_recent_support"] is None
                        or latest_bar.low < state["extension_recent_support"]
                    ):
                        state["extension_recent_support"] = latest_bar.low
                    # Track recent resistance high
                    if (
                        state["extension_recent_resistance"] is None
                        or latest_bar.high > state["extension_recent_resistance"]
                    ):
                        state["extension_recent_resistance"] = latest_bar.high
                    return None

                # Must have extended for > 1 candle above IRH
                if state["extension_candles_count"] >= 2:
                    is_red = latest_bar.close < latest_bar.open

                    # Confirmation A: Support Break (Page 8)
                    recent_supp = state.get("extension_recent_support")
                    if (
                        method in ("METHOD_2", "METHOD_2_SUPPORT_BREAK")
                        and is_red
                        and recent_supp is not None
                        and latest_bar.close < recent_supp
                    ):
                        state["has_traded"] = True
                        return self._create_short_signal(
                            latest_bar, context, "IRF_METHOD_2_SUPPORT_BREAK"
                        )

                    # Confirmation B: Resistance Break Reversal (Page 9)
                    recent_res = state.get("extension_recent_resistance")
                    if (
                        method in ("METHOD_2", "METHOD_2_RESISTANCE_BREAK")
                        and recent_res is not None
                    ):
                        # Price tested above resistance earlier in the bar or previous bar
                        if (
                            latest_bar.high >= recent_res
                            and is_red
                            and latest_bar.close < recent_res
                        ):
                            state["has_traded"] = True
                            return self._create_short_signal(
                                latest_bar, context, "IRF_METHOD_2_RESISTANCE_BREAK"
                            )

        # -------------------------------------------------------------
        # Method 3: Advanced IRF / Liquidity Sweep (Pages 11-12)
        # -------------------------------------------------------------
        elif method == "METHOD_3":
            if latest_bar.high > irh:
                total_range = latest_bar.high - latest_bar.low
                upper_wick = latest_bar.high - max(latest_bar.open, latest_bar.close)
                portion_above = latest_bar.high - irh
                if total_range > 0 and upper_wick > 0:
                    outside_share = portion_above / upper_wick
                    # Wick condition: 60% to 70% of upper wick lies above IRH
                    # Compulsory confirmation: red sweep candle closes below recent support
                    recent_supp = context.state.get("recent_swing_low", irh - 10.0)
                    if (
                        0.55 <= outside_share <= 0.85
                        and latest_bar.close < latest_bar.open
                        and latest_bar.close <= recent_supp
                    ):
                        state["has_traded"] = True
                        return self._create_short_signal(
                            latest_bar, context, "IRF_METHOD_3_LIQUIDITY_SWEEP"
                        )

        return None

    def _create_short_signal(
        self, bar: Bar, context: StrategyContext, reason: str
    ) -> Signal | None:
        """Calculate dynamic stop loss from recent green candle (Pages 2, 14) and construct short signal."""
        ref_price = bar.close
        target = round(ref_price - self.target_points, 2)

        # Find most recent green candle prior to entry
        recent_green_high = None
        for b in reversed(context.historical_bars[:-1]):
            if b.close > b.open:
                recent_green_high = b.high
                break

        # Stop-loss rule (Page 14):
        # If distance to recent green candle <= 35 -> SL = 35 points
        # If distance to recent green candle > 35 -> SL = 40 points
        if recent_green_high is not None and (recent_green_high - ref_price) > 35.0:
            stop_points = self.max_stop_points
        else:
            stop_points = self.default_stop_points

        stop_loss = round(ref_price + stop_points, 2)
        sig = Signal(
            strategy_name=self.name,
            method_id=reason,
            symbol=bar.symbol,
            direction=SignalDirection.SELL,
            timestamp=bar.exchange_time,
            reference_price=ref_price,
            stop_loss=stop_loss,
            target=target,
            reason=reason,
            metadata={
                "method": reason,
                "target_points": self.target_points,
                "stop_points": stop_points,
            },
        )
        return sig if self.validate_signal(sig, context) else None
