"""Central Strategy Registry for dynamic strategy discovery and lifecycle management."""

from trading_platform.core.exceptions import StrategyRegistrationError
from trading_platform.strategies.base import BaseStrategy
from trading_platform.strategies.irf import IRFStrategy
from trading_platform.strategies.orb import ORBStrategy
from trading_platform.strategies.pdr import PDRStrategy
from trading_platform.strategies.pds import PDSStrategy
from trading_platform.strategies.rf import RFStrategy


class StrategyRegistry:
    """Singleton-style registry for managing strategy implementations."""

    def __init__(self) -> None:
        self._registry: dict[str, type[BaseStrategy]] = {}

    def register(self, name: str, strategy_cls: type[BaseStrategy]) -> None:
        """Register a strategy class under a unique name."""
        key = name.upper()
        if key in self._registry:
            raise StrategyRegistrationError(f"Strategy '{key}' is already registered.")
        self._registry[key] = strategy_cls

    def get(self, name: str) -> type[BaseStrategy]:
        """Retrieve strategy class by name."""
        key = name.upper()
        if key not in self._registry:
            raise StrategyRegistrationError(
                f"Strategy '{key}' is not registered. Available: {list(self._registry.keys())}"
            )
        return self._registry[key]

    def list_strategies(self) -> list[str]:
        """Return list of all registered strategy names."""
        return sorted(list(self._registry.keys()))

    def unregister(self, name: str) -> None:
        """Remove a strategy from the registry."""
        key = name.upper()
        if key in self._registry:
            del self._registry[key]


# Global default registry instance with 5 canonical strategies registered
registry = StrategyRegistry()
registry.register("IRF", IRFStrategy)
registry.register("ORB", ORBStrategy)
registry.register("PDR", PDRStrategy)
registry.register("PDS", PDSStrategy)
registry.register("RF", RFStrategy)
