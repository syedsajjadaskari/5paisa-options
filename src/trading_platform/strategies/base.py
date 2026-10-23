"""Base Strategy Interface and State Machine for multi-method strategies."""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from trading_platform.core.enums import SignalDirection, StrategyStatus
from trading_platform.core.models import Bar, Signal


class StrategyContext(BaseModel):
    """Execution context passed to strategies during backtesting or live execution."""

    current_time: Any | None = None
    historical_bars: list[Bar] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    active_position: Any | None = None

    def add_bar(self, bar: Bar) -> None:
        """Add latest closed bar to context history."""
        self.historical_bars.append(bar)
        self.current_time = bar.exchange_time

    @property
    def latest_bar(self) -> Bar | None:
        """Return the most recently completed bar."""
        if self.historical_bars:
            return self.historical_bars[-1]
        return None


class BaseStrategy(ABC):
    """Standardized base class for all algorithmic strategies."""

    def __init__(
        self,
        name: str,
        config: dict[str, Any] | None = None,
        status: StrategyStatus = StrategyStatus.RESEARCH,
    ) -> None:
        self.name = name
        self.status = status
        self.config: dict[str, Any] = config or {}
        self.original_parameters: dict[str, Any] = {}
        self.optimized_parameters: dict[str, Any] = {}
        self._state_machine: dict[str, Any] = {}

    @abstractmethod
    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        """Pre-calculate indicators, initialize levels, and configure parameter sets."""
        pass

    @abstractmethod
    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        """Process incoming closed bar, advance state machine, and evaluate entry methods."""
        pass

    @abstractmethod
    def generate_signal(self, context: StrategyContext) -> Signal | None:
        """Evaluate entry methods against the current context and return confirmed signal."""
        pass

    def validate_signal(self, signal: Signal, context: StrategyContext) -> bool:
        """Validate signal sanity before dispatching to risk management."""
        if signal.direction == SignalDirection.HOLD:
            return False
        if signal.stop_loss <= 0 or signal.target <= 0:
            return False
        if signal.direction == SignalDirection.BUY and signal.target <= signal.reference_price:
            return False
        if signal.direction == SignalDirection.SELL and signal.target >= signal.reference_price:
            return False
        return True

    def get_parameters(self) -> dict[str, Any]:
        """Return active strategy parameters."""
        return {
            "original": self.original_parameters,
            "optimized": self.optimized_parameters,
        }

    def reset_state(self) -> None:
        """Reset internal state machine (e.g. at start of new day or run)."""
        self._state_machine.clear()
        if hasattr(self, "_daily_state") and isinstance(self._daily_state, dict):
            self._daily_state.clear()
        if hasattr(self, "_daily_evaluated") and isinstance(self._daily_evaluated, dict):
            self._daily_evaluated.clear()
