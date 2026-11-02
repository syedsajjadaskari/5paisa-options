"""Engine lifecycle tests on SYNTHETIC data: TARGET / STOP / EOD / RISK for CE and PE."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from engine_fakes import (
    BASE_PRICE,
    LABEL_RESEARCH,
    TRADE_DATE,
    Harness,
    at,
    find,
    kinds,
    open_trade,
    with_account,
)

from trading_platform.live import costs
from trading_platform.live.models import EngineState, PositionStatus

# BUY (CE): stop 25810 / target 25920 ; SELL (PE): stop 25890 / target 25780  (40 / 70 points)
GEOMETRY = {
    "CE": {"stop": 25810.0, "target": 25920.0, "stop_px": 25809.0, "target_px": 25921.0},
    "PE": {"stop": 25890.0, "target": 25780.0, "stop_px": 25891.0, "target_px": 25779.0},
}


def test_entry_fill_is_ask_plus_slippage_with_rebased_levels(tmp_path):
    h = Harness(tmp_path)
    sig_events, fill_events = open_trade(h, "PE", ask=100.0, bid=99.0)
    assert kinds(sig_events)[:2] == ["SIGNAL", "ENTRY_PENDING"]
    opened = find(fill_events, "TRADE_OPENED")
    assert len(opened) == 1
    pos = opened[0].payload["position"]
    assert pos["entry_premium"] == 100.5  # ask 100 + 0.5 slippage
    assert pos["entry_ask"] == 100.0 and pos["entry_bid"] == 99.0
    assert pos["option_type"] == "PE" and pos["direction"] == "BEARISH"
    assert pos["lots"] == 2 and pos["quantity"] == 150 and pos["lot_size"] == 75
    assert pos["stop_spot"] == 25890.0 and pos["target_spot"] == 25780.0
    assert pos["label"] == LABEL_RESEARCH
    assert pos["remaining_cash"] == h.engine.account.cash
    assert pos["remaining_cash"] == pytest.approx(
        250000.0 - 100.5 * 150 - pos["entry_charges"], abs=0.01
    )
    # checkpoint was written with the OPEN position before the event was returned
    cp = json.loads(h.ckpt.read_text())
    assert cp["position"]["status"] == "OPEN" and cp["position"]["trade_id"] == pos["trade_id"]
    assert h.engine.state == EngineState.RUNNING


def test_stop_and_target_are_rebased_to_entry_spot_not_signal_reference(tmp_path):
    h = Harness(tmp_path)
    h.strategies["PDS"].script = {"10:00": {}}
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    # the market drifted 6 points in the signal's favour (SELL) before the quote arrived
    ev = h.poll(at(10, 5, 3), BASE_PRICE - 6, bid=99.0, ask=100.0, side="PE")
    pos = find(ev, "TRADE_OPENED")[0].payload["position"]
    assert pos["entry_spot"] == BASE_PRICE - 6
    assert pos["stop_spot"] == BASE_PRICE - 6 + 40 and pos["target_spot"] == BASE_PRICE - 6 - 70


@pytest.mark.parametrize("side", ["CE", "PE"])
def test_target_exit_full_lifecycle(tmp_path, side):
    h = Harness(tmp_path)
    open_trade(h, side)
    g = GEOMETRY[side]
    ev = h.poll(at(10, 9, 0), g["target_px"], bid=120.0, ask=121.0, side=side)
    closed = find(ev, "TRADE_CLOSED")
    assert len(closed) == 1
    t = closed[0].payload["trade"]
    assert t["exit_reason"] == "TARGET"
    assert float(t["exit_premium"]) == 119.5  # bid 120 - 0.5
    assert float(t["entry_premium"]) == 100.5
    assert float(t["gross_pnl"]) == pytest.approx((119.5 - 100.5) * 150)
    assert t["label"] == LABEL_RESEARCH
    assert h.engine.position is None
    rows = h.rows()
    assert len(rows) == 1 and rows[0]["trade_id"] == t["trade_id"]
    cp = json.loads(h.ckpt.read_text())
    assert cp["position"] is None and cp["closed_trade_ids_today"] == [t["trade_id"]]
    assert h.engine.snapshot()["last_closed_trade"]["trade_id"] == t["trade_id"]


@pytest.mark.parametrize("side", ["CE", "PE"])
def test_stop_exit_full_lifecycle(tmp_path, side):
    h = Harness(tmp_path)
    open_trade(h, side)
    g = GEOMETRY[side]
    ev = h.poll(at(10, 8, 0), g["stop_px"], bid=70.0, ask=71.0, side=side)
    t = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    assert t["exit_reason"] == "STOP"
    assert float(t["net_pnl"]) < 0
    detail = json.loads(t["exit_detail_json"])
    assert "stop" in detail["condition"] and detail["spot_at_trigger"] == g["stop_px"]
    assert detail["option_exit_bid"] == 70.0
    assert detail["original_stop_spot"] == g["stop"]


@pytest.mark.parametrize("side", ["CE", "PE"])
def test_eod_square_off_by_clock_with_cached_bid(tmp_path, side):
    h = Harness(tmp_path)
    open_trade(h, side)
    # keep the feed alive up to 15:14, then the clock alone drives the square-off
    h.poll(at(15, 14, 58), BASE_PRICE, bid=101.0, ask=102.0, side=side)
    assert h.engine.position is not None
    ev = h.tick(at(15, 15, 0))
    assert find(ev, "TRADE_CLOSED") == []  # bid was received before 15:15:00 -> not valid for it
    assert h.engine.position.status == PositionStatus.EXIT_PENDING
    assert find(ev, "EXIT_PENDING")
    ev = h.poll(at(15, 15, 2), BASE_PRICE, bid=101.0, ask=102.0, side=side)
    t = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    assert t["exit_reason"] == "EOD"
    assert h.engine.position is None


@pytest.mark.parametrize("side", ["CE", "PE"])
def test_daily_loss_halts_and_closes_with_risk(tmp_path, side):
    c = with_account(__import__("test_core_helpers").cfg(), max_daily_loss_pct=1.0)
    h = Harness(tmp_path, config=c)
    open_trade(h, side)
    # bid collapses: unrealized ~ -(100.5-60)*150 = -6075 => far beyond 1% (2500) of 250000
    ev = h.poll(at(10, 7, 0), BASE_PRICE, bid=60.0, ask=61.0, side=side)
    assert "RISK" in kinds(ev)
    t = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    assert t["exit_reason"] == "RISK"
    assert h.engine.state == EngineState.HALTED_RISK
    snap = h.engine.snapshot()
    assert snap["risk"]["state"] == "HALTED"
    assert snap["account"]["daily_loss_used"] > snap["account"]["daily_loss_limit"] - 1


def test_charges_applied_exactly_once_and_balance_reconciles(tmp_path):
    h = Harness(tmp_path)
    _, fill = open_trade(h, "PE", ask=100.0, bid=99.0)
    pos = find(fill, "TRADE_OPENED")[0].payload["position"]
    ev = h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    t = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    qty = 150
    entry_chg = costs.charges("BUY", TRADE_DATE, 100.5, qty).total
    exit_chg = costs.charges("SELL", TRADE_DATE, 119.5, qty).total
    assert float(t["entry_charges"]) == pytest.approx(entry_chg, abs=0.01)
    assert float(t["exit_charges"]) == pytest.approx(exit_chg, abs=0.01)
    assert float(t["total_charges"]) == pytest.approx(entry_chg + exit_chg, abs=0.01)
    gross = (119.5 - 100.5) * qty
    assert float(t["gross_pnl"]) == pytest.approx(gross)
    assert float(t["net_pnl"]) == pytest.approx(gross - entry_chg - exit_chg, abs=0.01)
    # balance_after == start + net; cash == balance_after; ledger fully reconciled
    assert float(t["balance_after"]) == pytest.approx(250000.0 + float(t["net_pnl"]), abs=0.01)
    acct = h.engine.account
    assert acct.cash == pytest.approx(float(t["balance_after"]), abs=0.01)
    assert acct.state.realized_pnl == pytest.approx(float(t["net_pnl"]), abs=0.01)
    assert pos["entry_charges"] == pytest.approx(entry_chg, abs=0.01)
    # slippage is only in the fill price: charges were computed at the fill prices, once
    assert float(t["entry_slippage"]) == 0.5 and float(t["exit_slippage"]) == 0.5
    # exactly one ledger row
    assert len(h.rows()) == 1


def test_exit_bid_floor_never_below_five_paise(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    ev = h.poll(at(10, 8, 0), 25891.0, bid=0.3, ask=0.6, side="PE")
    t = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    assert float(t["exit_premium"]) == 0.05


def test_no_second_entry_while_position_open_and_signal_rejected(tmp_path):
    from engine_fakes import ScriptedStrategy

    from trading_platform.core.enums import SignalDirection

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
    ev = h.send_candle(10, 5, delay=1.0)
    rej = find(ev, "ENTRY_REJECTED")
    assert rej and rej[0].payload["rejection"] == "POSITION_OPEN"
    assert len(h.engine.executed_signal_ids) == 2


def test_event_journal_is_compact_and_has_no_market_data(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    events = h.events.read_events(TRADE_DATE)
    assert {"SIGNAL", "ENTRY_PENDING", "TRADE_OPENED", "TRADE_CLOSED"} <= {
        e["kind"] for e in events
    }
    text = (h.root / "journal" / f"events_{TRADE_DATE:%Y%m%d}.jsonl").read_text()
    assert "candles" not in text and "quotes" not in text
    assert max(len(line) for line in text.splitlines()) < 8192
    sig = next(e for e in events if e["kind"] == "SIGNAL")
    assert sig["payload"]["signal_id"] == "PDS:METHOD_1:20261028T1000"
    assert sig["payload"]["conditions"]["satisfied"] == [
        "synthetic_condition_a",
        "synthetic_condition_b",
    ]
    assert timedelta(0) < at(10, 5) - at(10, 0)
