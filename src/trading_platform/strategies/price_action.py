"""Reusable price action calculations and candle classification functions."""

from trading_platform.core.models import Bar


def get_body_ratio(bar: Bar) -> float:
    """Calculate the ratio of the candle body to its total range [0.0, 1.0]."""
    total_range = bar.high - bar.low
    if total_range == 0:
        return 0.0
    return abs(bar.close - bar.open) / total_range


def get_upper_wick_ratio(bar: Bar) -> float:
    """Calculate the ratio of the upper wick to the total range [0.0, 1.0]."""
    total_range = bar.high - bar.low
    if total_range == 0:
        return 0.0
    return (bar.high - max(bar.open, bar.close)) / total_range


def get_lower_wick_ratio(bar: Bar) -> float:
    """Calculate the ratio of the lower wick to the total range [0.0, 1.0]."""
    total_range = bar.high - bar.low
    if total_range == 0:
        return 0.0
    return (min(bar.open, bar.close) - bar.low) / total_range


def is_hpr_pin_bar(bar: Bar, min_upper_wick_ratio: float = 0.50) -> bool:
    """Check if the bar is a Higher Price Rejection (HPR) pin bar."""
    return get_upper_wick_ratio(bar) >= min_upper_wick_ratio


def is_lpr_pin_bar(bar: Bar, min_lower_wick_ratio: float = 0.50) -> bool:
    """Check if the bar is a Lower Price Rejection (LPR) pin bar."""
    return get_lower_wick_ratio(bar) >= min_lower_wick_ratio


def is_strong_body_candle(bar: Bar, min_body_ratio: float = 0.50) -> bool:
    """Check if candle has a solid decisive body."""
    return get_body_ratio(bar) >= min_body_ratio
