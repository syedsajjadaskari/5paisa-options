"""Regression tests for the verified F1 findings (engine / app / provider / broker resilience).

SYNTHETIC data only; no network, no broker, no secrets.  Each test pins one defect that review
found and that is now fixed.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime, timedelta

import pytest
from engine_fakes import (
    BASE_PRICE,
    TRADE_DATE,
    Harness,
    ScriptedStrategy,
    at,
    find,
    open_trade,
    quote,
)

from trading_platform.core.enums import SignalDirection
from trading_platform.live.checkpoint import load_checkpoint
from trading_platform.live.models import (
    CandleSource,
    EngineState,
    PositionStatus,
    RunMode,
)
from trading_platform.live.providers.base import ManualClock


def snap_pos(h):
    return h.engine.snapshot()["position"]


# =========================================================================== 1. no fill after close


def test_exit_pending_at_close_never_books_a_post_close_fill(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    tid = h.engine.position.trade_id
    ev = h.poll(at(15, 15, 0), BASE_PRICE)  # square-off, no fresh bid -> EXIT_PENDING
    assert find(ev, "EXIT_PENDING") and not find(ev, "TRADE_CLOSED")
    assert h.engine.position.exit_pending_reason.value == "EOD"
    h.tick(at(15, 30, 1))
    assert h.engine.state == EngineState.BLOCKED
    # a perfectly fresh bid arrives AFTER the close: it is not a tradable price
    ev = h.poll(at(15, 31, 0), BASE_PRICE, bid=80.0, ask=81.0, side="PE")
    assert not find(ev, "TRADE_CLOSED")
    assert h.rows() == []
    pos = h.engine.position
    assert pos is not None and pos.status == PositionStatus.EXIT_PENDING
    p = snap_pos(h)
    assert "market closed" in p["exit_pending_detail"] and "no fill" in p["exit_pending_detail"]
    assert p["exit_pending_reason"] == "EOD"
    cp = json.loads(h.ckpt.read_text())
    assert cp["position"]["trade_id"] == tid and cp["position"]["status"] == "EXIT_PENDING"
    # exactly at 15:30:00 is already closed (>= market_close)
    ev = h.poll(at(15, 30, 0), BASE_PRICE, bid=80.0, ask=81.0, side="PE")
    assert not find(ev, "TRADE_CLOSED") and h.rows() == []


def test_exit_pending_position_blocks_the_next_session_as_recovery_required(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(15, 15, 0), BASE_PRICE)
    h.poll(at(15, 31, 0), BASE_PRICE, bid=80.0, ask=81.0, side="PE")
    h.engine.shutdown("test")
    tomorrow = date(2026, 10, 29)
    eng = h.new_engine(clock=ManualClock(at(9, 5, day=tomorrow)))
    r = eng.start_session(tomorrow)
    assert not r.ok and r.state == EngineState.RECOVERY_REQUIRED
    assert eng.position is None and not eng._monitor_allowed  # never auto-resumed or closed


def test_a_bid_received_just_before_the_close_still_closes_normally(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(15, 15, 0), BASE_PRICE)
    ev = h.poll(at(15, 29, 59), BASE_PRICE, bid=80.0, ask=81.0, side="PE")
    t = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    assert t["exit_reason"] == "EOD" and t["exit_time"].startswith("2026-10-28T15:29:59")


# =========================================================================== 2. state / reason


def test_state_reason_follows_the_clock_after_cutoff_and_close(tmp_path):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS")})
    h.start()
    h.warm((10, 0))
    h.poll(at(10, 0, 5), BASE_PRICE)
    assert h.engine.state == EngineState.RUNNING and h.engine.state_reason == "market open"
    s = h.engine.snapshot()
    assert s["strategies"][0]["state"] == "MONITORING"

    h.poll(at(14, 30, 5), BASE_PRICE)
    assert h.engine.state == EngineState.RUNNING
    assert "NO_NEW_ENTRIES" in h.engine.state_reason
    row = h.engine.snapshot()["strategies"][0]
    assert row["state"] == "DONE_FOR_DAY" and "entries closed" in row["note"]

    h.poll(at(15, 30, 5), BASE_PRICE)
    assert "market closed" in h.engine.state_reason
    assert "market open" not in h.engine.state_reason
    assert h.engine.snapshot()["session"]["state_reason"] == h.engine.state_reason


def test_cutoff_and_close_emit_their_events_once(tmp_path):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS")})
    h.start()
    h.poll(at(10, 0, 5), BASE_PRICE)
    ev = h.poll(at(14, 30, 5), BASE_PRICE) + h.poll(at(14, 31, 5), BASE_PRICE)
    texts = [e.text for e in ev if e.kind == "STATE_CHANGE"]
    assert len([t for t in texts if "NO_NEW_ENTRIES" in t]) == 1
    ev = h.poll(at(15, 30, 5), BASE_PRICE) + h.poll(at(15, 31, 5), BASE_PRICE)
    assert len([e for e in ev if e.kind == "STATE_CHANGE" and e.text == "market closed"]) == 1


def test_long_data_outage_shows_degraded_not_market_open(tmp_path):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS")})
    h.start()
    h.poll(at(10, 0, 0), BASE_PRICE)
    assert h.engine.state_reason == "market open"
    h.poll(at(10, 0, 10), no_spot=True)  # short gap: not yet worth a banner
    assert h.engine.state_reason == "market open"
    h.poll(at(10, 2, 0), no_spot=True)
    assert h.engine.state == EngineState.RUNNING
    assert h.engine.state_reason.startswith("DEGRADED")
    assert "market open" not in h.engine.state_reason
    h.poll(at(10, 2, 5), BASE_PRICE)  # recovered
    assert h.engine.state_reason == "market open"


# =========================================================================== 3. snapshot truth


def test_markers_carry_exit_reason_strategy_direction_and_option_type(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")  # target
    markers = h.engine.snapshot()["markers"]
    entry = next(m for m in markers if m["kind"] == "ENTRY")
    exit_ = next(m for m in markers if m["kind"] == "EXIT")
    assert entry["strategy"] == "PDS" and entry["option_type"] == "PE"
    assert entry["direction"] == "BEARISH"
    assert exit_["strategy"] == "PDS" and exit_["exit_reason"] == "TARGET"
    assert exit_["direction"] == "BEARISH" and exit_["option_type"] == "PE"


def test_connection_block_comes_from_the_engines_own_last_poll(tmp_path):
    h = Harness(tmp_path)
    h.start()
    # the app's provider status (written one poll LATE) claims everything is fine ...
    h.engine.set_connection_info({"status": "CONNECTED", "notes": []})
    h.poll(at(10, 0, 0), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    assert h.engine.snapshot()["connection"]["status"] == "CONNECTED"
    # ... but this poll delivered no spot: the snapshot written in the SAME on_poll says so
    h.poll(at(10, 0, 30), no_spot=True)
    on_disk = json.loads(h.snap_path.read_text())
    assert on_disk["written_at"].startswith("2026-10-28T10:00:30")
    conn = on_disk["connection"]
    assert conn["status"] == "DEGRADED" and conn["stale"] is True
    assert conn["data_age_seconds"] == pytest.approx(30.0, abs=0.01)
    # never any spot at all -> DISCONNECTED
    h2 = Harness(tmp_path / "other")
    h2.start()
    h2.engine.set_connection_info({"status": "CONNECTED"})
    h2.poll(at(10, 0, 0), no_spot=True)
    assert h2.engine.snapshot()["connection"]["status"] == "DISCONNECTED"


def test_provider_status_can_only_make_the_connection_worse(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.poll(at(10, 0, 0), BASE_PRICE)
    h.engine.set_connection_info({"status": "DISCONNECTED"})
    assert h.engine.snapshot()["connection"]["status"] == "DISCONNECTED"


def replay_harness(tmp_path):
    h = Harness(tmp_path, mode=RunMode.REPLAY)
    h.strategies["PDS"].script = {"10:00": {"direction": SignalDirection.SELL}}
    h.start()
    for m in range(15, 60, 5):
        h.send_candle(9, m, source=CandleSource.SIMULATED_REPLAY)
    h.poll(at(9, 59, 58), BASE_PRICE)
    return h


def test_replay_snapshot_is_simulated_with_wall_clock_written_at_and_sim_time(tmp_path):
    h = replay_harness(tmp_path)
    ev = h.send_candle(10, 0, delay=1.0, source=CandleSource.SIMULATED_REPLAY)
    s = h.engine.snapshot()
    assert s["schema_version"] == 1
    assert s["connection"]["broker"] == "SIMULATED (no broker)"
    assert s["connection"]["status"] == "SIMULATED"
    sim = datetime.fromisoformat(s["sim_time"])
    assert sim == h.clock.now()
    written = datetime.fromisoformat(s["written_at"])
    assert abs((written - datetime.now(tz=written.tzinfo)).total_seconds()) < 120
    on_disk = json.loads(h.snap_path.read_text())
    assert on_disk["sim_time"] is not None and on_disk["connection"]["status"] == "SIMULATED"
    # SIGNAL: the tier is shown as a simulation, never as an opt-in
    sig = find(ev, "SIGNAL")[0]
    assert "REPLAY_SIMULATED" in sig.text and "RESEARCH_PAPER" not in sig.text
    assert sig.payload["tier"] == "REPLAY_SIMULATED"
    assert "not an opt-in" in sig.payload["tier_text"]
    assert s["strategies"][0]["tier"] == "REPLAY_SIMULATED"


def test_live_snapshot_keeps_broker_and_has_no_sim_time(tmp_path):
    h = Harness(tmp_path)
    h.start()
    h.poll(at(10, 0, 0), BASE_PRICE)
    s = h.engine.snapshot()
    assert s["connection"]["broker"] == "5paisa" and s["sim_time"] is None
    assert s["written_at"].startswith("2026-10-28T10:00:00")


def test_live_signal_event_keeps_the_real_tier(tmp_path):
    h = Harness(tmp_path)
    ev1, _ = open_trade(h, "PE")
    sig = find(ev1, "SIGNAL")[0]
    assert sig.payload["tier"] == "RESEARCH_PAPER" and "[RESEARCH_PAPER]" in sig.text


# =========================================================================== 4. position fields


class RuleStrategy(ScriptedStrategy):
    """Emits a real-looking machine rule id as the reason."""

    def generate_signal(self, context):
        sig = super().generate_signal(context)
        if sig is None:
            return None
        return sig.model_copy(update={"reason": "ORB_METHOD_1_PDH_BREAKOUT"})


def test_position_dict_has_human_labels_and_depth_facts(tmp_path):
    h = Harness(
        tmp_path,
        strategies={"ORB": RuleStrategy("ORB", {"10:00": {"direction": SignalDirection.BUY}})},
    )
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    h.send_candle(10, 0, delay=1.0)
    # ask_qty 10 is far below the order quantity: the TOP_OF_BOOK_QTY warning must be surfaced
    ce = h.contract("CE")
    ev = h.poll(at(10, 5, 3), BASE_PRICE, extra={ce.scrip_code: quote(
        ce.scrip_code, 99.0, 100.0, at(10, 5, 3), ask_qty=10)})  # fmt: skip
    opened = find(ev, "TRADE_OPENED")[0]
    p = opened.payload["position"]
    assert p["strategy_label"] == "ORB M1"
    assert p["rule_id"] == "ORB_METHOD_1_PDH_BREAKOUT" == p["entry_reason"]
    assert p["reason_text"] != p["rule_id"] and "PDH" in p["reason_text"].upper()
    assert p["direction_label"] == "BUY CE"
    assert p["entry_ask_qty"] == 10
    assert any("TOP_OF_BOOK_QTY" in w for w in p["entry_warnings"])
    assert "TOP_OF_BOOK_QTY" in opened.text  # the operator sees it in the event text
    j = h.journal_events("TRADE_OPENED")[0]["payload"]
    assert j["entry_ask_qty"] == 10 and any("TOP_OF_BOOK_QTY" in w for w in j["entry_warnings"])
    assert j["reason_text"] == p["reason_text"] and j["rule_id"] == p["rule_id"]
    # the exit carries the depth facts in exit_detail_json too
    ev = h.poll(at(10, 9, 0), 25921.0, bid=120.0, ask=121.0, side="CE")  # target hit
    row = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    detail = json.loads(row["exit_detail_json"])
    assert detail["exit_bid_qty"] == 5000 and detail["entry_ask_qty"] == 10
    assert any("TOP_OF_BOOK_QTY" in w for w in detail["entry_warnings"])


def test_bid_staleness_and_exit_pending_reason_in_the_position_dict(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")  # last bid 99.0 received 10:05:03
    p = h.engine.snapshot()["position"]
    assert p["bid_is_stale"] is False and p["current_bid_age_seconds"] == pytest.approx(0.0)
    h.poll(at(10, 5, 30), BASE_PRICE)  # spot only: the held contract's bid is now 27s old
    p = snap_pos(h)
    assert p["bid_is_stale"] is True and p["current_bid_age_seconds"] == pytest.approx(27.0)
    assert p["exit_pending_reason"] is None
    h.poll(at(10, 8, 0), 25891.0)  # stop hit, no bid
    p = snap_pos(h)
    assert p["status"] == "EXIT_PENDING" and p["exit_pending_reason"] == "STOP"
    assert p["exit_pending_reason_text"] and p["bid_is_stale"] is True
    assert p["exit_pending_detail"]


def test_exit_detail_records_spot_age_and_flags_a_stale_exit_spot(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 6, 0), BASE_PRICE)  # last spot tick 10:06:00
    ev = h.poll(at(15, 15, 0), no_spot=True)  # EOD square-off, no spot, no bid
    assert find(ev, "EXIT_PENDING") and not find(ev, "TRADE_CLOSED")
    ev = h.poll(at(15, 15, 30), no_spot=True, bid=80.0, ask=81.0, side="PE")
    row = find(ev, "TRADE_CLOSED")[0].payload["trade"]
    detail = json.loads(row["exit_detail_json"])
    assert detail["exit_spot_stale"] is True
    assert detail["exit_spot_age_seconds"] == pytest.approx(18570.0, abs=1)
    assert detail["exit_spot_basis"] == "last_known_or_trigger"
    assert any("STALE_EXIT_SPOT" in w for w in detail["exit_warnings"])
    assert "STALE_EXIT_SPOT" in find(ev, "TRADE_CLOSED")[0].text


def test_exit_detail_for_a_normal_exit_is_not_flagged_stale(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    ev = h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")
    detail = json.loads(find(ev, "TRADE_CLOSED")[0].payload["trade"]["exit_detail_json"])
    assert detail["exit_spot_stale"] is False and detail["exit_spot_age_seconds"] == 0.0


# =========================================================================== 5. no fresh spot


def test_no_fresh_spot_with_an_open_position_warns_once_per_episode(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")  # spot tick 10:05:03
    ev = h.poll(at(10, 5, 30), no_spot=True, bid=99.0, ask=100.0, side="PE")
    risk = find(ev, "RISK")
    assert len(risk) == 1
    assert (
        risk[0].payload["severity"] == "WARNING" and risk[0].payload["condition"] == "NO_FRESH_SPOT"
    )
    assert "stop/target not evaluated: no fresh spot" in risk[0].text
    assert h.engine.state_reason.startswith("stop/target not evaluated: no fresh spot")
    # same episode: silent
    ev = h.poll(at(10, 5, 40), no_spot=True, bid=99.0, ask=100.0, side="PE")
    assert not find(ev, "RISK")
    assert h.engine.snapshot()["session"]["state_reason"].startswith("stop/target not evaluated")
    # spot recovers: silent resume, reason back to normal
    ev = h.poll(at(10, 5, 50), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    assert not find(ev, "RISK") and h.engine.state_reason == "market open"
    # a NEW episode announces again
    ev = h.poll(at(10, 7, 0), no_spot=True, bid=99.0, ask=100.0, side="PE")
    assert len(find(ev, "RISK")) == 1
    assert len([e for e in h.events.read_events(TRADE_DATE) if e["kind"] == "RISK"]) == 2


def test_no_fresh_spot_warning_is_not_emitted_when_flat(tmp_path):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS")})
    h.start()
    h.poll(at(10, 0, 0), BASE_PRICE)
    ev = h.poll(at(10, 1, 0), no_spot=True)
    assert not find(ev, "RISK")


# =========================================================================== 6. poisoned cache


def test_future_stamped_quote_is_ignored_and_does_not_poison_the_cache(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    pe = h.contract("PE").scrip_code
    # one quote stamped 10 minutes in the FUTURE
    h.poll(at(10, 6, 0), BASE_PRICE, extra={pe: quote(pe, 95.0, 96.0, at(10, 16, 0))})
    assert h.engine._quotes[pe].received_at == at(10, 5, 3)  # the genuine quote is still cached
    assert any("in the future" in n for n in h.engine.snapshot()["connection"]["notes"])
    # genuine quotes keep working: a stop exit closes on a fresh bid
    ev = h.poll(at(10, 7, 0), 25891.0, bid=80.0, ask=81.0, side="PE")
    assert find(ev, "TRADE_CLOSED")


def test_future_stamped_quote_is_logged_only_once(tmp_path):
    h = Harness(tmp_path)
    h.start()
    logged: list[str] = []
    h.engine.error_logger.error = lambda msg, *a, **k: logged.append(str(msg))
    pe = h.contract("PE").scrip_code
    for sec in (0, 1, 2):
        h.poll(at(10, 6, sec), BASE_PRICE, extra={pe: quote(pe, 95.0, 96.0, at(10, 26, 0))})
    assert len([m for m in logged if "in the future" in m]) == 1
    assert pe not in h.engine._quotes


def test_small_clock_skew_within_tolerance_is_accepted(tmp_path):
    h = Harness(tmp_path)
    h.start()
    pe = h.contract("PE").scrip_code
    h.poll(
        at(10, 6, 0),
        BASE_PRICE,
        extra={pe: quote(pe, 95.0, 96.0, at(10, 6, 0) + timedelta(seconds=0.5))},
    )
    assert pe in h.engine._quotes


# =========================================================================== 11. sticky BLOCKED


def _break_checkpoint_writes(monkeypatch, fail_times: int):
    import trading_platform.live.engine as eng_mod

    real = eng_mod.save_checkpoint
    state = {"left": fail_times}

    def flaky(*a, **k):
        if state["left"] > 0:
            state["left"] -= 1
            raise OSError("ENOSPC (SYNTHETIC)")
        return real(*a, **k)

    monkeypatch.setattr(eng_mod, "save_checkpoint", flaky)
    return state


def test_single_failed_checkpoint_write_is_retried_and_blocked_clears(tmp_path, monkeypatch):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS")})
    h.start()
    h.poll(at(10, 0, 0), BASE_PRICE)
    assert h.engine.state == EngineState.RUNNING
    out: list = []
    _break_checkpoint_writes(monkeypatch, 1)
    assert h.engine._save_checkpoint(out) is False
    assert h.engine.state == EngineState.BLOCKED
    assert "internal error in save_checkpoint" in h.engine.state_reason
    ev = h.poll(at(10, 0, 5), BASE_PRICE)  # next tick: the retried write succeeds
    assert h.engine.state == EngineState.RUNNING
    assert any(e.kind == "STATE_CHANGE" and "BLOCKED -> RUNNING" in e.text for e in ev)
    assert json.loads(h.ckpt.read_text())["session_date"] == TRADE_DATE.isoformat()


def test_blocked_stays_while_the_checkpoint_keeps_failing(tmp_path, monkeypatch):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS")})
    h.start()
    h.poll(at(10, 0, 0), BASE_PRICE)
    _break_checkpoint_writes(monkeypatch, 3)
    h.engine._save_checkpoint([])
    h.poll(at(10, 0, 5), BASE_PRICE)  # retry 1 fails
    h.poll(at(10, 0, 6), BASE_PRICE)  # retry 2 fails
    assert h.engine.state == EngineState.BLOCKED
    h.poll(at(10, 0, 7), BASE_PRICE)  # 4th write works
    assert h.engine.state == EngineState.RUNNING


def test_blocked_stays_when_a_position_inconsistency_exists(tmp_path, monkeypatch):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    _break_checkpoint_writes(monkeypatch, 1)
    h.engine._save_checkpoint([])
    assert h.engine.state == EngineState.BLOCKED
    # memory disagrees: the position vanished while the account still holds the open leg
    saved = h.engine.position
    h.engine.position = None
    h.poll(at(10, 5, 10), BASE_PRICE)
    assert h.engine.state == EngineState.BLOCKED  # writes work again, but state is inconsistent
    h.engine.position = saved
    h.poll(at(10, 5, 11), BASE_PRICE, bid=99.0, ask=100.0, side="PE")
    assert h.engine.state == EngineState.RUNNING  # consistent again -> recovered


def test_other_blocked_reasons_are_never_auto_cleared(tmp_path):
    h = Harness(tmp_path, strategies={"PDS": ScriptedStrategy("PDS")})
    h.start()
    h.poll(at(10, 0, 0), BASE_PRICE)
    out: list = []
    h.engine._fail("on_candle", RuntimeError("boom (SYNTHETIC)"), out)
    assert h.engine.state == EngineState.BLOCKED
    h.poll(at(10, 0, 5), BASE_PRICE)
    h.poll(at(10, 0, 10), BASE_PRICE)
    assert h.engine.state == EngineState.BLOCKED


# =========================================================================== 7. provider / config


def test_candle_cross_check_knobs_reach_the_provider_from_the_yaml():
    from dataclasses import asdict

    from test_core_config import raw

    from trading_platform.live.config import parse_live_config
    from trading_platform.live.providers.fivepaisa import ProviderConfig

    default = parse_live_config(raw())
    assert default.candles.require_sample_crosscheck is True  # fail-closed default NOT weakened
    pc = ProviderConfig.from_live_config(asdict(default.candles), asdict(default.polling))
    assert pc.require_sample_crosscheck is True and pc.uncrosschecked_min_age_s == 60.0

    r = raw()
    r["candles"]["require_sample_crosscheck"] = False
    r["candles"]["uncrosschecked_min_age_seconds"] = 120
    c = parse_live_config(r)
    pc = ProviderConfig.from_live_config(asdict(c.candles), asdict(c.polling))
    assert pc.require_sample_crosscheck is False and pc.uncrosschecked_min_age_s == 120.0


def test_a_config_without_the_new_candle_keys_still_loads_fail_closed():
    from test_core_config import raw

    from trading_platform.live.config import parse_live_config

    r = raw()
    r["candles"].pop("require_sample_crosscheck", None)
    r["candles"].pop("uncrosschecked_min_age_seconds", None)
    c = parse_live_config(r)
    assert c.candles.require_sample_crosscheck is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("require_sample_crosscheck", "false"),
        ("require_sample_crosscheck", 0),
        ("uncrosschecked_min_age_seconds", 0),
        ("uncrosschecked_min_age_seconds", -5),
        ("uncrosschecked_min_age_seconds", "60"),
    ],
)
def test_invalid_cross_check_settings_are_rejected(key, value):
    from test_core_config import raw

    from trading_platform.live.config import ConfigError, parse_live_config

    r = raw()
    r["candles"][key] = value
    with pytest.raises(ConfigError):
        parse_live_config(r)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("require_sample_crosscheck", "false"),
        ("require_sample_crosscheck", 1),
        ("uncrosschecked_min_age_seconds", 0),
        ("uncrosschecked_min_age_seconds", True),
        ("uncrosschecked_min_age_seconds", float("nan")),
    ],
)
def test_provider_config_validates_the_cross_check_settings(key, value):
    from trading_platform.live.providers.fivepaisa import ProviderConfig

    with pytest.raises(ValueError):
        ProviderConfig.from_live_config({key: value}, {})


def test_candle_worker_can_read_ticks_while_the_poll_thread_appends(tmp_path):
    """The deque is appended by the poll thread and iterated by the candle worker."""
    from datetime import timedelta as td

    from prov_fakes import TEST_DAY, FakeSdkClient, StubBroker, make_clock, spot_response
    from prov_fakes import at as pat

    from trading_platform.live.providers.fivepaisa import FivePaisaLiveProvider, ProviderConfig

    client = FakeSdkClient()
    clock = make_clock(pat(9, 14))
    provider = FivePaisaLiveProvider(
        broker=StubBroker(tmp_path, client),
        config=ProviderConfig(min_call_interval_s=0.0, tick_buffer_size=4000),
        clock=clock,
        sleep_fn=lambda s: None,
        monotonic_fn=lambda: 0.0,
    )
    errors: list[BaseException] = []
    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                provider._sample_near(pat(9, 20))
                provider.sampled_forming_candle()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        base = pat(9, 15)
        for i in range(1500):
            when = base + td(seconds=i * 0.2)
            clock.set(when + td(milliseconds=100))
            client.spot = lambda req, w=when, p=22500.0 + i * 0.05: spot_response(p, w)
            provider.poll([])
    finally:
        stop.set()
        t.join(5)
    assert errors == [] and TEST_DAY is not None


# =========================================================================== 10. recovery acknowledgement (engine + checkpoint)


def _closed_trade_day(tmp_path):
    h = Harness(tmp_path)
    open_trade(h, "PE")
    h.poll(at(10, 9, 0), 25779.0, bid=120.0, ask=121.0, side="PE")  # target -> closed trade
    assert len(h.rows()) == 1
    return h


def _write_ack(h, **over):
    from trading_platform.live.checkpoint import (
        build_recovery_ack,
        recovery_ack_path,
        write_recovery_ack,
    )

    ack = build_recovery_ack(
        h.rows(), today=TRADE_DATE, run_mode="LIVE", renamed_to="checkpoint.recovered-x.json",
        now=at(10, 20),
    )  # fmt: skip
    ack.update(over)
    write_recovery_ack(recovery_ack_path(h.ckpt), ack)
    return ack


def test_matching_acknowledgement_makes_the_day_startable_again(tmp_path):
    h = _closed_trade_day(tmp_path)
    row = h.rows()[0]
    h.ckpt.unlink()
    plain = h.new_engine(clock=ManualClock(at(10, 20)))
    r = plain.start_session(TRADE_DATE)
    assert not r.ok and r.state == EngineState.RECOVERY_REQUIRED  # the dead end, without an ack
    _write_ack(h)
    eng = h.new_engine(clock=ManualClock(at(10, 21)))
    r = eng.start_session(TRADE_DATE)
    assert r.ok and r.state == EngineState.READY and "START_FRESH" in r.recovery
    assert eng.account.cash == pytest.approx(float(row["balance_after"]), abs=0.01)
    assert eng.account.state.realized_pnl == pytest.approx(float(row["net_pnl"]), abs=0.01)
    assert eng.closed_trade_ids == [row["trade_id"]]
    # the daily-loss base is the equity BEFORE today's first trade
    assert eng.account.session_open_equity == pytest.approx(250000.0, abs=0.01)
    assert eng.position is None


@pytest.mark.parametrize(
    "tamper",
    [
        {"trades_csv_rows": 2},
        {"trades_csv_net_pnl": 123.45},
        {"trades_csv_last_trade_id": "SOMETHING-ELSE"},
        {"session_date": "2026-10-27"},
        {"run_mode": "REPLAY"},
        {"schema": 99},
    ],
)
def test_a_mismatching_acknowledgement_stays_recovery_required(tmp_path, tamper):
    h = _closed_trade_day(tmp_path)
    h.ckpt.unlink()
    _write_ack(h, **tamper)
    eng = h.new_engine(clock=ManualClock(at(10, 21)))
    r = eng.start_session(TRADE_DATE)
    assert not r.ok and r.state == EngineState.RECOVERY_REQUIRED


def test_acknowledgement_is_void_once_trades_csv_changes(tmp_path):
    from test_persist_helpers import make_trade

    h = _closed_trade_day(tmp_path)
    h.ckpt.unlink()
    _write_ack(h)
    h.trades.append_trade(make_trade("LATER", net=10.0, balance=250010.0, day=TRADE_DATE))
    eng = h.new_engine(clock=ManualClock(at(10, 21)))
    r = eng.start_session(TRADE_DATE)
    assert not r.ok and r.state == EngineState.RECOVERY_REQUIRED


def test_acknowledgement_never_overrides_a_ledger_that_does_not_reconcile(tmp_path):
    h = _closed_trade_day(tmp_path)
    h.ckpt.unlink()
    _write_ack(h)
    # a configured starting capital that disagrees with the ledger: fresh start must fail closed
    from test_core_helpers import cfg, with_account

    h.cfg = with_account(cfg(), starting_capital_inr=300000)
    eng = h.new_engine(clock=ManualClock(at(10, 21)))
    r = eng.start_session(TRADE_DATE)
    assert not r.ok and r.state == EngineState.RECOVERY_REQUIRED


# =========================================================================== app level


def _app_modules():
    import test_app_live as live

    return live


def test_acknowledge_recovery_with_closed_trades_unblocks_the_next_start(tmp_path):
    live = _app_modules()
    config = live.research_config(tmp_path)
    first = live.make_run(tmp_path, ["--research-paper"], until=(10, 40), config=config)
    assert first.go() == 0
    assert len(live.rows_of(first)) == 1
    first.checkpoint.unlink()  # lost / corrupt checkpoint on a day that already has a trade

    blocked = live.make_run(
        tmp_path, ["--research-paper"], start=(10, 45), until=(10, 50), config=config
    )
    assert blocked.go() == live.app.EXIT_STARTUP_FAILED
    assert "RECOVERY_REQUIRED" in blocked.flat and "--acknowledge-recovery" in blocked.flat

    ack = live.make_run(tmp_path, ["--acknowledge-recovery"], start=(10, 45), config=config)
    assert ack.go() == 0
    assert "recovery_ack.json" in ack.flat and "1 closed trade" in ack.flat
    ack_file = json.loads((first.runtime / "recovery_ack.json").read_text())
    assert ack_file["trades_csv_rows"] == 1 and ack_file["session_date"] == "2026-10-28"

    again = live.make_run(
        tmp_path, ["--research-paper"], start=(10, 45), until=(10, 50), config=config
    )
    assert again.go() == 0
    assert "START_FRESH" in again.flat
    assert len(live.rows_of(again)) == 1  # nothing was invented or duplicated


def test_acknowledge_refuses_to_record_when_the_ledger_does_not_reconcile(tmp_path):
    live = _app_modules()
    config = live.research_config(tmp_path)
    first = live.make_run(tmp_path, ["--research-paper"], until=(10, 40), config=config)
    assert first.go() == 0
    first.checkpoint.unlink()
    csv_text = first.trades_csv.read_text(encoding="utf-8")
    # corrupt the ledger: change net_pnl of the only row so start + sum(net) != balance_after
    import csv as _csv
    import io

    rows = list(_csv.DictReader(io.StringIO(csv_text)))
    rows[0]["net_pnl"] = str(float(rows[0]["net_pnl"]) + 777.0)
    buf = io.StringIO()
    w = _csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    first.trades_csv.write_text(buf.getvalue(), encoding="utf-8")

    ack = live.make_run(tmp_path, ["--acknowledge-recovery"], start=(10, 45), config=config)
    assert ack.go() == live.app.EXIT_STARTUP_FAILED
    assert "does not reconcile" in ack.flat
    assert not (first.runtime / "recovery_ack.json").exists()


def test_late_start_prints_an_honest_cross_check_warning(tmp_path):
    live = _app_modules()
    # started at 10:03: the provider cannot supply any earlier candle (no ticks of its own)
    run = live.make_run(tmp_path, [], start=(10, 3), until=(10, 8), withhold_candles_from=at(9, 15))
    run.go()
    assert "candle cross-check" in run.flat
    assert re.search(r"! +\d+\. candle cross-check", run.flat)  # warning glyph, not a green tick
    assert "cannot be cross-checked" in run.flat and "BLOCKED" in run.flat
    assert "fail-closed default kept" in run.flat.lower()


def test_late_start_warning_names_the_relaxed_setting_when_it_is_off(tmp_path):
    live = _app_modules()
    config = write_config_with(tmp_path, require_sample_crosscheck=False)
    run = live.make_run(
        tmp_path, [], start=(10, 3), until=(10, 8), withhold_candles_from=at(9, 15), config=config
    )
    run.go()
    assert "cannot be cross-checked" in run.flat and "UNCHECKED" in run.flat


def write_config_with(tmp_path, **candles):
    from app_fakes import write_config

    return write_config(tmp_path, candles=candles)


def test_on_time_start_prints_no_cross_check_warning(tmp_path):
    live = _app_modules()
    run = live.make_run(tmp_path, [], start=(9, 10), until=(9, 40))
    run.go()
    assert "cannot be cross-checked" not in run.flat


def test_startup_states_the_request_budget_at_default_settings(tmp_path):
    live = _app_modules()
    run = live.make_run(tmp_path, [], start=(9, 56), until=(10, 0))
    run.go()
    assert "request budget ~3.0 req/s flat" in run.flat
    assert "up to ~4.0 req/s with a position" in run.flat and "UNVERIFIED" in run.flat


def test_request_budget_text_follows_the_configured_intervals(tmp_path):
    import yaml
    from app_fakes import write_config

    config = write_config(tmp_path)
    raw = yaml.safe_load(config.read_text())
    raw["polling"]["option_depth_interval_seconds"] = 2.0
    config.write_text(yaml.safe_dump(raw))
    live = _app_modules()
    run = live.make_run(tmp_path, [], start=(9, 56), until=(10, 0), config=config)
    run.go()
    assert "request budget ~2.0 req/s flat" in run.flat


def test_option_depth_polls_respect_the_configured_interval(tmp_path):
    from test_prov_poll import CE, PE, Rig

    from trading_platform.live.providers.fivepaisa import ProviderConfig

    pc = ProviderConfig.from_live_config({}, {"option_depth_interval_seconds": 3.0})
    rig = Rig(tmp_path, option_depth_interval_s=pc.option_depth_interval_s)
    rig.provider.poll([CE, PE])
    for _ in range(2):  # 1 s later, 2 s later: depth is reused, only spot is called again
        rig.mono += 1.0
        rig.provider.poll([CE, PE])
    assert rig.client.depth_requests() == [CE, PE]
    rig.mono += 1.5  # 3.5 s after the first fetch
    rig.provider.poll([CE, PE])
    assert rig.client.depth_requests() == [CE, PE, CE, PE]


# =========================================================================== replay presentation


def _replay_available() -> bool:
    try:
        from trading_platform.live.providers import replay as replay_mod

        return replay_mod.DEFAULT_5M_PATH.exists() and replay_mod.DEFAULT_1D_PATH.exists()
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _replay_available(), reason="verified historical dataset absent")
def test_replay_run_never_claims_a_broker_connection(tmp_path):
    import test_app_replay as rp

    rt = tmp_path / "rt"
    rc, out = rp.run_replay(rt)
    assert rc == 0
    flat = " ".join(out.replace("│", " ").split())
    # startup row 1 shows the REPLAY date (not wall-clock today), row 4 says there is no login
    assert "1. app" in flat and "replay of 2025-08-18 Monday" in flat
    assert "4. no broker login (replay)" in flat and "4. broker login" not in flat
    assert re.search(r"! +10\. spot & quote freshness", flat)  # non-blocking warning glyph
    snap = json.loads((rt / "replay" / "live_state.json").read_text())
    assert snap["schema_version"] == 1
    assert snap["connection"]["broker"] == "SIMULATED (no broker)"
    assert snap["connection"]["status"] == "SIMULATED"
    assert snap["sim_time"].startswith("2025-08-18")
    written = datetime.fromisoformat(snap["written_at"])
    assert abs((written - datetime.now(tz=written.tzinfo)).total_seconds()) < 3600  # WALL clock
    assert snap["session"]["mode"] == "REPLAY"


@pytest.mark.skipif(not _replay_available(), reason="verified historical dataset absent")
def test_replay_connection_info_is_simulated_in_every_snapshot_on_disk(tmp_path):
    import test_app_replay as rp

    rt = tmp_path / "rt"
    seen: list[dict] = []
    from trading_platform.live.snapshot import SnapshotWriter

    real = SnapshotWriter.write

    def spy(self, data, *a, **k):
        seen.append(data["connection"])
        return real(self, data, *a, **k)

    SnapshotWriter.write = spy  # type: ignore[method-assign]
    try:
        rc, _ = rp.run_replay(rt)
    finally:
        SnapshotWriter.write = real  # type: ignore[method-assign]
    assert rc == 0 and seen
    assert {c["status"] for c in seen} == {"SIMULATED"}
    assert {c["broker"] for c in seen} == {"SIMULATED (no broker)"}


# =========================================================================== 9. shutdown


def _stuck_feed(tmp_path):
    from test_app_misc import FeedProvider, build_feed

    prov = FeedProvider()
    ex = app_mod().DaemonExecutor()
    h, feed, mono = build_feed(tmp_path, ex, prov)
    return prov, ex, h, feed, mono


def app_mod():
    from trading_platform.live import app

    return app


def test_candle_feed_close_never_hangs_behind_a_stuck_worker(tmp_path):
    prov, ex, h, feed, mono = _stuck_feed(tmp_path)
    prov.available = []
    h.set(at(9, 20, 5))
    feed.step(at(9, 20, 5))  # submits the fetch; the provider call blocks on its gate
    assert prov.entered.wait(2)
    assert ex.thread.daemon  # a daemon worker never keeps the interpreter alive
    feed.CLOSE_JOIN_SECONDS = 0.2
    started = time.monotonic()
    clean = feed.close()
    elapsed = time.monotonic() - started
    assert clean is False  # the in-flight fetch was abandoned ...
    assert elapsed < 2.0  # ... instead of being waited for
    prov.gate.set()  # let the abandoned worker finish so the test leaves no thread behind
    ex.join(2)


def test_candle_feed_close_cancels_queued_work_and_is_idempotent(tmp_path):
    prov, ex, h, feed, mono = _stuck_feed(tmp_path)
    prov.gate.set()
    fut1 = ex.submit(lambda: 1)
    assert fut1.result(2) == 1
    assert feed.close() is True  # idle worker: joined
    assert feed.close() is True
    with pytest.raises(RuntimeError):
        ex.submit(lambda: 2)


def test_cleanup_survives_a_second_ctrl_c_without_a_traceback(tmp_path):
    app = app_mod()
    run = object.__new__(app._Run)
    said: list[str] = []
    run.say = lambda text, style="": said.append(text)
    run._prev_handlers = {}
    run.provider = None
    run.lock = None

    class Feed:
        def close(self):
            raise KeyboardInterrupt  # the operator pressed Ctrl+C again during the join

    run.feed = Feed()
    run._cleanup()  # must not raise
    assert run.feed is not None


def test_repeated_sigint_during_a_live_run_still_checkpoints_and_exits_cleanly(tmp_path, capfd):
    import signal

    live = _app_modules()
    run = live.make_run(
        tmp_path,
        ["--research-paper"],
        until=None,
        sigint_at=at(10, 12),
        signal_num=signal.SIGINT,
        signal_repeat=3,
        config=live.research_config(tmp_path),
        signals=True,
    )
    assert run.go() == 0
    err = capfd.readouterr().err
    assert "Traceback" not in err and "KeyboardInterrupt" not in err
    cp = load_checkpoint(run.checkpoint)
    assert cp.position is not None  # the open virtual position was preserved, never faked closed
    assert "virtual position preserved, NOT closed" in run.flat
