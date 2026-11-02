"""SYNTHETIC tests: atomic writes, trades CSV, event journal, error log."""

from __future__ import annotations

import json
import logging
from datetime import datetime

import pytest
from test_persist_helpers import DAY, make_trade

from trading_platform.live import journal
from trading_platform.live.journal import (
    EventJournal,
    EventRejectedError,
    JournalSchemaError,
    TradeCsvJournal,
    atomic_write_json,
    atomic_write_text,
    get_error_logger,
)
from trading_platform.live.models import IST, TradeRecord

# ------------------------------------------------------------------ atomic writes


def test_atomic_write_roundtrip_and_no_tmp_left(tmp_path):
    p = tmp_path / "sub" / "a.json"
    atomic_write_json(p, {"x": 1, "d": DAY})
    assert json.loads(p.read_text()) == {"x": 1, "d": "2026-10-27"}
    assert [f.name for f in p.parent.iterdir()] == ["a.json"]


def test_atomic_write_crash_between_tmp_and_replace_keeps_old_file(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    atomic_write_text(p, "OLD")

    def boom(src, dst):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(journal.os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_text(p, "NEW-PARTIAL")
    monkeypatch.undo()
    assert p.read_text() == "OLD"
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"]  # tmp cleaned up


def test_hard_crash_leaving_tmp_file_does_not_affect_target(tmp_path):
    p = tmp_path / "state.json"
    atomic_write_text(p, "OLD")
    (tmp_path / ".state.json.99999.1.tmp").write_text("half writt")  # as if killed mid-write
    assert p.read_text() == "OLD"
    atomic_write_text(p, "NEWER")
    assert p.read_text() == "NEWER"


def test_atomic_write_rejects_nan(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_json(p, {"v": 1})
    with pytest.raises(ValueError):
        atomic_write_json(p, {"v": float("nan")})
    assert json.loads(p.read_text()) == {"v": 1}


# ------------------------------------------------------------------ trades CSV


def test_header_once_rows_roundtrip_and_columns(tmp_path):
    j = TradeCsvJournal(tmp_path / "journal" / "trades.csv")
    assert j.append_trade(make_trade("T1", 100)) is True
    assert j.append_trade(make_trade("T2", -40)) is True
    lines = j.path.read_text().split("\n")
    assert lines[0].split(",") == TradeRecord.csv_columns()
    assert sum(1 for ln in lines if ln.startswith("trade_id,")) == 1
    load = j.read_trades()
    assert load.clean and [r["trade_id"] for r in load.rows] == ["T1", "T2"]
    assert load.rows[0]["entry_reason"] == "multi line, with comma"  # newline flattened, comma kept
    df = j.load_trades_df()
    assert list(df["net_pnl"]) == [100.0, -40.0]
    assert j.path.stat().st_mode & 0o777 == 0o600


def test_duplicate_trade_id_is_noop_even_after_restart(tmp_path):
    path = tmp_path / "trades.csv"
    j = TradeCsvJournal(path)
    assert j.append_trade(make_trade("T1")) is True
    assert j.append_trade(make_trade("T1", net=999)) is False
    j2 = TradeCsvJournal(path)  # new process
    assert j2.append_trade(make_trade("T1")) is False
    assert [r["trade_id"] for r in j2.load_trades()] == ["T1"]
    assert j2.load_trades()[0]["net_pnl"] == "100.0"


def test_torn_last_line_is_ignored_reported_and_quarantined(tmp_path):
    path = tmp_path / "trades.csv"
    j = TradeCsvJournal(path)
    j.append_trade(make_trade("T1"))
    with open(path, "a") as f:
        f.write('T2,2026-10-27,"S1",SYNTH')  # crash mid-row, no newline, open quote
    load = TradeCsvJournal(path).read_trades()
    assert [r["trade_id"] for r in load.rows] == ["T1"]
    assert load.torn_tail and not load.clean
    assert any("torn" in p for p in load.problems())
    # next append repairs: torn bytes quarantined, new row is clean and loadable
    j2 = TradeCsvJournal(path)
    assert j2.append_trade(make_trade("T2", 5)) is True
    assert j2.repairs
    assert (tmp_path / "trades.csv.torn").read_text().startswith("T2,2026-10-27")
    load2 = j2.read_trades()
    assert load2.clean and [r["trade_id"] for r in load2.rows] == ["T1", "T2"]


def test_malformed_middle_row_reported_not_fatal(tmp_path):
    path = tmp_path / "trades.csv"
    j = TradeCsvJournal(path)
    j.append_trade(make_trade("T1"))
    with open(path, "a") as f:
        f.write("bad,row\n")
    j.append_trade(make_trade("T3"))
    load = TradeCsvJournal(path).read_trades()
    assert [r["trade_id"] for r in load.rows] == ["T1", "T3"]
    assert load.malformed and load.malformed[0][0] == 3


def test_column_validation_missing_and_refuse_append(tmp_path):
    path = tmp_path / "trades.csv"
    cols = TradeRecord.csv_columns()
    path.write_text(",".join(cols[:-1] + ["surprise"]) + "\n")
    j = TradeCsvJournal(path)
    load = j.read_trades()
    assert load.missing_columns == [cols[-1]] and load.unexpected_columns == ["surprise"]
    assert not load.schema_ok
    with pytest.raises(JournalSchemaError):
        j.append_trade(make_trade("T1"))
    assert path.read_text().count("\n") == 1  # untouched


def test_missing_file_loads_empty(tmp_path):
    j = TradeCsvJournal(tmp_path / "none.csv")
    assert j.load_trades() == [] and j.read_trades().file_exists is False


def test_replay_root_is_independent(tmp_path):
    live = TradeCsvJournal(tmp_path / "journal" / "trades.csv")
    replay = TradeCsvJournal(tmp_path / "replay" / "journal" / "trades.csv")
    live.append_trade(make_trade("T1"))
    replay.append_trade(make_trade("T1", label="REPLAY_SIMULATED"))
    assert live.load_trades()[0]["label"] == "SYNTHETIC_TEST"
    assert replay.load_trades()[0]["label"] == "REPLAY_SIMULATED"


# ------------------------------------------------------------------ events


def _t(h=10, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=IST)


def test_event_append_read_seq_and_ist_date_file(tmp_path):
    ej = EventJournal(tmp_path / "ev")
    r1 = ej.append_event("SIGNAL", {"signal_id": "S", "d": DAY, "t": _t()}, _t())
    r2 = ej.append_event("ENTRY", {"premium": 101.5}, _t(10, 5))
    assert (r1["seq"], r2["seq"]) == (1, 2)
    assert (tmp_path / "ev" / "events_20261027.jsonl").exists()
    assert [e["kind"] for e in ej.read_events(DAY)] == ["SIGNAL", "ENTRY"]
    # seq continues after restart
    assert EventJournal(tmp_path / "ev").append_event("EXIT", {}, _t(11))["seq"] == 3


def test_event_date_is_ist_not_utc(tmp_path):
    ej = EventJournal(tmp_path)
    utc = datetime.fromisoformat("2026-10-26T20:00:00+00:00")  # = 27 Oct 01:30 IST
    ej.append_event("STATE", {}, utc)
    assert (tmp_path / "events_20261027.jsonl").exists()


def test_event_torn_last_line_tolerated_and_isolated(tmp_path):
    ej = EventJournal(tmp_path)
    ej.append_event("A", {"n": 1}, _t())
    path = ej.path_for(DAY)
    with open(path, "a") as f:
        f.write('{"seq":2,"t":"2026-10-27T10:')  # torn
    rep = ej.read_events_with_report(DAY)
    assert [e["kind"] for e in rep.events] == ["A"] and len(rep.torn_lines) == 1
    ej2 = EventJournal(tmp_path)
    ej2.append_event("B", {}, _t(10, 1))
    assert [e["kind"] for e in ej2.read_events(DAY)] == ["A", "B"]


@pytest.mark.parametrize("key", ["ticks", "quotes", "candles", "Bars", "tick_history", "depth"])
def test_market_data_keys_refused(tmp_path, key):
    ej = EventJournal(tmp_path)
    with pytest.raises(EventRejectedError):
        ej.append_event("X", {"ctx": {key: [1, 2]}}, _t())
    assert not ej.path_for(DAY).exists()


def test_event_size_and_array_guards(tmp_path):
    ej = EventJournal(tmp_path)
    with pytest.raises(EventRejectedError):
        ej.append_event("X", {"blob": "a" * 9000}, _t())
    with pytest.raises(EventRejectedError):
        ej.append_event("X", {"vals": list(range(500))}, _t())
    with pytest.raises(EventRejectedError):
        ej.append_event("", {}, _t())
    ok = ej.append_event("X", {"vals": list(range(30)), "entry_quote": {"bid": 1, "ask": 2}}, _t())
    assert ok["seq"] == 1  # a single entry quote is a decision fact, allowed


def test_event_redaction(tmp_path):
    ej = EventJournal(tmp_path)
    ej.append_event(
        "ERROR",
        {
            "AccessToken": "abcdef123456",
            "msg": "login failed password=hunter2hunter2 Bearer abcdefghij1234567890",
            "nested": [{"api_key": "zzz"}],
        },
        _t(),
    )
    raw = ej.path_for(DAY).read_text()
    for leak in ("abcdef123456", "hunter2", "abcdefghij1234567890", "zzz"):
        assert leak not in raw
    assert "[REDACTED]" in raw


def test_events_are_one_json_object_per_line(tmp_path):
    ej = EventJournal(tmp_path)
    ej.append_event("A", {"text": "line1\nline2"}, _t())
    lines = ej.path_for(DAY).read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["payload"]["text"] == "line1\nline2"


# ------------------------------------------------------------------ error log


def test_error_logger_redacts_and_is_idempotent(tmp_path):
    lg = get_error_logger(tmp_path / "logs")
    assert get_error_logger(tmp_path / "logs") is lg and len(lg.handlers) == 1
    lg.error("login failed password=sekret12345 for user")
    try:
        raise RuntimeError("bad request token Bearer abcdefghijklmnop12345")
    except RuntimeError:
        lg.exception("boom")
    lg.info("below threshold")
    for h in lg.handlers:
        h.flush()
    text = (tmp_path / "logs" / "errors.log").read_text()
    assert "sekret12345" not in text and "abcdefghijklmnop12345" not in text
    assert "login failed" in text and "below threshold" not in text
    assert (tmp_path / "logs" / "errors.log").stat().st_mode & 0o777 == 0o600
    assert lg.propagate is False and lg.level == logging.WARNING
