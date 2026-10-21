"""Previous Day Support (PDS) Strategy Implementation.

Source Rulebook: PDS strategy.pdf (7 pages)
Classification: Intraday Gap-Down Role-Reversal Short-Selling Playbook
Instrument: NIFTY 50 Index (5-min chart)
Trading Bias: Sell (Short) ONLY

Core Mechanism:
- Yesterday's support zone acts as resistance after a gap-down open.
- Gap-Down Gatekeeper: Session must open gap-down (Open_today < Close_yesterday).
- Price rallies up from below to test yesterday's support zone.
- Three Entry Methods at Resistance Zone (Page 5):
  * Method 1: Good Red Candle (clear decisive rejection body, close near low).
  * Method 2: Red Pin Bar with Higher Price Rejection (HPR, long upper wick rejected, candle must close red).
  * Method 3: Break of Small Support Fallback (Page 5, used when initial red candle is weak/small;
    breaks minor support established near the zone).

Risk Parameters:
- Profit Target: Fixed 70 points (Target = Entry - 70)
- Stop-Loss: 35 or 40 points based on distance to recent green candle (Pages 1, 2, 5)
- Forced EOD Square-Off: 15:15 IST
"""

from datetime import time
from typing import Any

from trading_platform.core.enums import SignalDirection, StrategyStatus
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext
from trading_platform.strategies.price_action import is_hpr_pin_bar, is_strong_body_candle


class PDSStrategy(BaseStrategy):
    """Canonical Previous Day Support (PDS) Strategy implementing all 3 rulebook methods."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(name="PDS", config=config, status=StrategyStatus.RESEARCH)
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
        """Process incoming 5-minute bar and evaluate PDS short setups."""
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        """Evaluate PDS gap-down rally-and-rejection state machine across Methods 1, 2, and 3."""
        latest_bar = context.latest_bar
        if latest_bar is None:
            return None

        day_key = latest_bar.exchange_time.date().isoformat()
        if day_key not in self._daily_state:
            self._daily_state[day_key] = {
                "is_gap_down_verified": None,
                "zone_tested": False,
                "weak_candle_seen": False,
                "minor_support_level": None,
                "has_traded": False,
            }

        state = self._daily_state[day_key]
        if state["has_traded"]:
            return None

        bar_time = latest_bar.exchange_time.time()

        pdl = context.state.get("pdl")
        prev_close = context.state.get("prev_close")
        pd_supp_body = context.state.get("pd_supp_body", (pdl + 15.0) if pdl is not None else None)

        if pdl is None or prev_close is None:
            return None

        # 1. First Bar: Gap-Down Gatekeeper Check (Pages 1, 2)
        if state["is_gap_down_verified"] is None:
            if bar_time in (time(9, 15), time(9, 20)):
                if latest_bar.open < prev_close:
                    state["is_gap_down_verified"] = True
                else:
                    state["is_gap_down_verified"] = False
                    return None
            else:
                return None

        if not state["is_gap_down_verified"]:
            return None

        # 2. Zone Retest Check (Support acting as Resistance, Page 4)
        zone_bottom = pdl
        zone_top = pd_supp_body if pd_supp_body is not None else (zone_bottom + 15.0)

        # Retest occurs if bar High pushes up to touch zone (or within 5 pt proximity)
        if latest_bar.high >= (zone_bottom - 5.0) and latest_bar.low <= (zone_top + 5.0):
            state["zone_tested"] = True

        # 3. Confirmation Trigger (Page 5)
        if state["zone_tested"]:
            is_red = latest_bar.close < latest_bar.open
            method = self.config.get("method", "METHOD_1")

            confirmed = False
            entry_reason = "NO_TRIGGER"

            # Method 1: Good Red Candle (clear decisive body, close near low)
            if method == "METHOD_1":
                if is_red and is_strong_body_candle(latest_bar, 0.45):
                    confirmed = True
                    entry_reason = "PDS_METHOD_1_STRONG_RED_CANDLE"
                else:
                    state["weak_candle_seen"] = True
                    # Track minor support for fallback
                    state["minor_support_level"] = latest_bar.low

            # Method 2: Red Pin Bar with HPR
            elif method == "METHOD_2":
                if is_red and is_hpr_pin_bar(latest_bar, 0.45):
                    confirmed = True
                    entry_reason = "PDS_METHOD_2_HPR_PIN_BAR"
                else:
                    state["weak_candle_seen"] = True
                    state["minor_support_level"] = latest_bar.low

            # Method 3: Break of a Small Support (Fallback)
            elif method == "METHOD_3":
                minor_supp = state.get("minor_support_level")
                if minor_supp is not None and latest_bar.close < minor_supp and is_red:
                    confirmed = True
                    entry_reason = "PDS_METHOD_3_SMALL_SUPPORT_BREAK"
                else:
                    # Update minor support as consolidation forms near zone
                    state["minor_support_level"] = min(
                        state["minor_support_level"] or latest_bar.low, latest_bar.low
                    )

            if confirmed:
                state["has_traded"] = True
                ref_price = latest_bar.close
                target = round(ref_price - self.target_points, 2)

                # Stop loss based on distance to recent green candle (Pages 1, 5)
                recent_green_high = None
                for b in reversed(context.historical_bars[:-1]):
                    if b.close > b.open:
                        recent_green_high = b.high
                        break

                if recent_green_high is not None and (recent_green_high - ref_price) > 35.0:
                    stop_points = self.max_stop_points
                else:
                    stop_points = self.default_stop_points

                stop_loss = round(ref_price + stop_points, 2)

                sig = Signal(
                    strategy_name=self.name,
                    method_id=entry_reason,
                    symbol=latest_bar.symbol,
                    direction=SignalDirection.SELL,
                    timestamp=latest_bar.exchange_time,
                    reference_price=ref_price,
                    stop_loss=stop_loss,
                    target=target,
                    reason=entry_reason,
                    metadata={
                        "method": entry_reason,
                        "target_points": self.target_points,
                        "stop_points": stop_points,
                    },
                )
                return sig if self.validate_signal(sig, context) else None

        return None
