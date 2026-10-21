"""CLI script to import and validate historical CSV candle data into Parquet / DuckDB."""

import argparse
import sys
from pathlib import Path

from trading_platform.core.enums import Exchange, InstrumentType, Timeframe
from trading_platform.data.providers.csv_provider import CSVDataProvider
from trading_platform.data.storage import DataStorage


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest CSV historical candles into Parquet/DuckDB"
    )
    parser.add_argument(
        "--file", type=str, default="data/samples/nifty50_5min_sample.csv", help="CSV file path"
    )
    parser.add_argument("--symbol", type=str, default="NIFTY 50", help="Trading Symbol")
    parser.add_argument("--interval", type=str, default="5m", help="Candle resolution")

    args = parser.parse_args()
    csv_path = Path(args.file)
    if not csv_path.exists():
        print(f"Error: File not found {csv_path}")
        sys.exit(1)

    print(f"Loading and validating {csv_path} for {args.symbol} ...")
    provider = CSVDataProvider()
    bars = provider.load_from_file(
        file_path=csv_path,
        symbol=args.symbol,
        interval=Timeframe(args.interval),
        exchange=Exchange.NSE,
        instrument_type=InstrumentType.INDEX,
    )
    print(f"Successfully validated {len(bars)} bars.")

    storage = DataStorage()
    parquet_path = storage.save_bars(bars)
    print(f"Saved to Parquet partition: {parquet_path}")

    # Verify query
    df = storage.query_df(f"SELECT COUNT(*) AS total_bars FROM read_parquet('{parquet_path}')")
    print(f"DuckDB count verification: {df['total_bars'].iloc[0]} bars confirmed.")
    storage.close()


if __name__ == "__main__":
    main()
