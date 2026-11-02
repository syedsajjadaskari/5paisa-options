"""Engine entry / candle-gating tests on SYNTHETIC data."""

from __future__ import annotations

from engine_fakes import (
    BASE_PRICE,
    LABEL_REPLAY,
    Harness,
    RaisingStrategy,
    ScriptedStrategy,
    at,
    candle,
    find,
    kinds,
    quote,
    with_exec,
)

from trading_platform.core.enums import SignalDirection
from trading_platform.live.models import CandleSource, EngineState, ExecutionTier, RunMode

BUY, SELL = SignalDirection.BUY, SignalDirection.SELL


def rejection(events):
    ev = find(events, "ENTRY_REJECTED")
    return ev[0].payload["rejection"] if ev else None


def test_shadow_only_never_fills_and_is_first_class(tmp_path):
    h = Harness(tmp_path, tiers={"PDS": ExecutionTier.SHADOW_ONLY})
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    ev = h.send_candle(10, 0, delay=1.0)
    assert kinds(ev) == ["SIGNAL", "ENTRY_REJECTED"]
    assert rejection(ev) == "SHADOW_ONLY"
    h.poll(at(10, 5, 3), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    assert h.engine.position is None and h.engine.pending is None
    assert h.engine.account.cash == 250000.0 and h.rows() == []
    snap = h.engine.snapshot()
    row = next(r for r in snap["strategies"] if r["name"] == "PDS")
    assert row["state"] == "SIGNAL_SHADOW" and row["tier"] == "SHADOW_ONLY"
    assert row["last_signal"]["direction"] == "BEARISH"
    assert row["last_rejection"].startswith("SHADOW_ONLY")
    assert snap["position"] is None
    assert any(m["kind"] == "SIGNAL" for m in snap["markers"])
    assert not any(m["kind"] == "ENTRY" for m in snap["markers"])
    # the signal is journaled
    assert h.journal_events("SIGNAL") and h.journal_events("ENTRY_REJECTED")


def test_shadow_signal_does_not_take_the_slot_from_a_fill_capable_strategy(tmp_path):
    strategies = {
        "PDS": ScriptedStrategy("PDS", {"10:00": {"direction": SELL}}),
        "ORB": ScriptedStrategy("ORB", {"10:00": {"direction": BUY}}),
    }
    h = Harness(tmp_path, strategies=strategies, tiers={"PDS": ExecutionTier.SHADOW_ONLY})
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    ev = h.send_candle(10, 0, delay=1.0)
    rejs = {e.payload["strategy"]: e.payload["rejection"] for e in find(ev, "ENTRY_REJECTED")}
    assert rejs == {"PDS": "SHADOW_ONLY"}
    assert h.engine.pending is not None and h.engine.pending.signal.strategy == "ORB"


def test_rf_is_never_evaluated(tmp_path):
    rf = RaisingStrategy("RF")
    strategies = {"PDS": ScriptedStrategy("PDS"), "RF": rf}
    h = Harness(tmp_path, strategies=strategies, tiers={"RF": ExecutionTier.DISABLED})
    h.start()
    h.warm((10, 0))
    assert rf.evaluated == 0
    row = next(r for r in h.engine.snapshot()["strategies"] if r["name"] == "RF")
    assert row["state"] == "DISABLED" and row["tier"] == "DISABLED"


def test_strategy_without_authorization_is_disabled_by_default(tmp_path):
    ghost = ScriptedStrategy("ORB", {"09:15": {"direction": BUY}})
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS"), "ORB": ghost})
    h.auth.pop("ORB")
    h.engine = h.new_engine()
    h.start()
    h.warm((9, 30))
    assert ghost.evaluated == 0


def test_cutoff_boundary_candle_closing_at_1430_is_rejected(tmp_path):
    script = {"14:20": {"direction": SELL}, "14:25": {"direction": SELL}}
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS", script)})
    h.start()
    h.warm((14, 20))
    h.poll(at(14, 24, 58), BASE_PRICE)
    ev = h.send_candle(14, 20, delay=1.0)  # closes 14:25:00 -> still allowed
    assert h.engine.pending is not None and rejection(ev) is None
    h.poll(at(14, 25, 3), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    assert h.engine.position is not None  # filled at 14:25:03 (< 14:30)
    h.poll(at(14, 29, 58), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    ev = h.send_candle(14, 25, delay=1.0)  # closes 14:30:00 -> cutoff, the exact reason
    rej = find(ev, "ENTRY_REJECTED")
    assert rej[0].payload["rejection"] == "AFTER_ENTRY_CUTOFF"
    assert "14:30:00" in rej[0].payload["detail"]


def test_cutoff_blocks_a_flat_account_signal_on_the_1425_candle(tmp_path):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS", {"14:25": {}})})
    h.start()
    h.warm((14, 25))
    h.poll(at(14, 29, 58), BASE_PRICE)
    ev = h.send_candle(14, 25, delay=1.0)
    rej = find(ev, "ENTRY_REJECTED")
    assert rej[0].payload["rejection"] == "AFTER_ENTRY_CUTOFF"
    assert h.engine.pending is None and h.engine.position is None


def test_fill_after_cutoff_is_rejected_even_if_signal_candle_was_early(tmp_path):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS", {"14:20": {}})})
    h.start()
    h.warm((14, 20))
    h.poll(at(14, 24, 58), BASE_PRICE)
    h.send_candle(14, 20, delay=1.0)
    assert h.engine.pending is not None
    ev = h.poll(at(14, 30, 0), BASE_PRICE, bid=99.0, ask=100.0, side="PE", quote_t=at(14, 29, 59))
    # 14:25:01 + 30s timeout has long passed -> ENTRY_TIMEOUT is the first exact reason
    assert rejection(ev) in {"ENTRY_TIMEOUT", "AFTER_ENTRY_CUTOFF"}
    assert h.engine.position is None


def test_duplicate_signal_id_from_checkpoint_is_rejected(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.engine.executed_signal_ids.append("PDS:METHOD_1:20261028T1000")
    h.engine._save_checkpoint()
    # a restart (same-day flat checkpoint -> RESUME) keeps the executed id
    eng2 = h.new_engine()
    res = eng2.start_session(at(9, 0).date())
    assert res.ok and "PDS:METHOD_1:20261028T1000" in eng2.executed_signal_ids
    h.engine = eng2
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    ev = h.send_candle(10, 0, delay=1.0)
    assert rejection(ev) == "DUPLICATE_SIGNAL"
    assert eng2.pending is None and eng2.position is None


def test_same_candle_arbitration_fifo_then_priority(tmp_path):
    strategies = {
        "IRF": ScriptedStrategy("IRF", {"10:00": {"direction": SELL}}, method="METHOD_2"),
        "ORB": ScriptedStrategy("ORB", {"10:00": {"direction": BUY}}),
        "PDS": ScriptedStrategy("PDS", {"10:00": {"direction": SELL}}),
    }
    h = Harness(tmp_path, strategies=strategies)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    ev = h.send_candle(10, 0, delay=1.0)
    assert [e.payload["strategy"] for e in find(ev, "SIGNAL")] == ["PDS", "ORB", "IRF"]
    lost = {e.payload["strategy"]: e.payload["rejection"] for e in find(ev, "ENTRY_REJECTED")}
    assert lost == {"ORB": "ARBITRATION_LOST", "IRF": "ARBITRATION_LOST"}
    assert h.engine.pending.signal.strategy == "PDS"
    assert "PDS:METHOD_1:20261028T1000" in lost_detail(ev)


def lost_detail(ev):
    return " ".join(e.payload["detail"] for e in find(ev, "ENTRY_REJECTED"))


def test_stale_quote_keeps_entry_pending_then_timeout_with_exact_detail(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)  # decision 10:05:00, engine accepts at 10:05:01
    # a quote received AFTER the decision but already 8s old when used (>5s max age)
    ev = h.poll(at(10, 5, 9), BASE_PRICE, bid=99.0, ask=100.0, side="PE", quote_t=at(10, 5, 1))
    assert h.engine.pending is not None and h.engine.position is None
    assert "stale" in h.engine.pending.last_detail.lower() or "age" in h.engine.pending.last_detail
    ev = h.poll(at(10, 5, 40), BASE_PRICE, bid=99.0, ask=100.0, side="PE", quote_t=at(10, 5, 1))
    assert rejection(ev) == "ENTRY_TIMEOUT"
    assert "30s" in find(ev, "ENTRY_REJECTED")[0].payload["detail"]
    assert h.engine.pending is None and h.rows() == []


def test_quote_older_than_signal_is_never_used(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    # received 2s BEFORE the candle closed (a pre-signal ask) though still "fresh" by age
    h.poll(at(10, 5, 1, 500000), BASE_PRICE, bid=99.0, ask=100.0, side="PE", quote_t=at(10, 4, 58))
    assert h.engine.position is None
    assert "before signal" in h.engine.pending.last_detail
    ev = h.poll(
        at(10, 5, 4), BASE_PRICE, bid=99.0, ask=100.0, side="PE"
    )  # a proper post-signal quote
    assert find(ev, "TRADE_OPENED")


def test_no_quote_at_all_never_fabricates_a_fill(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    for sec in (3, 10, 20, 29):
        h.poll(at(10, 5, sec), BASE_PRICE)  # spot only, no option quotes
    assert h.engine.position is None and h.engine.account.cash == 250000.0
    ev = h.poll(at(10, 5, 35), BASE_PRICE)
    assert rejection(ev) == "ENTRY_TIMEOUT"
    assert h.engine.position is None and h.rows() == []


def test_wide_spread_rejected_then_times_out(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    h.poll(at(10, 5, 3), BASE_PRICE, bid=80.0, ask=100.0, side="PE")  # spread 20 > 5
    assert h.engine.position is None and "spread" in h.engine.pending.last_detail


def test_crossed_quote_not_filled(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    h.poll(at(10, 5, 3), BASE_PRICE, bid=101.0, ask=100.0, side="PE")
    assert h.engine.position is None and h.engine.pending is not None


def test_price_moved_through_signal_stop_rejects(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    ev = h.poll(at(10, 5, 3), BASE_PRICE + 41, bid=99.0, ask=100.0, side="PE")  # SELL stop 25890
    assert rejection(ev) == "PRICE_MOVED"
    assert h.engine.position is None


def test_premium_budget_and_insufficient_funds_reject_with_reason(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    # 75 x 800 = 60,000 > 20% (50,000) of 250,000 -> not even one lot fits
    ev = h.poll(at(10, 5, 3), BASE_PRICE, bid=799.0, ask=800.0, side="PE")
    assert rejection(ev) == "PREMIUM_BUDGET"
    assert h.engine.position is None and h.engine.account.cash == 250000.0


def test_non_provider_candle_ignored_in_live_mode(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    bad = candle(10, 0, source=CandleSource.SAMPLED_TICKS)
    h.set(bad.close_time)
    ev = h.engine.on_candle(bad)
    assert kinds(ev) == ["CANDLE_BLOCKED"] and "SAMPLED_TICKS" in ev[0].text
    assert h.strategies["PDS"].seen[-1].strftime("%H:%M") == "09:55"  # 10:00 never evaluated
    for src in (CandleSource.HISTORICAL_FILE, CandleSource.SIMULATED_REPLAY):
        ev = h.engine.on_candle(candle(10, 0, source=src))
        assert kinds(ev) == ["CANDLE_BLOCKED"]
    assert h.engine.snapshot()["connection"]["candle_trusted"] is False
    # the genuine one is still accepted afterwards
    good = h.send_candle(10, 0)
    assert "CANDLE_BLOCKED" not in kinds(good)
    assert h.strategies["PDS"].seen[-1].strftime("%H:%M") == "10:00"


def test_replay_mode_accepts_only_simulated_replay_candles(tmp_path):
    h = Harness(tmp_path, mode=RunMode.REPLAY)
    h.start()
    ev = h.send_candle(9, 15, source=CandleSource.PROVIDER_COMPLETED)
    assert "CANDLE_BLOCKED" in kinds(ev) and h.strategies["PDS"].evaluated == 0
    ev = h.send_candle(9, 15, source=CandleSource.SIMULATED_REPLAY)
    assert "CANDLE_BLOCKED" not in kinds(ev) and h.strategies["PDS"].evaluated == 1
    assert h.auth["PDS"].label == LABEL_REPLAY


def test_duplicate_and_older_candles_are_not_reevaluated(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    n = h.strategies["PDS"].evaluated
    h.set(at(10, 2))
    ev = h.engine.on_candle(candle(9, 55))
    assert kinds(ev) == ["CANDLE_IGNORED"] and h.strategies["PDS"].evaluated == n
    ev = h.engine.on_candle(candle(9, 15))
    assert kinds(ev) == ["CANDLE_IGNORED"]


def test_candle_before_it_has_closed_is_refused(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.warm((10, 0))
    h.set(at(10, 2))
    ev = h.engine.on_candle(candle(10, 0))  # closes 10:05
    assert kinds(ev) == ["CANDLE_BLOCKED"] and "after now" in ev[0].text


def test_gap_blocks_strategies_until_backfilled(tmp_path):
    h = Harness(
        tmp_path, strategies={"PDS": ScriptedStrategy("PDS", {"10:05": {"direction": SELL}})}
    )
    h.start()
    h.warm((10, 0))
    h.send_candle(10, 0)
    ev = h.send_candle(10, 10)  # 10:05 missing
    assert kinds(ev) == ["CANDLE_BLOCKED"] and "10:05" in ev[0].text
    assert h.engine.missing_candle_opens() == [at(10, 5)]
    assert h.strategies["PDS"].seen[-1].strftime("%H:%M") == "10:00"  # 10:10 not evaluated
    snap = h.engine.snapshot()
    assert snap["strategies"][0]["state"] == "BLOCKED" and "10:05" in snap["strategies"][0]["note"]
    assert snap["connection"]["candle_trusted"] is False
    # backfill restores contiguity: both candles are evaluated in order, signal is LATE
    h.set(at(10, 12))
    ev = h.engine.on_candle(candle(10, 5))
    assert [t.strftime("%H:%M") for t in h.strategies["PDS"].seen[-2:]] == ["10:05", "10:10"]
    assert h.engine.missing_candle_opens() == []
    # the 10:05 signal was evaluated 7 minutes after its candle closed -> not actionable
    assert rejection(ev) == "CANDLE_NOT_TRUSTED"
    assert h.engine.snapshot()["strategies"][0]["state"] != "BLOCKED"


def test_on_candle_missing_blocks_with_reason_no_substitution(tmp_path):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS")})
    h.start()
    h.warm((10, 0))
    h.set(at(10, 6, 30))
    ev = h.engine.on_candle_missing(at(10, 0), {"summary": "provider returned nothing"})
    assert kinds(ev) == ["CANDLE_BLOCKED"] and "10:00" in ev[0].text
    row = h.engine.snapshot()["strategies"][0]
    assert row["state"] == "BLOCKED" and "10:00" in row["note"]
    h.engine.set_forming_candle(candle(10, 0, source=CandleSource.SAMPLED_TICKS))
    assert h.strategies["PDS"].seen[-1].strftime("%H:%M") == "09:55"  # forming candle never fed
    snap = h.engine.snapshot()
    assert snap["current_candle"]["authoritative"] is False
    # delivery of the real candle clears the block
    h.send_candle(10, 0, delay=60)
    assert h.engine.snapshot()["strategies"][0]["state"] == "MONITORING"


def test_levels_not_ready_marks_warming_up_and_suppresses_signals(tmp_path):
    h = Harness(tmp_path, problems=["fewer than 200 prior 5m closes for EMA200"])
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    ev = h.send_candle(10, 0, delay=1.0)
    row = h.engine.snapshot()["strategies"][0]
    assert row["state"] == "WARMING_UP" and "EMA200" in row["note"]
    assert rejection(ev) == "ENGINE_NOT_RUNNING"
    assert h.engine.pending is None


def test_strategy_exception_is_isolated(tmp_path):
    boom = RaisingStrategy("ORB", boom_at="09:20")
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS"), "ORB": boom})
    h.start()
    ev = h.warm((9, 30))
    assert any(e.kind == "ERROR" and "ORB" in e.text for e in ev)
    rows = {r["name"]: r for r in h.engine.snapshot()["strategies"]}
    assert rows["ORB"]["state"] == "BLOCKED" and rows["PDS"]["state"] == "MONITORING"
    assert h.engine.state in (EngineState.READY, EngineState.RUNNING)  # engine itself unaffected
    assert (
        boom.evaluated == 2
    )  # 09:15 ok, 09:20 raised; a strategy that raised is not fed again (fail closed)


def test_builder_receives_every_bar_and_strategy_context_state_is_assigned(tmp_path):
    from engine_fakes import FakeBuilder

    h = Harness(tmp_path)
    h.start()
    h.warm((9, 30))
    b = FakeBuilder.instances[-1]
    assert [t.strftime("%H:%M") for t in b.completed] == ["09:15", "09:20", "09:25"]
    ctx = h.engine._contexts["PDS"]
    assert ctx.state["levels_date"] == "2026-10-28" and ctx.state["pdh"] == 26000.0


def test_restore_candles_is_silent(tmp_path):
    h = Harness(
        tmp_path, strategies={"PDS": ScriptedStrategy("PDS", {"10:00": {"direction": SELL}})}
    )
    h.start()
    h.set(at(10, 20))
    cs = [candle(9, 15 + 5 * i) if 15 + 5 * i < 60 else candle(10, 5 * i - 45) for i in range(9)]
    h.set(at(10, 6))
    cs = [candle(9, m) for m in range(15, 60, 5)] + [candle(10, 0)]
    ev = h.engine.restore_candles(cs)
    assert h.strategies["PDS"].seen[-1].strftime("%H:%M") == "10:00"
    rej = find(ev, "ENTRY_REJECTED")
    assert rej and rej[0].payload["rejection"] == "CANDLE_NOT_TRUSTED"
    assert h.engine.pending is None and h.engine.position is None
    assert len(h.engine.snapshot()["candles"]) == len(cs)
    assert quote(1, 1, 2, at(10, 6)).spread == 1
    assert with_exec(h.cfg, max_spread_points=9).execution.max_spread_points == 9
