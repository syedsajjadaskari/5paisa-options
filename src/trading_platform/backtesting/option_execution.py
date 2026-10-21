"""Realistic options trade execution simulator with anti-lookahead rules.

Key Execution Guardrails:
1. Anti-Lookahead: A signal confirmed on candle t close is executed strictly at candle t+1 Open.
2. Timing Verification: Asserts next_option_bar.exchange_time >= signal.timestamp.
3. Stale Quote Rejection: Checks bid/ask quotes for inverted spreads (ask < bid) or zero values.
   Falls back to conservative OHLC open + slippage if quotes are stale.
4. No Artificial Capping: Slippage on Buy is NEVER capped down to bar High (which would artificially
   improve backtest profitability). Slippage is strictly paid.
5. Missing-Price Guard: Rejects execution if next bar option candle is absent or non-positive.
6. Complete Audit Trail: Records every order, fill, slippage deduction, and rejection reason.
"""

from dataclasses import dataclass
from datetime import datetime

from trading_platform.backtesting.indian_options_costs import (
    IndianOptionsCostCalculator,
)
from trading_platform.core.models import Signal
from trading_platform.data.option_chain import OptionBar, OptionContractMetadata
from trading_platform.execution.options_risk_manager import OptionsRiskManager, PositionSizeResult
from trading_platform.options.pnl import OptionTradeResult, calculate_option_pnl
from trading_platform.options.premium_exits import ExitDecision


@dataclass(frozen=True)
class ExecutionAuditLog:
    """Audit record of simulated execution events."""

    timestamp: datetime
    event_type: str  # 'ENTRY_ORDER', 'ENTRY_FILL', 'REJECTED', 'EXIT_SIGNAL', 'EXIT_FILL'
    price: float
    details: str


class OptionExecutionSimulator:
    """Simulates realistic options long trades using discrete next-bar price events."""

    def __init__(
        self,
        cost_calculator: IndianOptionsCostCalculator | None = None,
        risk_manager: OptionsRiskManager | None = None,
        slippage_points: float = 0.50,
        max_bar_volume_pct: float | None = None,  # Optional liquidity cap per bar
    ) -> None:
        self.cost_calculator = cost_calculator or IndianOptionsCostCalculator(
            slippage_points_per_unit=slippage_points
        )
        self.risk_manager = risk_manager or OptionsRiskManager()
        self.slippage_points = slippage_points
        self.max_bar_volume_pct = max_bar_volume_pct
        self.audit_logs: list[ExecutionAuditLog] = []

    def simulate_entry(
        self,
        signal: Signal,
        contract: OptionContractMetadata,
        next_option_bar: OptionBar | None,
        premium_stop_pct: float | None = None,
    ) -> tuple[float | None, PositionSizeResult, str]:
        """Simulate entry on the next bar open following signal confirmation at bar close.

        Anti-Lookahead Rule:
        A signal confirmed at close of candle t enters strictly at the Open price
        of candle t+1 (plus conservative slippage or valid ask quote).
        """
        # 1. Missing Price Rejection
        if next_option_bar is None:
            reason = f"REJECTED_MISSING_PRICE: No option candle available on next bar for {contract.tradingsymbol}"
            self.audit_logs.append(ExecutionAuditLog(signal.timestamp, "REJECTED", 0.0, reason))
            return None, PositionSizeResult(False, 0, 0, 0.0, reason), reason

        # 2. Timing Alignment & Anti-Lookahead Assertion
        if next_option_bar.exchange_time < signal.timestamp:
            reason = (
                f"REJECTED_LOOKAHEAD_VIOLATION: Next bar time ({next_option_bar.exchange_time}) "
                f"is earlier than signal confirmation ({signal.timestamp})"
            )
            self.audit_logs.append(
                ExecutionAuditLog(next_option_bar.exchange_time, "REJECTED", 0.0, reason)
            )
            return None, PositionSizeResult(False, 0, 0, 0.0, reason), reason

        if next_option_bar.open <= 0:
            reason = f"REJECTED_INVALID_PRICE: Next bar open price is non-positive ({next_option_bar.open})"
            self.audit_logs.append(
                ExecutionAuditLog(next_option_bar.exchange_time, "REJECTED", 0.0, reason)
            )
            return None, PositionSizeResult(False, 0, 0, 0.0, reason), reason

        # 3. Determine Executable Fill Price with Stale Quote Handling
        is_quote_valid = False
        if next_option_bar.ask is not None and next_option_bar.ask > 0:
            if next_option_bar.bid is not None and next_option_bar.bid > next_option_bar.ask:
                # Inverted quote: stale or erroneous book
                self.audit_logs.append(
                    ExecutionAuditLog(
                        next_option_bar.exchange_time,
                        "STALE_QUOTE_FALLBACK",
                        next_option_bar.ask,
                        f"Inverted quotes (Bid {next_option_bar.bid} > Ask {next_option_bar.ask}). Falling back to OHLC.",
                    )
                )
            else:
                is_quote_valid = True

        if is_quote_valid and next_option_bar.ask is not None:
            fill_price = round(next_option_bar.ask + self.slippage_points, 2)
        else:
            # Conservative OHLC fill: Open + slippage
            # Never cap down to High, as that would artificially improve fill price
            fill_price = round(next_option_bar.open + self.slippage_points, 2)

        # 4. Risk Management & Position Sizing
        size_res = self.risk_manager.calculate_position_size(
            entry_premium=fill_price,
            lot_size=contract.lot_size,
            contract_id=contract.tradingsymbol,
            premium_stop_pct=premium_stop_pct,
        )

        if not size_res.is_approved:
            self.audit_logs.append(
                ExecutionAuditLog(
                    next_option_bar.exchange_time, "REJECTED", fill_price, size_res.reason
                )
            )
            return None, size_res, size_res.reason

        # 5. Volume / Liquidity Guardrail (if enabled)
        if (
            self.max_bar_volume_pct is not None
            and next_option_bar.volume is not None
            and next_option_bar.volume > 0
        ):
            max_qty_allowed = int(next_option_bar.volume * (self.max_bar_volume_pct / 100.0))
            if size_res.quantity > max_qty_allowed:
                reason = (
                    f"REJECTED_INSUFFICIENT_LIQUIDITY: Order size {size_res.quantity} > "
                    f"permitted {self.max_bar_volume_pct}% of bar volume ({max_qty_allowed})"
                )
                self.audit_logs.append(
                    ExecutionAuditLog(next_option_bar.exchange_time, "REJECTED", fill_price, reason)
                )
                return None, PositionSizeResult(False, 0, 0, 0.0, reason), reason

        self.risk_manager.register_position_opened(contract.tradingsymbol)
        self.audit_logs.append(
            ExecutionAuditLog(
                next_option_bar.exchange_time,
                "ENTRY_FILL",
                fill_price,
                f"Filled {size_res.quantity} units ({size_res.lots} lots) of {contract.tradingsymbol} at ₹{fill_price:.2f}",
            )
        )
        return fill_price, size_res, "APPROVED"

    def simulate_exit(
        self,
        exit_decision: ExitDecision,
        current_option_bar: OptionBar,
        entry_price: float,
        quantity: int,
        lots: int,
        contract: OptionContractMetadata,
        entry_time: datetime,
        trade_id: str,
        mfe_premium: float | None = None,
        mae_premium: float | None = None,
    ) -> OptionTradeResult:
        """Simulate trade exit and compute net financial outcome with full regulatory costs."""
        # Determine exit fill price with slippage (or Bid quote if available and valid)
        is_quote_valid = False
        if current_option_bar.bid is not None and current_option_bar.bid > 0:
            if (
                current_option_bar.ask is not None
                and current_option_bar.bid > current_option_bar.ask
            ):
                # Inverted book
                pass
            else:
                is_quote_valid = True

        if is_quote_valid and current_option_bar.bid is not None:
            exit_fill_price = max(0.05, round(current_option_bar.bid - self.slippage_points, 2))
        else:
            # Sell slippage deducted from exit decision price, floored at NSE min tick 0.05
            exit_fill_price = max(0.05, round(exit_decision.exit_price - self.slippage_points, 2))

        # Calculate time-versioned costs
        costs = self.cost_calculator.calculate_costs(
            trade_date=current_option_bar.trading_date,
            entry_premium=entry_price,
            exit_premium=exit_fill_price,
            quantity=quantity,
            apply_slippage=True,
        )

        # Calculate Net P&L and metrics
        trade_result = calculate_option_pnl(
            trade_id=trade_id,
            underlying_symbol=contract.underlying_symbol,
            expiry_date=contract.expiry_date.isoformat(),
            strike_price=contract.strike_price,
            option_type=contract.option_type,
            quantity=quantity,
            lots=lots,
            entry_time=entry_time,
            entry_premium=entry_price,
            exit_time=current_option_bar.exchange_time,
            exit_premium=exit_fill_price,
            exit_reason=exit_decision.exit_reason,
            costs=costs,
            mfe_premium=mfe_premium,
            mae_premium=mae_premium,
            is_uncertain_fill=exit_decision.is_uncertain_fill,
        )

        self.risk_manager.record_trade_completion(
            net_pnl=trade_result.net_pnl,
            contract_id=contract.tradingsymbol,
        )

        self.audit_logs.append(
            ExecutionAuditLog(
                current_option_bar.exchange_time,
                "EXIT_FILL",
                exit_fill_price,
                f"Exited {quantity} units at {exit_fill_price:.2f} ({exit_decision.exit_reason}). Net P&L: ₹{trade_result.net_pnl:.2f}",
            )
        )
        return trade_result
