"""Previous Day Resistance (PDR) Strategy Implementation.

Source Rulebook: PDR_Strategy_Guide.pdf
Classification: Intraday Gap-Up Support Retest (Long Playbook)
Instrument: NIFTY 50 Index (5-min chart)
Trading Bias: Buy (Long) ONLY

Core Mechanism:
- Yesterday's resistance zone acts as support after a gap-up open.
- Gap-Up Gatekeeper: Session must open gap-up (Open_today > Close_yesterday).
- Price pulls back to test the resistance zone.
- Bullish confirmation: Green candle forms at or bouncing off the zone.
- Entry: Long at the close of the confirming green candle.

Risk Parameters:
- Profit Target: Fixed 70 points (Target = Entry + 70)
- Stop-Loss: 35 or 40 points (Stop = Entry - 35 or Entry - 40)
- Forced EOD Square-Off: 15:15 IST
"""

from datetime import time
from typing import Any

from trading_platform.core.enums import SignalDirection, StrategyStatus
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext


class PDRStrategy(BaseStrategy):
    """Canonical Previous Day Resistance (PDR) Strategy."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(name="PDR", config=config, status=StrategyStatus.RESEARCH)
        self.target_points = float(self.config.get("target_points", 70.0))
        self.default_stop_points = float(self.config.get("default_stop_points", 35.0))
        self.max_stop_points = float(self.config.get("max_stop_points", 40.0))
        self.eod_time = time(15, 15)
        self._daily_state: dict[str, dict[str, Any]] = {}

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        """Initialize parameters."""
        self.config.update(config)
        self.target_points = float(self.config.get("target_points", 70.0))

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        """Process incoming 5-minute bar and evaluate PDR long setups."""
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        """Evaluate PDR gap-up retest state machine."""
        latest_bar = context.latest_bar
        if latest_bar is None:
            return None

        day_key = latest_bar.exchange_time.date().isoformat()
        if day_key not in self._daily_state:
            self._daily_state[day_key] = {
                "is_gap_up_verified": None,
                "zone_tested": False,
                "has_traded": False,
            }

        state = self._daily_state[day_key]
        if state["has_traded"]:
            return None

        bar_time = latest_bar.exchange_time.time()

        # Gather previous day levels from context state
        pdh = context.state.get("pdh")
        prev_close = context.state.get("prev_close")
        pd_res_body = context.state.get("pd_res_body")

        if pdh is None or prev_close is None or pd_res_body is None:
            # Cannot trade PDR without the verified prior resistance zone and close.
            return None

        # 1. First Bar: Gap-Up Gatekeeper Check
        if state["is_gap_up_verified"] is None:
            if bar_time in (time(9, 15), time(9, 20)):
                if latest_bar.open > prev_close:
                    state["is_gap_up_verified"] = True
                else:
                    state["is_gap_up_verified"] = False
                    return None
            else:
                return None

        if not state["is_gap_up_verified"]:
            return None

        # 2. Zone Retest Check
        zone_top = pdh
        zone_bottom = pd_res_body

        # Retest occurs if bar Low dips into or touches zone (or within proximity)
        zone_buffer = float(self.config.get("zone_buffer_points", self.config.get("zone_proximity_pts", 5.0)))
        if latest_bar.low <= (zone_top + zone_buffer) and latest_bar.high >= zone_bottom:
            state["zone_tested"] = True

        # 3. Confirmation Trigger: After zone tested, look for bullish green candle closing up
        if state["zone_tested"]:
            if latest_bar.close > latest_bar.open and latest_bar.close >= zone_bottom:
                state["has_traded"] = True
                ref_price = latest_bar.close
                target = round(ref_price + self.target_points, 2)

                # Stop loss based on zone depth
                stop_points = self.default_stop_points
                if (ref_price - zone_bottom) > 35.0:
                    stop_points = self.max_stop_points

                stop_loss = round(ref_price - stop_points, 2)

                sig = Signal(
                    strategy_name=self.name,
                    method_id="PDR_RETEST_BOUNCE",
                    symbol=latest_bar.symbol,
                    direction=SignalDirection.BUY,
                    timestamp=latest_bar.exchange_time,
                    reference_price=ref_price,
                    stop_loss=stop_loss,
                    target=target,
                    reason="PDR_RETEST_BOUNCE",
                    metadata={
                        "method": "PDR_RETEST_BOUNCE",
                        "target_points": self.target_points,
                        "stop_points": stop_points,
                    },
                )
                return sig if self.validate_signal(sig, context) else None

        return None
