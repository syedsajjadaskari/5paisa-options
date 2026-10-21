"""Base interface for all market data providers."""

from abc import ABC, abstractmethod
from datetime import datetime

from trading_platform.core.models import Bar


class BaseDataProvider(ABC):
    """Abstract data provider contract."""

    @abstractmethod
    def load_historical_bars(
        self,
        symbol: str,
        interval: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Bar]:
        """Fetch or load historical candles adhering to the Bar model."""
        pass
