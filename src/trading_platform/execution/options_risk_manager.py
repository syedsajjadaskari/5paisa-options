"""Centralized Options Risk Management and Position Sizing Engine."""

from dataclasses import dataclass

from trading_platform.core.exceptions import RiskLimitViolationError


@dataclass(frozen=True)
class OptionsRiskPolicy:
    """Configurable risk parameters for option buying strategies."""

    initial_capital: float = 100000.0
    max_position_capital_pct: float = 10.0  # Max 10% capital in single trade
    max_risk_per_trade_pct: float = 1.0  # Max 1% portfolio risk per trade
    max_daily_loss_pct: float = 2.0  # Max 2% loss per day
    max_open_positions: int = 1
    no_averaging_down: bool = True


@dataclass(frozen=True)
class PositionSizeResult:
    """Calculated position sizing outcome."""

    is_approved: bool
    lots: int
    quantity: int
    capital_required: float
    reason: str


class OptionsRiskManager:
    """Enforces institutional portfolio risk limits and sizing for options trades."""

    def __init__(self, policy: OptionsRiskPolicy | None = None) -> None:
        self.policy = policy or OptionsRiskPolicy()
        self.current_capital = self.policy.initial_capital
        self.daily_realized_loss = 0.0
        self.active_positions_count = 0
        self._open_contracts: set[str] = set()

    def reset_daily_stats(self) -> None:
        """Reset daily tracking metrics at session open."""
        self.daily_realized_loss = 0.0
        self.active_positions_count = 0
        self._open_contracts.clear()

    def record_trade_completion(self, net_pnl: float, contract_id: str) -> None:
        """Update risk metrics upon trade exit."""
        self.current_capital += net_pnl
        if net_pnl < 0:
            self.daily_realized_loss += abs(net_pnl)

        if contract_id in self._open_contracts:
            self._open_contracts.remove(contract_id)
            self.active_positions_count = max(0, self.active_positions_count - 1)

    def calculate_position_size(
        self,
        entry_premium: float,
        lot_size: int,
        contract_id: str,
        premium_stop_pct: float | None = None,
    ) -> PositionSizeResult:
        """Determine approved lot quantity under capital and risk constraints.

        Critical Distinction:
        - For underlying-based stops, delta is non-linear; the entire premium deployed
          represents maximum capital at risk.
        - For premium-based stops, risk is defined as Premium * premium_stop_pct.
        """
        if entry_premium <= 0:
            return PositionSizeResult(False, 0, 0, 0.0, "REJECTED_ZERO_PREMIUM")

        if lot_size <= 0:
            return PositionSizeResult(False, 0, 0, 0.0, f"REJECTED_INVALID_LOT_SIZE: {lot_size}")

        cost_per_lot = entry_premium * lot_size

        # 0. Check Absolute Capital Sufficiency
        if cost_per_lot > self.current_capital:
            return PositionSizeResult(
                False,
                0,
                0,
                0.0,
                f"REJECTED_INSUFFICIENT_CAPITAL: Cost per lot ₹{cost_per_lot:.2f} > account capital ₹{self.current_capital:.2f}",
            )

        # 1. Check Daily Loss Limit
        max_daily_allowed = (self.policy.max_daily_loss_pct / 100.0) * self.policy.initial_capital
        if self.daily_realized_loss >= max_daily_allowed:
            return PositionSizeResult(
                False,
                0,
                0,
                0.0,
                f"REJECTED_MAX_DAILY_LOSS: Realized loss ₹{self.daily_realized_loss:.2f} >= limit ₹{max_daily_allowed:.2f}",
            )

        # 2. Check Open Position Limit
        if self.active_positions_count >= self.policy.max_open_positions:
            return PositionSizeResult(
                False,
                0,
                0,
                0.0,
                f"REJECTED_MAX_OPEN_POSITIONS: Active {self.active_positions_count} >= limit {self.policy.max_open_positions}",
            )

        # 3. Check No Averaging Down
        if self.policy.no_averaging_down and contract_id in self._open_contracts:
            return PositionSizeResult(
                False,
                0,
                0,
                0.0,
                f"REJECTED_AVERAGING_DOWN: Position for {contract_id} is already open",
            )

        # 4. Sizing by Maximum Capital Allocation
        max_alloc_capital = (self.policy.max_position_capital_pct / 100.0) * self.current_capital

        if cost_per_lot > max_alloc_capital:
            return PositionSizeResult(
                False,
                0,
                0,
                0.0,
                f"REJECTED_CAPITAL_EXCEEDED: Cost per lot ₹{cost_per_lot:.2f} > max allocation ₹{max_alloc_capital:.2f}",
            )

        lots_by_capital = int(max_alloc_capital // cost_per_lot)

        # 5. Sizing by Maximum Risk Per Trade
        lots = lots_by_capital
        if premium_stop_pct is not None and premium_stop_pct > 0:
            max_risk_inr = (self.policy.max_risk_per_trade_pct / 100.0) * self.current_capital
            risk_per_lot = cost_per_lot * premium_stop_pct
            lots_by_risk = int(max_risk_inr // risk_per_lot)
            lots = min(lots_by_capital, lots_by_risk)

        if lots < 1:
            return PositionSizeResult(
                False, 0, 0, 0.0, f"REJECTED_INSUFFICIENT_SIZE: Permitted lots {lots} < 1"
            )

        total_qty = lots * lot_size
        capital_req = cost_per_lot * lots

        return PositionSizeResult(
            is_approved=True,
            lots=lots,
            quantity=total_qty,
            capital_required=round(capital_req, 2),
            reason="APPROVED",
        )

    def register_position_opened(self, contract_id: str) -> None:
        """Mark position as active."""
        if self.active_positions_count >= self.policy.max_open_positions:
            raise RiskLimitViolationError("Cannot open position: max positions reached.")
        self.active_positions_count += 1
        self._open_contracts.add(contract_id)
