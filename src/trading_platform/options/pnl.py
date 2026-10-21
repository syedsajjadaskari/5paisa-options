"""Options trade P&L calculation and performance analytics."""

from dataclasses import dataclass
from datetime import datetime

from trading_platform.backtesting.indian_options_costs import OptionTradeCostBreakdown


@dataclass(frozen=True)
class OptionTradeResult:
    """Comprehensive financial and operational result of an options round-trip trade."""

    trade_id: str
    underlying_symbol: str
    expiry_date: str
    strike_price: float
    option_type: str
    quantity: int
    lots: int
    entry_time: datetime
    entry_premium: float
    exit_time: datetime
    exit_premium: float
    exit_reason: str  # 'UNDERLYING_TARGET', 'UNDERLYING_SL', 'PREMIUM_TARGET', 'PREMIUM_SL', 'EOD_EXIT', 'EXPIRED'
    gross_pnl: float
    net_pnl: float
    capital_deployed: float
    return_on_capital_pct: float
    costs: OptionTradeCostBreakdown
    mfe_premium: float | None = None
    mae_premium: float | None = None
    is_uncertain_fill: bool = False


def calculate_option_pnl(
    trade_id: str,
    underlying_symbol: str,
    expiry_date: str,
    strike_price: float,
    option_type: str,
    quantity: int,
    lots: int,
    entry_time: datetime,
    entry_premium: float,
    exit_time: datetime,
    exit_premium: float,
    exit_reason: str,
    costs: OptionTradeCostBreakdown,
    mfe_premium: float | None = None,
    mae_premium: float | None = None,
    is_uncertain_fill: bool = False,
) -> OptionTradeResult:
    """Compute financial returns, net P&L, and capital metrics for an option trade."""
    if quantity <= 0:
        raise ValueError(f"Quantity must be positive, got {quantity}")

    capital_deployed = round(entry_premium * quantity, 2)
    gross_pnl = round((exit_premium - entry_premium) * quantity, 2)
    net_pnl = round(gross_pnl - costs.total_transaction_costs, 2)

    return_on_capital_pct = 0.0
    if capital_deployed > 0:
        return_on_capital_pct = round((net_pnl / capital_deployed) * 100.0, 2)

    return OptionTradeResult(
        trade_id=trade_id,
        underlying_symbol=underlying_symbol,
        expiry_date=expiry_date,
        strike_price=strike_price,
        option_type=option_type,
        quantity=quantity,
        lots=lots,
        entry_time=entry_time,
        entry_premium=entry_premium,
        exit_time=exit_time,
        exit_premium=exit_premium,
        exit_reason=exit_reason,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        capital_deployed=capital_deployed,
        return_on_capital_pct=return_on_capital_pct,
        costs=costs,
        mfe_premium=mfe_premium,
        mae_premium=mae_premium,
        is_uncertain_fill=is_uncertain_fill,
    )
