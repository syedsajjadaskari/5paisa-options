"""Event classes for the event-driven simulation and execution engine."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_platform.core.models import Bar, Fill, Order, Signal


class Event(BaseModel):
    """Base event model."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(description="Event timestamp")
    event_type: str = Field(description="Event classification name")


class BarEvent(Event):
    """New candle arrival event."""

    bar: Bar
    event_type: str = "BAR"


class SignalEvent(Event):
    """Strategy generated signal event."""

    signal: Signal
    event_type: str = "SIGNAL"


class OrderEvent(Event):
    """Order routing event."""

    order: Order
    event_type: str = "ORDER"


class FillEvent(Event):
    """Order fill event."""

    fill: Fill
    event_type: str = "FILL"


class RiskEvent(Event):
    """Risk rejection or warning event."""

    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
    event_type: str = "RISK"
