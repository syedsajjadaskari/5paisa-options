"""Virtual Portfolio Tracker with ₹250,000 capital accounting and 20% premium allocation limit."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ_IST = ZoneInfo("Asia/Kolkata")


@dataclass
class ActivePositionRecord:
    """Active option contract position details."""

    symbol: str
    scrip_code: int
    option_type: str  # 'CE' or 'PE'
    strike: float
    expiry: str
    lot_size: int
    lots: int
    quantity: int
    entry_premium: float
    entry_time: str
    underlying_entry_spot: float
    underlying_stop_loss: float
    underlying_target: float
    strategy_name: str
    method_id: str
    direction: str
    order_id: str
    current_market_price: float = 0.0
    unrealized_pnl: float = 0.0
    entry_charges: float = 0.0


class VirtualPortfolio:
    """Manages virtual account equity, cash balances, and position allocation."""

    ACCOUNT_NAMESPACE = "PAPER_VIRTUAL"

    def __init__(
        self,
        starting_capital: float = 250000.0,
        max_allocation_pct: float = 20.0,
    ) -> None:
        self.starting_capital = float(starting_capital)
        self.available_cash = float(starting_capital)
        self.max_allocation_pct = float(max_allocation_pct)
        self.invested_capital = 0.0
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.peak_equity = float(starting_capital)
        self.active_position: ActivePositionRecord | None = None
        self.trade_history: list[dict[str, Any]] = []
        self._loaded_state_generation: str | None = None

    @property
    def total_equity(self) -> float:
        """Total marked-to-market account equity."""
        return round(self.available_cash + self.invested_capital + self.unrealized_pnl, 2)

    @property
    def daily_drawdown_pct(self) -> float:
        """Peak-to-trough equity drawdown percentage."""
        current_eq = self.total_equity
        if current_eq > self.peak_equity:
            self.peak_equity = current_eq
        if self.peak_equity > 0:
            return round(((self.peak_equity - current_eq) / self.peak_equity) * 100.0, 2)
        return 0.0

    @property
    def max_allowed_allocation_inr(self) -> float:
        """Maximum INR that can be committed to a single option position (e.g. 20% of equity)."""
        return round((self.max_allocation_pct / 100.0) * self.total_equity, 2)

    def check_allocation_capacity(self, required_cash: float) -> tuple[bool, str]:
        """Validate if required position premium satisfies the 20% allocation cap and available cash."""
        cap = self.max_allowed_allocation_inr
        if required_cash > self.available_cash:
            return (
                False,
                f"REJECTED_INSUFFICIENT_CASH: Required ₹{required_cash:.2f} > Available ₹{self.available_cash:.2f}",
            )
        if required_cash > cap:
            return (
                False,
                f"REJECTED_ALLOCATION_LIMIT: Required ₹{required_cash:.2f} > Max 20% Allocation ₹{cap:.2f}",
            )
        return True, "APPROVED"

    def open_position(
        self,
        contract: ActivePositionRecord,
        entry_fill_price: float,
        total_entry_charges: float,
    ) -> None:
        """Register newly opened long option position and deduct capital."""
        required_premium = round(entry_fill_price * contract.quantity, 2)
        total_deduction = round(required_premium + total_entry_charges, 2)

        self.available_cash = round(self.available_cash - total_deduction, 2)
        self.invested_capital = round(self.invested_capital + required_premium, 2)
        contract.current_market_price = entry_fill_price
        contract.unrealized_pnl = 0.0
        contract.entry_charges = round(total_entry_charges, 2)
        self.active_position = contract

        # Update peak equity
        if self.total_equity > self.peak_equity:
            self.peak_equity = self.total_equity

    def close_position(
        self,
        exit_fill_price: float,
        total_exit_charges: float,
    ) -> dict[str, Any] | None:
        """Close active position, credit proceeds, and calculate realized P&L."""
        if self.active_position is None:
            return None

        pos = self.active_position
        gross_proceeds = round(exit_fill_price * pos.quantity, 2)
        net_proceeds = round(gross_proceeds - total_exit_charges, 2)

        # Net PnL = Proceeds - Invested Capital
        pos_pnl = round(
            gross_proceeds
            - self.invested_capital
            - pos.entry_charges
            - total_exit_charges,
            2,
        )

        self.available_cash = round(self.available_cash + net_proceeds, 2)
        self.realized_pnl = round(self.realized_pnl + pos_pnl, 2)
        self.invested_capital = 0.0
        self.unrealized_pnl = 0.0

        trade_summary = {
            "symbol": pos.symbol,
            "quantity": pos.quantity,
            "entry_premium": pos.entry_premium,
            "exit_premium": exit_fill_price,
            "realized_pnl": pos_pnl,
            "closed_at": datetime.now(TZ_IST).isoformat(),
        }
        self.trade_history.append(trade_summary)
        self.active_position = None

        if self.total_equity > self.peak_equity:
            self.peak_equity = self.total_equity

        return trade_summary

    def update_mtm(self, current_premium: float) -> None:
        """Update mark-to-market valuation on active position."""
        if self.active_position is not None and current_premium > 0:
            self.active_position.current_market_price = current_premium
            current_value = round(current_premium * self.active_position.quantity, 2)
            self.unrealized_pnl = round(current_value - self.invested_capital, 2)
            self.active_position.unrealized_pnl = self.unrealized_pnl
        else:
            self.unrealized_pnl = 0.0

    def save_state(
        self, filepath: str | Path, state_generation: str | None = None
    ) -> None:
        """Persist portfolio state to JSON."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "account_namespace": self.ACCOUNT_NAMESPACE,
            "funding_source": "CONFIGURED_SIMULATION_CAPITAL",
            "starting_capital": self.starting_capital,
            "available_cash": self.available_cash,
            "invested_capital": self.invested_capital,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "peak_equity": self.peak_equity,
            "active_position": self.active_position.__dict__ if self.active_position else None,
            "saved_at": datetime.now(TZ_IST).isoformat(),
            "state_generation": state_generation,
        }
        tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}_{uuid.uuid4().hex[:6]}")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)

    def load_state(self, filepath: str | Path) -> bool:
        """Restore portfolio state from JSON."""
        path = Path(filepath)
        if not path.exists():
            return False

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            if data.get("account_namespace") != self.ACCOUNT_NAMESPACE:
                return False

            self.starting_capital = float(data.get("starting_capital", self.starting_capital))
            self.available_cash = float(data.get("available_cash", self.available_cash))
            self.invested_capital = float(data.get("invested_capital", 0.0))
            self.realized_pnl = float(data.get("realized_pnl", 0.0))
            self.unrealized_pnl = float(data.get("unrealized_pnl", 0.0))
            self.peak_equity = float(data.get("peak_equity", self.starting_capital))

            pos_data = data.get("active_position")
            if pos_data:
                self.active_position = ActivePositionRecord(**pos_data)
            else:
                self.active_position = None
            self._loaded_state_generation = data.get("state_generation")
            return True
        except Exception:
            return False
