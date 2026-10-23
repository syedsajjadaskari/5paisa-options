"""Session Normalization and Boundary Timestamp Audit Module.

Analyzes provider-specific bar timestamp labeling conventions, distinguishes
continuous market hours from post-market auction/settlement snapshots,
and implements session normalization policies without data loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from enum import StrEnum

from trading_platform.core.enums import Timeframe
from trading_platform.core.models import Bar


class SessionNormalizationMode(StrEnum):
    """Supported session normalization policies."""

    RAW = "RAW"  # Keep provider data untouched (includes 15:30 bar and post-market extensions)
    CONTINUOUS_TRADING = (
        "CONTINUOUS_TRADING"  # Strictly continuous hours (09:15-15:25 for 5m, 09:15-15:29 for 1m)
    )
    SETTLEMENT_INCLUDED = (
        "SETTLEMENT_INCLUDED"  # Continuous hours + exact 15:30 closing settlement anchor
    )


@dataclass(frozen=True)
class SessionNormalizationAuditResult:
    """Detailed audit metrics comparing raw vs normalized bars."""

    total_raw_bars: int
    total_normalized_bars: int
    post_market_bars_removed: int
    unique_dates: int
    normal_sessions_count: int
    special_sessions_count: int
    bars_per_normal_session: int


class SessionNormalizer:
    """Normalizes exchange bar sessions and strips spurious post-market extension intervals."""

    # Continuous trading market hours on NSE India: 09:15:00 to 15:30:00 IST
    CONTINUOUS_START = time(9, 15)
    CONTINUOUS_END_1M = time(15, 29)
    CONTINUOUS_END_5M = time(15, 25)
    SETTLEMENT_TIME = time(15, 30)

    # Special session: Diwali Muhurat (e.g. 2025-10-21, 13:45 to 14:45 IST)
    MUHURAT_DATE = date(2025, 10, 21)
    MUHURAT_START = time(13, 45)
    MUHURAT_END_1M = time(14, 44)
    MUHURAT_END_5M = time(14, 40)
    MUHURAT_SETTLEMENT = time(14, 45)

    @classmethod
    def normalize_bars(
        cls,
        bars: list[Bar],
        mode: SessionNormalizationMode = SessionNormalizationMode.CONTINUOUS_TRADING,
    ) -> list[Bar]:
        """Filter and normalize bars according to the declared exchange policy."""
        if mode == SessionNormalizationMode.RAW:
            return list(bars)

        normalized: list[Bar] = []
        for b in bars:
            b_date = b.exchange_time.date()
            b_time = b.exchange_time.time()
            tf = b.interval

            # 1. Handle Diwali Muhurat Special Session
            if b_date == cls.MUHURAT_DATE:
                if b_time < cls.MUHURAT_START:
                    continue
                if mode == SessionNormalizationMode.CONTINUOUS_TRADING:
                    end_limit = cls.MUHURAT_END_1M if tf == Timeframe.M1 else cls.MUHURAT_END_5M
                    if b_time <= end_limit:
                        normalized.append(b)
                elif mode == SessionNormalizationMode.SETTLEMENT_INCLUDED:
                    if b_time <= cls.MUHURAT_SETTLEMENT:
                        normalized.append(b)
                continue

            # 2. Handle Normal Continuous NSE Sessions
            if b_time < cls.CONTINUOUS_START:
                continue

            if mode == SessionNormalizationMode.CONTINUOUS_TRADING:
                end_limit = cls.CONTINUOUS_END_1M if tf == Timeframe.M1 else cls.CONTINUOUS_END_5M
                if b_time <= end_limit:
                    normalized.append(b)
            elif mode == SessionNormalizationMode.SETTLEMENT_INCLUDED:
                # Include up to 15:30:00 (closing settlement snapshot), drop post-market auction bars
                if b_time <= cls.SETTLEMENT_TIME:
                    normalized.append(b)

        return normalized

    @classmethod
    def audit_session_integrity(
        cls,
        bars: list[Bar],
        mode: SessionNormalizationMode = SessionNormalizationMode.CONTINUOUS_TRADING,
    ) -> SessionNormalizationAuditResult:
        """Perform a statistical audit of raw vs normalized session bars."""
        norm_bars = cls.normalize_bars(bars, mode=mode)
        raw_dates = {b.exchange_time.date() for b in bars}
        special_dates = {cls.MUHURAT_DATE}.intersection(raw_dates)
        normal_dates_count = len(raw_dates) - len(special_dates)

        tf = bars[0].interval if bars else Timeframe.M5
        expected_per_session = 75 if tf == Timeframe.M5 else 375
        if mode == SessionNormalizationMode.SETTLEMENT_INCLUDED:
            expected_per_session += 1

        return SessionNormalizationAuditResult(
            total_raw_bars=len(bars),
            total_normalized_bars=len(norm_bars),
            post_market_bars_removed=len(bars) - len(norm_bars),
            unique_dates=len(raw_dates),
            normal_sessions_count=normal_dates_count,
            special_sessions_count=len(special_dates),
            bars_per_normal_session=expected_per_session,
        )
