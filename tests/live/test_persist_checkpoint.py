"""SYNTHETIC tests: checkpoint save/load and the restart recovery matrix."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from test_persist_helpers import (
    DAY,
    START,
    flat_account,
    make_cp,
    make_position,
    make_trade,
    row_of,
)

from trading_platform.live.checkpoint import (
    Checkpoint,
    LoadFailure,
    LoadKind,
    RecoveryAction,
    load_checkpoint,
    position_from_dict,
    position_to_dict,
    recovery_decision,
    save_checkpoint,
)
from trading_platform.live.models import IST, PositionStatus

NOW = datetime(2026, 10, 27, 9, 20, tzinfo=IST)
NEXT_DAY = date(2026, 10, 28)


def decide(cp, rows=None, today=DAY, **kw):
    return recovery_decision(cp, rows, today=today, now=NOW, **kw)


# ------------------------------------------------------------------ save / load


def test_roundtrip_with_position_and_dedupe(tmp_path):
    pos = make_position(status=PositionStatus.EXIT_PENDING)
    pos.exit_pending_since = NOW
    cp = make_cp(position=pos, executed_signal_ids=["a", "b", "a"], last_event_seq=7)
    path = tmp_path / "rt" / "checkpoint.json"
    save_checkpoint(path, cp, now=NOW)
    loaded = load_checkpoint(path)
    assert isinstance(loaded, Checkpoint)
    assert loaded.written_at == NOW.isoformat()
    assert loaded.executed_signal_ids == ["a", "b"] and loaded.last_event_seq == 7
    assert loaded.position["status"] == "EXIT_PENDING"
    assert position_from_dict(loaded.position) == pos
    assert position_to_dict(position_from_dict(loaded.position)) == loaded.position
    assert [f.name for f in path.parent.iterdir()] == ["checkpoint.json"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_failures_never_raise(tmp_path):
    p = tmp_path / "c.json"
    assert load_checkpoint(p).kind is LoadKind.MISSING
    p.write_text("{not json")
    assert load_checkpoint(p).kind is LoadKind.CORRUPT
    p.write_bytes(b"\xff\xfe\x00")
    assert isinstance(load_checkpoint(p), LoadFailure)
    p.write_text("[]")
    assert load_checkpoint(p).kind is LoadKind.SCHEMA
    good = make_cp().to_dict()
    p.write_text(json.dumps({**good, "schema_version": 99}))
    assert "schema_version" in load_checkpoint(p).reason
    bad = dict(good)
    del bad["account"]
    p.write_text(json.dumps(bad))
    assert load_checkpoint(p).kind is LoadKind.SCHEMA
    p.write_text(json.dumps({**good, "last_event_seq": "x"}))
    assert isinstance(load_checkpoint(p), LoadFailure)
    p.write_text(json.dumps({**good, "session_date": "garbage"}))
    assert isinstance(load_checkpoint(p), LoadFailure)


# ------------------------------------------------------------------ recovery matrix


def test_no_checkpoint_is_fresh():
    for cp in (None, LoadFailure(LoadKind.MISSING, "none")):
        d = decide(cp)
        assert d.action is RecoveryAction.START_FRESH


def test_no_checkpoint_but_todays_trades_exist_is_recovery_required():
    d = decide(None, [row_of(make_trade("T1"))])
    assert d.action is RecoveryAction.RECOVERY_REQUIRED and d.details["trades_today"] == 1
    # only older-date history is fine
    old = row_of(make_trade("T0", day=date(2026, 10, 1)))
    assert decide(None, [old]).action is RecoveryAction.START_FRESH


@pytest.mark.parametrize("kind", [LoadKind.CORRUPT, LoadKind.SCHEMA, LoadKind.UNREADABLE])
def test_corrupt_checkpoint_is_recovery_required(kind):
    d = decide(LoadFailure(kind, "boom"))
    assert d.action is RecoveryAction.RECOVERY_REQUIRED and "boom" in d.reason


def test_earlier_date_flat_carries_account_only():
    t = make_trade("T1", net=500.0)
    acct = flat_account(500.0)
    acct.update(trades_today=1, wins_today=1, session_realized_pnl=500.0, session_open_equity=START)
    cp = make_cp(day=DAY, account=acct, closed_trade_ids_today=["T1"], executed_signal_ids=["s"])
    d = decide(cp, [row_of(t)], today=NEXT_DAY)
    assert d.action is RecoveryAction.START_FRESH and d.checkpoint is None
    c = d.carried_account
    assert c["cash"] == START + 500 and c["realized_pnl"] == 500 and c["starting_capital"] == START
    assert c["session_open_equity"] == START + 500
    assert c["trades_today"] == c["wins_today"] == c["losses_today"] == 0
    assert c["session_realized_pnl"] == 0 and c["invested"] == 0 and c["unrealized_pnl"] == 0


@pytest.mark.parametrize("status", [PositionStatus.OPEN, PositionStatus.EXIT_PENDING])
def test_overnight_position_is_recovery_required_never_resumed(status):
    cp = make_cp(day=DAY, position=make_position(status=status))
    d = decide(cp, today=NEXT_DAY)
    assert d.action is RecoveryAction.RECOVERY_REQUIRED
    assert "overnight" in d.reason and d.details["open_status"] == status.value
    assert d.checkpoint is cp  # preserved untouched for the operator


@pytest.mark.parametrize("status", [PositionStatus.OPEN, PositionStatus.EXIT_PENDING])
def test_same_day_consistent_position_resumes(status):
    pos = make_position(status=status)
    cp = make_cp(position=pos, executed_signal_ids=["x"])
    d = decide(cp)
    assert d.action is RecoveryAction.RESUME
    assert d.checkpoint.position["trade_id"] == "P1" and d.checkpoint.position["status"] == status
    assert pos.signal.signal_id in d.checkpoint.executed_signal_ids  # dedupe preserved
    assert cp.executed_signal_ids == ["x"]  # caller's checkpoint not mutated


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda p: p.update(quantity=0), "lots/quantity"),
        (lambda p: p.update(lots=0), "lots/quantity"),
        (lambda p: p.pop("entry_premium"), "entry_premium"),
        (lambda p: p.update(entry_premium=0.0), "entry_premium must be > 0"),
        (lambda p: p.update(entry_time=None), "entry_time"),
        (lambda p: p.update(contract=None), "contract missing"),
        (lambda p: p["contract"].update(symbol=""), "contract.symbol"),
        (lambda p: p["contract"].update(option_type="XX"), "option_type"),
        (lambda p: p["contract"].update(lot_size=50), "quantity 75 != lots 1 x lot_size 50"),
        (lambda p: p.update(status="CLOSED"), "status"),
        (lambda p: p.update(trade_id=""), "trade_id"),
        (lambda p: p.update(signal=None), "signal missing"),
        (lambda p: p.update(entry_spot=float("nan")), "entry_spot"),
    ],
)
def test_same_day_inconsistent_position_is_recovery_required(mutate, needle):
    cp = make_cp(position=make_position())
    mutate(cp.position)
    d = decide(cp)
    assert d.action is RecoveryAction.RECOVERY_REQUIRED
    assert needle in d.reason


def test_same_day_flat_resumes_with_flags_preserved():
    t = make_trade("T1", net=-300.0)
    acct = flat_account(-300.0)
    cp = make_cp(
        account=acct, closed_trade_ids_today=["T1"], executed_signal_ids=["sig1"],
        strategy_daily_flags={"PDS": {"done": True}}, risk_flags={"halted": True, "reason": "loss"},
    )  # fmt: skip
    d = decide(cp, [row_of(t)])
    assert d.action is RecoveryAction.RESUME
    r = d.checkpoint
    assert r.executed_signal_ids == ["sig1"] and r.risk_flags["halted"] is True
    assert r.strategy_daily_flags == {"PDS": {"done": True}}


def test_persisted_recovery_required_state_is_sticky():
    d = decide(make_cp(engine_state="RECOVERY_REQUIRED"))
    assert d.action is RecoveryAction.RECOVERY_REQUIRED


def test_account_realized_mismatch_reports_exact_numbers():
    cp = make_cp(account=flat_account(500.0), closed_trade_ids_today=["T1"])
    d = decide(cp, [row_of(make_trade("T1", net=100.0))])
    assert d.action is RecoveryAction.RECOVERY_REQUIRED
    assert d.details["checkpoint_realized_pnl"] == 500.0
    assert d.details["csv_sum_net_pnl"] == 100.0 and d.details["difference"] == 400.0
    assert "500.00" in d.reason and "100.00" in d.reason


def test_flat_cash_mismatch_reports_exact_numbers():
    cp = make_cp(account=flat_account(100.0, cash=START + 777.0), closed_trade_ids_today=["T1"])
    d = decide(cp, [row_of(make_trade("T1", net=100.0))])
    assert d.action is RecoveryAction.RECOVERY_REQUIRED
    assert d.details["checkpoint_cash"] == START + 777.0
    assert d.details["expected_cash"] == START + 100.0 and d.details["difference"] == 677.0


def test_account_with_empty_ledger_but_nonzero_realized_is_flagged():
    d = decide(make_cp(account=flat_account(250.0)))
    assert d.action is RecoveryAction.RECOVERY_REQUIRED


def test_checkpoint_closed_trade_missing_from_ledger():
    cp = make_cp(closed_trade_ids_today=["T9"])
    d = decide(cp, [])
    assert d.action is RecoveryAction.RECOVERY_REQUIRED and "T9" in d.reason


def test_crash_between_close_and_checkpoint_is_reconciled_as_closed():
    """CSV row for P1 written, checkpoint still shows P1 open with the pre-close account."""
    prior = make_trade("T0", net=200.0, balance=START + 200.0)
    closing = make_trade("P1", net=-50.0, balance=START + 150.0)
    acct = flat_account(200.0)
    acct.update(invested=7612.5, cash=START + 200.0 - 7612.5 - 25.0, trades_today=1, wins_today=1,
                session_realized_pnl=200.0)  # fmt: skip
    cp = make_cp(position=make_position("P1"), account=acct, closed_trade_ids_today=["T0"])
    d = decide(cp, [row_of(prior), row_of(closing)])
    assert d.action is RecoveryAction.RESUME
    r = d.checkpoint
    assert r.position is None and "P1" in r.closed_trade_ids_today
    assert r.account["realized_pnl"] == 150.0 and r.account["cash"] == START + 150.0
    assert r.account["invested"] == 0 and r.account["trades_today"] == 2
    assert r.account["losses_today"] == 1 and r.account["session_realized_pnl"] == 150.0
    assert d.details["reconciled_trade_id"] == "P1" and "no duplicate" in d.reason
    assert cp.position is not None  # input not mutated


def test_reconcile_when_checkpoint_account_already_applied():
    closing = make_trade("P1", net=-50.0, balance=START - 50.0)
    acct = flat_account(-50.0)
    cp = make_cp(position=make_position("P1"), account=acct, closed_trade_ids_today=["P1"])
    d = decide(cp, [row_of(closing)])
    assert d.action is RecoveryAction.RESUME and d.checkpoint.position is None
    assert d.checkpoint.account["trades_today"] == 0  # not double counted
    assert d.details["account_advanced_from_ledger"] is False


def test_reconcile_with_unexplained_account_is_recovery_required():
    closing = make_trade("P1", net=-50.0)
    cp = make_cp(position=make_position("P1"), account=flat_account(999.0))
    d = decide(cp, [row_of(closing)])
    assert d.action is RecoveryAction.RECOVERY_REQUIRED
    assert d.details["csv_sum_net_pnl"] == -50.0 and d.details["checkpoint_realized_pnl"] == 999.0


def test_reconcile_needs_ledger_rows_when_only_ids_given():
    cp = make_cp(position=make_position("P1"))
    d = decide(cp, None, trades_csv_ids={"P1"})
    assert d.action is RecoveryAction.RECOVERY_REQUIRED


def test_ids_only_skips_pnl_checks_but_still_gates_position():
    cp = make_cp(position=make_position("P1"), account=flat_account(5.0), closed_trade_ids_today=["T0"])
    d = decide(cp, None, trades_csv_ids={"T0"})
    assert d.action is RecoveryAction.RESUME and d.details["account_check"] == "SKIPPED_NO_ROWS"


def test_reconciled_position_from_earlier_day_starts_fresh_carrying_account():
    closing = make_trade("P1", net=80.0, balance=START + 80.0)
    cp = make_cp(day=DAY, position=make_position("P1"), account=flat_account(0.0))
    d = decide(cp, [row_of(closing)], today=NEXT_DAY)
    assert d.action is RecoveryAction.START_FRESH
    assert d.carried_account["cash"] == START + 80 and d.carried_account["realized_pnl"] == 80


def test_run_mode_mismatch_and_future_date():
    cp = make_cp(run_mode="REPLAY")
    assert decide(cp, run_mode="LIVE").action is RecoveryAction.RECOVERY_REQUIRED
    assert decide(make_cp(day=DAY + timedelta(days=3))).action is RecoveryAction.RECOVERY_REQUIRED


def test_bad_account_dict():
    cp = make_cp(account={"cash": "abc"})
    d = decide(cp)
    assert d.action is RecoveryAction.RECOVERY_REQUIRED and "account" in d.reason


def test_ledger_net_pnl_unreadable_is_recovery_required():
    cp = make_cp()
    row = row_of(make_trade("T1"))
    row["net_pnl"] = "n/a"
    assert decide(cp, [row]).action is RecoveryAction.RECOVERY_REQUIRED


def test_decision_defaults_today_from_now():
    d = recovery_decision(make_cp(), None, now=NOW)
    assert d.action is RecoveryAction.RESUME
