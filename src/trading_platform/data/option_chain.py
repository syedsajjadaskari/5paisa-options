"""Option chain models, contract metadata, and option bar schemas."""

from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trading_platform.core.enums import DataSource, Exchange, Timeframe


class OptionContractMetadata(BaseModel):
    """Point-in-time option contract specification."""

    model_config = ConfigDict(frozen=True)

    underlying_symbol: str = "NIFTY"
    expiry_date: date
    strike_price: float
    option_type: Literal["CE", "PE"]
    scrip_code: int = Field(description="5paisa numerical token or exchange token")
    exchange: Exchange = Exchange.NSE
    exchange_segment: str = "D"  # 'D' for derivatives, 'C' for cash
    lot_size: int = Field(gt=0, description="Lot size active on this contract date")
    tick_size: float = Field(default=0.05, gt=0)
    effective_from: date | None = None
    effective_to: date | None = None

    @property
    def tradingsymbol(self) -> str:
        """Formatted trading symbol, e.g., 'NIFTY 24OCT25000CE'."""
        exp_str = self.expiry_date.strftime("%d%b%y").upper()
        strike_int = int(self.strike_price) if self.strike_price.is_integer() else self.strike_price
        return f"{self.underlying_symbol}{exp_str}{strike_int}{self.option_type}"


class OptionBar(BaseModel):
    """Normalized OHLCV and market depth bar for an Option contract."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(description="Bar open timestamp in UTC")
    exchange_time: datetime = Field(description="Bar open timestamp in Asia/Kolkata")
    trading_date: date = Field(description="Trading session calendar date")
    underlying_symbol: str = "NIFTY"
    expiry_date: date
    strike_price: float
    option_type: Literal["CE", "PE"]
    scrip_code: int = Field(description="Broker scrip code")
    exchange_segment: str = "D"
    lot_size: int
    tick_size: float = 0.05
    interval: Timeframe = Timeframe.M1
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int = Field(ge=0, default=0)
    open_interest: int | None = Field(default=None)
    bid: float | None = Field(default=None, description="Best bid price at bar close if available")
    ask: float | None = Field(default=None, description="Best ask price at bar close if available")
    data_source: DataSource = DataSource.FIVEPAISA_HISTORICAL
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(ZoneInfo("UTC")))

    @field_validator("high")
    @classmethod
    def validate_high(cls, v: float, info) -> float:
        values = info.data
        if "open" in values and v < values["open"]:
            raise ValueError(f"Option High {v} cannot be less than Open {values['open']}")
        return v

    @field_validator("low")
    @classmethod
    def validate_low(cls, v: float, info) -> float:
        values = info.data
        if "open" in values and v > values["open"]:
            raise ValueError(f"Option Low {v} cannot be greater than Open {values['open']}")
        if "high" in values and v > values["high"]:
            raise ValueError(f"Option Low {v} cannot be greater than High {values['high']}")
        return v

    @field_validator("close")
    @classmethod
    def validate_close(cls, v: float, info) -> float:
        values = info.data
        if "high" in values and v > values["high"]:
            raise ValueError(f"Option Close {v} cannot exceed High {values['high']}")
        if "low" in values and v < values["low"]:
            raise ValueError(f"Option Close {v} cannot be below Low {values['low']}")
        return v


class OptionChainEntry(BaseModel):
    """Single strike row in an active option chain."""

    strike_price: float
    ce_scrip_code: int | None = None
    pe_scrip_code: int | None = None
    ce_ltp: float | None = None
    pe_ltp: float | None = None
    ce_volume: int = 0
    pe_volume: int = 0
    ce_oi: int | None = None
    pe_oi: int | None = None
    ce_bid: float | None = None
    ce_ask: float | None = None
    pe_bid: float | None = None
    pe_ask: float | None = None


class OptionChainSnapshot(BaseModel):
    """Complete option chain for a given underlying and expiry date."""

    underlying_symbol: str
    underlying_price: float
    expiry_date: date
    timestamp: datetime
    strikes: list[OptionChainEntry]
