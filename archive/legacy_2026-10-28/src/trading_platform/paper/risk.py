"""Institutional Risk Management and Position Sizing Engine for NIFTY Options Paper Trading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from trading_platform.paper.portfolio import VirtualPortfolio

TZ_IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class RiskCheckResult:
    """Outcome of risk manager pre-trade validation."""

    is_approved: bool
    permitted_lots: int
    quantity: int
    capital_required: float
    reason: str


class PaperRiskManager:
    """Enforces institutional limits: 20% premium allocation cap, 2-lot ceiling, 2% daily loss limit."""

    def __init__(
        self,
        starting_capital: float = 250000.0,
        max_lots_per_trade: int = 2,
        max_open_positions: int = 1,
        max_allocation_pct: float = 20.0,
        daily_loss_limit_pct: float = 2.0,
        entry_cutoff_time: dt_time = dt_time(14, 30),
        square_off_time: dt_time = dt_time(15, 15),
        max_quote_age_seconds: float = 5.0,
        max_spread_pct: float = 2.0,
        stop_after_daily_limit: bool = True,
    ) -> None:
        self.starting_capital = starting_capital
        self.max_lots_per_trade = max_lots_per_trade
        self.max_open_positions = max_open_positions
        self.max_allocation_pct = max_allocation_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.entry_cutoff_time = entry_cutoff_time
        self.square_off_time = square_off_time
        self.max_quote_age_seconds = max_quote_age_seconds
        self.max_spread_pct = max_spread_pct
        self.stop_after_daily_limit = stop_after_daily_limit

        self.daily_realized_loss: float = 0.0
        self.consecutive_losses: int = 0
        self.kill_switch_active: bool = False

    @property
    def max_daily_loss_allowed_inr(self) -> float:
        """Maximum daily loss allowed in INR (e.g. 2% of starting capital = ₹5,000 on ₹250k)."""
        return round((self.daily_loss_limit_pct / 100.0) * self.starting_capital, 2)

    def trigger_kill_switch(self, reason: str = "MANUAL_TRIGGER") -> None:
        """Engage emergency kill switch to block new entries and flag square-off."""
        self.kill_switch_active = True

    def reset_daily_metrics(self) -> None:
        """Reset daily tracking metrics at session open."""
        self.daily_realized_loss = 0.0
        self.consecutive_losses = 0
        self.kill_switch_active = False

    def record_trade_exit(self, net_pnl: float) -> None:
        """Update risk metrics upon trade completion."""
        if net_pnl < 0:
            self.daily_realized_loss += abs(net_pnl)
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def calculate_position_size(
        self,
        premium_ask: float,
        lot_size: int,
        portfolio: VirtualPortfolio,
        check_time: datetime | None = None,
    ) -> RiskCheckResult:
        """Calculate permitted lot size under all risk, capital, and allocation constraints.

        Constraints:
        1. Kill Switch: No new entries if engaged.
        2. Daily Loss Limit: Rejects if realized loss >= 2.0% (₹5,000).
        3. Session Cutoff: Rejects if time >= 14:30 IST.
        4. Concurrency: Rejects if an open position already exists.
        5. Capital & Allocation: Rejects if even 1 lot exceeds available cash or 20% allocation.
        6. Lot Sizing: Evaluates whether 2 lots fit within 20% allocation cap and available cash.
        """
        now = (check_time or datetime.now(TZ_IST)).astimezone(TZ_IST)
        t_now = now.time()

        # 1. Kill Switch Check
        if self.kill_switch_active:
            return RiskCheckResult(False, 0, 0, 0.0, "REJECTED_KILL_SWITCH: Kill switch is active")

        # 2. Daily Loss Limit Check
        if self.stop_after_daily_limit and self.daily_realized_loss >= self.max_daily_loss_allowed_inr:
            return RiskCheckResult(
                False,
                0,
                0,
                0.0,
                f"REJECTED_DAILY_LOSS_LIMIT: Realized loss ₹{self.daily_realized_loss:.2f} >= limit ₹{self.max_daily_loss_allowed_inr:.2f}",
            )

        # 3. Session Time Cutoff (14:30 IST)
        if t_now >= self.entry_cutoff_time:
            return RiskCheckResult(
                False,
                0,
                0,
                0.0,
                f"REJECTED_ENTRY_CUTOFF: Current time {t_now.strftime('%H:%M')} >= cutoff {self.entry_cutoff_time.strftime('%H:%M')}",
            )

        # 4. Position Concurrency Check (Max 1 Open Position)
        if portfolio.active_position is not None:
            return RiskCheckResult(
                False,
                0,
                0,
                0.0,
                "REJECTED_POSITION_CONCURRENCY: Maximum 1 concurrent position allowed",
            )

        if premium_ask <= 0:
            return RiskCheckResult(False, 0, 0, 0.0, "REJECTED_INVALID_PREMIUM: Ask quote non-positive")

        if lot_size <= 0:
            return RiskCheckResult(False, 0, 0, 0.0, f"REJECTED_INVALID_LOT_SIZE: Lot size {lot_size} <= 0")

        # 5. Position Sizing & Allocation Check
        cost_per_lot = premium_ask * lot_size
        max_allowed_allocation = portfolio.max_allowed_allocation_inr  # 20% of equity

        # Check 1 lot affordability
        if cost_per_lot > portfolio.available_cash:
            return RiskCheckResult(
                False,
                0,
                0,
                0.0,
                f"REJECTED_INSUFFICIENT_CASH: 1 lot cost ₹{cost_per_lot:.2f} > cash ₹{portfolio.available_cash:.2f}",
            )

        if cost_per_lot > max_allowed_allocation:
            return RiskCheckResult(
                False,
                0,
                0,
                0.0,
                f"REJECTED_ALLOCATION_LIMIT: 1 lot cost ₹{cost_per_lot:.2f} > 20% cap ₹{max_allowed_allocation:.2f}",
            )

        # Sizing: check if 2 lots are affordable within limits
        cost_2_lots = cost_per_lot * 2.0
        if (
            self.max_lots_per_trade >= 2
            and cost_2_lots <= max_allowed_allocation
            and cost_2_lots <= portfolio.available_cash
        ):
            chosen_lots = 2
            total_cost = cost_2_lots
        else:
            chosen_lots = 1
            total_cost = cost_per_lot

        total_qty = chosen_lots * lot_size

        return RiskCheckResult(
            is_approved=True,
            permitted_lots=chosen_lots,
            quantity=total_qty,
            capital_required=round(total_cost, 2),
            reason="APPROVED",
        )
