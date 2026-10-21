"""CSV market data provider for local backtesting and research datasets."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.core.enums import DataSource, Exchange, InstrumentType, Timeframe
from trading_platform.core.exceptions import MissingDataError
from trading_platform.core.models import Bar
from trading_platform.data.providers.base import BaseDataProvider
from trading_platform.data.validation import DataValidator

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


class CSVDataProvider(BaseDataProvider):
    """Loads, normalizes, and validates historical candles from CSV files."""

    def __init__(self, validator: DataValidator | None = None) -> None:
        self.validator = validator or DataValidator(strict_mode=True)

    def load_from_file(
        self,
        file_path: str | Path,
        symbol: str = "NIFTY 50",
        instrument_id: str = "999920000",
        interval: Timeframe = Timeframe.M5,
        exchange: Exchange = Exchange.NSE,
        instrument_type: InstrumentType = InstrumentType.INDEX,
    ) -> list[Bar]:
        """Parse a local CSV file into a validated list of Bar models."""
        path = Path(file_path)
        if not path.exists():
            raise MissingDataError(f"Data file does not exist: {path}")

        df = pd.read_csv(path)
        # Normalize column headers
        df.columns = [c.strip().lower() for c in df.columns]

        if "timestamp" not in df.columns and "date" in df.columns:
            if "time" in df.columns:
                df["timestamp"] = pd.to_datetime(
                    df["date"].astype(str) + " " + df["time"].astype(str)
                )
            else:
                df["timestamp"] = pd.to_datetime(df["date"])
        else:
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        # Validate sorting and basic constraints
        self.validator.validate_dataframe(df)

        bars: list[Bar] = []
        for _, row in df.iterrows():
            raw_ts = row["timestamp"]
            if hasattr(raw_ts, "to_pydatetime"):
                dt = raw_ts.to_pydatetime()
            else:
                dt = raw_ts

            # Assign IST timezone if naive, then calculate UTC
            if dt.tzinfo is None:
                ist_time = dt.replace(tzinfo=TZ_IST)
            else:
                ist_time = dt.astimezone(TZ_IST)
            utc_time = ist_time.astimezone(TZ_UTC)

            bar = Bar(
                timestamp=utc_time,
                exchange_time=ist_time,
                symbol=symbol,
                instrument_id=instrument_id,
                exchange=exchange,
                instrument_type=instrument_type,
                interval=interval,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row.get("volume", 0)),
                open_interest=int(row["open_interest"])
                if pd.notna(row.get("open_interest"))
                else None,
                source=DataSource.CSV,
            )
            bars.append(bar)

        self.validator.validate_bars(bars)
        return bars

    def load_historical_bars(
        self,
        symbol: str,
        interval: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Bar]:
        """BaseDataProvider interface implementation."""
        # Standard default sample lookup
        sample_path = Path("data/samples/nifty50_5min_sample.csv")
        if sample_path.exists():
            bars = self.load_from_file(sample_path, symbol=symbol)
            if start_time:
                bars = [b for b in bars if b.exchange_time >= start_time]
            if end_time:
                bars = [b for b in bars if b.exchange_time <= end_time]
            return bars
        raise MissingDataError(f"No CSV data found for symbol {symbol}")
