"""High-fidelity Paper Broker for simulated order matching."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from trading_platform.brokers.base import BaseBroker
from trading_platform.core.enums import OperationalMode, OrderSide, OrderStatus
from trading_platform.core.models import Fill, Order, Position


class PaperBroker(BaseBroker):
    """Simulated execution broker adapter."""

    def __init__(
        self,
        initial_balance: float = 500000.0,
        slippage_points: float = 1.0,
        brokerage_per_order: float = 20.0,
    ) -> None:
        super().__init__(mode=OperationalMode.PAPER, live_approved=False)
        self.initial_balance = initial_balance
        self.current_cash = initial_balance
        self.slippage_points = slippage_points
        self.brokerage_per_order = brokerage_per_order

        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._fills: list[Fill] = []

    def connect(self) -> bool:
        """Paper broker connection is always instant and offline-capable."""
        self.is_connected = True
        return True

    def disconnect(self) -> None:
        """Disconnect simulated session."""
        self.is_connected = False

    def place_order(self, order: Order) -> Order:
        """Simulate order acceptance and immediate execution at simulated fill price."""
        if not self.is_connected:
            self.connect()

        # Simulate execution price
        base_price = order.price if order.price else 25000.0  # Default reference if market order
        if order.side == OrderSide.BUY:
            fill_price = base_price + self.slippage_points
        else:
            fill_price = base_price - self.slippage_points

        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.average_fill_price = fill_price
        order.updated_at = datetime.now(ZoneInfo("UTC"))

        self._orders[order.order_id] = order

        # Record Fill
        fill = Fill(
            fill_id=f"FILL_{order.order_id}",
            order_id=order.order_id,
            timestamp=order.updated_at,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            slippage=self.slippage_points,
            brokerage=self.brokerage_per_order,
            statutory_charges=15.0,  # Approximate charges
        )
        self._fills.append(fill)

        # Update Position
        symbol = order.symbol
        existing = self._positions.get(symbol, Position(symbol=symbol))
        if order.side == OrderSide.BUY:
            new_qty = existing.quantity + order.quantity
        else:
            new_qty = existing.quantity - order.quantity

        existing.quantity = new_qty
        existing.average_entry_price = fill_price
        existing.current_market_price = fill_price
        self._positions[symbol] = existing

        # Deduct brokerage from cash
        self.current_cash -= self.brokerage_per_order + 15.0
        return order

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

    def get_orders(self) -> list[Order]:
        """Return all placed orders."""
        return list(self._orders.values())

    def get_margins(self) -> dict[str, Any]:
        """Return virtual account balances."""
        return {
            "initial_balance": self.initial_balance,
            "available_cash": self.current_cash,
            "currency": "INR",
            "mode": "PAPER",
        }
