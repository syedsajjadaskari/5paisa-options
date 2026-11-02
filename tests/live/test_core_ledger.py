"""VirtualAccount accounting tests (live/ledger.py) on SYNTHETIC fills."""

from __future__ import annotations

import json

import pytest
from test_core_helpers import TRADE_DATE

from trading_platform.live.costs import (
    charges,
    entry_fill_price,
    exit_fill_price,
    round_trip_estimate,
)
from trading_platform.live.ledger import (
    CheckpointError,
    DoubleCloseError,
    InsufficientFundsError,
    PositionStateError,
    QuantityMismatchError,
    VirtualAccount,
)

QTY = 130
ENTRY = entry_fill_price(100.0, 0.5)  # 100.5
BUY = charges("BUY", TRADE_DATE, ENTRY, QTY).total  # 29.41


def fresh() -> VirtualAccount:
    a = VirtualAccount(250000.0)
    a.start_session()
    return a


def test_start_session_sets_open_equity_and_resets_counters():
    a = VirtualAccount(250000.0)
    assert a.session_open_equity == 0.0
    assert a.start_session() == 250000.0
    assert a.state.session_open_equity == 250000.0
    assert a.state.trades_today == 0 and a.state.session_realized_pnl == 0


def test_open_debits_premium_plus_entry_charges_once():
    a = fresh()
    a.open("T1", QTY, ENTRY, BUY)
    assert a.cash == pytest.approx(250000 - ENTRY * QTY - BUY)
    assert a.state.invested == pytest.approx(ENTRY * QTY)
    assert a.state.unrealized_pnl == 0
    assert a.equity == pytest.approx(250000 - BUY)  # only the charge has been spent


def test_mark_is_gross_unrealized_to_bid():
    a = fresh()
    a.open("T1", QTY, ENTRY, BUY)
    assert a.mark(110.5) == pytest.approx((110.5 - ENTRY) * QTY)
    assert a.state.unrealized_pnl == pytest.approx(1300.0)
    assert a.equity == pytest.approx(250000 - BUY + 1300.0)
    assert a.mark(90.0) == pytest.approx(-1365.0)
    assert a.equity_at_bid(100.5) == pytest.approx(250000 - BUY)
    assert a.state.unrealized_pnl == pytest.approx(-1365.0)  # equity_at_bid does not mutate


def test_equity_identity_cash_plus_invested_plus_unrealized():
    a = fresh()
    a.open("T1", QTY, ENTRY, BUY)
    for bid in (0.0, 50.0, 100.5, 123.45):
        a.mark(bid)
        s = a.state
        assert s.cash + s.invested + s.unrealized_pnl == pytest.approx(s.equity)
        assert s.equity == pytest.approx(250000 - BUY + (bid - ENTRY) * QTY, abs=0.011)


def test_close_net_pnl_is_gross_minus_both_charges_applied_once():
    a = fresh()
    a.open("T1", QTY, ENTRY, BUY)
    bid = 119.5 + 0.5
    exit_fill = exit_fill_price(bid, 0.5)  # 119.5
    sell = charges("SELL", TRADE_DATE, exit_fill, QTY).total
    res = a.close("T1", exit_fill, sell, QTY)
    assert res.gross_pnl == pytest.approx((exit_fill - ENTRY) * QTY) == pytest.approx(2470.0)
    assert (BUY, sell) == (29.41, 53.34)
    assert res.net_pnl == pytest.approx(2470.0 - 29.41 - 53.34) == pytest.approx(2387.25)
    # cash moved by exactly the net P&L: charges are not applied twice
    assert a.cash == pytest.approx(250000 + res.net_pnl)
    assert a.equity == pytest.approx(a.cash)
    s = a.state
    assert s.invested == 0 and s.unrealized_pnl == 0
    assert s.realized_pnl == pytest.approx(res.net_pnl) and s.session_realized_pnl == pytest.approx(
        res.net_pnl
    )
    assert (s.trades_today, s.wins_today, s.losses_today) == (1, 1, 0)
    rt = round_trip_estimate(TRADE_DATE, ENTRY, exit_fill, QTY)
    assert res.net_pnl == pytest.approx(rt.net_pnl)
    assert res.balance_after == pytest.approx(a.cash)


def test_losing_trade_counts_loss_and_reduces_equity():
    a = fresh()
    a.open("T1", QTY, ENTRY, BUY)
    ex = exit_fill_price(80.0, 0.5)
    res = a.close("T1", ex, charges("SELL", TRADE_DATE, ex, QTY).total)
    assert res.net_pnl < (ex - ENTRY) * QTY
    assert a.state.losses_today == 1 and a.state.wins_today == 0
    assert a.equity == pytest.approx(250000 + res.net_pnl)


def test_close_twice_raises_and_changes_nothing():
    a = fresh()
    a.open("T1", QTY, ENTRY, BUY)
    a.close("T1", 101.0, 50.0)
    cash, realized = a.cash, a.state.realized_pnl
    with pytest.raises(DoubleCloseError):
        a.close("T1", 101.0, 50.0)
    assert (a.cash, a.state.realized_pnl) == (cash, realized)
    with pytest.raises(PositionStateError):
        a.close("T2", 101.0, 50.0)  # nothing open


def test_sell_quantity_must_equal_open_quantity():
    a = fresh()
    a.open("T1", QTY, ENTRY, BUY)
    for q in (QTY - 1, QTY + 1, 1):
        with pytest.raises(QuantityMismatchError):
            a.close("T1", 101.0, 50.0, quantity=q)
    assert a.has_open_position and a.open_quantity == QTY  # still open, untouched
    a.close("T1", 101.0, 50.0, quantity=QTY)


def test_wrong_trade_id_cannot_close():
    a = fresh()
    a.open("T1", QTY, ENTRY, BUY)
    with pytest.raises(PositionStateError):
        a.close("OTHER", 101.0, 50.0)


def test_only_one_position_and_no_trade_id_reuse():
    a = fresh()
    a.open("T1", 75, 100.0, 20.0)
    with pytest.raises(PositionStateError):
        a.open("T2", 75, 100.0, 20.0)
    a.close("T1", 100.0, 20.0)
    with pytest.raises(PositionStateError):
        a.open("T1", 75, 100.0, 20.0)


@pytest.mark.parametrize("qty", [0, -75, 1.5, True])
def test_never_negative_or_fractional_position(qty):
    with pytest.raises(ValueError):
        fresh().open("T1", qty, 100.0, 20.0)  # type: ignore[arg-type]


def test_insufficient_cash_rejected_atomically():
    a = VirtualAccount(1000.0)
    a.start_session()
    with pytest.raises(InsufficientFundsError):
        a.open("T1", 75, 100.0, 20.0)
    assert a.cash == 1000.0 and not a.has_open_position


def test_invalid_bid_and_prices_rejected():
    a = fresh()
    a.open("T1", 75, 100.0, 20.0)
    with pytest.raises(ValueError):
        a.mark(-1.0)
    with pytest.raises(ValueError):
        a.mark(float("nan"))
    with pytest.raises(ValueError):
        a.close("T1", 0.0, 20.0)
    with pytest.raises(ValueError):
        a.close("T1", 100.0, -1.0)
    assert fresh().mark(100.0) == 0.0  # flat -> nothing to mark


def test_paise_exact_no_float_drift_over_many_trades():
    a = fresh()
    expected = 250000.0
    for i in range(200):
        a.open(f"T{i}", 75, 0.05, 0.01)
        a.close(f"T{i}", 0.10, 0.01)
        expected += 0.05 * 75 - 0.02
    assert a.cash == pytest.approx(expected, abs=0.005)
    assert round(a.cash * 100) == a.cash * 100 or abs(a.cash * 100 - round(a.cash * 100)) < 1e-6


def test_session_open_equity_vs_starting_capital_after_prior_day_pnl():
    a = fresh()
    a.open("D1", QTY, ENTRY, BUY)
    ex = exit_fill_price(70.0, 0.5)
    res = a.close("D1", ex, charges("SELL", TRADE_DATE, ex, QTY).total)
    assert res.net_pnl < 0
    # next morning
    soff = a.start_session()
    assert soff == pytest.approx(250000 + res.net_pnl)
    assert soff < a.state.starting_capital
    assert a.state.session_open_equity == pytest.approx(soff)
    assert a.state.realized_pnl == pytest.approx(res.net_pnl)  # cumulative persists
    assert a.state.session_realized_pnl == 0 and a.state.trades_today == 0  # daily counters reset
    assert a.state.starting_capital == 250000.0


def test_start_session_with_explicit_equity_and_guards():
    a = VirtualAccount(250000.0)
    assert a.start_session(240000.0) == 240000.0
    with pytest.raises(ValueError):
        a.start_session(0.0)
    a.open("T1", 75, 100.0, 20.0)
    with pytest.raises(PositionStateError):
        a.start_session()


def test_checkpoint_roundtrip_flat_and_open():
    a = fresh()
    a.open("T1", QTY, ENTRY, BUY)
    a.close("T1", 105.0, 40.0)
    a.start_session()
    a.open("T2", 75, 90.0, 21.0)
    a.mark(95.0)
    ck = json.loads(json.dumps(a.to_checkpoint()))  # must be JSON-serialisable
    b = VirtualAccount.restore_from_checkpoint(ck)
    assert b.to_checkpoint() == a.to_checkpoint()
    assert b.state == a.state
    assert b.has_open_position and b.open_quantity == 75
    with pytest.raises(DoubleCloseError):
        b.close("T1", 100.0, 10.0)  # closed ids survive restart
    res = b.close("T2", 95.0, 30.0, 75)
    assert res.net_pnl == pytest.approx(75 * 5.0 - 21.0 - 30.0)


def test_checkpoint_corruption_detected():
    a = fresh()
    a.open("T1", 75, 100.0, 20.0)
    good = a.to_checkpoint()
    bad = dict(good, cash="999999.00")
    with pytest.raises(CheckpointError, match="reconcile"):
        VirtualAccount.restore_from_checkpoint(bad)
    with pytest.raises(CheckpointError):
        VirtualAccount.restore_from_checkpoint(dict(good, schema=99))
    with pytest.raises(CheckpointError):
        VirtualAccount.restore_from_checkpoint({"schema": 1})
    with pytest.raises(CheckpointError):
        VirtualAccount.restore_from_checkpoint(dict(good, cash="abc"))
    flat = fresh().to_checkpoint()
    with pytest.raises(CheckpointError):
        VirtualAccount.restore_from_checkpoint(dict(flat, unrealized_pnl="5.00"))


def test_snapshot_is_detached_copy():
    a = fresh()
    snap = a.snapshot()
    a.open("T1", 75, 100.0, 20.0)
    assert snap.invested == 0 and a.state.invested > 0
