"""High-fidelity Paper Broker for simulated order matching with Indian options cost models and strict safety safeguards."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_platform.backtesting.indian_options_costs import IndianOptionsCostCalculator
from trading_platform.brokers.base import BaseBroker
from trading_platform.core.enums import OperationalMode, OrderSide, OrderStatus
from trading_platform.core.exceptions import LiveTradingPermissionError
from trading_platform.core.models import Fill, Order, Position


class PaperBroker(BaseBroker):
    """Simulated execution broker adapter strictly restricted to paper trading."""

    def __init__(
        self,
        initial_balance: float = 100000.0,
        slippage_points: float = 0.50,
        brokerage_per_order: float = 20.0,
        cost_calculator: IndianOptionsCostCalculator | None = None,
    ) -> None:
        super().__init__(mode=OperationalMode.PAPER, live_approved=False)
        self.initial_balance = float(initial_balance)
        self.current_cash = float(initial_balance)
        self.slippage_points = float(slippage_points)
        self.brokerage_per_order = float(brokerage_per_order)
        self.cost_calculator = cost_calculator or IndianOptionsCostCalculator(
            brokerage_per_order=self.brokerage_per_order,
            slippage_points_per_unit=self.slippage_points,
        )

        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._fills: list[Fill] = []
        self._total_statutory_charges_paid: float = 0.0
        self._total_brokerage_paid: float = 0.0

    def assert_live_allowed(self) -> None:
        """Paper broker permanently forbids live order execution."""
        raise LiveTradingPermissionError(
            "PaperBroker is strictly isolated for paper simulation. "
            "Live order submission or passthrough is prohibited under all circumstances."
        )

    def connect(self) -> bool:
        """Paper broker connection is instantaneous and offline-capable."""
        self.is_connected = True
        return True

    def disconnect(self) -> None:
        """Disconnect simulated session."""
        self.is_connected = False

    def place_order(self, order: Order) -> Order:
        """Fallback order placement for underlying or generic test orders."""
        if not self.is_connected:
            self.connect()

        base_price = order.price if order.price and order.price > 0 else 100.0
        effective_slippage = self.slippage_points
        if order.side == OrderSide.BUY:
            fill_price = round(base_price + effective_slippage, 2)
        else:
            fill_price = round(max(0.05, base_price - effective_slippage), 2)

        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.average_fill_price = fill_price
        order.updated_at = datetime.now(ZoneInfo("UTC"))
        self._orders[order.order_id] = order

        charges = round(self.brokerage_per_order + 15.0, 2)
        self.current_cash = round(self.current_cash - charges, 2)
        self._total_brokerage_paid += self.brokerage_per_order
        self._total_statutory_charges_paid += 15.0

        fill = Fill(
            fill_id=f"FILL_{order.order_id}",
            order_id=order.order_id,
            timestamp=order.updated_at,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            slippage=effective_slippage,
            brokerage=self.brokerage_per_order,
            statutory_charges=15.0,
        )
        self._fills.append(fill)

        symbol = order.symbol
        existing = self._positions.get(symbol, Position(symbol=symbol))
        if order.side == OrderSide.BUY:
            total_qty = existing.quantity + order.quantity
            if total_qty > 0:
                weighted_entry = (
                    (existing.quantity * existing.average_entry_price)
                    + (order.quantity * fill_price)
                ) / total_qty
                existing.average_entry_price = round(weighted_entry, 2)
            existing.quantity = total_qty
            existing.current_market_price = fill_price
            existing.strategy_name = order.strategy_name
        else:
            new_qty = existing.quantity - order.quantity
            closed_qty = min(existing.quantity, order.quantity)
            if closed_qty > 0:
                trade_pnl = (fill_price - existing.average_entry_price) * closed_qty
                existing.realized_pnl = round(existing.realized_pnl + trade_pnl, 2)
            existing.quantity = new_qty
            existing.current_market_price = fill_price

        self._positions[symbol] = existing
        return order

    def execute_option_order(
        self,
        order: Order,
        bid: float | None = None,
        ask: float | None = None,
        trade_date: date | None = None,
        slippage_points: float | None = None,
    ) -> Order:
        """Simulate realistic options order execution matching against live Level 2 quote depth.

        Rules:
        - Buy Order: Matches against the best executable Ask + slippage.
        - Sell Order: Matches against the best executable Bid - slippage.
        - If required quote is missing or non-positive: Rejects order with NO_EXECUTABLE_QUOTE.
        - Computes statutory Indian taxes (STT, GST, Stamp Duty, Exchange/SEBI fees, 5paisa brokerage).
        """
        if not self.is_connected:
            self.connect()

        t_date = trade_date or datetime.now(ZoneInfo("Asia/Kolkata")).date()
        effective_slippage = (
            slippage_points if slippage_points is not None else self.slippage_points
        )

        order.updated_at = datetime.now(ZoneInfo("UTC"))

        # 1. Price Matching against Depth
        if order.side == OrderSide.BUY:
            if ask is None or ask <= 0:
                order.status = OrderStatus.REJECTED
                self._orders[order.order_id] = order
                return order

            fill_price = round(ask + effective_slippage, 2)
            leg_cost = self.cost_calculator.calculate_single_leg_costs(
                trade_date=t_date,
                side="BUY",
                premium=fill_price,
                quantity=order.quantity,
                apply_slippage=False,  # Slippage is already baked into fill_price
            )
            required_cash = round((fill_price * order.quantity) + leg_cost.total_charges, 2)

            # Capital sufficiency check
            if required_cash > self.current_cash:
                order.status = OrderStatus.REJECTED
                self._orders[order.order_id] = order
                return order

            # Deduct cash
            self.current_cash = round(self.current_cash - required_cash, 2)

        elif order.side == OrderSide.SELL:
            if bid is None or bid <= 0:
                order.status = OrderStatus.REJECTED
                self._orders[order.order_id] = order
                return order

            fill_price = round(max(0.05, bid - effective_slippage), 2)
            leg_cost = self.cost_calculator.calculate_single_leg_costs(
                trade_date=t_date,
                side="SELL",
                premium=fill_price,
                quantity=order.quantity,
                apply_slippage=False,
            )
            gross_proceeds = fill_price * order.quantity
            net_proceeds = round(gross_proceeds - leg_cost.total_charges, 2)

            # Credit cash
            self.current_cash = round(self.current_cash + net_proceeds, 2)

        else:
            order.status = OrderStatus.REJECTED
            self._orders[order.order_id] = order
            return order

        # Order filled
        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.average_fill_price = fill_price
        self._orders[order.order_id] = order

        statutory_only = round(leg_cost.total_charges - leg_cost.brokerage, 2)
        self._total_brokerage_paid += leg_cost.brokerage
        self._total_statutory_charges_paid += statutory_only

        # Record Fill
        fill = Fill(
            fill_id=f"FILL_{order.order_id}",
            order_id=order.order_id,
            timestamp=order.updated_at,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            slippage=effective_slippage,
            brokerage=leg_cost.brokerage,
            statutory_charges=statutory_only,
        )
        self._fills.append(fill)

        # Update Position
        symbol = order.symbol
        existing = self._positions.get(symbol, Position(symbol=symbol))

        if order.side == OrderSide.BUY:
            total_qty = existing.quantity + order.quantity
            if total_qty > 0:
                weighted_entry = (
                    (existing.quantity * existing.average_entry_price)
                    + (order.quantity * fill_price)
                ) / total_qty
                existing.average_entry_price = round(weighted_entry, 2)
            existing.quantity = total_qty
            existing.current_market_price = fill_price
            existing.strategy_name = order.strategy_name
        else:
            # Sell reduces or closes long position
            new_qty = existing.quantity - order.quantity
            closed_qty = min(existing.quantity, order.quantity)
            if closed_qty > 0:
                trade_pnl = (fill_price - existing.average_entry_price) * closed_qty
                existing.realized_pnl = round(existing.realized_pnl + trade_pnl, 2)
            existing.quantity = new_qty
            existing.current_market_price = fill_price

        self._positions[symbol] = existing
        return order

    def update_market_price(self, symbol: str, current_price: float) -> None:
        """Update mark-to-market prices and unrealized P&L on open positions."""
        if symbol in self._positions:
            pos = self._positions[symbol]
            pos.current_market_price = current_price
            if pos.quantity != 0:
                pos.unrealized_pnl = round(
                    (current_price - pos.average_entry_price) * pos.quantity, 2
                )
            else:
                pos.unrealized_pnl = 0.0

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending paper order."""
        if order_id in self._orders:
            order = self._orders[order_id]
            if order.status in (OrderStatus.PENDING_SUBMIT, OrderStatus.SUBMITTED):
                order.status = OrderStatus.CANCELLED
                return True
        return False

    def get_positions(self) -> list[Position]:
        """Return active positions."""
        return list(self._positions.values())

    def get_open_positions(self) -> list[Position]:
        """Return positions with non-zero quantity."""
        return [p for p in self._positions.values() if p.quantity != 0]

    def get_orders(self) -> list[Order]:
        """Return all placed orders."""
        return list(self._orders.values())

    def get_fills(self) -> list[Fill]:
        """Return all executed fills."""
        return list(self._fills)

    def get_margins(self) -> dict[str, Any]:
        """Return virtual account balances and P&L metrics."""
        open_positions = self.get_open_positions()
        unrealized_total = sum(p.unrealized_pnl for p in open_positions)
        realized_total = sum(p.realized_pnl for p in self._positions.values())
        invested_capital = sum(p.average_entry_price * p.quantity for p in open_positions)
        total_equity = round(self.current_cash + invested_capital + unrealized_total, 2)

        return {
            "initial_balance": self.initial_balance,
            "available_cash": self.current_cash,
            "invested_capital": round(invested_capital, 2),
            "unrealized_pnl": round(unrealized_total, 2),
            "realized_pnl": round(realized_total, 2),
            "total_equity": total_equity,
            "total_brokerage_paid": round(self._total_brokerage_paid, 2),
            "total_statutory_paid": round(self._total_statutory_charges_paid, 2),
            "currency": "INR",
            "mode": "PAPER_ONLY",
        }

    def save_state(self, filepath: str | Path) -> None:
        """Persist state to JSON for deterministic restart recovery."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "initial_balance": self.initial_balance,
            "current_cash": self.current_cash,
            "slippage_points": self.slippage_points,
            "brokerage_per_order": self.brokerage_per_order,
            "total_brokerage_paid": self._total_brokerage_paid,
            "total_statutory_charges_paid": self._total_statutory_charges_paid,
            "orders": {k: v.model_dump(mode="json") for k, v in self._orders.items()},
            "positions": {k: v.model_dump(mode="json") for k, v in self._positions.items()},
            "fills": [f.model_dump(mode="json") for f in self._fills],
            "saved_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_state(self, filepath: str | Path) -> bool:
        """Restore state from persisted JSON."""
        path = Path(filepath)
        if not path.exists():
            return False

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            self.initial_balance = float(data.get("initial_balance", self.initial_balance))
            self.current_cash = float(data.get("current_cash", self.current_cash))
            self.slippage_points = float(data.get("slippage_points", self.slippage_points))
            self.brokerage_per_order = float(
                data.get("brokerage_per_order", self.brokerage_per_order)
            )
            self._total_brokerage_paid = float(data.get("total_brokerage_paid", 0.0))
            self._total_statutory_charges_paid = float(
                data.get("total_statutory_charges_paid", 0.0)
            )

            self._orders = {k: Order(**v) for k, v in data.get("orders", {}).items()}
            self._positions = {k: Position(**v) for k, v in data.get("positions", {}).items()}
            self._fills = [Fill(**f) for f in data.get("fills", [])]
            return True
        except Exception:
            return False
