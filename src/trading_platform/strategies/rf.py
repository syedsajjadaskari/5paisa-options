"""Reverse Failure (RF) Strategy Implementation.

Source Rulebook: RF strategy.pdf
Classification: Intraday Uptrend Dip-and-Reclaim Long Setup (Opposite of IRF)
Instrument: NIFTY 50 Index (5-min chart)
Trading Bias: Buy (Long) ONLY

Core Mechanism:
- Trend Filter: Established uptrend (price > 200 EMA).
- Gap Gatekeeper: Gap-up open within yesterday's range (Close_yesterday < Open_today <= High_yesterday).
- Reference Range: First 5-minute candle (09:15-09:20 AM IST), wicks included (RF High & RF Low).
- Breakdown: Candle closes strictly below RF Low.
- Reclaim: Subsequent candle closes back inside the range (> RF Low).
- Entry Trigger: Canonical interpretation enters Long on the close of the reclaim candle.

Rulebook Ambiguities (Page 8 of PDF):
- Target: Marked 'OPEN' in PDF. Canonical implementation uses symmetric 60 points or records as unresolved.
- Stop-Loss: 35-40 points below entry.
- Forced EOD Square-Off: 15:15 IST
"""

from datetime import time
from typing import Any

from trading_platform.core.enums import SignalDirection, StrategyStatus
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext


class RFStrategy(BaseStrategy):
    """Canonical Reverse Failure (RF) Strategy."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(name="RF", config=config, status=StrategyStatus.RESEARCH)
        self.target_points = float(self.config.get("target_points", 60.0))
        self.default_stop_points = float(self.config.get("default_stop_points", 35.0))
        self.max_stop_points = float(self.config.get("max_stop_points", 40.0))
        self.eod_time = time(15, 15)
        self.strict_rulebook_mode = bool(self.config.get("strict_rulebook_mode", False))
        self._daily_state: dict[str, dict[str, Any]] = {}

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        """Initialize parameters."""
        self.config.update(config)
        self.target_points = float(self.config.get("target_points", 60.0))

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        """Process incoming 5-minute bar and evaluate RF reclaim setups."""
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        """Evaluate RF dip-and-reclaim state machine."""
        latest_bar = context.latest_bar
        if latest_bar is None:
            return None

        day_key = latest_bar.exchange_time.date().isoformat()
        if day_key not in self._daily_state:
            self._daily_state[day_key] = {
                "rf_candle": None,
                "rfh": None,
                "rfl": None,
                "is_gap_verified": None,
                "breakdown_candle": None,
                "has_traded": False,
            }

        state = self._daily_state[day_key]
        if state["has_traded"]:
            return None

        bar_time = latest_bar.exchange_time.time()

        pdh = context.state.get("pdh")
        prev_close = context.state.get("prev_close")
        ema_200 = context.state.get("ema_200")

        # 1. First Bar: Establish RF Range and Gap Requirement
        if state["rf_candle"] is None:
            if bar_time in (time(9, 15), time(9, 20)):
                # Check gap-up within yesterday's range (if prev levels provided)
                if prev_close is not None and pdh is not None:
                    if not (prev_close < latest_bar.open <= pdh):
                        state["is_gap_verified"] = False
                        return None
                    state["is_gap_verified"] = True

                # Check 200 EMA uptrend filter
                if ema_200 is not None and latest_bar.close < ema_200:
                    state["is_gap_verified"] = False
                    return None

                state["rf_candle"] = latest_bar
                state["rfh"] = latest_bar.high
                state["rfl"] = latest_bar.low
            return None

        if state.get("is_gap_verified") is False:
            return None

        rfl = state["rfl"]

        # 2. Step A: Breakdown below RF Low
        if state["breakdown_candle"] is None:
            if latest_bar.close < rfl:
                state["breakdown_candle"] = latest_bar
            return None

        # 3. Step B: Reclaim inside RF Range
        else:
            if latest_bar.close > rfl:
                # Reclaim confirmed!
                state["has_traded"] = True

                # Check rulebook openness
                if self.strict_rulebook_mode:
                    # In strict rulebook mode, Page 8 leaves target OPEN
                    # Emit with unresolved status metadata
                    entry_reason = "RF_UNRESOLVED_RULEBOOK_TARGET"
                else:
                    entry_reason = "RF_CANONICAL_RECLAIM_LONG"

                ref_price = latest_bar.close
                target = round(ref_price + self.target_points, 2)

                # Stop loss calculation
                stop_points = self.default_stop_points
                if state["breakdown_candle"] is not None:
                    breakdown_low = state["breakdown_candle"].low
                    if (ref_price - breakdown_low) > 35.0:
                        stop_points = self.max_stop_points

                stop_loss = round(ref_price - stop_points, 2)

                sig = Signal(
                    strategy_name=self.name,
                    method_id=entry_reason,
                    symbol=latest_bar.symbol,
                    direction=SignalDirection.BUY,
                    timestamp=latest_bar.exchange_time,
                    reference_price=ref_price,
                    stop_loss=stop_loss,
                    target=target,
                    reason=entry_reason,
                    metadata={
                        "method": entry_reason,
                        "target_points": self.target_points,
                        "stop_points": stop_points,
                        "is_rulebook_unresolved": self.strict_rulebook_mode,
                    },
                )
                return sig if self.validate_signal(sig, context) else None

            # If candle extends lower, keep tracking breakdown candle
            if latest_bar.close < state["breakdown_candle"].close:
                state["breakdown_candle"] = latest_bar

        return None
