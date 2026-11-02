"""Snapshot schema / watch list / shutdown / misc engine API tests on SYNTHETIC data."""

from __future__ import annotations

import json

import pytest
from engine_fakes import (
    BASE_PRICE,
    LABEL_REPLAY,
    LABEL_RESEARCH,
    TRADE_DATE,
    Harness,
    ScriptedStrategy,
    at,
    candle,
    find,
    open_trade,
    quote,
)

from trading_platform.core.enums import SignalDirection
from trading_platform.live.models import CandleSource, EngineState, RunMode
from trading_platform.live.snapshot import (
    MAX_CANDLES,
    OPTIONAL_KEYS,
    REQUIRED_KEYS,
    SnapshotStatus,
    read_snapshot,
)

TOP = {
    "schema_version", "written_at", "session", "connection", "spot", "current_candle", "quotes",
    "levels", "candles", "markers", "account", "risk", "strategies", "position", "last_event",
    "recent_events", "last_closed_trade",
}  # fmt: skip
SESSION = {
    "session_id", "date", "mode", "label", "pid", "started_at", "engine_state", "state_reason",
    "market_status",
}  # fmt: skip
CONNECTION = {
    "broker", "status", "last_valid_quote_at", "data_age_seconds", "stale", "candle_source",
    "candle_trusted", "notes",
}  # fmt: skip
SPOT = {"price", "exchange_time", "day_high", "day_low", "prev_close"}
ACCOUNT = {
    "starting_capital", "cash", "equity", "realized_pnl", "session_realized_pnl",
    "unrealized_pnl", "session_open_equity", "trades_today", "wins_today", "losses_today",
    "daily_loss_limit", "daily_loss_used",
}  # fmt: skip
RISK = {"state", "max_daily_loss_inr", "entry_cutoff", "square_off", "cutoff_passed"}
POSITION = {
    "trade_id", "strategy", "method", "direction", "option_type", "symbol", "strike", "expiry",
    "lots", "quantity", "entry_time", "entry_spot", "entry_bid", "entry_ask", "entry_premium",
    "current_bid", "stop_spot", "target_spot", "dist_to_stop", "dist_to_target", "gross_pnl",
    "net_pnl_estimate", "pnl_pct", "holding_seconds", "status", "entry_reason", "label",
    "exit_pending_reason", "remaining_cash",
}  # fmt: skip
STRATEGY = {
    "name", "label", "tier", "state", "last_evaluated_candle", "levels", "conditions_satisfied",
    "conditions_failed", "last_signal", "last_rejection", "trades_today", "pnl_today", "note",
}  # fmt: skip


def test_snapshot_schema_flat_session(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.poll(at(10, 0, 5), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    h.poll(at(10, 0, 6), BASE_PRICE, bid=99.0, ask=100.0, side="CE")
    h.engine.set_forming_candle(candle(10, 0, source=CandleSource.SAMPLED_TICKS))
    h.engine.set_connection_info({"status": "CONNECTED", "notes": ["provider ok (SYNTHETIC)"]})
    s = h.engine.snapshot()
    assert TOP <= set(s) and set(REQUIRED_KEYS) <= set(s) and set(OPTIONAL_KEYS) <= set(s)
    assert s["schema_version"] == 1
    assert SESSION <= set(s["session"]) and s["session"]["mode"] == "LIVE"
    assert s["session"]["label"] == LABEL_RESEARCH and s["session"]["market_status"] == "OPEN"
    assert CONNECTION <= set(s["connection"]) and s["connection"]["status"] == "CONNECTED"
    assert s["connection"]["candle_source"] == "PROVIDER_COMPLETED"
    assert s["connection"]["candle_trusted"] is True and s["connection"]["stale"] is False
    assert "provider ok (SYNTHETIC)" in s["connection"]["notes"]
    assert SPOT <= set(s["spot"]) and s["spot"]["price"] == BASE_PRICE
    assert s["current_candle"]["authoritative"] is False
    assert s["current_candle"]["source"] == "SAMPLED_TICKS"
    assert {"CE", "PE"} == set(s["quotes"]) and s["quotes"]["PE"]["bid"] == 99.0
    assert s["quotes"]["CE"]["strike"] == 25850.0 and s["quotes"]["PE"]["ask"] == 100.0
    assert s["levels"]["PDH"] == 26000.0 and s["levels"]["support_zone"] == [25700.0, 25740.0]
    assert len(s["candles"]) == 9 and s["candles"][0]["source"] == "PROVIDER_COMPLETED"
    assert {"t", "o", "h", "l", "c", "source"} <= set(s["candles"][0])
    assert ACCOUNT <= set(s["account"]) and s["account"]["daily_loss_limit"] == 25000.0
    assert RISK <= set(s["risk"]) and s["risk"]["state"] == "OK" and not s["risk"]["cutoff_passed"]
    assert s["risk"]["entry_cutoff"] == "14:30" and s["risk"]["square_off"] == "15:15"
    assert s["position"] is None and s["last_closed_trade"] is None
    assert s["strategies"][0].keys() >= STRATEGY
    json.dumps(s)  # fully JSON-serialisable


def test_snapshot_schema_with_position_and_markers(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 6, 0), 25840.0, bid=104.0, ask=105.0, side="PE")
    s = h.engine.snapshot()
    assert POSITION <= set(s["position"])
    assert s["position"]["status"] == "OPEN" and s["position"]["label"] == LABEL_RESEARCH
    kinds_ = [m["kind"] for m in s["markers"]]
    assert kinds_ == ["SIGNAL", "ENTRY"]
    assert all(
        {"kind", "t", "price", "strategy", "trade_id", "text"} <= set(m) for m in s["markers"]
    )
    assert (
        s["markers"][1]["price"] == BASE_PRICE
        and s["markers"][1]["trade_id"] == s["position"]["trade_id"]
    )
    row = s["strategies"][0]
    assert row["state"] == "IN_TRADE" and row["last_signal"]["signal_id"].startswith("PDS:")
    assert s["last_event"]["kind"] in {"TRADE_OPENED", "STATE_CHANGE"}
    h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    s = h.engine.snapshot()
    assert s["position"] is None and s["last_closed_trade"]["exit_reason"] == "TARGET"
    assert [m["kind"] for m in s["markers"]] == ["SIGNAL", "ENTRY", "EXIT"]
    assert s["account"]["trades_today"] == 1 and s["account"]["wins_today"] == 1
    assert s["strategies"][0]["trades_today"] == 1 and s["strategies"][0]["pnl_today"] > 0


def test_snapshot_is_bounded(tmp_path):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS")})
    h.start()
    h.warm((15, 30))
    s = h.engine.snapshot()
    assert len(s["candles"]) <= MAX_CANDLES and len(s["recent_events"]) <= 50
    assert len(s["candles"]) == 75 - 0 or len(s["candles"]) == MAX_CANDLES
    assert len(s["markers"]) <= 200


def test_written_snapshot_is_readable_and_fresh(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    view = read_snapshot(h.snap_path, now=h.clock.now())
    assert view.status == SnapshotStatus.OK
    assert view.data["position"]["trade_id"] == h.engine.position.trade_id
    assert view.data["session"]["session_id"] == "SYNTH-S1"


def test_forced_writes_bypass_the_writer_throttle_for_trade_events(tmp_path):
    h = Harness(tmp_path, min_interval=3600.0)
    open_trade(h, "PE")
    data = json.loads(h.snap_path.read_text())
    assert data["position"] is not None  # TRADE_OPENED snapshot was not throttled away
    h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    data = json.loads(h.snap_path.read_text())
    assert data["position"] is None and data["last_closed_trade"] is not None
    assert h.engine.snapshot_writer.min_interval_seconds == 3600.0  # restored


def test_replay_snapshot_and_records_carry_replay_label(tmp_path):
    h = Harness(tmp_path, mode=RunMode.REPLAY)
    h.start()
    for m in range(15, 60, 5):
        h.send_candle(9, m, source=CandleSource.SIMULATED_REPLAY)
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0, source=CandleSource.SIMULATED_REPLAY)
    h.poll(at(10, 5, 3), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    s = h.engine.snapshot()
    assert s["session"]["mode"] == "REPLAY" and s["session"]["label"] == LABEL_REPLAY
    assert s["position"]["label"] == LABEL_REPLAY
    h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    assert h.rows()[0]["label"] == LABEL_REPLAY
    assert json.loads(h.ckpt.read_text())["run_mode"] == "REPLAY"


def test_watch_scrips_order_and_limit(tmp_path):
    h = Harness(tmp_path)
    h.start()
    # before any spot: candidate pair from the previous close (25820 -> ATM 25800)
    w0 = h.engine.watch_scrips()
    assert len(w0) == 2
    open_trade(h, "PE")
    held = h.engine.position.contract.scrip_code
    h.poll(at(10, 6, 0), 25880.0, bid=99.0, ask=100.0, side="PE")  # ATM moved to 25900
    w = h.engine.watch_scrips()
    assert w[0] == held and len(w) == 3 and len(set(w)) == 3
    assert h.engine.pending is None


def test_watch_scrips_pending_entry_second(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    pend = h.engine.pending.contract.scrip_code
    w = h.engine.watch_scrips()
    assert w[0] == pend and len(w) <= 8 and len(w) == len(set(w))


def test_shutdown_preserves_open_position_and_reports_it(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    res = h.engine.shutdown("Ctrl-C")
    assert res.checkpoint_written and res.position_preserved
    assert any("OPEN" in u and "NOT closed" in u for u in res.unresolved)
    assert res.final_state == EngineState.STOPPED
    cp = json.loads(h.ckpt.read_text())
    assert cp["position"]["status"] == "OPEN" and h.rows() == []
    assert h.journal_events("SHUTDOWN")


def test_shutdown_flat_and_pending_entry_cancelled(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    res = h.engine.shutdown("done")
    assert not res.position_preserved and any("pending entry" in u for u in res.unresolved)
    assert h.rows() == [] and h.engine.account.cash == 250000.0


def test_internal_error_fails_safe_position_preserved_entries_blocked(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    open_trade(h, "PE")

    def boom(*a, **k):
        raise ZeroDivisionError("SYNTHETIC bug")

    monkeypatch.setattr(h.engine, "_exit_trigger", boom)
    ev = h.poll(at(10, 6, 0), 25891.0, bid=99.0, ask=100.0, side="PE")
    assert [e.kind for e in ev if e.kind in ("ERROR", "STATE_CHANGE")][:2] == [
        "ERROR",
        "STATE_CHANGE",
    ]
    assert h.engine.state == EngineState.BLOCKED
    assert h.engine.position is not None and h.rows() == []
    # repeated identical errors are throttled in the event stream but keep failing safe
    ev = h.poll(at(10, 6, 1), 25891.0, bid=99.0, ask=100.0, side="PE")
    assert [e.kind for e in ev] == []
    assert h.engine.position is not None


def test_entries_blocked_in_blocked_state(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.engine.state = EngineState.BLOCKED
    h.engine.state_reason = "SYNTHETIC block"
    h.poll(at(9, 59, 58), BASE_PRICE)
    ev = h.send_candle(10, 0, delay=1.0)
    rej = find(ev, "ENTRY_REJECTED")
    assert (
        rej[0].payload["rejection"] == "ENGINE_NOT_RUNNING"
        and "SYNTHETIC block" in rej[0].payload["detail"]
    )


def test_label_on_every_record(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    assert h.rows()[0]["label"] == LABEL_RESEARCH
    for kind in ("SIGNAL", "TRADE_OPENED", "TRADE_CLOSED", "ENTRY_PENDING"):
        assert all(e["payload"]["label"] for e in h.journal_events(kind))
    assert json.loads(h.ckpt.read_text())["label"] == LABEL_RESEARCH
    assert quote(1, 1, 1, at(10, 0)).bid == 1 and SignalDirection.BUY and TRADE_DATE
    assert pytest
