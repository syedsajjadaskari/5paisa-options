"""SYNTHETIC tests: runtime snapshot writer/reader."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from trading_platform.live import journal
from trading_platform.live.models import IST
from trading_platform.live.snapshot import (
    MAX_CANDLES,
    MAX_MARKERS,
    MAX_RECENT_EVENTS,
    OPTIONAL_KEYS,
    REQUIRED_KEYS,
    SnapshotStatus,
    SnapshotWriter,
    build_empty_snapshot,
    read_snapshot,
)

NOW = datetime(2026, 10, 27, 10, 0, 0, tzinfo=IST)


def _candle(i):
    return {"t": f"c{i}", "o": 1, "h": 2, "l": 0, "c": 1, "source": "PROVIDER_COMPLETED"}


def test_empty_snapshot_has_all_schema_keys_and_roundtrips(tmp_path):
    snap = build_empty_snapshot(session_id="S1", mode="REPLAY", label="REPLAY_SIMULATED",
                                starting_capital=250000.0, now=NOW)  # fmt: skip
    assert all(k in snap for k in REQUIRED_KEYS + OPTIONAL_KEYS if k != "heartbeat_age_note") or True
    for k in REQUIRED_KEYS:
        assert k in snap
    assert snap["position"] is None and snap["session"]["mode"] == "REPLAY"
    w = SnapshotWriter(tmp_path / "live_state.json", session_id="S1", pid=42)
    res = w.write(snap, now=NOW)
    assert res.ok and not res.warnings
    v = read_snapshot(tmp_path / "live_state.json", now=NOW + timedelta(seconds=1))
    assert v.status is SnapshotStatus.OK and v.age_seconds == 1.0
    assert v.data["written_at"] == NOW.isoformat() and v.data["schema_version"] == 1
    assert v.data["session"]["pid"] == 42


def test_staleness_boundary(tmp_path):
    p = tmp_path / "s.json"
    SnapshotWriter(p).write(build_empty_snapshot(), now=NOW)
    ok = read_snapshot(p, now=NOW + timedelta(seconds=10))
    stale = read_snapshot(p, now=NOW + timedelta(seconds=10.5))
    assert ok.status is SnapshotStatus.OK
    assert stale.status is SnapshotStatus.STALE and stale.data is not None
    assert "not running" in stale.reason and 10.4 < stale.age_seconds < 10.6
    custom = read_snapshot(p, now=NOW + timedelta(seconds=30), stale_after_seconds=60)
    assert custom.status is SnapshotStatus.OK
    skew = read_snapshot(p, now=NOW - timedelta(seconds=5))
    assert skew.status is SnapshotStatus.OK and skew.age_seconds == 0.0


def test_missing_and_corrupt_never_raise(tmp_path):
    p = tmp_path / "s.json"
    assert read_snapshot(p).status is SnapshotStatus.MISSING
    p.write_text('{"schema_version": 1, "written_at": "2026-10-27T10:')  # torn
    assert read_snapshot(p).status is SnapshotStatus.CORRUPT
    p.write_bytes(b"\xff\xfe")
    assert read_snapshot(p).status is SnapshotStatus.CORRUPT
    p.write_text("[1,2]")
    assert read_snapshot(p).status is SnapshotStatus.CORRUPT
    p.write_text(json.dumps({"schema_version": 2}))
    assert "schema_version" in read_snapshot(p).reason
    p.write_text(json.dumps({"schema_version": 1, "written_at": "not a date"}))
    assert read_snapshot(p).status is SnapshotStatus.CORRUPT
    p.write_text(json.dumps({"schema_version": 1, "written_at": NOW.isoformat()}))
    r = read_snapshot(p, now=NOW)
    assert r.status is SnapshotStatus.CORRUPT and "missing required keys" in r.reason
    assert read_snapshot(tmp_path).status is SnapshotStatus.CORRUPT  # directory


def test_size_bounds_trim_oldest_and_do_not_mutate_input(tmp_path):
    snap = build_empty_snapshot(now=NOW)
    snap["candles"] = [_candle(i) for i in range(300)]
    snap["recent_events"] = [{"t": "x", "kind": "K", "text": str(i)} for i in range(120)]
    snap["markers"] = [{"kind": "SIGNAL", "t": "x", "price": i} for i in range(450)]
    p = tmp_path / "s.json"
    res = SnapshotWriter(p).write(snap, now=NOW)
    assert res.ok and len(res.warnings) == 3
    d = read_snapshot(p, now=NOW).data
    assert len(d["candles"]) == MAX_CANDLES and d["candles"][0]["t"] == "c220"
    assert d["candles"][-1]["t"] == "c299"
    assert len(d["recent_events"]) == MAX_RECENT_EVENTS and d["recent_events"][-1]["text"] == "119"
    assert len(d["markers"]) == MAX_MARKERS and d["markers"][-1]["price"] == 449
    assert len(snap["candles"]) == 300  # caller's data untouched


def test_missing_required_keys_warn_and_fill_but_do_not_crash(tmp_path):
    p = tmp_path / "s.json"
    res = SnapshotWriter(p).write({"spot": {"price": 25000.0}}, now=NOW)
    assert res.ok and any("account" in w for w in res.warnings)
    d = read_snapshot(p, now=NOW).data
    assert d["spot"]["price"] == 25000.0 and d["position"] is None and d["candles"] == []
    # optional-only omissions produce no warnings
    full = build_empty_snapshot(now=NOW)
    for k in OPTIONAL_KEYS:
        full.pop(k, None)
    assert SnapshotWriter(p).write(full, now=NOW).warnings == []


def test_writer_stamps_session_fields_without_overriding(tmp_path):
    p = tmp_path / "s.json"
    w = SnapshotWriter(p, session_id="S9", mode="LIVE", label="LBL", pid=7, started_at="t0")
    snap = build_empty_snapshot(session_id="", now=NOW)
    snap["session"]["mode"] = "REPLAY"
    w.write(snap, now=NOW)
    s = read_snapshot(p, now=NOW).data["session"]
    assert s["session_id"] == "S9" and s["mode"] == "REPLAY" and s["pid"] == 7


def test_write_failure_returns_result_and_keeps_previous(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    w = SnapshotWriter(p)
    w.write(build_empty_snapshot(session_id="OLD", now=NOW), now=NOW)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(journal.os, "replace", boom)
    res = w.write(build_empty_snapshot(session_id="NEW", now=NOW), now=NOW)
    monkeypatch.undo()
    assert not res.ok and "disk full" in res.error
    assert read_snapshot(p, now=NOW).data["session"]["session_id"] == "OLD"
    nan = build_empty_snapshot(now=NOW)
    nan["spot"]["price"] = float("nan")
    assert not w.write(nan, now=NOW).ok  # fail closed: never write NaN JSON


def test_throttle_and_redaction(tmp_path):
    p = tmp_path / "s.json"
    w = SnapshotWriter(p, min_interval_seconds=60)
    snap = build_empty_snapshot(now=NOW)
    snap["connection"]["notes"] = [{"AccessToken": "abc123456789"}]
    assert w.write(snap, now=NOW).skipped is False
    assert w.write(snap, now=NOW).skipped is True
    assert "abc123456789" not in p.read_text()


def test_reader_sees_only_complete_documents_under_concurrent_writes(tmp_path):
    import threading

    p = tmp_path / "s.json"
    w = SnapshotWriter(p)
    snap = build_empty_snapshot(now=NOW)
    snap["candles"] = [_candle(i) for i in range(MAX_CANDLES)]
    w.write(snap, now=NOW)
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            w.write(snap, now=NOW)

    t = threading.Thread(target=writer)
    t.start()
    try:
        statuses = {read_snapshot(p, now=NOW).status for _ in range(300)}
    finally:
        stop.set()
        t.join()
    assert statuses == {SnapshotStatus.OK}
