"""Simulation and real-time market clock utilities."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")

MARKET_OPEN_TIME = time(9, 15, 0)
MARKET_CLOSE_TIME = time(15, 30, 0)
INTRADAY_EXIT_TIME = time(15, 15, 0)


class MarketClock:
    """Manages Indian market trading session time and simulation clock."""

    def __init__(self, start_time: datetime | None = None) -> None:
        self._current_time: datetime = start_time or datetime.now(TZ_IST)

    @property
    def current_time(self) -> datetime:
        """Return current clock time in IST."""
        return self._current_time

    def set_time(self, new_time: datetime) -> None:
        """Advance or set the clock in simulation mode."""
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=TZ_IST)
        else:
            self._current_time = new_time.astimezone(TZ_IST)

    @classmethod
    def to_ist(cls, dt: datetime) -> datetime:
        """Convert any timezone-aware datetime to IST."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ_IST)
        return dt.astimezone(TZ_IST)

    @classmethod
    def to_utc(cls, dt: datetime) -> datetime:
        """Convert any timezone-aware datetime to UTC."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ_IST).astimezone(TZ_UTC)
        return dt.astimezone(TZ_UTC)

    def is_market_open(self, dt: datetime | None = None) -> bool:
        """Check if time falls within regular NSE market trading hours (09:15 - 15:30 IST)."""
        check_time = self.to_ist(dt or self._current_time)
        t = check_time.time()
        # Monday (0) to Friday (4)
        if check_time.weekday() > 4:
            return False
        return MARKET_OPEN_TIME <= t <= MARKET_CLOSE_TIME

    def is_eod_exit_time(self, dt: datetime | None = None) -> bool:
        """Check if time is at or beyond intraday forced square-off time (15:15 IST)."""
        check_time = self.to_ist(dt or self._current_time)
        return check_time.time() >= INTRADAY_EXIT_TIME
