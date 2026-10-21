"""Unit tests for DataStorage with DuckDB and Parquet."""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_platform.core.enums import DataSource, Exchange, InstrumentType, Timeframe
from trading_platform.core.models import Bar
from trading_platform.data.storage import DataStorage

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


def test_save_and_query_parquet_via_duckdb(tmp_path: Path):
    storage = DataStorage(
        base_dir=tmp_path / "processed",
        duckdb_path=tmp_path / "test_market_data.duckdb",
    )

    t0 = datetime(2024, 10, 18, 9, 15, tzinfo=TZ_IST)
    bars = [
        Bar(
            timestamp=(t0 + timedelta(minutes=i * 5)).astimezone(TZ_UTC),
            exchange_time=t0 + timedelta(minutes=i * 5),
            symbol="NIFTY 50",
            instrument_id="999920000",
            exchange=Exchange.NSE,
            instrument_type=InstrumentType.INDEX,
            interval=Timeframe.M5,
            open=24800.0 + i,
            high=24820.0 + i,
            low=24790.0 + i,
            close=24810.0 + i,
            volume=1000 * (i + 1),
            source=DataSource.SYNTHETIC,
        )
        for i in range(10)
    ]

    saved_file = storage.save_bars(bars)
    assert saved_file.exists()
    assert saved_file.suffix == ".parquet"

    # Query via DuckDB direct query
    df = storage.query_df(
        "SELECT count(*) as count, avg(close) as avg_close FROM read_parquet(?)", [str(saved_file)]
    )
    assert df["count"].iloc[0] == 10
    assert df["avg_close"].iloc[0] > 24800.0

    # Load back as Bar objects
    loaded_bars = storage.load_bars_from_parquet(symbol="NIFTY 50", interval="5m")
    assert len(loaded_bars) == 10
    assert loaded_bars[0].symbol == "NIFTY 50"
    assert loaded_bars[0].open == 24800.0

    storage.close()
