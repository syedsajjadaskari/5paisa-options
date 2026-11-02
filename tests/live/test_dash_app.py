"""Boot the Streamlit script with AppTest against SYNTHETIC runtime dirs (no network, no broker)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from test_dash_synthetic import (
    DAY,
    synthetic_snapshot,
    synthetic_trade,
    write_snapshot,
    write_trades_csv,
)

SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "paper_trading_dashboard.py")


def _run(monkeypatch, runtime: Path, source: str | None = None) -> AppTest:
    monkeypatch.setenv("PAPER_RUNTIME_DIR", str(runtime))
    at = AppTest.from_file(SCRIPT, default_timeout=90)
    at.run()
    if source:
        at.sidebar.radio(key="source").set_value(source)
        at.run()
    return at


def _all_text(at: AppTest) -> str:
    parts = [e.value for e in (*at.info, *at.warning, *at.error, *at.markdown, *at.caption)]
    return "\n".join(str(p) for p in parts)


def test_no_session_state(monkeypatch, tmp_path) -> None:
    at = _run(monkeypatch, tmp_path)
    assert not at.exception, [e.value for e in at.exception]
    assert "No active session" in _all_text(at)


def test_corrupt_snapshot_state(monkeypatch, tmp_path) -> None:
    (tmp_path / "live_state.json").write_text("{broken")
    at = _run(monkeypatch, tmp_path)
    assert not at.exception, [e.value for e in at.exception]
    assert "No active session" in _all_text(at)


def test_stale_snapshot_state(monkeypatch, tmp_path) -> None:
    write_snapshot(tmp_path / "live_state.json", synthetic_snapshot(datetime(2026, 10, 26, 8, 0, 0), position=True))
    at = _run(monkeypatch, tmp_path)
    assert not at.exception, [e.value for e in at.exception]
    text = _all_text(at)
    assert "No active session" in text and "stale" in text.lower()


def test_active_position_state(monkeypatch, tmp_path) -> None:
    write_snapshot(tmp_path / "live_state.json", synthetic_snapshot(position=True))
    write_trades_csv(tmp_path / "journal" / "trades.csv", [synthetic_trade(1)])
    at = _run(monkeypatch, tmp_path)
    assert not at.exception, [e.value for e in at.exception]
    text = _all_text(at)
    assert "No active session" not in text
    assert "signals logged, no virtual fills" in text
    assert "ORB" in text and "NIFTY CE SYNTH" in text
    assert "SAMPLED" in text  # current candle is flagged non-authoritative


def test_closed_trades_state(monkeypatch, tmp_path) -> None:
    rows = [synthetic_trade(1, net=500.0), synthetic_trade(2, strategy="PDS", net=-200.0,
                                                           exit_reason="STOP", hhmm="10:30")]  # fmt: skip
    write_trades_csv(tmp_path / "journal" / "trades.csv", rows)
    at = _run(monkeypatch, tmp_path)
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.dataframe) >= 1  # history table
    text = _all_text(at)
    assert "LIVE PAPER" in text
    assert "WHY THE TRADE OPENED" in text and "WHY THE TRADE CLOSED" in text
    assert "HISTORICAL BACKTEST" in text  # separate section, never mixed


def test_blocked_state_banner(monkeypatch, tmp_path) -> None:
    write_snapshot(tmp_path / "live_state.json", synthetic_snapshot(engine_state="BLOCKED"))
    at = _run(monkeypatch, tmp_path)
    assert not at.exception, [e.value for e in at.exception]
    assert any("BLOCKED" in e.value for e in at.error)


def test_replay_source_is_labelled_and_separate(monkeypatch, tmp_path) -> None:
    write_trades_csv(tmp_path / "journal" / "trades.csv", [synthetic_trade(1)])
    write_snapshot(tmp_path / "replay" / "live_state.json", synthetic_snapshot(mode="REPLAY"))
    write_trades_csv(tmp_path / "replay" / "journal" / "trades.csv",
                     [synthetic_trade(7, label="REPLAY_SIMULATED")])  # fmt: skip
    at = _run(monkeypatch, tmp_path, source="Replay (simulated)")
    assert not at.exception, [e.value for e in at.exception]
    text = _all_text(at)
    assert "REPLAY SIMULATED" in text
    ids = {str(v) for df in at.dataframe if "trade_id" in df.value for v in df.value["trade_id"]}
    assert "T-SYNTH-007" in ids and "T-SYNTH-001" not in ids
    assert "REPLAY SIMULATED paper (₹)" in text


@pytest.mark.parametrize("day", [DAY])
def test_runtime_dir_default_is_repo_runtime(day) -> None:
    from trading_platform.dashboard import data

    assert data.resolve_runtime_dir({}).name == "runtime"
