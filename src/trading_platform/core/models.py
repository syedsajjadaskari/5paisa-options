"""Domain models for market data, signals, orders, and trades."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trading_platform.core.enums import (
    DataSource,
    Exchange,
    InstrumentType,
    OrderSide,
    OrderStatus,
    OrderType,
    SignalDirection,
    StrategyName,
    Timeframe,
)


class Bar(BaseModel):
    """Normalized OHLCV candle model."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(description="Bar open timestamp in UTC")
    exchange_time: datetime = Field(description="Bar open timestamp in Asia/Kolkata")
    symbol: str = Field(description="Trading symbol")
    instrument_id: str = Field(description="Exchange/Broker instrument scrip code")
    exchange: Exchange = Field(default=Exchange.NSE)
    instrument_type: InstrumentType = Field(default=InstrumentType.INDEX)
    interval: Timeframe = Field(default=Timeframe.M5)
    open: float = Field(gt=0, description="Opening price")
    high: float = Field(gt=0, description="Highest price")
    low: float = Field(gt=0, description="Lowest price")
    close: float = Field(gt=0, description="Closing price")
    volume: int = Field(ge=0, default=0, description="Traded volume")
    open_interest: int | None = Field(default=None, description="Open interest if derivative")
    source: DataSource = Field(default=DataSource.CSV)
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(ZoneInfo("UTC")))

    @field_validator("high")
    @classmethod
    def validate_high(cls, v: float, info: Any) -> float:
        """Validate high >= open and high >= low."""
        values = info.data
        if "open" in values and v < values["open"]:
            raise ValueError(f"High {v} cannot be less than Open {values['open']}")
        return v

    @field_validator("low")
    @classmethod
    def validate_low(cls, v: float, info: Any) -> float:
        """Validate low <= open and low <= high."""
        values = info.data
        if "open" in values and v > values["open"]:
            raise ValueError(f"Low {v} cannot be greater than Open {values['open']}")
        if "high" in values and v > values["high"]:
            raise ValueError(f"Low {v} cannot be greater than High {values['high']}")
        return v

    @field_validator("close")
    @classmethod
    def validate_close(cls, v: float, info: Any) -> float:
        """Validate close between low and high."""
        values = info.data
        if "high" in values and v > values["high"]:
            raise ValueError(f"Close {v} cannot exceed High {values['high']}")
        if "low" in values and v < values["low"]:
            raise ValueError(f"Close {v} cannot be below Low {values['low']}")
        return v

    @property
    def range(self) -> float:
        """Total price range of the candle."""
        return self.high - self.low

    @property
    def body(self) -> float:
        """Candle body height."""
        return abs(self.close - self.open)

    @property
    def is_green(self) -> bool:
        """True if bullish candle."""
        return self.close > self.open

    @property
    def is_red(self) -> bool:
        """True if bearish candle."""
        return self.close < self.open

    @property
    def upper_wick(self) -> float:
        """Upper shadow length."""
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        """Lower shadow length."""
        return min(self.open, self.close) - self.low


class Signal(BaseModel):
    """Strategy output signal (Never directly places broker orders)."""

    model_config = ConfigDict(frozen=True)

    strategy_name: StrategyName | str
    method_id: str = Field(description="Unique method identifier, e.g., 'IRF_METHOD_1'")
    symbol: str
    direction: SignalDirection
    timestamp: datetime = Field(description="Signal generation timestamp (bar close)")
    reference_price: float = Field(gt=0, description="Price at signal generation")
    stop_loss: float = Field(gt=0, description="Planned stop-loss price")
    target: float = Field(gt=0, description="Planned target price")
    reason: str = Field(description="Explanation of the rule triggered")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def risk_points(self) -> float:
        """Distance in points to stop loss."""
        return abs(self.reference_price - self.stop_loss)

    @property
    def reward_points(self) -> float:
        """Distance in points to target."""
        return abs(self.target - self.reference_price)


class Order(BaseModel):
    """Order dispatch model."""

    order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int = Field(gt=0)
    price: float | None = None
    stop_price: float | None = None
    status: OrderStatus = OrderStatus.PENDING_SUBMIT
    created_at: datetime
    updated_at: datetime
    filled_quantity: int = 0
    average_fill_price: float = 0.0
    strategy_name: str | None = None
    method_id: str | None = None


class Fill(BaseModel):
    """Trade execution record."""

    fill_id: str
    order_id: str
    timestamp: datetime
    symbol: str
    side: OrderSide
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    slippage: float = 0.0
    brokerage: float = 0.0
    statutory_charges: float = 0.0


class Position(BaseModel):
    """Active open position."""

    symbol: str
    quantity: int = 0  # Positive for Long, negative for Short
    average_entry_price: float = 0.0
    current_market_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    strategy_name: str | None = None


class Trade(BaseModel):
    """Completed round-trip trade."""

    trade_id: str
    strategy_name: str
    method_id: str
    symbol: str
    side: OrderSide
    quantity: int
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    stop_loss: float
    target: float
    gross_pnl: float
    net_pnl: float
    total_costs: float
    exit_reason: str  # 'TARGET', 'STOP_LOSS', 'EOD_EXIT', 'RISK_KILL'
    entry_rule_description: str
    is_uncertain_fill: bool = False
