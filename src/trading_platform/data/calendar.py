"""NSE India exchange calendar and trading session manager."""

from datetime import date, datetime

from trading_platform.core.clock import MARKET_CLOSE_TIME, MARKET_OPEN_TIME, TZ_IST

try:
    import exchange_calendars as xcals

    _HAS_XCALS = True
except ImportError:
    _HAS_XCALS = False

# Official NSE National Holidays for 2024, 2025, and 2026 (Excluding regular weekends)
NSE_OFFICIAL_HOLIDAYS: set[date] = {
    # 2024
    date(2024, 1, 22),   # Special Holiday (Ayodhya)
    date(2024, 1, 26),   # Republic Day
    date(2024, 3, 8),    # Mahashivratri
    date(2024, 3, 25),   # Holi
    date(2024, 3, 29),   # Good Friday
    date(2024, 4, 11),   # Id-Ul-Fitr (Ramzan Id)
    date(2024, 4, 17),   # Shri Ram Navami
    date(2024, 5, 1),    # Maharashtra Day
    date(2024, 5, 20),   # General Elections (Mumbai)
    date(2024, 6, 17),   # Bakri Id
    date(2024, 7, 17),   # Muharram
    date(2024, 8, 15),   # Independence Day
    date(2024, 10, 2),   # Mahatma Gandhi Jayanti
    date(2024, 11, 1),   # Diwali Laxmi Pujan (Regular closed; Muhurat trading evening)
    date(2024, 11, 15),  # Guru Nanak Jayanti
    date(2024, 11, 20),  # Maharashtra Assembly Elections
    date(2024, 12, 25),  # Christmas
    # 2025
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr
    date(2025, 4, 10),   # Shri Mahavir Jayanti
    date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Mahatma Gandhi Jayanti / Dussehra
    date(2025, 10, 21),  # Diwali (Laxmi Pujan)
    date(2025, 10, 22),  # Diwali Balipratipada
    date(2025, 11, 5),   # Prakash Gurpurb Sri Guru Nanak Dev
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 15),   # Maharashtra Municipal Corporation Elections
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Holi
    date(2026, 3, 26),   # Shri Ram Navami
    date(2026, 3, 31),   # Shri Mahavir Jayanti
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 5, 28),   # Bakri Id
    date(2026, 6, 26),   # Muharram
    date(2026, 9, 14),   # Ganesh Chaturthi
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 10),  # Diwali Balipratipada
    date(2026, 11, 24),  # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
}

# Sessions observed in the audit data but not supported by the standard
# continuous-session strategy rules.  They remain visible to data QA, but the
# paper engine must not generate entries on them until an authoritative,
# strategy-specific schedule is configured.
NSE_UNSUPPORTED_SPECIAL_SESSIONS: dict[date, str] = {
    date(2025, 10, 21): "MUHURAT",
    date(2026, 2, 1): "BUDGET_SUNDAY",
}


class NSECalendar:
    """Manages NSE trading days, market hours, and holiday schedules."""

    def __init__(self) -> None:
        self._xcal = None
        if _HAS_XCALS:
            try:
                self._xcal = xcals.get_calendar("XNSE")
            except Exception:
                self._xcal = None

    def is_trading_day(self, check_date: date) -> bool:
        """Return True if check_date is an official NSE trading day."""
        if check_date.weekday() >= 5:  # Saturday and Sunday
            return False

        if check_date in NSE_OFFICIAL_HOLIDAYS:
            return False

        if self._xcal is not None:
            try:
                return bool(self._xcal.is_session(check_date.isoformat()))
            except Exception:
                pass

        return True

    def is_unsupported_special_session(self, check_date: date) -> bool:
        """Return whether the date requires a special schedule not enabled for entries."""
        return check_date in NSE_UNSUPPORTED_SPECIAL_SESSIONS

    def is_entry_session_supported(self, check_date: date) -> bool:
        """Return whether standard strategy entries are permitted on the date."""
        return self.is_trading_day(check_date) and not self.is_unsupported_special_session(check_date)

    def is_regular_trading_minute(self, dt: datetime) -> bool:
        """Check if datetime is within standard trading hours (09:15 - 15:30 IST) on a valid trading day."""
        dt_ist = dt.astimezone(TZ_IST) if dt.tzinfo else dt.replace(tzinfo=TZ_IST)
        if self.is_unsupported_special_session(dt_ist.date()):
            return False
        if not self.is_trading_day(dt_ist.date()):
            return False
        return MARKET_OPEN_TIME <= dt_ist.time() <= MARKET_CLOSE_TIME

    def get_expected_5min_bars_count(self) -> int:
        """Return the count of standard 5-minute bars in a full normal session (75 bars)."""
        # 09:15 to 15:30 = 6 hours 15 minutes = 375 minutes / 5 = 75 bars
        return 75
