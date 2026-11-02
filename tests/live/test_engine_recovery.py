"""Engine restart / recovery tests on SYNTHETIC data."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from engine_fakes import (
    BASE_PRICE,
    TRADE_DATE,
    Harness,
    ScriptedStrategy,
    at,
    candle,
    find,
    open_trade,
)

from trading_platform.core.enums import SignalDirection
from trading_platform.live.models import EngineState, ExecutionTier, PositionStatus, RunMode
from trading_platform.live.providers.base import ManualClock


class Crash(BaseException):
    """Simulates a process death (not an Exception: handlers must not swallow it)."""


def day_candles(upto_hm: tuple[int, int]):
    out, t = [], at(9, 15)
    while t < at(*upto_hm):
        out.append(candle(t.hour, t.minute))
        t += timedelta(minutes=5)
    return out


def restart(h: Harness, now, **kw):
    """Fresh engine on the same files (new process); the strategies are fresh objects too."""
    h.strategies["PDS"] = ScriptedStrategy("PDS", {"10:00": {"direction": SignalDirection.SELL}})
    h.set(now)
    eng = h.new_engine(strategies=h.strategies, **kw)
    h.engine = eng
    return eng


def test_start_fresh_account_and_checkpoint(tmp_path):
    h = Harness(tmp_path)
    r = h.start()
    assert r.ok and r.state == EngineState.READY and r.recovery.startswith("START_FRESH")
    assert h.engine.account.session_open_equity == 250000.0
    cp = json.loads(h.ckpt.read_text())
    assert cp["run_mode"] == "LIVE" and cp["position"] is None
    assert h.journal_events("RECOVERY")
    assert h.strategies["PDS"].reset_calls == 1


def test_restart_with_open_position_resumes_without_duplicate_entry(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    tid = h.engine.position.trade_id
    cash = h.engine.account.cash
    h.poll(at(10, 6, 0), BASE_PRICE - 5, bid=104.0, ask=105.0, side="PE")

    eng = restart(h, at(10, 7, 0))
    r = eng.start_session(TRADE_DATE)
    assert r.ok and r.recovery.startswith("RESUME")
    assert eng.position is not None and eng.position.trade_id == tid
    assert eng.account.cash == cash and eng.account.has_open_position
    assert "PDS:METHOD_1:20261028T1000" in eng.executed_signal_ids
    # the provider re-supplies today's completed candles incl. the signal candle: silent
    ev = eng.restore_candles(day_candles((10, 5)))
    assert find(ev, "TRADE_OPENED") == [] and eng.pending is None
    rejected = find(ev, "ENTRY_REJECTED")
    assert rejected and rejected[0].payload["rejection"] == "DUPLICATE_SIGNAL"
    assert len(h.rows()) == 0
    # the restored position is monitored and closes exactly once
    ev = h.poll(at(10, 8, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    assert len(find(ev, "TRADE_CLOSED")) == 1 and len(h.rows()) == 1
    assert h.rows()[0]["trade_id"] == tid
    # restart again after the close: flat, no position, same ledger
    eng3 = restart(h, at(10, 20))
    r3 = eng3.start_session(TRADE_DATE)
    assert r3.ok and eng3.position is None
    assert eng3.account.cash == pytest.approx(float(h.rows()[0]["balance_after"]), abs=0.01)
    assert len(h.rows()) == 1
    assert eng3.account.state.trades_today == 1


def test_restored_position_keeps_levels_and_label(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    pos = h.engine.position
    eng = restart(h, at(10, 7, 0))
    eng.start_session(TRADE_DATE)
    p2 = eng.position
    assert (p2.stop_spot, p2.target_spot, p2.entry_premium, p2.label, p2.quantity) == (
        pos.stop_spot, pos.target_spot, pos.entry_premium, pos.label, pos.quantity,
    )  # fmt: skip
    assert eng.snapshot()["strategies"][0]["state"] == "IN_TRADE"


def test_exit_pending_survives_restart(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 8, 0), 25891.0)  # stop, no bid -> EXIT_PENDING
    eng = restart(h, at(10, 8, 30))
    r = eng.start_session(TRADE_DATE)
    assert r.ok and eng.position.status == PositionStatus.EXIT_PENDING
    assert eng.position.exit_pending_reason.value == "STOP"
    # bid received BEFORE the (restored) pending-since is not valid; a later one is
    h.engine = eng
    ev = h.poll(at(10, 8, 40), 25895.0, bid=80.0, ask=81.0, side="PE")
    t = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    assert t["exit_reason"] == "STOP" and float(t["exit_premium"]) == 79.5


def test_crash_after_csv_append_reconciles_without_duplicate_trade(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    tid = h.engine.position.trade_id

    import trading_platform.live.engine as eng_mod

    def die(*a, **k):
        raise Crash()

    monkeypatch.setattr(eng_mod, "save_checkpoint", die)
    with pytest.raises(Crash):
        h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    monkeypatch.undo()
    # CSV has the row; the checkpoint on disk still says OPEN (the crash window)
    assert [r["trade_id"] for r in h.rows()] == [tid]
    assert json.loads(h.ckpt.read_text())["position"]["status"] == "OPEN"

    eng = restart(h, at(10, 10, 0))
    r = eng.start_session(TRADE_DATE)
    assert r.ok and "RESUME" in r.recovery and "already closed" in r.recovery
    assert eng.position is None  # reconciled as CLOSED
    row = h.rows()[0]
    assert eng.account.cash == pytest.approx(float(row["balance_after"]), abs=0.01)
    assert eng.account.state.realized_pnl == pytest.approx(float(row["net_pnl"]), abs=0.01)
    assert eng.account.state.trades_today == 1 and not eng.account.has_open_position
    assert tid in eng.closed_trade_ids
    assert len(h.rows()) == 1  # no duplicate trade, ever
    # and the account can trade again afterwards (flat, consistent)
    cp = json.loads(h.ckpt.read_text())
    assert cp["position"] is None and cp["closed_trade_ids_today"] == [tid]


def test_corrupt_checkpoint_is_recovery_required_and_blocks_entries(tmp_path):
    h = Harness(tmp_path)
    h.ckpt.parent.mkdir(parents=True, exist_ok=True)
    h.ckpt.write_text("{ this is not json (SYNTHETIC)")
    r = h.start()
    assert not r.ok and r.state == EngineState.RECOVERY_REQUIRED
    assert "CORRUPT" in r.recovery
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    ev = h.send_candle(10, 0, delay=1.0)
    rej = find(ev, "ENTRY_REJECTED")
    assert rej[0].payload["rejection"] == "RECOVERY_REQUIRED"
    assert h.engine.pending is None and h.engine.position is None
    # evidence preserved: the corrupt file is never overwritten
    assert h.ckpt.read_text() == "{ this is not json (SYNTHETIC)"
    snap = h.engine.snapshot()
    assert snap["session"]["engine_state"] == "RECOVERY_REQUIRED"
    assert snap["recovery"]["reason"].startswith("checkpoint CORRUPT")
    assert h.engine.shutdown("test").checkpoint_written is False
    assert h.ckpt.read_text() == "{ this is not json (SYNTHETIC)"


def test_trades_today_without_checkpoint_is_recovery_required(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    h.ckpt.unlink()
    eng = restart(h, at(10, 20))
    r = eng.start_session(TRADE_DATE)
    assert not r.ok and "today's trades exist" in r.reason


def test_run_mode_mismatch_is_recovery_required(tmp_path):
    h = Harness(tmp_path)
    h.start()
    replay = Harness(tmp_path, mode=RunMode.REPLAY, root_name="rt")  # same files, other mode
    r = replay.start()
    assert not r.ok and "run_mode" in r.reason


def test_inconsistent_ledger_with_consistent_position_still_monitors_but_blocks_entries(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    # corrupt the ledger: a rogue trade row appears (account no longer reconciles)
    from test_persist_helpers import make_trade

    h.trades.append_trade(make_trade("ROGUE", net=500.0, balance=250500.0, day=TRADE_DATE))
    eng = restart(h, at(10, 7, 0))
    r = eng.start_session(TRADE_DATE)
    assert not r.ok and r.state == EngineState.RECOVERY_REQUIRED
    assert eng.position is not None and eng._monitor_allowed
    assert "monitoring allowed" in eng.snapshot()["recovery"]["monitoring"]
    # the open position is still protected: stop/target/EOD continue
    ev = h.poll(at(10, 8, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    assert find(ev, "TRADE_CLOSED")
    assert eng.state == EngineState.RECOVERY_REQUIRED  # entries stay blocked
    assert json.loads(h.ckpt.read_text())["engine_state"] == "RECOVERY_REQUIRED"


def test_overnight_position_is_never_monitored_or_resumed(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    tomorrow = date(2026, 10, 29)
    eng = h.new_engine(clock=ManualClock(at(9, 5, day=tomorrow)))
    r = eng.start_session(tomorrow)
    assert not r.ok and eng.position is None and not eng._monitor_allowed
    ev = eng.on_poll(
        __import__("trading_platform.live.providers.base", fromlist=["PollResult"]).PollResult(
            spot=None
        )
    )
    assert find(ev, "TRADE_CLOSED") == []


def test_new_day_after_flat_checkpoint_carries_account_forward(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    cash = h.engine.account.cash
    assert cash > 250000.0
    tomorrow = date(2026, 10, 29)
    eng = h.new_engine(clock=ManualClock(at(9, 5, day=tomorrow)))
    r = eng.start_session(tomorrow)
    assert r.ok and r.recovery.startswith("START_FRESH")
    assert eng.account.cash == pytest.approx(cash, abs=0.01)
    assert eng.account.session_open_equity == pytest.approx(cash, abs=0.01)
    assert eng.account.state.trades_today == 0
    assert eng.executed_signal_ids == []
    assert eng.closed_trade_ids == []


def test_shadow_only_session_resumes_cleanly_and_never_has_a_position(tmp_path):
    h = Harness(tmp_path, tiers={"PDS": ExecutionTier.SHADOW_ONLY})
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    eng = restart(h, at(10, 20))
    r = eng.start_session(TRADE_DATE)
    assert r.ok and r.recovery.startswith("RESUME") and eng.position is None
    assert eng.account.cash == 250000.0
    assert "PDS:METHOD_1:20261028T1000" in eng.executed_signal_ids


def test_halt_flag_survives_restart(tmp_path):
    from engine_fakes import with_account
    from test_core_helpers import cfg

    h = Harness(tmp_path, config=with_account(cfg(), max_daily_loss_pct=1.0))
    open_trade(h, "PE")
    h.poll(at(10, 7, 0), BASE_PRICE, bid=60.0, ask=61.0, side="PE")
    assert h.engine.state == EngineState.HALTED_RISK
    eng = restart(h, at(10, 20))
    r = eng.start_session(TRADE_DATE)
    assert r.ok is True and r.state == EngineState.HALTED_RISK


def test_same_day_inconsistent_position_is_not_managed(tmp_path):
    from test_persist_helpers import flat_account, make_cp, make_position

    from trading_platform.live.checkpoint import position_to_dict, save_checkpoint

    h = Harness(tmp_path)
    pos = make_position("P1", day=TRADE_DATE)
    cp = make_cp(day=TRADE_DATE, position=pos, account=flat_account())
    bad = position_to_dict(pos)
    bad["quantity"] = 76  # != lots x lot_size
    cp.position = bad
    save_checkpoint(h.ckpt, cp)
    before = h.ckpt.read_text()
    r = h.start()
    assert not r.ok and "inconsistent" in r.reason
    assert h.engine.position is None and not h.engine._monitor_allowed
    assert h.ckpt.read_text() == before  # untouched


def test_replay_mode_forces_a_replay_label_even_if_auth_label_is_wrong(tmp_path):
    h = Harness(tmp_path, mode=RunMode.REPLAY, label="RESEARCH_PAPER_PROVISIONAL")
    assert h.engine._auth_label("PDS") == "REPLAY_SIMULATED"
