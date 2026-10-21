"""Unit tests for NSECalendar."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

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
