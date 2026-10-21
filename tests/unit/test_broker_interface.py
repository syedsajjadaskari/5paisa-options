"""Unit tests for Broker interfaces and safeguards."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading_platform.brokers.base import BaseBroker
from trading_platform.brokers.paper_broker import PaperBroker
from trading_platform.core.enums import OperationalMode, OrderSide, OrderStatus, OrderType
from trading_platform.core.exceptions import LiveTradingPermissionError
from trading_platform.core.models import Order


class MockLiveBroker(BaseBroker):
    def connect(self) -> bool:
        self.is_connected = True
        return True

    def disconnect(self) -> None:
        self.is_connected = False

    def place_order(self, order: Order) -> Order:
        self.assert_live_allowed()
        return order

    def cancel_order(self, order_id: str) -> bool:
        self.assert_live_allowed()
        return True

    def get_positions(self):
        return []

    def get_orders(self):
        return []

    def get_margins(self):
        return {}


def test_paper_broker_execution():
    broker = PaperBroker(initial_balance=100000.0, slippage_points=1.5)
    broker.connect()
    assert broker.is_connected is True

    order = Order(
        order_id="ORD_101",
        client_order_id="CL_101",
        symbol="NIFTY 50",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=25,
        price=24800.0,
        created_at=datetime.now(ZoneInfo("UTC")),
        updated_at=datetime.now(ZoneInfo("UTC")),
    )

    executed = broker.place_order(order)
    assert executed.status == OrderStatus.FILLED
    assert executed.filled_quantity == 25
    assert executed.average_fill_price == 24800.0 + 1.5  # Slippage added on BUY

    # Verify positions and cash balance
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 25

    margins = broker.get_margins()
    assert margins["available_cash"] < 100000.0  # Brokerage and statutory charges deducted


def test_live_broker_blocks_unauthorized_execution():
    # Attempting to place an order when mode is RESEARCH/PAPER must raise LiveTradingPermissionError
    broker = MockLiveBroker(mode=OperationalMode.RESEARCH, live_approved=False)
    order = Order(
        order_id="ORD_102",
        client_order_id="CL_102",
        symbol="NIFTY 50",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=25,
        price=24800.0,
        created_at=datetime.now(ZoneInfo("UTC")),
        updated_at=datetime.now(ZoneInfo("UTC")),
    )
    with pytest.raises(LiveTradingPermissionError):
        broker.place_order(order)
