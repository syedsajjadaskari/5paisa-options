"""Parquet Lake storage and DuckDB analytical query engine."""

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from trading_platform.core.enums import DataSource, Exchange, InstrumentType, Timeframe
from trading_platform.core.models import Bar


class DataStorage:
    """Manages Parquet files with ZSTD compression and DuckDB querying."""

    def __init__(
        self,
        base_dir: str | Path = "data/processed",
        duckdb_path: str | Path = "data/processed/market_data.duckdb",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.duckdb_path = Path(duckdb_path)
        self.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
        self._con: duckdb.DuckDBPyConnection | None = None

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or initialize DuckDB database connection."""
        if self._con is None:
            self._con = duckdb.connect(str(self.duckdb_path))
        return self._con

    def close(self) -> None:
        """Close database connection."""
        if self._con is not None:
            self._con.close()
            self._con = None

    def _get_parquet_path(self, symbol: str, interval: str, year: int) -> Path:
        """Generate standardized partition path for Parquet storage."""
        clean_symbol = symbol.replace(" ", "_").upper()
        dir_path = self.base_dir / clean_symbol / str(year)
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path / f"bars_{interval}.parquet"

    def save_bars(self, bars: list[Bar]) -> Path:
        """Convert a list of Bar models into partitioned Parquet with ZSTD compression."""
        if not bars:
            raise ValueError("No bars provided to save.")

        records = [b.model_dump() for b in bars]
        df = pd.DataFrame(records)

        # Ensure correct datatypes
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["exchange_time"] = pd.to_datetime(df["exchange_time"])

        symbol = bars[0].symbol
        interval = (
            bars[0].interval.value if hasattr(bars[0].interval, "value") else str(bars[0].interval)
        )
        year = bars[0].exchange_time.year

        target_file = self._get_parquet_path(symbol, interval, year)

        # PyArrow table with ZSTD compression
        table = pa.Table.from_pandas(df)
        pq.write_table(table, target_file, compression="zstd")
        return target_file

    def query_df(self, query: str, params: list[Any] | None = None) -> pd.DataFrame:
        """Execute a DuckDB SQL query and return results as a Pandas DataFrame."""
        con = self.get_connection()
        if params:
            return con.execute(query, params).df()
        return con.execute(query).df()

    def load_bars_from_parquet(
        self,
        symbol: str,
        interval: str = "5m",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[Bar]:
        """Query stored Parquet files via DuckDB and return validated Bar models."""
        clean_symbol = symbol.replace(" ", "_").upper()
        pattern = str(self.base_dir / clean_symbol / "*" / f"bars_{interval}.parquet")

        query = f"SELECT * FROM read_parquet('{pattern}') WHERE 1=1"
        params: list[Any] = []

        if start_date:
            query += " AND exchange_time >= ?"
            params.append(start_date.isoformat())
        if end_date:
            query += " AND exchange_time <= ?"
            params.append(end_date.isoformat())

        query += " ORDER BY timestamp ASC"

        df = self.query_df(query, params if params else None)
        bars: list[Bar] = []
        for _, row in df.iterrows():
            bars.append(
                Bar(
                    timestamp=pd.to_datetime(row["timestamp"]).to_pydatetime(),
                    exchange_time=pd.to_datetime(row["exchange_time"]).to_pydatetime(),
                    symbol=row["symbol"],
                    instrument_id=str(row["instrument_id"]),
                    exchange=Exchange(row["exchange"]),
                    instrument_type=InstrumentType(row["instrument_type"]),
                    interval=Timeframe(row["interval"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]),
                    open_interest=int(row["open_interest"])
                    if pd.notna(row.get("open_interest"))
                    else None,
                    source=DataSource(row["source"]),
                )
            )
        return bars
