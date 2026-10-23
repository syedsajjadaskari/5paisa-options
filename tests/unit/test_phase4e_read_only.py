"""Phase 4E collector, candle-quality, and shadow-mode regressions."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from trading_platform.core.enums import DataSource
from trading_platform.core.models import Bar
from trading_platform.data.calendar import NSECalendar
from trading_platform.data.candle_quality import validate_5m_frame
from trading_platform.data.live_market_feed import FiveMinuteCandleAggregator, SpotTick
from trading_platform.data.read_only_collector import ReadOnlyCollector, ReadOnlyCollectorConfig
from trading_platform.paper.shadow import ShadowSignalCollector, classify_rejection_reason

TZ_IST = ZoneInfo("Asia/Kolkata")


def test_collector_process_lock_prevents_duplicate_sessions(tmp_path: Path) -> None:
    config = ReadOnlyCollectorConfig(
        state_file=tmp_path / "state.json",
        lock_file=tmp_path / "collector.lock",
        db_path=tmp_path / "collector.duckdb",
        archive_dir=tmp_path / "archive",
    )
    first = ReadOnlyCollector(config)
    second = ReadOnlyCollector(config)
    first.acquire_lock()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            second.acquire_lock()
    finally:
        first.release_lock()


def test_collector_heartbeat_is_atomic_and_read_only(tmp_path: Path) -> None:
    config = ReadOnlyCollectorConfig(
        state_file=tmp_path / "state.json",
        lock_file=tmp_path / "collector.lock",
        db_path=tmp_path / "collector.duckdb",
        archive_dir=tmp_path / "archive",
    )
    collector = ReadOnlyCollector(config)
    collector._persist_heartbeat("RUNNING")
    state = ReadOnlyCollector.inspect_state(config.state_file)
    assert state["mode"] == "READ_ONLY"
    assert state["status"] == "RUNNING"
    assert "account_namespace" not in state


def test_candle_quality_detects_alignment_and_gap() -> None:
    base = datetime(2025, 8, 1, 9, 15)
    rows = [
        {"timestamp": base, "open": 100, "high": 102, "low": 99, "close": 101},
        {"timestamp": base + timedelta(minutes=10), "open": 101, "high": 103, "low": 100, "close": 102},
    ]
    metrics = validate_5m_frame(pd.DataFrame(rows))
    assert metrics["aligned_rows"] == 2
    assert metrics["missing_intervals"] == 1
    assert metrics["complete"] is False


def test_shadow_mode_rejects_ambiguous_rules_without_execution() -> None:
    ts = datetime(2025, 8, 1, 9, 15, tzinfo=TZ_IST)
    bar = Bar(
        timestamp=ts.astimezone(ZoneInfo("UTC")),
        exchange_time=ts,
        symbol="NIFTY 50",
        instrument_id="999920000",
        open=100,
        high=102,
        low=99,
        close=101,
    )
    shadow = ShadowSignalCollector()
    shadow.evaluate_closed_candle(bar, replay=True)
    assert len(shadow.rows) == 5
    assert all(row["status"] == "REJECTED" for row in shadow.rows)
    assert all(row["execution_created"] is False for row in shadow.rows)
    assert all(row["direction"] == "NONE" for row in shadow.rows)


def test_closed_session_collector_stops_without_api_calls(tmp_path: Path, monkeypatch) -> None:
    config = ReadOnlyCollectorConfig(
        state_file=tmp_path / "state.json",
        lock_file=tmp_path / "collector.lock",
        db_path=tmp_path / "collector.duckdb",
        archive_dir=tmp_path / "archive",
    )
    collector = ReadOnlyCollector(config)
    monkeypatch.setattr(collector.calendar, "is_regular_trading_minute", lambda _: False)
    summary = collector.run(max_cycles=1)
    assert summary["quality"]["spot_valid"] == 0
    assert ReadOnlyCollector.inspect_state(config.state_file)["status"] == "STOPPED"


def _bar(ts: datetime) -> Bar:
    return Bar(
        timestamp=ts.astimezone(ZoneInfo("UTC")),
        exchange_time=ts,
        symbol="NIFTY 50",
        instrument_id="999920000",
        open=100,
        high=102,
        low=99,
        close=101,
    )


def _tick(ts: datetime, price: float = 100.0) -> SpotTick:
    return SpotTick(
        symbol="NIFTY 50",
        scrip_code=999920000,
        last_rate=price,
        high=price,
        low=price,
        prev_close=price,
        provider_timestamp=ts,
        ingestion_timestamp=ts + timedelta(seconds=1),
        raw_provider_timestamp=ts.isoformat(),
    )


def test_all_shadow_rejection_categories_are_stable() -> None:
    examples = {
        "REJECTED_INCOMPLETE_OR_FUTURE_CANDLE": "INCOMPLETE_CANDLE",
        "REJECTED_MISSING_POINT_IN_TIME_LEVELS": "MISSING_REFERENCE_DATA",
        "REJECTED_STALE_UNDERLYING": "STALE_UNDERLYING",
        "REJECTED_INSUFFICIENT_HISTORY": "INSUFFICIENT_HISTORY",
        "REJECTED_RULEBOOK_AMBIGUITY_PENDING_DEVELOPER_CONFIRMATION": "RULEBOOK_AMBIGUITY",
        "REJECTED_INDICATOR_NOT_READY": "INDICATOR_NOT_READY",
        "REJECTED_MARKET_SESSION_RESTRICTION": "MARKET_SESSION_RESTRICTION",
        "REJECTED_STRATEGY_CONDITION_FALSE": "STRATEGY_CONDITION_FALSE",
        "INTERNAL_ERROR:parser": "INTERNAL_ERROR",
    }
    assert {classify_rejection_reason(key) for key in examples} == set(examples.values())
    assert all(classify_rejection_reason(key) == value for key, value in examples.items())


def test_shadow_preserves_false_condition_distinct_from_missing_reference() -> None:
    shadow = ShadowSignalCollector()
    shadow.evaluate_closed_candle(
        _bar(datetime(2025, 8, 1, 9, 15, tzinfo=TZ_IST)),
        previous_day_levels={"pdh": 102.0, "pdl": 98.0, "prev_close": 100.0},
        replay=True,
        strategy_condition=False,
    )
    assert all(row["rejection_category"] == "STRATEGY_CONDITION_FALSE" for row in shadow.rows[:4])
    assert all(row["execution_created"] is False for row in shadow.rows)


def test_shadow_gate_precedence_covers_stale_future_history_indicator_and_session() -> None:
    cases = [
        ({"candle_complete": False}, "INCOMPLETE_CANDLE"),
        ({"stale_underlying": True}, "STALE_UNDERLYING"),
        ({"history_sufficient": False}, "INSUFFICIENT_HISTORY"),
        ({"indicator_ready": False}, "INDICATOR_NOT_READY"),
        ({"session_supported": False}, "MARKET_SESSION_RESTRICTION"),
    ]
    for kwargs, expected in cases:
        shadow = ShadowSignalCollector()
        shadow.evaluate_closed_candle(
            _bar(datetime(2025, 8, 1, 9, 15, tzinfo=TZ_IST)),
            previous_day_levels={"pdh": 102.0},
            replay=True,
            **kwargs,
        )
        assert all(row["rejection_category"] == expected for row in shadow.rows)


def test_live_aggregator_emits_only_after_boundary_and_marks_sampled_source() -> None:
    aggregator = FiveMinuteCandleAggregator()
    assert aggregator.process_tick(_tick(datetime(2026, 1, 2, 9, 15, 1, tzinfo=TZ_IST))) is None
    closed = aggregator.process_tick(_tick(datetime(2026, 1, 2, 9, 20, tzinfo=TZ_IST), 101))
    assert closed is not None
    assert closed.exchange_time == datetime(2026, 1, 2, 9, 15, tzinfo=TZ_IST)
    assert closed.source == DataSource.FIVEPAISA_WS


def test_collector_detects_duplicate_observation_and_candle_gap(tmp_path: Path, monkeypatch) -> None:
    config = ReadOnlyCollectorConfig(
        state_file=tmp_path / "state.json",
        lock_file=tmp_path / "collector.lock",
        db_path=tmp_path / "collector.duckdb",
        archive_dir=tmp_path / "archive",
    )
    collector = ReadOnlyCollector(config)
    monkeypatch.setattr(collector.calendar, "is_regular_trading_minute", lambda _: True)
    collector.feed.is_connected = True
    ticks = iter(
        [
            _tick(datetime(2026, 1, 2, 9, 15, 1, tzinfo=TZ_IST)),
            _tick(datetime(2026, 1, 2, 9, 15, 1, tzinfo=TZ_IST)),
            _tick(datetime(2026, 1, 2, 9, 30, tzinfo=TZ_IST), 101),
        ]
    )
    monkeypatch.setattr(collector.feed, "fetch_spot_tick", lambda: next(ticks))
    collector.recorder.record_spot_tick = lambda _tick: None
    collector.recorder.record_candle = lambda *args, **kwargs: None
    collector.recorder.archive_atm_option_chain = lambda **kwargs: None
    collector.recorder.last_archive_stats = {"valid": 0, "rejected": 0, "deduplicated": 0}
    collector.collect_once()
    collector.collect_once()
    collector.collect_once()
    assert collector.quality["spot_deduplicated"] == 1
    assert collector.quality["candle_missing_intervals"] == 2
    assert collector.quality["candle_rejected"] == 1


def test_collector_read_only_run_creates_no_virtual_orders_or_fills(tmp_path: Path, monkeypatch) -> None:
    config = ReadOnlyCollectorConfig(
        state_file=tmp_path / "state.json",
        lock_file=tmp_path / "collector.lock",
        db_path=tmp_path / "collector.duckdb",
        archive_dir=tmp_path / "archive",
    )
    collector = ReadOnlyCollector(config)
    monkeypatch.setattr(collector.calendar, "is_regular_trading_minute", lambda _: False)
    collector.run(max_cycles=1)
    import duckdb

    con = duckdb.connect(str(config.db_path), read_only=True)
    assert con.execute("SELECT COUNT(*) FROM virtual_orders").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM virtual_fills").fetchone()[0] == 0
    con.close()


def test_restart_recovery_reacquires_lock_after_safe_shutdown(tmp_path: Path, monkeypatch) -> None:
    config = ReadOnlyCollectorConfig(
        state_file=tmp_path / "state.json",
        lock_file=tmp_path / "collector.lock",
        db_path=tmp_path / "collector.duckdb",
        archive_dir=tmp_path / "archive",
    )
    monkeypatch.setattr(NSECalendar, "is_regular_trading_minute", lambda _self, _dt: False)
    first = ReadOnlyCollector(config)
    assert first.run(max_cycles=1)["status"] == "STOPPED"
    second = ReadOnlyCollector(config)
    assert second.run(max_cycles=1)["status"] == "STOPPED"
    assert ReadOnlyCollector.inspect_state(config.state_file)["status"] == "STOPPED"


def test_holiday_and_unsupported_special_session_are_not_regular_sessions() -> None:
    calendar = NSECalendar()
    assert not calendar.is_regular_trading_minute(datetime(2026, 10, 20, 10, 0, tzinfo=TZ_IST))
    assert not calendar.is_regular_trading_minute(datetime(2025, 10, 21, 10, 0, tzinfo=TZ_IST))
