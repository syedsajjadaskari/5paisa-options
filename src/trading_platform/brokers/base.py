"""Standard Broker Interface with strict multi-layer live safeguards."""

from abc import ABC, abstractmethod
from typing import Any

from trading_platform.core.enums import OperationalMode
from trading_platform.core.exceptions import LiveTradingPermissionError
from trading_platform.core.models import Order, Position


class BaseBroker(ABC):
    """Abstract broker interface."""

    def __init__(
        self,
        mode: OperationalMode = OperationalMode.PAPER,
        live_approved: bool = False,
    ) -> None:
        self.mode = mode
        self.live_approved = live_approved
        self.is_connected: bool = False

    def assert_live_allowed(self) -> None:
        """Enforce strict gatekeeper preventing inadvertent live order submission."""
        if self.mode != OperationalMode.LIVE or not self.live_approved:
            raise LiveTradingPermissionError(
                f"Live order execution is strictly prohibited. "
                f"Current Mode: {self.mode}, Live Approved: {self.live_approved}. "
                f"Default operational mode is strictly PAPER."
            )

    @abstractmethod
    def connect(self) -> bool:
        """Establish session / authentication."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Terminate session."""
        pass

    @abstractmethod
    def place_order(self, order: Order) -> Order:
        """Submit order to broker or simulation."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel pending order."""
        pass

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Fetch current active positions."""
        pass

    @abstractmethod
    def get_orders(self) -> list[Order]:
        """Fetch current order book."""
        pass

    @abstractmethod
    def get_margins(self) -> dict[str, Any]:
        """Fetch available cash and margin balance."""
        pass
