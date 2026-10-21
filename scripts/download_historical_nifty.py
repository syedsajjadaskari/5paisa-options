#!/usr/bin/env python3
"""CLI utility to download historical NIFTY 50 candles via 5paisa adapter.

Usage:
    python scripts/download_historical_nifty.py --start-date 2021-10-01 --end-date 2024-10-18
"""

import argparse
import sys
from datetime import date

from trading_platform.data.historical_downloader import (
    HistoricalDataDownloader,
    IngestionStatus,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download historical NIFTY 50 candles via 5paisa")
    parser.add_argument(
        "--start-date",
        type=lambda s: date.fromisoformat(s),
        default=date(2021, 10, 1),
        help="Start date in YYYY-MM-DD format (default: 3 years back)",
    )
    parser.add_argument(
        "--end-date",
        type=lambda s: date.fromisoformat(s),
        default=date(2024, 10, 18),
        help="End date in YYYY-MM-DD format (default: 2024-10-18)",
    )
    parser.add_argument(
        "--interval",
        choices=["1m", "5m"],
        default="5m",
        help="Candle timeframe interval (default: 5m)",
    )

    args = parser.parse_args()

    print("=" * 70)
    print("5PAISA HISTORICAL NIFTY 50 DATA DOWNLOADER")
    print("=" * 70)
    print(f"Target Range : {args.start_date} to {args.end_date}")
    print(f"Timeframe    : {args.interval}")
    print("-" * 70)

    downloader = HistoricalDataDownloader()
    report = downloader.ingest_nifty_history(
        start_date=args.start_date,
        end_date=args.end_date,
        interval=args.interval,
    )

    print(f"Status       : {report.status.value}")
    print(f"Message      : {report.message}")

    if report.status == IngestionStatus.AUTHENTICATION_REQUIRED:
        print("\nACTION REQUIRED:")
        print("  1. Copy .env.example to .env")
        print("  2. Add your 5paisa App Name, User ID, User Key, and TOTP secret.")
        print("  3. Re-run this script to start the authenticated download.")
        sys.exit(0)
    elif report.status == IngestionStatus.SUCCESS:
        print(f"Candles Saved: {report.candles_count} across {report.trading_days_count} days")
        sys.exit(0)
    else:
        print("Ingestion failed or returned zero records.")
        sys.exit(1)


if __name__ == "__main__":
    main()
