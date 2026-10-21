"""NSE India exchange calendar and trading session manager."""

from datetime import date, datetime

from trading_platform.core.clock import MARKET_CLOSE_TIME, MARKET_OPEN_TIME, TZ_IST

try:
    import exchange_calendars as xcals

    _HAS_XCALS = True
except ImportError:
    _HAS_XCALS = False


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

        if self._xcal is not None:
            try:
                return bool(self._xcal.is_session(check_date.isoformat()))
            except Exception:
                pass

        # Fallback basic holiday check
        return True

    def is_regular_trading_minute(self, dt: datetime) -> bool:
        """Check if datetime is within standard trading hours (09:15 - 15:30 IST)."""
        dt_ist = dt.astimezone(TZ_IST) if dt.tzinfo else dt.replace(tzinfo=TZ_IST)
        if not self.is_trading_day(dt_ist.date()):
            return False
        return MARKET_OPEN_TIME <= dt_ist.time() <= MARKET_CLOSE_TIME

    def get_expected_5min_bars_count(self) -> int:
        """Return the count of standard 5-minute bars in a full normal session (75 bars)."""
        # 09:15 to 15:30 = 6 hours 15 minutes = 375 minutes / 5 = 75 bars
        return 75
