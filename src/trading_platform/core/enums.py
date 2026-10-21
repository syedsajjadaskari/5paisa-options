"""Domain enumerations for the trading platform."""

from enum import StrEnum


class Environment(StrEnum):
    """Platform operating environment."""

    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    LIVE = "LIVE"


class OperationalMode(StrEnum):
    """Broker execution mode."""

    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    LIVE = "LIVE"


class InstrumentType(StrEnum):
    """Financial instrument classification."""

    INDEX = "INDEX"
    EQUITY = "EQUITY"
    FUTURES = "FUTURES"
    OPTION_CE = "OPTCE"
    OPTION_PE = "OPTPE"


class Exchange(StrEnum):
    """Exchange identifier."""

    NSE = "NSE"
    NFO = "NFO"
    BSE = "BSE"
    MCX = "MCX"


class OrderSide(StrEnum):
    """Order transaction side."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """Order execution classification."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "SL"
    STOP_LOSS_MARKET = "SL-M"


class OrderStatus(StrEnum):
    """Lifecycle state of an order."""

    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class SignalDirection(StrEnum):
    """Strategy signal intent."""

    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"
    HOLD = "HOLD"


class StrategyName(StrEnum):
    """Supported canonical strategy identifiers."""

    IRF = "IRF"
    ORB = "ORB"
    PDR = "PDR"
    PDS = "PDS"
    RF = "RF"
    DUMMY = "DUMMY"


class StrategyStatus(StrEnum):
    """Strategy validation status."""

    RESEARCH = "RESEARCH"
    PAPER_READY = "PAPER_READY"
    LIVE_CANDIDATE = "LIVE_CANDIDATE"


class DataSource(StrEnum):
    """Market data provider source."""

    CSV = "CSV"
    PARQUET = "PARQUET"
    FIVEPAISA_HISTORICAL = "FIVEPAISA_HISTORICAL"
    FIVEPAISA_WS = "FIVEPAISA_WS"
    SYNTHETIC = "SYNTHETIC"


class Timeframe(StrEnum):
    """Supported bar timeframes."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"
