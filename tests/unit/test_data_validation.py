"""Unit tests for DataValidator."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading_platform.core.enums import DataSource, Exchange, InstrumentType, Timeframe
from trading_platform.core.exceptions import DataValidationError
from trading_platform.core.models import Bar
from trading_platform.data.validation import DataValidator

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


def create_sample_bar(
    dt_ist: datetime,
    open_p: float = 24800,
    high_p: float = 24820,
    low_p: float = 24790,
    close_p: float = 24810,
) -> Bar:
    return Bar(
        timestamp=dt_ist.astimezone(TZ_UTC),
        exchange_time=dt_ist,
        symbol="NIFTY 50",
        instrument_id="999920000",
        exchange=Exchange.NSE,
        instrument_type=InstrumentType.INDEX,
        interval=Timeframe.M5,
        open=open_p,
        high=high_p,
        low=low_p,
        close=close_p,
        volume=1000,
        source=DataSource.CSV,
    )


def test_validator_accepts_clean_bars():
    validator = DataValidator(strict_mode=True)
    t0 = datetime(2024, 10, 18, 9, 15, tzinfo=TZ_IST)
    bars = [
        create_sample_bar(t0),
        create_sample_bar(t0 + timedelta(minutes=5)),
        create_sample_bar(t0 + timedelta(minutes=10)),
    ]
    res = validator.validate_bars(bars)
    assert res.is_valid is True
    assert res.total_bars == 3
    assert len(res.errors) == 0


def test_validator_catches_duplicates():
    validator = DataValidator(strict_mode=False)
    t0 = datetime(2024, 10, 18, 9, 15, tzinfo=TZ_IST)
    bars = [
        create_sample_bar(t0),
        create_sample_bar(t0),  # Duplicate
    ]
    res = validator.validate_bars(bars)
    assert res.is_valid is False
    assert res.duplicate_count == 1


def test_validator_catches_out_of_order():
    validator = DataValidator(strict_mode=False)
    t0 = datetime(2024, 10, 18, 9, 15, tzinfo=TZ_IST)
    bars = [
        create_sample_bar(t0 + timedelta(minutes=5)),
        create_sample_bar(t0),  # Earlier timestamp later in sequence
    ]
    res = validator.validate_bars(bars)
    assert res.is_valid is False
    assert res.out_of_order_count == 1


def test_validator_strict_mode_raises():
    validator = DataValidator(strict_mode=True)
    t0 = datetime(2024, 10, 18, 9, 15, tzinfo=TZ_IST)
    bars = [create_sample_bar(t0), create_sample_bar(t0)]
    with pytest.raises(DataValidationError):
        validator.validate_bars(bars)
