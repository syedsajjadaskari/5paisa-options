"""Platform exception hierarchy."""


class TradingPlatformError(Exception):
    """Base exception for all trading platform errors."""

    pass


class ConfigurationError(TradingPlatformError):
    """Raised when configuration validation or loading fails."""

    pass


class DataError(TradingPlatformError):
    """Base class for data pipeline errors."""

    pass


class DataValidationError(DataError):
    """Raised when incoming market data violates invariants."""

    pass


class MissingDataError(DataError):
    """Raised when required data is not found or empty."""

    pass


class StrategyError(TradingPlatformError):
    """Base class for strategy lifecycle errors."""

    pass


class StrategyRegistrationError(StrategyError):
    """Raised when duplicate or invalid strategy registration occurs."""

    pass


class BrokerError(TradingPlatformError):
    """Base class for broker adapter errors."""

    pass


class BrokerAuthenticationError(BrokerError):
    """Raised when broker API authentication fails or credentials are invalid."""

    pass


class LiveTradingPermissionError(BrokerError):
    """Raised when live trading action is attempted without explicit authorization."""

    pass


class RiskLimitViolationError(TradingPlatformError):
    """Raised when an order or trade violates risk controls."""

    pass
