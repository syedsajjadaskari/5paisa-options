"""Data quality validation rules and anomaly detection."""

from typing import Any

import pandas as pd

from trading_platform.core.exceptions import DataValidationError
from trading_platform.core.models import Bar


class DataValidationResult:
    """Contains outcome of data validation checks."""

    def __init__(self) -> None:
        self.is_valid: bool = True
        self.total_bars: int = 0
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.duplicate_count: int = 0
        self.inverted_ohlc_count: int = 0
        self.out_of_order_count: int = 0

    def add_error(self, message: str) -> None:
        self.is_valid = False
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


class DataValidator:
    """Performs deterministic validation on market data bars and DataFrames."""

    def __init__(self, strict_mode: bool = True) -> None:
        self.strict_mode = strict_mode

    def validate_bars(self, bars: list[Bar]) -> DataValidationResult:
        """Validate a chronological list of Bar models."""
        result = DataValidationResult()
        result.total_bars = len(bars)

        if not bars:
            result.add_error("Bar list is empty.")
            return result

        seen_timestamps: set[Any] = set()
        prev_time = None

        for idx, bar in enumerate(bars):
            # 1. Duplicates check
            if bar.timestamp in seen_timestamps:
                result.duplicate_count += 1
                result.add_error(f"Duplicate timestamp detected at index {idx}: {bar.timestamp}")
            seen_timestamps.add(bar.timestamp)

            # 2. Chronological order
            if prev_time and bar.timestamp <= prev_time:
                result.out_of_order_count += 1
                result.add_error(
                    f"Out of order timestamp at index {idx}: {bar.timestamp} <= {prev_time}"
                )
            prev_time = bar.timestamp

            # 3. OHLC Invariants
            if not (bar.high >= bar.open and bar.high >= bar.close):
                result.inverted_ohlc_count += 1
                result.add_error(
                    f"High ({bar.high}) is lower than Open/Close ({bar.open}/{bar.close}) at {bar.timestamp}"
                )
            if not (bar.low <= bar.open and bar.low <= bar.close):
                result.inverted_ohlc_count += 1
                result.add_error(
                    f"Low ({bar.low}) is higher than Open/Close ({bar.open}/{bar.close}) at {bar.timestamp}"
                )
            if bar.high < bar.low:
                result.inverted_ohlc_count += 1
                result.add_error(f"High ({bar.high}) < Low ({bar.low}) at {bar.timestamp}")

        if not result.is_valid and self.strict_mode:
            raise DataValidationError(
                f"Data validation failed with {len(result.errors)} errors: {result.errors[:3]}"
            )

        return result

    def validate_dataframe(self, df: pd.DataFrame) -> DataValidationResult:
        """Validate a pandas DataFrame adhering to canonical schema."""
        result = DataValidationResult()
        result.total_bars = len(df)

        required_cols = {"timestamp", "open", "high", "low", "close"}
        missing = required_cols - set(df.columns)
        if missing:
            result.add_error(f"Missing required columns in DataFrame: {missing}")
            if self.strict_mode:
                raise DataValidationError(f"Missing columns: {missing}")
            return result

        # Check chronological sorting
        if not df["timestamp"].is_monotonic_increasing:
            result.out_of_order_count += 1
            result.add_error("DataFrame timestamps are not strictly monotonic increasing.")

        # Check duplicates
        dup_count = int(df.duplicated(subset=["timestamp"]).sum())
        if dup_count > 0:
            result.duplicate_count = dup_count
            result.add_error(f"Found {dup_count} duplicate timestamps.")

        # Check OHLC validity
        invalid_high = df[(df["high"] < df["open"]) | (df["high"] < df["close"])]
        if not invalid_high.empty:
            result.inverted_ohlc_count += len(invalid_high)
            result.add_error(f"Found {len(invalid_high)} bars where High < Open or Close.")

        invalid_low = df[(df["low"] > df["open"]) | (df["low"] > df["close"])]
        if not invalid_low.empty:
            result.inverted_ohlc_count += len(invalid_low)
            result.add_error(f"Found {len(invalid_low)} bars where Low > Open or Close.")

        if not result.is_valid and self.strict_mode:
            raise DataValidationError(f"DataFrame validation failed: {result.errors}")

        return result
