"""Engine exit / EXIT_PENDING / data-quality tests on SYNTHETIC data."""

from __future__ import annotations

import json
from datetime import date

import pytest
from engine_fakes import (
    BASE_PRICE,
    TRADE_DATE,
    Harness,
    ScriptedStrategy,
    at,
    find,
    kinds,
    open_trade,
)

from trading_platform.core.enums import SignalDirection
from trading_platform.live.models import EngineState, PositionStatus


def test_missing_bid_gives_exit_pending_then_resolves_without_inventing_a_fill(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    # stop hit, but the poll carries NO option quote at all
    ev = h.poll(at(10, 8, 0), 25891.0)
    assert kinds(ev) == ["EXIT_PENDING"] and h.rows() == []
    pos = h.engine.position
    assert pos.status == PositionStatus.EXIT_PENDING and pos.exit_pending_reason.value == "STOP"
    cp = json.loads(h.ckpt.read_text())
    assert (
        cp["position"]["status"] == "EXIT_PENDING"
        and cp["position"]["exit_pending_reason"] == "STOP"
    )
    # retry every poll: still nothing, the event is announced ONCE
    ev = h.poll(at(10, 8, 1), 25895.0)
    assert ev == [] and h.rows() == []
    snap = h.engine.snapshot()
    assert snap["position"]["status"] == "EXIT_PENDING"
    assert snap["position"]["exit_pending_reason"] == "STOP"
    assert snap["account"]["cash"] < 250000.0  # nothing was credited
    # a bid with received_at >= trigger arrives -> closes at THAT bid - slippage
    ev = h.poll(at(10, 8, 3), 25895.0, bid=80.0, ask=81.0, side="PE")
    t = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    assert t["exit_reason"] == "STOP" and float(t["exit_premium"]) == 79.5
    detail = json.loads(t["exit_detail_json"])
    assert detail["was_exit_pending"] is True and detail["spot_at_trigger"] == 25891.0
    assert h.engine.position is None


def test_bid_received_before_the_trigger_is_not_used(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 7, 0), BASE_PRICE, bid=95.0, ask=96.0, side="PE")  # a fine, fresh bid at 10:07:00
    ev = h.poll(at(10, 7, 2), 25891.0, bid=95.0, ask=96.0, side="PE", quote_t=at(10, 7, 1))
    assert kinds(ev) == ["EXIT_PENDING"]  # bid is fresh (1s) but older than the 10:07:02 trigger
    assert "before the trigger" in ev[0].payload["why_pending"]
    ev = h.poll(at(10, 7, 3), 25892.0, bid=94.0, ask=95.0, side="PE")
    assert find(ev, "TRADE_CLOSED")


def test_stale_bid_is_not_used_to_exit(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    ev = h.poll(at(10, 7, 9), 25891.0, bid=95.0, ask=96.0, side="PE", quote_t=at(10, 7, 2))
    assert kinds(ev) == ["EXIT_PENDING"] and h.rows() == []


def test_entries_blocked_while_exit_pending(tmp_path):
    strategies = {
        "PDS": ScriptedStrategy("PDS", {"10:00": {"direction": SignalDirection.SELL}}),
        "ORB": ScriptedStrategy("ORB", {"10:05": {"direction": SignalDirection.BUY}}),
    }
    h = Harness(tmp_path, strategies=strategies)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    h.poll(at(10, 5, 3), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    h.poll(at(10, 6, 0), 25891.0)  # stop, no bid -> pending
    assert h.engine.position.status == PositionStatus.EXIT_PENDING
    ev = h.send_candle(10, 5, delay=1.0)
    rej = find(ev, "ENTRY_REJECTED")
    assert rej[0].payload["rejection"] == "EXIT_PENDING"


def test_stale_spot_makes_no_exit_decision_but_squareoff_proceeds(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 6, 0), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    # spot tick is 20s old when received (stale): beyond the stop, yet NO exit decision
    ev = h.poll(at(10, 7, 0), 25900.0, bid=60.0, ask=61.0, side="PE", spot_t=at(10, 6, 40))
    assert find(ev, "TRADE_CLOSED") == [] and h.engine.position.status == PositionStatus.OPEN
    # NB: spot_t only sets received_at-- make it truly stale via the clock
    h.set(at(10, 7, 30))
    ev = h.engine.on_tick_clock()
    assert find(ev, "TRADE_CLOSED") == []
    assert h.engine.snapshot()["connection"]["stale"] is True
    # square-off by CLOCK does not need a fresh spot
    h.poll(at(15, 14, 59), no_spot=True, bid=61.0, ask=62.0, side="PE")
    ev = h.poll(at(15, 15, 1), no_spot=True, bid=61.0, ask=62.0, side="PE")
    t = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    assert t["exit_reason"] == "EOD"
    assert json.loads(t["exit_detail_json"])["exit_spot_basis"] == "last_known_or_trigger"


def test_out_of_order_spot_tick_is_ignored(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 6, 0), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    # an OLD tick (exchange time earlier than the last accepted one) beyond the stop
    ev = h.poll(at(10, 6, 5), 25895.0, bid=99.0, ask=100.0, side="PE", spot_t=at(10, 6, 5))
    assert find(ev, "TRADE_CLOSED")  # sanity: a normal in-order tick through the stop exits
    h2 = Harness(tmp_path / "b")
    open_trade(h2, "PE")
    h2.poll(at(10, 6, 10), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    from engine_fakes import spot_tick

    from trading_platform.live.providers.base import PollResult

    h2.set(at(10, 6, 12))
    old = spot_tick(25900.0, at(10, 6, 12), exchange_time=at(10, 6, 5))
    ev = h2.engine.on_poll(PollResult(spot=old, quotes={}))
    assert find(ev, "TRADE_CLOSED") == [] and h2.engine.position.status == PositionStatus.OPEN
    assert any("out-of-order" in n for n in h2.engine.snapshot()["connection"]["notes"])


def test_dead_feed_at_squareoff_still_starts_exit_pending_via_clock(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "CE")
    h.poll(at(10, 6, 0), BASE_PRICE, bid=99.0, ask=100.0, side="CE")
    ev = h.tick(at(15, 15, 0))  # no polls at all since 10:06
    pend = find(ev, "EXIT_PENDING")
    assert len(pend) == 1 and pend[0].payload["reason"] == "EOD"
    assert h.engine.position.status == PositionStatus.EXIT_PENDING
    assert h.rows() == [] and h.engine.account.cash < 250000.0


def test_unresolved_exit_after_close_is_loud_preserved_and_blocks_next_session(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.tick(at(15, 15, 0))
    ev = h.tick(at(15, 30, 1))
    sc = [e for e in find(ev, "STATE_CHANGE") if "UNRESOLVED" in e.text]
    assert sc and "RECOVERY_REQUIRED" in sc[0].text
    assert h.engine.state == EngineState.BLOCKED
    assert h.engine.position.status == PositionStatus.EXIT_PENDING
    res = h.engine.shutdown("end of day")
    assert res.position_preserved and res.unresolved and "EXIT_PENDING" in res.unresolved[0]
    cp = json.loads(h.ckpt.read_text())
    assert cp["position"]["status"] == "EXIT_PENDING"
    # next trading day: recovery_decision -> RECOVERY_REQUIRED (never auto-flattened)
    from test_core_helpers import cfg  # noqa: F401

    from trading_platform.live.providers.base import ManualClock

    tomorrow = date(2026, 10, 29)
    clock2 = ManualClock(at(9, 0, day=tomorrow))
    eng2 = h.new_engine(clock=clock2)
    r = eng2.start_session(tomorrow)
    assert not r.ok and r.state == EngineState.RECOVERY_REQUIRED
    assert "overnight position" in r.reason
    assert eng2.position is None  # not auto-resumed, not monitored
    assert json.loads(h.ckpt.read_text())["position"]["status"] == "EXIT_PENDING"  # untouched
    assert eng2.snapshot()["recovery"]["checkpoint_position"]["status"] == "EXIT_PENDING"


def test_mark_uses_bid_never_mid_and_snapshot_position_fields(tmp_path):
    h = Harness(tmp_path)
    _, fill = open_trade(h, "PE", ask=100.0, bid=90.0)  # spread 10 > 5 -> use tighter book
    assert h.engine.position is None  # wide book was refused
    h2 = Harness(tmp_path / "b")
    open_trade(h2, "PE", ask=100.0, bid=99.0)
    h2.poll(at(10, 6, 0), 25850.0 - 10, bid=104.0, ask=108.0, side="PE")
    snap = h2.engine.snapshot()
    p = snap["position"]
    assert p["current_bid"] == 104.0
    assert snap["account"]["unrealized_pnl"] == pytest.approx(
        (104.0 - 100.5) * 150
    )  # bid, not mid 106
    assert p["gross_pnl"] == pytest.approx((104.0 - 100.5) * 150)
    assert p["net_pnl_estimate"] < p["gross_pnl"]  # exit slippage + both charges
    assert p["dist_to_stop"] == pytest.approx(25890.0 - 25840.0) and p[
        "dist_to_target"
    ] == pytest.approx(25840.0 - 25780.0)
    assert p["holding_seconds"] == pytest.approx(57.0)
    assert p["pnl_pct"] == pytest.approx(
        p["net_pnl_estimate"] / p["premium_invested"] * 100, abs=0.01
    )
    assert p["remaining_cash"] == h2.engine.account.cash
    assert snap["account"]["equity"] == pytest.approx(
        snap["account"]["cash"] + snap["account"]["invested"] + snap["account"]["unrealized_pnl"]
    )


def test_closing_twice_is_impossible(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    pos = h.engine.position
    ctx = None
    ev = h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    assert find(ev, "TRADE_CLOSED")
    from trading_platform.live.engine import _ExitContext
    from trading_platform.live.models import ExitReason

    ctx = _ExitContext(ExitReason.TARGET, at(10, 9, 0), "x", None, at(10, 9, 0), 1.0, 2.0)
    q = h.engine._quotes[pos.contract.scrip_code]
    with pytest.raises(AssertionError):
        h.engine._finalize_exit(pos, ctx, q, at(10, 9, 1), [])
    assert len(h.rows()) == 1 and h.engine.account.cash > 250000.0


def test_trades_csv_failure_keeps_the_position_and_account_untouched(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    cash_before = h.engine.account.cash

    def boom(trade):
        raise OSError("disk full (SYNTHETIC)")

    monkeypatch.setattr(h.trades, "append_trade", boom)
    ev = h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    assert "ERROR" in kinds(ev) and find(ev, "TRADE_CLOSED") == []
    assert h.engine.position is not None and h.engine.account.cash == cash_before
    assert h.engine.account.has_open_position
    assert h.engine.state == EngineState.BLOCKED  # entries blocked, position preserved
    monkeypatch.undo()
    ev = h.poll(at(10, 9, 5), 25779.0, bid=120.0, ask=121.0, side="PE")
    assert find(ev, "TRADE_CLOSED") and len(h.rows()) == 1  # the next poll closes it cleanly


def test_event_journal_rejection_does_not_crash_or_block(tmp_path, monkeypatch):
    from trading_platform.live.journal import EventRejectedError

    h = Harness(tmp_path)
    calls = []

    def reject(kind, payload=None, t=None):
        calls.append(kind)
        if payload and "journal_note" not in payload:
            raise EventRejectedError("too big (SYNTHETIC)")
        return {}

    monkeypatch.setattr(h.events, "append_event", reject)
    _, fill = open_trade(h, "PE")
    assert find(fill, "TRADE_OPENED") and h.engine.position is not None
    assert h.engine.state == EngineState.RUNNING
    assert "TRADE_OPENED" in calls


def test_snapshot_writer_failure_never_stops_trading(tmp_path, monkeypatch):
    h = Harness(tmp_path)

    def boom(*a, **k):
        raise OSError("snapshot disk (SYNTHETIC)")

    monkeypatch.setattr(h.writer, "write", boom)
    _, fill = open_trade(h, "PE")
    assert find(fill, "TRADE_OPENED") and h.engine.state == EngineState.RUNNING


def test_heartbeat_tick_writes_snapshot(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.tick(at(9, 20))
    assert h.snap_path.exists()
    h.snap_path.unlink()
    h.tick(at(9, 21))
    data = json.loads(h.snap_path.read_text())
    assert data["written_at"].startswith("2026-10-28T09:21:00")
    assert TRADE_DATE.isoformat() == data["session"]["date"]


def test_realized_loss_over_limit_halts_and_rejects_later_signals(tmp_path):
    from engine_fakes import with_account
    from test_core_helpers import cfg

    strategies = {
        "PDS": ScriptedStrategy("PDS", {"10:00": {"direction": SignalDirection.SELL}}),
        "ORB": ScriptedStrategy("ORB", {"10:10": {"direction": SignalDirection.BUY}}),
    }
    h = Harness(tmp_path, strategies=strategies, config=with_account(cfg(), max_daily_loss_pct=0.2))
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    h.poll(at(10, 5, 3), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    ev = h.poll(at(10, 6, 0), 25891.0, bid=79.0, ask=80.0, side="PE")  # stop AND loss > 500
    t = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    assert t["exit_reason"] == "STOP"  # the market exit wins the label; the halt is still raised
    assert "RISK" in kinds(ev) and h.engine.state == EngineState.HALTED_RISK
    h.send_candle(10, 5, delay=1.0)
    ev = h.send_candle(10, 10, delay=1.0)
    assert find(ev, "ENTRY_REJECTED")[0].payload["rejection"] == "DAILY_LOSS_LIMIT"
    assert h.engine.pending is None and len(h.rows()) == 1


def test_risk_exit_stays_pending_without_a_bid_after_restart(tmp_path):
    from engine_fakes import with_account
    from test_core_helpers import cfg

    h = Harness(tmp_path, config=with_account(cfg(), max_daily_loss_pct=1.0))
    open_trade(h, "PE")
    h.poll(at(10, 7, 0), BASE_PRICE, bid=60.0, ask=61.0, side="PE")
    assert h.rows() and h.engine.state == EngineState.HALTED_RISK  # closed at once (bid was there)
    # a second session where the halt was raised but the exit could not be filled
    h2 = Harness(tmp_path / "b", config=with_account(cfg(), max_daily_loss_pct=1.0))
    open_trade(h2, "PE")
    h2.engine._risk_halted = True
    h2.engine._risk_reason = "SYNTHETIC halt"
    ev = h2.poll(at(10, 7, 0), BASE_PRICE)  # no quotes
    assert find(ev, "EXIT_PENDING")[0].payload["reason"] == "RISK"
    assert h2.engine.position.status == PositionStatus.EXIT_PENDING and h2.rows() == []
    ev = h2.poll(at(10, 7, 3), BASE_PRICE, bid=90.0, ask=91.0, side="PE")
    assert find(ev, "TRADE_CLOSED")[0].payload["trade"]["exit_reason"] == "RISK"


def test_stopped_engine_ignores_further_input(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.engine.shutdown("done")
    assert h.poll(at(10, 0, 0), BASE_PRICE) == [] and h.tick(at(10, 0, 1)) == []
    assert h.send_candle(9, 15) == []
