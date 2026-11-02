"""Unit tests for dashboard.data loaders (SYNTHETIC files in tmp_path)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
from test_dash_synthetic import (
    DAY,
    synthetic_snapshot,
    synthetic_trade,
    write_snapshot,
    write_trades_csv,
)

from trading_platform.dashboard import data


def test_runtime_dir_env_override_and_default(tmp_path) -> None:
    assert data.resolve_runtime_dir({"PAPER_RUNTIME_DIR": str(tmp_path)}) == tmp_path
    assert data.resolve_runtime_dir({}) == data.PROJECT_ROOT / "runtime"
    assert data.resolve_runtime_dir({"PAPER_RUNTIME_DIR": "x/y"}) == data.PROJECT_ROOT / "x" / "y"


def test_parse_ts_variants() -> None:
    assert data.parse_ts("2026-10-26T10:00:00+05:30") == datetime(2026, 10, 26, 10, 0)
    assert data.parse_ts("2026-10-26T04:30:00+00:00") == datetime(2026, 10, 26, 10, 0)
    assert data.parse_ts("2026-10-26 10:00:00 IST") == datetime(2026, 10, 26, 10, 0)
    assert data.parse_ts("") is None and data.parse_ts("garbage") is None and data.parse_ts(None) is None


def test_snapshot_missing_corrupt_stale_ok(tmp_path) -> None:
    p = tmp_path / "live_state.json"
    assert data.load_snapshot(p).status == data.SNAPSHOT_MISSING
    p.write_text("{not json")
    assert data.load_snapshot(p).status == data.SNAPSHOT_CORRUPT
    p.write_text("[1,2]")
    assert data.load_snapshot(p).status == data.SNAPSHOT_CORRUPT

    now = datetime(2026, 10, 26, 10, 0, 30)
    write_snapshot(p, synthetic_snapshot(datetime(2026, 10, 26, 10, 0, 25)))
    ok = data.load_snapshot(p, now=now)
    assert ok.status == data.SNAPSHOT_OK and ok.active and ok.age_seconds == 5.0

    write_snapshot(p, synthetic_snapshot(datetime(2026, 10, 26, 8, 0, 0)))
    stale = data.load_snapshot(p, now=now)
    assert stale.status == data.SNAPSHOT_STALE and not stale.active
    assert stale.data is not None and "ago" in stale.message and stale.age_text() == "2.0h"

    write_snapshot(p, synthetic_snapshot(datetime(2026, 10, 26, 10, 0, 25), engine_state="STOPPED"))
    assert data.load_snapshot(p, now=now).status == data.SNAPSHOT_STOPPED


def test_snapshot_without_written_at_is_stale(tmp_path) -> None:
    p = tmp_path / "s.json"
    snap = synthetic_snapshot()
    snap.pop("written_at")
    write_snapshot(p, snap)
    info = data.load_snapshot(p)
    assert info.status == data.SNAPSHOT_STALE and info.age_seconds is None


def test_snapshot_wrong_mode_is_not_active(tmp_path) -> None:
    p = tmp_path / "s.json"
    write_snapshot(p, synthetic_snapshot(mode="REPLAY"))
    info = data.load_snapshot(p, expect_mode="LIVE")
    assert info.status == data.SNAPSHOT_WRONG_MODE and not info.active
    assert data.effective_snapshot(info) is None


def test_snapshot_candles_drop_invalid_rows_and_never_fill() -> None:
    snap = {"candles": [
        {"t": "2026-10-26T09:15:00+05:30", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "source": "PROVIDER_COMPLETED"},
        {"t": "2026-10-26T09:25:00+05:30", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "source": "PROVIDER_COMPLETED"},
        {"t": "bad", "o": 1, "h": 2, "l": 0.5, "c": 1.5},
        {"t": "2026-10-26T09:30:00+05:30", "o": None, "h": 2, "l": 0.5, "c": 1.5},
        "junk",
    ]}  # fmt: skip
    df = data.snapshot_candles(snap)
    assert list(df["time"]) == [datetime(2026, 10, 26, 9, 15), datetime(2026, 10, 26, 9, 25)]  # gap kept
    assert data.snapshot_candles(None).empty and data.snapshot_candles({}).empty


def test_snapshot_markers_normalised() -> None:
    ms = data.snapshot_markers(synthetic_snapshot(position=True))
    assert {m["kind"] for m in ms} == {"SIGNAL", "ENTRY"}
    assert data.snapshot_markers({"markers": [{"kind": "X", "t": "2026-10-26T09:15:00", "price": 1}]}) == []


def test_load_trades_missing_empty_and_typed(tmp_path) -> None:
    missing = data.load_trades(tmp_path / "nope.csv")
    assert not missing.exists and missing.df.empty
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    assert data.load_trades(empty).df.empty

    p = tmp_path / "journal" / "trades.csv"
    write_trades_csv(p, [synthetic_trade(1), synthetic_trade(2, net=-100.0, strategy="PDS")])
    td = data.load_trades(p)
    assert td.exists and not td.problems and len(td.df) == 2
    r = td.df.iloc[0]
    assert r["session_date"] == date.fromisoformat(DAY)
    assert r["entry_time"] == pd.Timestamp(f"{DAY} 09:50:10")
    assert r["net_pnl"] == 500.0 and r["expiry"] == date(2026, 10, 29)


def test_load_trades_tolerates_torn_tail_duplicates_and_missing_columns(tmp_path) -> None:
    p = tmp_path / "trades.csv"
    write_trades_csv(p, [synthetic_trade(1), synthetic_trade(1)])
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("T-SYNTH-009,2026-10-26,SY")  # torn final line
    td = data.load_trades(p)
    assert "T-SYNTH-001" in set(td.df["trade_id"])
    assert any("duplicate" in x for x in td.problems)

    q = tmp_path / "partial.csv"
    q.write_text("trade_id,strategy,net_pnl\nA,ORB,5\n")
    td2 = data.load_trades(q)
    assert len(td2.df) == 1 and any("lacks columns" in x for x in td2.problems)


def test_split_replay_rows() -> None:
    df = pd.DataFrame({"label": ["", "REPLAY_SIMULATED", "RESEARCH_PAPER_PROVISIONAL"]})
    live, replay = data.split_replay_rows(df)
    assert len(live) == 2 and len(replay) == 1


def test_load_source_separates_live_and_replay(tmp_path) -> None:
    write_snapshot(tmp_path / "live_state.json", synthetic_snapshot(mode="LIVE"))
    write_trades_csv(tmp_path / "journal" / "trades.csv",
                     [synthetic_trade(1), synthetic_trade(2, label="REPLAY_SIMULATED")])  # fmt: skip
    write_trades_csv(tmp_path / "replay" / "journal" / "trades.csv",
                     [synthetic_trade(3, label="REPLAY_SIMULATED")])  # fmt: skip
    live = data.load_source(tmp_path, data.SOURCE_LIVE)
    assert list(live.trades.df["trade_id"]) == ["T-SYNTH-001"] and live.excluded_rows == 1
    assert live.snapshot.active
    replay = data.load_source(tmp_path, data.SOURCE_REPLAY)
    assert list(replay.trades.df["trade_id"]) == ["T-SYNTH-003"] and replay.is_replay
    assert not replay.snapshot.active  # the LIVE snapshot is never shown as replay


def test_load_source_replay_snapshot_not_accepted_as_live(tmp_path) -> None:
    write_snapshot(tmp_path / "live_state.json", synthetic_snapshot(mode="REPLAY"))
    live = data.load_source(tmp_path, data.SOURCE_LIVE)
    assert live.snapshot.status == data.SNAPSHOT_WRONG_MODE
    assert data.load_source(tmp_path, data.SOURCE_REPLAY).snapshot.active


def test_events_skip_corrupt_lines_and_signal_markers(tmp_path) -> None:
    d = tmp_path / "journal"
    d.mkdir()
    (d / "events_20261026.jsonl").write_text(
        '{"seq":1,"t":"2026-10-26T09:30:00+05:30","kind":"SIGNAL","payload":'
        '{"strategy":"ORB","spot_reference":25005.0,"reason":"r"}}\n'
        "not-json\n"
        '{"seq":2,"t":"2026-10-26T09:31:00+05:30","kind":"SIGNAL","payload":{"strategy":"X"}}\n'
        '{"seq":3,"t":"2026-10-26T09:32:00+05:30","kind":"STATE","payload":{}}\n'
        '{"seq":4,"t":"2026-10-26T09:33'
    )
    events = data.load_events(d, date(2026, 10, 26))
    assert len(events) == 3
    ms = data.signal_markers_from_events(events)
    assert len(ms) == 1 and ms[0]["price"] == 25005.0 and ms[0]["strategy"] == "ORB"
    assert data.load_events(d, date(2026, 1, 1)) == []


def test_historical_loaders_missing_and_synthetic(tmp_path) -> None:
    assert data.load_historical(tmp_path / "x.parquet").empty
    ts = pd.date_range("2026-10-23 09:15", periods=3, freq="5min").append(
        pd.date_range("2026-10-26 09:15", periods=3, freq="5min"))
    df5 = pd.DataFrame({"timestamp": ts, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 0})
    p5 = tmp_path / "5m.parquet"
    df5.to_parquet(p5)
    h5 = data.load_historical(p5)
    assert data.historical_dates(h5) == [date(2026, 10, 23), date(2026, 10, 26)]
    assert len(data.candles_for_date(h5, date(2026, 10, 26))) == 3
    assert data.candles_for_date(h5, date(2026, 1, 1)).empty

    d1 = pd.DataFrame({"timestamp": pd.to_datetime(["2026-10-22", "2026-10-23"]), "open": 1.0,
                       "high": [10.0, 20.0], "low": [1.0, 2.0], "close": [5.0, 6.0], "volume": 0})
    p1 = tmp_path / "1d.parquet"
    d1.to_parquet(p1)
    lv = data.previous_day_levels(data.load_historical(p1), date(2026, 10, 26))
    assert (lv["PDH"], lv["PDL"], lv["PDC"]) == (20.0, 2.0, 6.0) and lv["prev_date"] == date(2026, 10, 23)
    assert data.previous_day_levels(data.load_historical(p1), date(2026, 10, 22)) == {}


def test_backtest_loaders(tmp_path) -> None:
    assert data.list_backtest_runs(tmp_path / "none") == []
    run = tmp_path / "runA"
    run.mkdir()
    (run / "summary.json").write_text('{"window": {}}')
    (run / "portfolio_trades.csv").write_text(
        "portfolio_trade_id,strategy_name,date,executed_entry_time,exit_time,direction,entry_price,"
        "stop_loss,target,exit_price,exit_reason,points_pnl,portfolio_decision\n"
        "P1,ORB,2025-08-01,2025-08-01 09:20:00 IST,2025-08-01 10:00:00 IST,BUY,1,0,2,2,TARGET,60,ACCEPTED\n"
        "P2,PDS,2025-08-01,2025-08-01 09:30:00 IST,2025-08-01 09:40:00 IST,SELL,1,2,0,2,STOP_LOSS,-40,REJECTED\n"
    )
    assert data.list_backtest_runs(tmp_path) == ["runA"]
    bt = data.load_backtest_run(tmp_path, "runA")
    assert not bt.problems and list(bt.trades["trade_id"]) == ["P1"]  # rejected row dropped
    assert bt.trades.iloc[0]["points_pnl"] == 60.0 and bt.trades.iloc[0]["date"] == date(2025, 8, 1)
    missing = data.load_backtest_run(tmp_path, "nope")
    assert missing.trades.empty and missing.problems


def test_strategy_disclosure_never_raises() -> None:
    d = data.strategy_disclosure("PDS")
    assert set(d) == {"manifest", "rules"}
    assert data.strategy_disclosure("NOT_A_STRATEGY") == {"manifest": [], "rules": []}


def test_format_age() -> None:
    assert data.format_age(None) == "unknown" and data.format_age(5) == "5s"
    assert data.format_age(600) == "10m" and data.format_age(3 * 3600) == "3.0h"
    assert data.format_age(timedelta(days=3).total_seconds()) == "3.0d"
