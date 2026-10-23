"""Independent integrity checks for five-minute NIFTY candles."""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

TZ_IST = ZoneInfo("Asia/Kolkata")


def validate_5m_frame(frame: pd.DataFrame, observation_time=None) -> dict[str, Any]:
    """Validate alignment, ordering, gaps and OHLC consistency without claiming tick exactness."""
    if frame.empty:
        return {
            "rows": 0,
            "aligned_rows": 0,
            "future_rows": 0,
            "ohlc_invalid_rows": 0,
            "missing_intervals": 0,
            "max_gap_minutes": 0.0,
            "complete": False,
            "intrabar_exact": False,
        }
    df = frame.copy()
    ts = pd.to_datetime(df["timestamp"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(TZ_IST)
    else:
        ts = ts.dt.tz_convert(TZ_IST)
    df["_ts"] = ts
    duplicate_rows = int(df["_ts"].duplicated(keep=False).sum())
    df = df.sort_values("_ts").drop_duplicates("_ts")
    aligned = (df["_ts"].dt.minute % 5 == 0) & (df["_ts"].dt.second == 0)
    if observation_time is not None:
        observation = pd.Timestamp(observation_time)
        if observation.tzinfo is None:
            observation = observation.tz_localize(TZ_IST)
        else:
            observation = observation.tz_convert(TZ_IST)
        future = df["_ts"] > observation
    else:
        future = pd.Series(False, index=df.index)
    invalid_ohlc = (
        (df["high"] < df[["open", "close", "low"]].max(axis=1))
        | (df["low"] > df[["open", "close", "high"]].min(axis=1))
        | (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    )
    diffs = df["_ts"].diff().dropna().dt.total_seconds() / 60.0
    missing = ((diffs / 5.0).round() - 1).clip(lower=0).sum()
    max_gap = float(diffs.max()) if not diffs.empty else 0.0
    return {
        "rows": int(len(df)),
        "aligned_rows": int(aligned.sum()),
        "future_rows": int(future.sum()),
        "duplicate_rows": duplicate_rows,
        "ohlc_invalid_rows": int(invalid_ohlc.sum()),
        "missing_intervals": int(missing),
        "max_gap_minutes": max_gap,
        "complete": bool(
            aligned.all()
            and not invalid_ohlc.any()
            and missing == 0
            and not future.any()
            and duplicate_rows == 0
        ),
        "intrabar_exact": False,
        "first_timestamp": df["_ts"].iloc[0].isoformat(),
        "last_timestamp": df["_ts"].iloc[-1].isoformat(),
    }


def expected_session_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return rows in the standard 09:15–15:30 five-minute session window."""
    if frame.empty:
        return frame
    result = frame.copy()
    ts = pd.to_datetime(result["timestamp"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize(TZ_IST)
    else:
        ts = ts.dt.tz_convert(TZ_IST)
    result["timestamp"] = ts
    return result[(result["timestamp"].dt.time >= pd.Timestamp("09:15").time()) & (result["timestamp"].dt.time <= pd.Timestamp("15:30").time())]
