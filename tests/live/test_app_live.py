"""LIVE code path of the app (live/app.py) on a SYNTHETIC scripted provider.

Real SessionEngine, real journals / checkpoint / lock, scripted strategy signals and fabricated
option books.  Nothing here is market data; no network, no broker, no secrets.
"""

from __future__ import annotations

import csv
import json
import os
import signal
import threading
from datetime import date, datetime

import pytest
from app_fakes import (
    Run,
    SyntheticLiveProvider,
    flat_then_slide,
    spy_sdk_logging,  # noqa: F401  (autouse fixture)
    write_config,
)
from test_core_helpers import TRADE_DATE, at

from trading_platform.data.calendar import NSE_OFFICIAL_HOLIDAYS
from trading_platform.live import app, costs
from trading_platform.live.authorization import LABEL_RESEARCH, RESEARCH_PAPER_ACK
from trading_platform.live.checkpoint import Checkpoint, load_checkpoint
from trading_platform.live.lock import SingleInstanceLock
from trading_platform.live.models import IST
from trading_platform.live.providers.base import ManualClock

NEXT_DAY = date(2026, 10, 29)  # Thursday, regular NSE session


def rows_of(run: Run) -> list[dict[str, str]]:
    if not run.trades_csv.exists():
        return []
    with run.trades_csv.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def new_run(tmp_path, *, until=(10, 30), day: date = TRADE_DATE, start=(9, 56), **prov_kw):
    clock = ManualClock(at(*start, day=day))
    stop = threading.Event()
    prov_kw.setdefault("spot_fn", flat_then_slide(at(10, 10, day=day)))
    provider = SyntheticLiveProvider(
        clock,
        day=day,
        stop_at=at(*until, day=day) if until else None,
        stop_event=stop,
        **prov_kw,
    )
    return clock, stop, provider


def make_run(
    tmp_path,
    argv=None,
    *,
    until=(10, 30),
    day: date = TRADE_DATE,
    start=(9, 56),
    config=None,
    signals=False,
    **prov_kw,
) -> Run:
    clock, stop, provider = new_run(tmp_path, until=until, day=day, start=start, **prov_kw)
    return Run(
        tmp_path,
        argv,
        provider=provider,
        clock=clock,
        stop_event=stop,
        config=config,
        signals=signals,
    )


def research_config(tmp_path, strategies=("ORB",), ack=None):
    return write_config(tmp_path, research=list(strategies), ack=ack)


# =========================================================================== (b) SHADOW_ONLY


def test_default_tiers_signal_journaled_but_no_position_or_account_change(tmp_path):
    run = make_run(tmp_path)
    assert run.go() == 0
    kinds = run.event_kinds()
    assert "SIGNAL" in kinds
    assert "TRADE_OPENED" not in kinds and "TRADE_CLOSED" not in kinds
    assert rows_of(run) == []
    cp = load_checkpoint(run.checkpoint)
    assert isinstance(cp, Checkpoint)
    assert cp.position is None
    assert cp.account["cash"] == 250000.0 and cp.account["realized_pnl"] == 0.0
    snap = json.loads(run.snapshot.read_text())
    assert snap["position"] is None
    assert snap["account"]["equity"] == 250000.0
    assert snap["session"]["mode"] == "LIVE"
    tiers = {s["name"]: s["tier"] for s in snap["strategies"]}
    assert tiers == {"PDS": "SHADOW_ONLY", "ORB": "SHADOW_ONLY", "IRF": "SHADOW_ONLY",
                     "PDR": "SHADOW_ONLY", "RF": "DISABLED"}  # fmt: skip
    assert "NO virtual fills" in run.flat
    assert "streamlit run scripts/paper_trading_dashboard.py" in run.flat
    # every record of the LIVE run carries the SHADOW_ONLY label and none claims research fills
    assert {e["payload"].get("label") for e in run.events()} == {"SHADOW_ONLY"}
    assert run.provider.closed
    probe = SingleInstanceLock(run.runtime / "app.lock")
    assert probe.acquire()  # lock released on exit
    probe.release()


# =========================================================================== (c) research gating


@pytest.mark.parametrize(
    "case, flag, cfg_kw, must_say",
    [
        ("flag only, config off", True, {}, ["research_paper.enabled is false"]),
        (
            "config on, no flag",
            False,
            {"research": ["ORB"]},
            ["CLI flag --research-paper not given"],
        ),
        (
            "wrong acknowledgement",
            True,
            {"research": ["ORB"], "ack": "yes"},
            ["acknowledgement does not equal the required phrase"],
        ),
        (
            "strategy not listed",
            True,
            {"research": ["PDS"]},
            ["ORB not listed in research_paper.strategies"],
        ),
    ],
)
def test_research_paper_gating_matrix_stays_shadow_with_exact_reason(
    tmp_path, case, flag, cfg_kw, must_say
):
    config = write_config(tmp_path, **cfg_kw) if cfg_kw else None
    run = make_run(tmp_path, ["--research-paper"] if flag else [], config=config)
    assert run.go() == 0, case
    kinds = run.event_kinds()
    assert "SIGNAL" in kinds and "TRADE_OPENED" not in kinds, case
    assert rows_of(run) == [], case
    for phrase in must_say:
        assert phrase in run.flat, (case, phrase)
    assert "SHADOW_ONLY" in run.flat
    snap = json.loads(run.snapshot.read_text())
    assert snap["position"] is None and snap["account"]["equity"] == 250000.0


def test_research_paper_all_conditions_fill_labelled_provisional(tmp_path):
    run = make_run(tmp_path, ["--research-paper"], until=(10, 40), config=research_config(tmp_path))
    assert run.go() == 0
    assert "research-paper: ACTIVE for ORB" in run.flat
    assert LABEL_RESEARCH in run.flat
    assert "VIRTUAL TRADE OPENED" in run.out and "VIRTUAL TRADE CLOSED" in run.out
    rows = rows_of(run)
    assert len(rows) == 1
    r = rows[0]
    assert r["label"] == LABEL_RESEARCH
    assert (r["strategy"], r["option_type"], r["direction"]) == ("ORB", "PE", "BEARISH")
    assert r["exit_reason"] == "TARGET"
    # fills: ask + slippage in, bid - slippage out
    assert float(r["entry_premium"]) == pytest.approx(float(r["entry_ask"]) + 0.5, abs=0.006)
    assert float(r["exit_premium"]) == pytest.approx(float(r["exit_bid"]) - 0.5, abs=0.006)
    # charges exactly once, balance reconciles
    qty = int(r["quantity"])
    entry = costs.charges("BUY", TRADE_DATE, float(r["entry_premium"]), qty).total
    exit_ = costs.charges("SELL", TRADE_DATE, float(r["exit_premium"]), qty).total
    assert float(r["entry_charges"]) == pytest.approx(entry, abs=0.011)
    assert float(r["exit_charges"]) == pytest.approx(exit_, abs=0.011)
    assert float(r["total_charges"]) == pytest.approx(entry + exit_, abs=0.02)
    assert float(r["gross_pnl"]) - float(r["total_charges"]) == pytest.approx(
        float(r["net_pnl"]), abs=0.011
    )
    assert 250000.0 + float(r["net_pnl"]) == pytest.approx(float(r["balance_after"]), abs=0.011)
    cp = load_checkpoint(run.checkpoint)
    assert cp.position is None and cp.account["cash"] == pytest.approx(
        float(r["balance_after"]), abs=0.011
    )
    kinds = run.event_kinds()
    assert kinds.count("TRADE_OPENED") == 1 and kinds.count("TRADE_CLOSED") == 1
    # only the listed strategy was granted fills
    snap = json.loads(run.snapshot.read_text())
    tiers = {s["name"]: s["tier"] for s in snap["strategies"]}
    assert tiers["ORB"] == "RESEARCH_PAPER" and tiers["PDS"] == "SHADOW_ONLY"


def test_full_session_holds_to_square_off_then_exits_when_flat(tmp_path):
    run = make_run(
        tmp_path,
        ["--research-paper"],
        until=None,
        step_s=60.0,
        spot_fn=flat_then_slide(None),
        config=research_config(tmp_path),
    )
    assert run.go() == 0
    rows = rows_of(run)
    assert len(rows) == 1 and rows[0]["exit_reason"] == "EOD"
    assert (
        datetime.fromisoformat(rows[0]["exit_time"]).time() >= datetime(2000, 1, 1, 15, 15).time()
    )
    assert (
        datetime.fromisoformat(rows[0]["entry_time"]).time() < datetime(2000, 1, 1, 14, 30).time()
    )
    assert "session complete" in run.flat
    assert run.clock.now() >= at(15, 35)
    snap = json.loads(run.snapshot.read_text())
    assert snap["session"]["engine_state"] == "STOPPED" and snap["position"] is None


# =========================================================================== (d) lock


def test_second_instance_exits_with_clear_message_and_never_logs_in(tmp_path):
    held = SingleInstanceLock(tmp_path / "runtime" / "app.lock", mode="LIVE")
    assert held.acquire()
    try:
        run = make_run(tmp_path)
        assert run.go() == app.EXIT_LOCK_HELD
        assert run.factory_calls == 0  # lock is taken BEFORE any provider/login
        assert "another instance is already running" in run.flat
        assert f"pid {os.getpid()}" in run.flat
        assert not run.checkpoint.exists() and not run.trades_csv.exists()
    finally:
        held.release()


# =========================================================================== (e) SIGINT / restart


def test_sigint_with_open_position_preserves_checkpoint_and_restart_resumes(tmp_path):
    before = signal.getsignal(signal.SIGINT)
    config = research_config(tmp_path)
    first = make_run(
        tmp_path,
        ["--research-paper"],
        until=None,
        sigint_at=at(10, 12),
        config=config,
        signals=True,
    )
    assert first.go() == 0
    assert signal.getsignal(signal.SIGINT) is before  # handlers restored
    assert "virtual position preserved, NOT closed" in first.flat
    assert "SIGINT" in first.flat
    cp = load_checkpoint(first.checkpoint)
    assert isinstance(cp, Checkpoint) and cp.position is not None
    assert cp.position["status"] == "OPEN"
    trade_id = cp.position["trade_id"]
    assert rows_of(first) == []  # no closed trade, nothing faked
    assert first.event_kinds().count("TRADE_OPENED") == 1
    probe = SingleInstanceLock(first.runtime / "app.lock")
    assert probe.acquire()  # lock released on shutdown
    probe.release()

    second = make_run(tmp_path, ["--research-paper"], until=(10, 45), start=(10, 13), config=config)
    assert second.go() == 0
    assert "RESUME" in second.flat and trade_id in second.flat
    kinds = second.event_kinds()
    assert kinds.count("TRADE_OPENED") == 1  # no duplicate entry after the restart
    rows = rows_of(second)
    assert len(rows) == 1 and rows[0]["trade_id"] == trade_id and rows[0]["exit_reason"] == "TARGET"
    assert load_checkpoint(second.checkpoint).position is None


def test_sigterm_and_repeated_signal_still_write_the_checkpoint(tmp_path):
    config = research_config(tmp_path)
    run = make_run(
        tmp_path, ["--research-paper"], until=None, sigint_at=at(10, 12), config=config,
        signals=True, signal_num=signal.SIGTERM, signal_repeat=3,
    )  # fmt: skip
    assert run.go() == 0
    assert "SIGTERM" in run.flat and "Repeated stop signal ignored" in run.flat
    cp = load_checkpoint(run.checkpoint)
    assert isinstance(cp, Checkpoint) and cp.position is not None
    assert rows_of(run) == []


# =========================================================================== (f) fail closed


def test_warmup_insufficient_fails_closed_with_the_exact_reason(tmp_path):
    reason = "insufficient history: need 600 prior 5m bars, got 12"
    run = make_run(tmp_path, warmup_error=reason)
    assert run.go() == app.EXIT_STARTUP_FAILED
    assert reason in run.flat and "fail closed" in run.flat
    assert run.provider.loop_polls == 0 and run.provider.candle_calls == 0
    assert not run.checkpoint.exists() and not run.trades_csv.exists() and not run.snapshot.exists()
    assert run.provider.closed
    probe = SingleInstanceLock(run.runtime / "app.lock")
    assert probe.acquire()
    probe.release()


def test_login_failure_exits_cleanly_without_trading(tmp_path):
    run = make_run(tmp_path, login_ok=False, login_detail="login failed: credentials rejected")
    assert run.go() == app.EXIT_STARTUP_FAILED
    assert "credentials rejected" in run.flat and "STARTUP BLOCKED" in run.flat
    assert run.provider.polls == 0
    assert not run.checkpoint.exists() and not run.trades_csv.exists()


def test_provider_construction_failure_is_reported_not_raised(tmp_path):
    def boom(**_kw):
        raise RuntimeError("FIVEPAISA_USER_KEY missing")

    clock = ManualClock(at(9, 56))
    run = Run(tmp_path, clock=clock, provider_factory=boom)
    assert run.go() == app.EXIT_STARTUP_FAILED
    assert "provider unavailable" in run.flat and not run.checkpoint.exists()


def test_no_contracts_fails_closed(tmp_path):
    run = make_run(tmp_path, no_contracts=True)
    assert run.go() == app.EXIT_STARTUP_FAILED
    assert "no NIFTY option contracts" in run.flat


def test_stale_spot_during_market_hours_blocks_but_preopen_only_warns(tmp_path):
    blocked = make_run(tmp_path / "a", start=(10, 30), spot_age_s=120.0)
    assert blocked.go() == app.EXIT_STARTUP_FAILED
    assert "spot tick is 120s old" in blocked.flat
    assert blocked.provider.loop_polls == 0

    warn = make_run(tmp_path / "b", start=(9, 0), until=(9, 2), spot_age_s=120.0)
    assert warn.go() == 0
    assert "WARNING (non-blocking)" in warn.flat and "expected outside market hours" in warn.flat


# =========================================================================== (g) holiday


@pytest.mark.parametrize(
    "when",
    [
        datetime(2026, 10, 31, 10, 0, tzinfo=IST),  # Saturday
        datetime(2026, 11, 1, 10, 0, tzinfo=IST),  # Sunday
        min(
            datetime(d.year, d.month, d.day, 10, 0, tzinfo=IST)
            for d in NSE_OFFICIAL_HOLIDAYS
            if d.weekday() < 5 and d.year >= 2026
        ),  # a weekday exchange holiday
    ],
)
def test_holiday_exits_zero_without_login(tmp_path, when):
    clock = ManualClock(when)
    run = Run(tmp_path, clock=clock, provider=SyntheticLiveProvider(clock))
    assert run.go() == 0
    assert run.factory_calls == 0
    assert "not an NSE trading day" in run.flat and "nothing to do" in run.flat
    assert not run.checkpoint.exists() and not run.trades_csv.exists()


# =========================================================================== (h) candle never arrives


def test_missing_provider_candle_blocks_strategies_without_tick_substitution(tmp_path):
    config = research_config(tmp_path)
    run = make_run(
        tmp_path,
        ["--research-paper"],
        until=(10, 30),
        config=config,
        withhold_candles_from=at(10, 0),
    )
    assert run.go() == 0
    kinds = run.event_kinds()
    assert "CANDLE_BLOCKED" in kinds
    assert "SIGNAL" not in kinds and "TRADE_OPENED" not in kinds
    assert rows_of(run) == []
    texts = " ".join(
        e["payload"].get("text", "") for e in run.events() if e["kind"] == "CANDLE_BLOCKED"
    )
    assert "not supplied by the provider" in texts and "no sampled-tick substitution" in texts
    snap = json.loads(run.snapshot.read_text())
    states = {s["name"]: s["state"] for s in snap["strategies"]}
    assert states["ORB"] == "BLOCKED" and states["PDS"] == "BLOCKED"
    assert snap["connection"]["candle_trusted"] is False
    assert (
        run.provider.forming_calls > 0
    )  # display-only forming candle was offered, never evaluated
    assert run.provider.candle_calls > 3  # kept retrying the backfill


# =========================================================================== (i) doctor


def test_doctor_all_green_runs_checks_only_and_writes_no_journal(tmp_path):
    run = make_run(tmp_path, ["--doctor"])
    assert run.go() == 0
    assert "DOCTOR: all checks passed" in run.flat
    assert run.provider.loop_polls == 0 and run.provider.candle_calls == 0
    assert not (run.runtime / "journal").exists()
    assert not run.checkpoint.exists() and not run.snapshot.exists() and not run.trades_csv.exists()
    assert run.provider.closed
    for step in ("app", "config & authorization", "single-instance lock", "broker login",
                 "exchange calendar", "previous trading-day OHLC", "warm-up history",
                 "reference levels", "contracts", "spot & quote freshness", "virtual account",
                 "stream loop"):  # fmt: skip
        assert step in run.flat, step


def test_doctor_failure_exit_code_one(tmp_path):
    run = make_run(tmp_path, ["--doctor"], login_ok=False)
    assert run.go() == 1
    assert "DOCTOR: FAILED" in run.flat and "DOCTOR: all checks passed" not in run.flat


def test_doctor_on_holiday_exits_zero(tmp_path):
    clock = ManualClock(datetime(2026, 10, 31, 10, 0, tzinfo=IST))
    run = Run(tmp_path, ["--doctor"], clock=clock, provider=SyntheticLiveProvider(clock))
    assert run.go() == 0 and "nothing to do" in run.flat


# =========================================================================== recovery acknowledgement


def test_recovery_after_overnight_position_blocks_then_operator_acknowledges(tmp_path):
    config = research_config(tmp_path)
    first = make_run(
        tmp_path,
        ["--research-paper"],
        until=None,
        sigint_at=at(10, 12),
        config=config,
        signals=True,
    )
    assert first.go() == 0
    assert load_checkpoint(first.checkpoint).position is not None

    # next day: an overnight position is never auto-resumed
    nxt = make_run(tmp_path, ["--research-paper"], day=NEXT_DAY, config=config)
    assert nxt.go() == app.EXIT_STARTUP_FAILED
    assert "RECOVERY_REQUIRED" in nxt.flat and "--acknowledge-recovery" in nxt.flat
    assert first.checkpoint.exists()  # untouched
    doctor = make_run(tmp_path, ["--doctor"], day=NEXT_DAY, config=config)
    assert doctor.go() == 1 and "acknowledge-recovery" in doctor.flat

    # explicit operator action
    ack = make_run(tmp_path, ["--acknowledge-recovery"], day=NEXT_DAY, config=config)
    assert ack.go() == 0
    assert "RECOVERY_REQUIRED" in ack.flat and "NOT closed" in ack.flat
    assert not first.checkpoint.exists()
    moved = list(first.runtime.glob("checkpoint.recovered-*.json"))
    assert len(moved) == 1 and json.loads(moved[0].read_text())["position"] is not None
    ack_events = [e for e in ack.events(NEXT_DAY) if e["kind"] == "RECOVERY_ACKNOWLEDGED"]
    assert len(ack_events) == 1 and ack_events[0]["payload"]["renamed_to"] == moved[0].name

    # afterwards the app can start again (fresh, no auto-resume of the old position)
    again = make_run(tmp_path, [], day=NEXT_DAY, until=(10, 5), config=config)
    assert again.go() == 0
    assert "START_FRESH" in again.flat


def test_acknowledge_refuses_a_resumable_checkpoint_and_handles_no_checkpoint(tmp_path):
    config = research_config(tmp_path)
    empty = make_run(tmp_path, ["--acknowledge-recovery"], config=config)
    assert empty.go() == 0 and "nothing to acknowledge" in empty.flat

    first = make_run(
        tmp_path,
        ["--research-paper"],
        until=None,
        sigint_at=at(10, 12),
        config=config,
        signals=True,
    )
    assert first.go() == 0
    before = first.checkpoint.read_bytes()
    refused = make_run(tmp_path, ["--acknowledge-recovery"], start=(10, 20), config=config)
    assert refused.go() == 1 and "Refused" in refused.flat
    assert first.checkpoint.read_bytes() == before


# =========================================================================== worker thread


def test_candles_are_fetched_on_a_background_worker_and_polling_continues(tmp_path):
    clock, stop, provider = new_run(tmp_path, until=(10, 8), start=(9, 56), step_s=1.0)
    run = Run(tmp_path, provider=provider, clock=clock, stop_event=stop)
    run.hooks.executor_factory = None  # production default: ThreadPoolExecutor(1)
    assert run.go() == 0
    assert provider.candle_threads[0] == "MainThread"  # startup catch-up is synchronous
    assert any(n.startswith("candle-fetch") for n in provider.candle_threads[1:])
    assert provider.loop_polls > 10 * provider.candle_calls  # polling was never blocked
    snap = json.loads(run.snapshot.read_text())
    assert snap["connection"]["candle_source"] == "PROVIDER_COMPLETED"
    assert not [k for k in run.event_kinds() if k == "ERROR"]


def test_provider_without_expected_candle_never_crashes_the_loop_on_exceptions(tmp_path):
    class Flaky(SyntheticLiveProvider):
        def completed_candles(self, trading_date, as_of):
            if self.armed and self.loop_polls % 2:
                raise RuntimeError("endpoint exploded token=abc123abc123abc123abc123")
            return super().completed_candles(trading_date, as_of)

    clock = ManualClock(at(9, 56))
    stop = threading.Event()
    provider = Flaky(clock, stop_at=at(10, 30), stop_event=stop)
    run = Run(tmp_path, provider=provider, clock=clock, stop_event=stop)
    assert run.go() == 0
    assert "abc123abc123abc123abc123" not in run.out  # redacted everywhere
    assert RESEARCH_PAPER_ACK not in run.out
