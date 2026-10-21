"""Evaluation of Underlying-Based, Premium-Based, and Hybrid option exit systems.

Key Realism & Conservative Execution Principles:
1. Anti-Lookahead: Exits are evaluated on completed bars or deterministic bar-level events.
2. Gap-Through-Stop: If a bar opens past the stop-loss level, the order is filled at the bar Open
   (or worse), NEVER at the nominal stop price.
3. Intrabar Stop Traversal: If an underlying stop triggers intrabar, the option exit is conservatively
   simulated using the lower of Open or Low to prevent optimistic close-price fills.
4. Intrabar SL/TP Conflict: If both stop-loss and profit target conditions are met within the same candle,
   the Stop-Loss takes absolute priority (worst-case assumption), flagged with `is_uncertain_fill=True`.
5. Timestamp Alignment: Asserts identical exchange timestamps between the underlying and option bar.
"""

from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar
from trading_platform.data.option_chain import OptionBar


class ExitMode(StrEnum):
    """Exit evaluation mode."""

    UNDERLYING_BASED = "UNDERLYING_BASED"
    PREMIUM_BASED = "PREMIUM_BASED"
    HYBRID = "HYBRID"


@dataclass(frozen=True)
class ExitDecision:
    """Decision outcome of exit evaluation."""

    should_exit: bool
    exit_price: float
    exit_reason: str
    is_uncertain_fill: bool = False


class ExitEvaluator:
    """Evaluates option exit conditions across Underlying, Premium, and Hybrid systems."""

    def __init__(
        self,
        mode: ExitMode | str = ExitMode.UNDERLYING_BASED,
        premium_target_pct: float | None = None,  # e.g., 0.50 (+50%)
        premium_stop_pct: float | None = None,  # e.g., 0.25 (-25%)
        eod_exit_time: time = time(15, 15, 0),
    ) -> None:
        self.mode = ExitMode(mode)
        self.premium_target_pct = premium_target_pct
        self.premium_stop_pct = premium_stop_pct
        self.eod_exit_time = eod_exit_time

    def evaluate_exit(
        self,
        underlying_bar: Bar,
        option_bar: OptionBar,
        entry_premium: float,
        signal_direction: SignalDirection | str,
        underlying_target: float,
        underlying_stop: float,
    ) -> ExitDecision:
        """Evaluate if position should exit on the current bar.

        Enforces timestamp alignment, gap-through-stop modeling, conservative intrabar
        option pricing, and SL priority during intrabar conflicts.
        """
        # 0. Timestamp Alignment Check
        if underlying_bar.exchange_time != option_bar.exchange_time:
            raise ValueError(
                f"Timestamp mismatch: underlying bar is at {underlying_bar.exchange_time}, "
                f"option bar is at {option_bar.exchange_time}"
            )

        # 1. End-Of-Day Forced Exit Check (at or after 15:15 IST)
        if option_bar.exchange_time.time() >= self.eod_exit_time:
            return ExitDecision(
                should_exit=True,
                exit_price=option_bar.open,
                exit_reason="EOD_EXIT",
            )

        sig_dir = SignalDirection(signal_direction)

        # 2. Check Underlying-Based Exit Conditions
        underlying_hit_sl = False
        underlying_hit_tp = False
        underlying_sl_price = option_bar.open

        if sig_dir == SignalDirection.BUY:  # Bullish underlying move
            if underlying_bar.low <= underlying_stop:
                underlying_hit_sl = True
                if underlying_bar.open <= underlying_stop:
                    # Gapped through stop at open: exit immediately at option open
                    underlying_sl_price = option_bar.open
                else:
                    # Intrabar drop: conservative fill at the worse price during bar
                    underlying_sl_price = min(option_bar.open, option_bar.low)
            if underlying_bar.high >= underlying_target:
                underlying_hit_tp = True

        elif sig_dir == SignalDirection.SELL:  # Bearish underlying move
            if underlying_bar.high >= underlying_stop:
                underlying_hit_sl = True
                if underlying_bar.open >= underlying_stop:
                    # Gapped through stop at open: exit immediately at option open
                    underlying_sl_price = option_bar.open
                else:
                    # Intrabar rally: conservative fill at the worse price during bar
                    underlying_sl_price = min(option_bar.open, option_bar.low)
            if underlying_bar.low <= underlying_target:
                underlying_hit_tp = True

        # 3. Check Premium-Based Exit Conditions
        premium_hit_sl = False
        premium_hit_tp = False
        prem_sl_price = 0.0
        prem_tp_price = 0.0

        if self.premium_stop_pct is not None and self.premium_stop_pct > 0:
            nominal_sl_level = entry_premium * (1.0 - self.premium_stop_pct)
            if option_bar.low <= nominal_sl_level:
                premium_hit_sl = True
                if option_bar.open <= nominal_sl_level:
                    # GAP-THROUGH-STOP: Candle opened below the stop price!
                    # Fill at the actual Open (or worse), never at the nominal stop
                    prem_sl_price = option_bar.open
                else:
                    # Intrabar touch: Stop triggered during the candle
                    prem_sl_price = nominal_sl_level

        if self.premium_target_pct is not None and self.premium_target_pct > 0:
            nominal_tp_level = entry_premium * (1.0 + self.premium_target_pct)
            if option_bar.high >= nominal_tp_level:
                premium_hit_tp = True
                # Limit order execution fills at target price (or open if gapped above)
                prem_tp_price = max(nominal_tp_level, option_bar.open)

        # 4. Mode-Specific Resolution
        if self.mode == ExitMode.UNDERLYING_BASED:
            if underlying_hit_sl and underlying_hit_tp:
                # Same-bar conflict: conservative worst-case SL priority
                return ExitDecision(
                    should_exit=True,
                    exit_price=underlying_sl_price,
                    exit_reason="UNDERLYING_SL_CONFLICT",
                    is_uncertain_fill=True,
                )
            if underlying_hit_sl:
                return ExitDecision(
                    should_exit=True,
                    exit_price=underlying_sl_price,
                    exit_reason="UNDERLYING_SL",
                )
            if underlying_hit_tp:
                return ExitDecision(
                    should_exit=True,
                    exit_price=option_bar.close,
                    exit_reason="UNDERLYING_TARGET",
                )

        elif self.mode == ExitMode.PREMIUM_BASED:
            if premium_hit_sl and premium_hit_tp:
                # Same-bar conflict: conservative worst-case SL priority
                return ExitDecision(
                    should_exit=True,
                    exit_price=prem_sl_price,
                    exit_reason="PREMIUM_SL_CONFLICT",
                    is_uncertain_fill=True,
                )
            if premium_hit_sl:
                return ExitDecision(
                    should_exit=True,
                    exit_price=prem_sl_price,
                    exit_reason="PREMIUM_SL",
                )
            if premium_hit_tp:
                return ExitDecision(
                    should_exit=True,
                    exit_price=prem_tp_price,
                    exit_reason="PREMIUM_TARGET",
                )

        elif self.mode == ExitMode.HYBRID:
            # First valid trigger exits. In conflict between SL and TP, SL takes precedence.
            if underlying_hit_sl and premium_hit_sl:
                # Both hit SL: pick the worse (lower) exit price
                exit_p = min(underlying_sl_price, prem_sl_price)
                return ExitDecision(
                    should_exit=True,
                    exit_price=exit_p,
                    exit_reason="HYBRID_SL",
                )
            if underlying_hit_sl:
                return ExitDecision(
                    should_exit=True,
                    exit_price=underlying_sl_price,
                    exit_reason="UNDERLYING_SL",
                )
            if premium_hit_sl:
                return ExitDecision(
                    should_exit=True,
                    exit_price=prem_sl_price,
                    exit_reason="PREMIUM_SL",
                )
            if underlying_hit_tp and premium_hit_tp:
                return ExitDecision(
                    should_exit=True,
                    exit_price=prem_tp_price,
                    exit_reason="HYBRID_TARGET",
                )
            if underlying_hit_tp:
                return ExitDecision(
                    should_exit=True,
                    exit_price=option_bar.close,
                    exit_reason="UNDERLYING_TARGET",
                )
            if premium_hit_tp:
                return ExitDecision(
                    should_exit=True,
                    exit_price=prem_tp_price,
                    exit_reason="PREMIUM_TARGET",
                )

        return ExitDecision(should_exit=False, exit_price=0.0, exit_reason="HOLD")
