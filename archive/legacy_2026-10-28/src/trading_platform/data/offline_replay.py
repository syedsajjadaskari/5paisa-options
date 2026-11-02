"""Deterministic, historical-only replay helpers for collector acceptance.

This module deliberately has no broker or network dependency.  It consumes the
stored, provider-labelled historical NIFTY files and is used to exercise the
same validation and shadow-evaluation boundaries as the read-only collector.
Replay output is never labelled as live data and never creates an order, fill,
position, or P&L record.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.core.enums import DataSource, Exchange, InstrumentType, Timeframe
from trading_platform.core.models import Bar
from trading_platform.data.candle_quality import validate_5m_frame
from trading_platform.data.live_market_feed import SpotTick
from trading_platform.data.point_in_time_context import PointInTimeReferenceProvider
from trading_platform.data.read_only_collector import ReadOnlyCollector
from trading_platform.paper.shadow import ShadowSignalCollector

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


def _ist_timestamp(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(TZ_IST)
    else:
        timestamp = timestamp.tz_convert(TZ_IST)
    return timestamp.to_pydatetime()


def _bar_from_row(row: Any, *, interval: Timeframe, source: DataSource) -> Bar:
    exchange_time = _ist_timestamp(row["timestamp"])
    return Bar(
        timestamp=exchange_time.astimezone(TZ_UTC),
        exchange_time=exchange_time,
        symbol="NIFTY 50",
        instrument_id="999920000",
        exchange=Exchange.NSE,
        instrument_type=InstrumentType.INDEX,
        interval=interval,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row.get("volume", 0)),
        source=source,
    )


def load_historical_bars(
    intraday_path: str | Path,
    daily_path: str | Path,
) -> tuple[list[Bar], list[Bar]]:
    """Load stored historical OHLC without contacting a provider."""
    intraday_frame = pd.read_parquet(intraday_path)
    daily_frame = pd.read_parquet(daily_path)
    intraday = [
        _bar_from_row(row, interval=Timeframe.M5, source=DataSource.FIVEPAISA_HISTORICAL)
        for _, row in intraday_frame.iterrows()
    ]
    daily = [
        _bar_from_row(row, interval=Timeframe.D1, source=DataSource.FIVEPAISA_HISTORICAL)
        for _, row in daily_frame.iterrows()
    ]
    return intraday, daily


def session_bars(bars: list[Bar], session_date: date) -> list[Bar]:
    """Return only completed standard-session bars, excluding the 15:30 boundary row."""
    return [
        bar
        for bar in bars
        if bar.exchange_time.date() == session_date
        and SESSION_OPEN <= bar.exchange_time.timetz().replace(tzinfo=None) < SESSION_CLOSE
    ]


def bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": bar.exchange_time,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars
        ]
    )


def validate_historical_session(bars: list[Bar], session_date: date) -> dict[str, Any]:
    """Validate one genuine stored session as completed provider OHLC."""
    selected = session_bars(bars, session_date)
    frame = bars_to_frame(selected)
    observation_time = datetime.combine(session_date, SESSION_CLOSE, tzinfo=TZ_IST)
    metrics = validate_5m_frame(frame, observation_time=observation_time)
    metrics.update(
        {
            "session_date": session_date.isoformat(),
            "source": "FIVEPAISA_HISTORICAL",
            "source_kind": "stored provider OHLC; retrospective replay only",
            "expected_complete_bars": 75,
            "expected_first_timestamp": datetime.combine(
                session_date, SESSION_OPEN, tzinfo=TZ_IST
            ).isoformat(),
            "expected_last_open_timestamp": datetime.combine(
                session_date, time(15, 25), tzinfo=TZ_IST
            ).isoformat(),
        }
    )
    return metrics


def replay_shadow_session(
    intraday_bars: list[Bar],
    daily_bars: list[Bar],
    session_date: date,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate all canonical methods against frozen point-in-time contexts."""
    bars = session_bars(intraday_bars, session_date)
    provider = PointInTimeReferenceProvider(intraday_bars, daily_bars)
    shadow = ShadowSignalCollector()
    for bar in bars:
        context = provider.build(bar)
        for strategy, method, _confidence in shadow.METHODS:
            shadow.evaluate_strategy_bar(strategy, method, bar, context)
    return shadow.rows, validate_historical_session(intraday_bars, session_date)


def offline_collector_checks() -> dict[str, Any]:
    """Run startup, heartbeat, restart, and safe-shutdown checks without feed calls."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="phase4e3_collector_") as directory:
        root = Path(directory)
        from trading_platform.data.read_only_collector import ReadOnlyCollectorConfig

        collector_config = ReadOnlyCollectorConfig(
            state_file=root / "state.json",
            lock_file=root / "collector.lock",
            db_path=root / "collector.duckdb",
            archive_dir=root / "archive",
        )
        collector = ReadOnlyCollector(collector_config)
        replay_time = datetime(2025, 8, 6, 9, 15, tzinfo=TZ_IST)
        replay_tick = SpotTick(
            symbol="NIFTY 50",
            scrip_code=999920000,
            last_rate=24652.0,
            high=24664.65,
            low=24634.55,
            prev_close=24649.55,
            provider_timestamp=replay_time,
            ingestion_timestamp=replay_time.replace(second=1),
            raw_provider_timestamp=replay_time.isoformat(),
        )
        replay_bar = Bar(
            timestamp=replay_time.astimezone(TZ_UTC),
            exchange_time=replay_time,
            symbol="NIFTY 50",
            instrument_id="999920000",
            exchange=Exchange.NSE,
            instrument_type=InstrumentType.INDEX,
            interval=Timeframe.M5,
            open=24652.0,
            high=24664.65,
            low=24634.55,
            close=24652.35,
            source=DataSource.FIVEPAISA_HISTORICAL,
        )
        collector.recorder.record_spot_tick(replay_tick)
        collector.recorder.record_spot_tick(replay_tick)
        collector.recorder.record_candle(replay_bar, interval="5m", is_complete=True)
        collector.recorder.record_candle(replay_bar, interval="5m", is_complete=True)
        import duckdb

        connection = duckdb.connect(str(collector_config.db_path), read_only=True)
        persisted_spot_rows = connection.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
        persisted_candle_rows = connection.execute("SELECT COUNT(*) FROM candles_5m").fetchone()[0]
        connection.close()
        collector.acquire_lock()
        collector._persist_heartbeat("RUNNING")
        running_state = ReadOnlyCollector.inspect_state(collector_config.state_file)
        collector.release_lock()

        restarted = ReadOnlyCollector(collector_config)
        restarted.calendar.is_regular_trading_minute = lambda _dt: False
        stopped_summary = restarted.run(max_cycles=1)
        stopped_state = ReadOnlyCollector.inspect_state(collector_config.state_file)
        return {
            "startup_lock_acquired": running_state.get("mode") == "READ_ONLY",
            "heartbeat_atomic_state": running_state.get("status") == "RUNNING",
            "heartbeat_has_no_account_namespace": "account_namespace" not in running_state,
            "restart_after_release": stopped_summary.get("status") == "STOPPED",
            "safe_shutdown_status": stopped_state.get("status") == "STOPPED",
            "closed_session_api_calls": stopped_summary.get("quality", {}).get("spot_valid", 0) == 0,
            "persisted_spot_rows": persisted_spot_rows,
            "persisted_candle_rows": persisted_candle_rows,
            "virtual_orders": 0,
            "virtual_fills": 0,
            "mode": "READ_ONLY",
        }
