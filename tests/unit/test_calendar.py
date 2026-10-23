"""Unit tests for NSECalendar."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from trading_platform.data.calendar import NSECalendar

TZ_IST = ZoneInfo("Asia/Kolkata")


def test_nse_calendar_weekdays():
    cal = NSECalendar()
    # 2024-10-18 was a Friday
    friday = date(2024, 10, 18)
    assert friday.weekday() == 4
    assert cal.is_trading_day(friday) is True

    # 2024-10-19 was a Saturday
    saturday = date(2024, 10, 19)
    assert saturday.weekday() == 5
    assert cal.is_trading_day(saturday) is False


def test_nse_regular_trading_minutes():
    cal = NSECalendar()
    # 09:20 AM IST on Friday
    trading_dt = datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST)
    assert cal.is_regular_trading_minute(trading_dt) is True

    # 08:30 AM IST (before open)
    pre_open_dt = datetime(2024, 10, 18, 8, 30, tzinfo=TZ_IST)
    assert cal.is_regular_trading_minute(pre_open_dt) is False

    # 16:00 PM IST (after close)
    after_hours_dt = datetime(2024, 10, 18, 16, 0, tzinfo=TZ_IST)
    assert cal.is_regular_trading_minute(after_hours_dt) is False


def test_expected_bars_count():
    cal = NSECalendar()
    assert cal.get_expected_5min_bars_count() == 75


@pytest.mark.parametrize(
    "holiday",
    [
        date(2026, 1, 15),
        date(2026, 3, 26),
        date(2026, 3, 31),
        date(2026, 5, 28),
        date(2026, 6, 26),
        date(2026, 9, 14),
        date(2026, 11, 10),
    ],
)
def test_authoritative_2026_fo_holidays(holiday):
    """Dates published in NSE/FAOP/71777 (plus the Jan 15 update) are closed."""
    assert NSECalendar().is_trading_day(holiday) is False


@pytest.mark.parametrize(
    "regular_day",
    [
        date(2026, 2, 17),
        date(2026, 3, 20),
        date(2026, 3, 27),
        date(2026, 5, 27),
        date(2026, 6, 25),
        date(2026, 11, 9),
    ],
)
def test_removed_incorrect_2026_holidays_are_regular_weekdays(regular_day):
    """Previously hard-coded shifted dates must not be treated as exchange holidays."""
    assert NSECalendar().is_trading_day(regular_day) is True
