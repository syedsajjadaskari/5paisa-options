"""Historical options data storage in Parquet with ZSTD compression and DuckDB querying."""

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from trading_platform.core.enums import DataSource, Timeframe
from trading_platform.data.option_chain import OptionBar


class HistoricalOptionsStorage:
    """Manages partitioned Parquet files and DuckDB queries for option contracts."""

    def __init__(
        self,
        base_dir: str | Path = "data/processed/options",
        duckdb_path: str | Path = "data/processed/market_data.duckdb",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.duckdb_path = Path(duckdb_path)
        self.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
        self._con: duckdb.DuckDBPyConnection | None = None

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            self._con = duckdb.connect(str(self.duckdb_path))
        return self._con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def _get_partition_path(self, underlying: str, year: int, interval: str) -> Path:
        dir_path = self.base_dir / underlying.upper() / str(year)
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path / f"option_bars_{interval}.parquet"

    def save_option_bars(self, bars: list[OptionBar]) -> Path:
        """Persist OptionBar records to ZSTD-compressed Parquet partition."""
        if not bars:
            raise ValueError("No option bars provided to save.")

        records = [b.model_dump() for b in bars]
        df = pd.DataFrame(records)

        # Datetime casting
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["exchange_time"] = pd.to_datetime(df["exchange_time"])
        df["trading_date"] = pd.to_datetime(df["trading_date"]).dt.date
        df["expiry_date"] = pd.to_datetime(df["expiry_date"]).dt.date

        underlying = bars[0].underlying_symbol
        year = bars[0].exchange_time.year
        interval = (
            bars[0].interval.value if hasattr(bars[0].interval, "value") else str(bars[0].interval)
        )

        target_file = self._get_partition_path(underlying, year, interval)

        # PyArrow table with ZSTD compression
        table = pa.Table.from_pandas(df)
        pq.write_table(table, target_file, compression="zstd")
        return target_file

    def query_option_bars(
        self,
        underlying: str = "NIFTY",
        interval: str = "1m",
        strike: float | None = None,
        option_type: str | None = None,
        expiry: Any | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[OptionBar]:
        """Query option bars from Parquet partitions via DuckDB."""
        pattern = str(self.base_dir / underlying.upper() / "*" / f"option_bars_{interval}.parquet")
        query = f"SELECT * FROM read_parquet('{pattern}') WHERE 1=1"
        params: list[Any] = []

        if strike is not None:
            query += " AND strike_price = ?"
            params.append(strike)
        if option_type is not None:
            query += " AND option_type = ?"
            params.append(option_type.upper())
        if expiry is not None:
            query += " AND expiry_date = ?"
            exp_str = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
            params.append(exp_str)
        if start_time is not None:
            query += " AND exchange_time >= ?"
            params.append(start_time.isoformat())
        if end_time is not None:
            query += " AND exchange_time <= ?"
            params.append(end_time.isoformat())

        query += " ORDER BY timestamp ASC"

        con = self.get_connection()
        df = con.execute(query, params).df()

        bars: list[OptionBar] = []
        for _, row in df.iterrows():
            bars.append(
                OptionBar(
                    timestamp=pd.to_datetime(row["timestamp"]).to_pydatetime(),
                    exchange_time=pd.to_datetime(row["exchange_time"]).to_pydatetime(),
                    trading_date=pd.to_datetime(row["trading_date"]).date(),
                    underlying_symbol=row["underlying_symbol"],
                    expiry_date=pd.to_datetime(row["expiry_date"]).date(),
                    strike_price=float(row["strike_price"]),
                    option_type=row["option_type"],
                    scrip_code=int(row["scrip_code"]),
                    exchange_segment=row["exchange_segment"],
                    lot_size=int(row["lot_size"]),
                    tick_size=float(row["tick_size"]),
                    interval=Timeframe(row["interval"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                    open_interest=int(row["open_interest"])
                    if pd.notna(row.get("open_interest"))
                    else None,
                    bid=float(row["bid"]) if pd.notna(row.get("bid")) else None,
                    ask=float(row["ask"]) if pd.notna(row.get("ask")) else None,
                    data_source=DataSource(row["data_source"]),
                )
            )
        return bars
