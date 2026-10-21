"""Integration tests for sample data ingestion, validation, and storage pipeline."""

from pathlib import Path

from trading_platform.data.providers.csv_provider import CSVDataProvider
from trading_platform.data.storage import DataStorage
from trading_platform.data.validation import DataValidator


def test_end_to_end_sample_data_pipeline(tmp_path: Path):
    sample_file = Path("data/samples/nifty50_5min_sample.csv")
    assert sample_file.exists(), "Sample NIFTY 50 CSV file must exist."

    # 1. Ingest via CSVDataProvider
    provider = CSVDataProvider(validator=DataValidator(strict_mode=True))
    bars = provider.load_from_file(sample_file)
    assert len(bars) == 75, "Full day should contain exactly 75 5-minute bars"

    # Verify first and last bar times
    assert bars[0].exchange_time.strftime("%H:%M:%S") == "09:15:00"
    assert bars[-1].exchange_time.strftime("%H:%M:%S") == "15:25:00"

    # 2. Save to Parquet
    storage = DataStorage(
        base_dir=tmp_path / "processed",
        duckdb_path=tmp_path / "integration_test.duckdb",
    )
    parquet_file = storage.save_bars(bars)
    assert parquet_file.exists()

    # 3. Query via DuckDB
    df = storage.query_df(
        "SELECT symbol, count(*) as count, min(low) as min_low, max(high) as max_high "
        "FROM read_parquet(?) GROUP BY symbol",
        [str(parquet_file)],
    )
    assert len(df) == 1
    assert df["symbol"].iloc[0] == "NIFTY 50"
    assert df["count"].iloc[0] == 75
    assert df["max_high"].iloc[0] > df["min_low"].iloc[0]

    storage.close()
