"""Instrument definitions and derivative contract models."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trading_platform.core.enums import Exchange, InstrumentType


class Instrument(BaseModel):
    """Base financial instrument."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    instrument_id: str
    exchange: Exchange = Exchange.NSE
    instrument_type: InstrumentType
    tick_size: float = 0.05
    lot_size: int = 1
    is_tradable: bool = True


class IndexInstrument(Instrument):
    """Cash market index (e.g., NIFTY 50). Non-tradable directly."""

    instrument_type: InstrumentType = InstrumentType.INDEX
    is_tradable: bool = False


class FutureInstrument(Instrument):
    """Futures derivative contract."""

    instrument_type: InstrumentType = InstrumentType.FUTURES
    expiry: date
    underlying: str = "NIFTY 50"
    lot_size: int = 25


class OptionInstrument(Instrument):
    """Option derivative contract."""

    instrument_type: InstrumentType = Field(default=InstrumentType.OPTION_CE)
    option_type: Literal["CE", "PE"]
    strike: float
    expiry: date
    underlying: str = "NIFTY 50"
    lot_size: int = 25


def create_nifty50_index() -> IndexInstrument:
    """Helper to instantiate the primary NIFTY 50 research instrument."""
    return IndexInstrument(
        symbol="NIFTY 50",
        instrument_id="999920000",
        exchange=Exchange.NSE,
        lot_size=25,
        tick_size=0.05,
        is_tradable=False,
    )
