"""NSECalendar special-session / holiday regressions (ported from retired Phase-4 suites)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from trading_platform.data.calendar import NSECalendar

TZ_IST = ZoneInfo("Asia/Kolkata")


def test_unsupported_special_sessions_block_entry_calendar() -> None:
    calendar = NSECalendar()
    assert calendar.is_unsupported_special_session(datetime(2025, 10, 21).date()) is True
    assert calendar.is_entry_session_supported(datetime(2025, 10, 21).date()) is False
    assert calendar.is_entry_session_supported(datetime(2026, 2, 1).date()) is False
    assert calendar.is_regular_trading_minute(
        datetime(2025, 10, 21, 14, 0, tzinfo=TZ_IST)
    ) is False


def test_holiday_and_unsupported_special_session_are_not_regular_sessions() -> None:
    calendar = NSECalendar()
    assert not calendar.is_regular_trading_minute(datetime(2026, 10, 20, 10, 0, tzinfo=TZ_IST))
    assert not calendar.is_regular_trading_minute(datetime(2025, 10, 21, 10, 0, tzinfo=TZ_IST))
