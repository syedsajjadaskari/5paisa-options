"""SYNTHETIC data builders for the replay-provider tests (no real market data, no network).

Everything here is labelled synthetic: a seeded random walk written to tmp parquet files in the
same column layout as ``data/processed/nifty`` plus a tiny fake scrip master.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.data.calendar import NSE_OFFICIAL_HOLIDAYS, NSE_UNSUPPORTED_SPECIAL_SESSIONS

IST = ZoneInfo("Asia/Kolkata")
TEST_DAY = date(2026, 2, 11)  # a Wednesday, regular NSE session
EARLY_DAY = date(2026, 1, 8)  # too little synthetic history before it
SYNTH_LOT = 65


def at(hh: int, mm: int, ss: int = 0, day: date = TEST_DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=IST)


def is_session(d: date) -> bool:
    return (
        d.weekday() < 5 and d not in NSE_OFFICIAL_HOLIDAYS and d not in NSE_UNSUPPORTED_SPECIAL_SESSIONS
    )


def session_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _tick(x: float) -> float:
    return round(round(x / 0.05) * 0.05, 2)


def build_frames(
    last_day: date = TEST_DAY, intraday_start: date = date(2026, 1, 1), seed: int = 7
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(5m frame, daily frame) of SYNTHETIC NIFTY-like bars up to and including ``last_day``."""
    rng = random.Random(seed)
    price = 24000.0
    rows5: list[dict] = []
    daily: list[dict] = []
    for d in session_days(date(2025, 1, 1), last_day):
        day_bars: list[dict] = []
        if d >= intraday_start:
            t = datetime(d.year, d.month, d.day, 9, 15)
            for _ in range(75):
                o = price
                c = _tick(o + rng.gauss(0, 9))
                hi = _tick(max(o, c) + abs(rng.gauss(0, 5)))
                lo = _tick(min(o, c) - abs(rng.gauss(0, 5)))
                day_bars.append(
                    {"timestamp": t, "open": o, "high": hi, "low": lo, "close": c, "volume": 0}
                )
                price = c
                t += timedelta(minutes=5)
            # the vendor's 15:30 stub row (must be dropped by the provider)
            day_bars.append(
                {"timestamp": t, "open": price, "high": price, "low": price, "close": price,
                 "volume": 0}
            )
            rows5.extend(day_bars)
            real = day_bars[:-1]
            daily.append(
                {
                    "timestamp": datetime(d.year, d.month, d.day),
                    "open": real[0]["open"],
                    "high": max(b["high"] for b in real),
                    "low": min(b["low"] for b in real),
                    "close": real[-1]["close"],
                    "volume": 0,
                }
            )
        else:
            o = price
            c = _tick(o + rng.gauss(0, 60))
            daily.append(
                {
                    "timestamp": datetime(d.year, d.month, d.day),
                    "open": o,
                    "high": _tick(max(o, c) + 30),
                    "low": _tick(min(o, c) - 30),
                    "close": c,
                    "volume": 0,
                }
            )
            price = c
    return pd.DataFrame(rows5), pd.DataFrame(daily)


def write_master(path: Path, lots: tuple[int, ...] = (SYNTH_LOT,)) -> Path:
    """Tiny FAKE scrip master (NIFTY options with ``lots`` + a BANKNIFTY decoy with lot 30)."""
    rows = []
    for i, lot in enumerate(lots):
        for opt in ("CE", "PE"):
            rows.append(
                {
                    "Exch": "N", "ExchType": "D", "SymbolRoot": "NIFTY", "ScripType": opt,
                    "LotSize": lot, "Expiry": f"2026-1{i}-27",
                }
            )
    rows.append(
        {"Exch": "N", "ExchType": "D", "SymbolRoot": "BANKNIFTY", "ScripType": "CE", "LotSize": 30,
         "Expiry": "2026-01-01"}
    )
    pd.DataFrame(rows).to_parquet(path)
    return path


def write_dataset(dirpath: Path, last_day: date = TEST_DAY) -> tuple[Path, Path, Path]:
    d5, d1 = build_frames(last_day)
    p5, p1, pm = dirpath / "5m.parquet", dirpath / "1d.parquet", dirpath / "master.parquet"
    d5.to_parquet(p5)
    d1.to_parquet(p1)
    write_master(pm)
    return p5, p1, pm
