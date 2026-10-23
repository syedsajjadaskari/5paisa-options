"""Phase 4E.3 deterministic historical replay and collector acceptance tests."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from trading_platform.data.offline_replay import (
    load_historical_bars,
    offline_collector_checks,
    replay_shadow_session,
    session_bars,
    validate_historical_session,
)

ROOT = Path(__file__).resolve().parents[2]
INTRADAY = ROOT / "data/processed/nifty/5m/nifty50_5m.parquet"
DAILY = ROOT / "data/processed/nifty/1d/nifty50_1d.parquet"


def test_stored_provider_session_is_exactly_75_completed_bars() -> None:
    intraday, _daily = load_historical_bars(INTRADAY, DAILY)
    bars = session_bars(intraday, date(2025, 8, 6))
    metrics = validate_historical_session(intraday, date(2025, 8, 6))
    assert len(bars) == 75
    assert metrics["complete"] is True
    assert metrics["future_rows"] == 0
    assert metrics["missing_intervals"] == 0
    assert metrics["source"] == "FIVEPAISA_HISTORICAL"


def test_historical_shadow_replay_keeps_false_conditions_separate_from_rf_ambiguity() -> None:
    intraday, daily = load_historical_bars(INTRADAY, DAILY)
    rows, _metrics = replay_shadow_session(intraday, daily, date(2025, 8, 6))
    assert len(rows) == 375
    assert {row["strategy"] for row in rows} == {"PDS", "PDR", "ORB", "IRF", "RF"}
    assert all(row["execution_created"] is False for row in rows)
    rf = [row for row in rows if row["strategy"] == "RF"]
    enabled = [row for row in rows if row["strategy"] != "RF"]
    assert {row["rejection_category"] for row in rf} == {"RULEBOOK_AMBIGUITY"}
    assert {row["rejection_category"] for row in enabled} == {"STRATEGY_CONDITION_FALSE"}


def test_offline_collector_checks_are_read_only_and_restartable() -> None:
    checks = offline_collector_checks()
    assert checks["mode"] == "READ_ONLY"
    assert checks["startup_lock_acquired"] is True
    assert checks["heartbeat_atomic_state"] is True
    assert checks["restart_after_release"] is True
    assert checks["safe_shutdown_status"] is True
    assert checks["closed_session_api_calls"] is True
    assert checks["persisted_spot_rows"] == 1
    assert checks["persisted_candle_rows"] == 1
    assert checks["virtual_orders"] == 0
    assert checks["virtual_fills"] == 0
