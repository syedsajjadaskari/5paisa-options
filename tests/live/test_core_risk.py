"""RiskGate tests (live/risk.py) on SYNTHETIC quotes/signals/accounts."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from test_core_helpers import (
    TRADE_DATE,
    at,
    cfg,
    make_contract,
    make_quote,
    make_signal,
    with_account,
    with_exec,
)

from trading_platform.live import costs
from trading_platform.live.ledger import VirtualAccount
from trading_platform.live.models import AccountState, EntryRejection, ExecutionTier
from trading_platform.live.risk import RiskGate

R = EntryRejection
RESEARCH = ExecutionTier.RESEARCH_PAPER


def gate(c=None) -> RiskGate:
    return RiskGate(c or cfg())


def account(
    start: float = 250000.0, cash: float | None = None, session_equity: float | None = None
) -> VirtualAccount:
    a = VirtualAccount(start, cash=cash)
    a.start_session(session_equity)
    return a


def codes(chk) -> set[EntryRejection]:
    return {r.code for r in chk.rejections}


def check(
    *,
    candle_open=None,
    now=None,
    quote=None,
    acct=None,
    tier=RESEARCH,
    contract=None,
    g=None,
    quote_at=None,
    **kw,
):
    candle_open = candle_open or at(10, 0)
    signal = make_signal(candle_open)
    now = now or signal.decision_time + timedelta(seconds=3)
    if quote is None and quote_at is not False:
        quote = make_quote(quote_at or now - timedelta(seconds=1))
    return (g or gate()).check_entry(
        signal=signal,
        tier=tier,
        contract=contract or make_contract(),
        quote=quote,
        account=acct or account(),
        now=now,
        **kw,
    )


# --------------------------------------------------------------------------- happy path


def test_baseline_entry_accepted_with_sizing():
    chk = check()
    assert chk.accepted and not chk.rejections
    assert chk.lots == 2 and chk.quantity == 150  # lot size 75 from the contract
    assert chk.fill_price == 100.5
    assert chk.entry_charges == costs.charges("BUY", TRADE_DATE, 100.5, 150).total
    assert chk.estimated_cost == pytest.approx(100.5 * 150 + chk.entry_charges)
    assert chk.premium_budget == pytest.approx(50000.0)
    d = chk.to_decision("S1")
    assert d.accepted and d.rejection is None


def test_rejected_decision_lists_every_failure():
    chk = check(quote_at=False)
    d = chk.to_decision("S1")
    assert not d.accepted and d.rejection == R.NO_QUOTE and "NO_QUOTE" in d.detail


# --------------------------------------------------------------------------- tiers / state


def test_shadow_and_disabled_tiers_never_fill():
    assert codes(check(tier=ExecutionTier.SHADOW_ONLY)) == {R.SHADOW_ONLY}
    assert R.STRATEGY_DISABLED in codes(check(tier=ExecutionTier.DISABLED))


def test_position_open_exit_pending_engine_state():
    a = account()
    a.open("T1", 75, 100.0, 20.0)
    assert R.POSITION_OPEN in codes(check(acct=a))
    assert R.EXIT_PENDING in codes(check(exit_pending=True))
    assert R.ENGINE_NOT_RUNNING in codes(check(engine_running=False))


def test_session_not_started_fails_closed():
    a = VirtualAccount(250000.0)  # start_session never called
    assert R.ENGINE_NOT_RUNNING in codes(check(acct=a))
    assert gate().daily_loss_breached(a) is True


# --------------------------------------------------------------------------- cutoff boundary


def test_candle_closing_exactly_1430_rejected():
    # candle opens 14:25, closes 14:30:00; fill at 14:30:00
    chk = check(candle_open=at(14, 25), now=at(14, 30, 0), quote_at=at(14, 30, 0))
    assert R.AFTER_ENTRY_CUTOFF in codes(chk) and not chk.accepted


def test_1425_candle_rejected_even_when_processed_instantly_before_fill_clock():
    # decision 14:30:00 alone triggers the cutoff; fill time 14:30:01 as well
    chk = check(candle_open=at(14, 25), now=at(14, 30, 1))
    assert R.AFTER_ENTRY_CUTOFF in codes(chk)
    details = " ".join(r.detail for r in chk.rejections if r.code == R.AFTER_ENTRY_CUTOFF)
    assert "candle closes 14:30:00" in details


def test_1420_candle_closing_1425_accepted():
    chk = check(candle_open=at(14, 20), now=at(14, 25, 3))
    assert chk.accepted, chk.rejections


def test_1420_candle_accepted_up_to_but_excluding_1430_fill():
    assert check(candle_open=at(14, 20), now=at(14, 29, 59), quote_at=at(14, 29, 58)).accepted
    late = check(candle_open=at(14, 20), now=at(14, 30, 0), quote_at=at(14, 29, 59))
    assert R.AFTER_ENTRY_CUTOFF in codes(late)  # fill timestamp must be < 14:30
    assert "fill time" in " ".join(r.detail for r in late.rejections)


def test_cutoff_helpers():
    g = gate()
    assert not g.cutoff_reached(at(14, 29, 59, 999999))
    assert g.cutoff_reached(at(14, 30))
    assert not g.square_off_due(at(15, 14, 59))
    assert g.square_off_due(at(15, 15))
    assert g.square_off_due(at(15, 29))


def test_square_off_due_blocks_entry():
    chk = check(candle_open=at(15, 10), now=at(15, 15, 2))
    assert R.AFTER_ENTRY_CUTOFF in codes(chk)
    assert any("square-off" in r.detail for r in chk.rejections)


# --------------------------------------------------------------------------- market session


def test_market_closed_before_open_after_close_weekend_holiday():
    assert R.MARKET_CLOSED in codes(check(candle_open=at(8, 50), now=at(8, 55, 3)))
    sat = date(2026, 10, 31)
    chk = check(candle_open=at(10, 0, day=sat), now=at(10, 5, 3, day=sat))
    assert R.MARKET_CLOSED in codes(chk)
    holiday = date(2026, 10, 20)  # Dussehra
    chk = check(candle_open=at(10, 0, day=holiday), now=at(10, 5, 3, day=holiday))
    assert R.MARKET_CLOSED in codes(chk)
    assert gate().market_open(at(9, 15)) and not gate().market_open(at(9, 14, 59))
    assert gate().market_open(at(15, 29, 59)) and not gate().market_open(at(15, 30))


def test_candle_not_complete_when_decision_after_now():
    chk = check(candle_open=at(10, 0), now=at(10, 4, 59), quote_at=at(10, 4, 58))
    assert R.CANDLE_NOT_COMPLETE in codes(chk)


def test_naive_datetimes_rejected():
    from datetime import datetime

    with pytest.raises(ValueError, match="timezone-aware"):
        gate().square_off_due(datetime(2026, 10, 28, 15, 0))


# --------------------------------------------------------------------------- quote rules


def test_no_quote_and_wrong_scrip():
    assert R.NO_QUOTE in codes(check(quote_at=False))
    assert R.NO_QUOTE in codes(check(quote=make_quote(at(10, 5, 2), scrip_code=999)))


def test_quote_freshness_judged_at_moment_of_use():
    q = make_quote(at(10, 5, 1))
    assert check(now=at(10, 5, 4), quote=q).accepted  # age 3s
    assert check(now=at(10, 5, 6), quote=q).accepted  # age exactly 5s is allowed
    stale = check(now=at(10, 5, 6, 1), quote=q)  # same quote, a moment later
    assert R.STALE_QUOTE in codes(stale)


def test_quote_from_future_is_rejected():
    chk = check(now=at(10, 5, 3), quote=make_quote(at(10, 5, 10)))
    assert R.STALE_QUOTE in codes(chk)


def test_quote_older_than_signal_decision_rejected():
    # fresh by age (3s) but received before the 10:05:00 candle close
    chk = check(now=at(10, 5, 2), quote=make_quote(at(10, 4, 59)))
    assert R.QUOTE_BEFORE_SIGNAL in codes(chk) and R.STALE_QUOTE not in codes(chk)
    assert check(now=at(10, 5, 2), quote=make_quote(at(10, 5, 0))).accepted  # == decision time ok


@pytest.mark.parametrize(
    "bid,ask",
    [(0.0, 100.0), (99.0, 0.0), (101.0, 100.0), (-1.0, 100.0), (float("nan"), 100.0)],
)
def test_zero_crossed_invalid_book_rejected(bid, ask):
    chk = check(quote=make_quote(at(10, 5, 2), bid=bid, ask=ask))
    assert R.CROSSED_OR_WIDE_QUOTE in codes(chk) and not chk.accepted


def test_locked_book_bid_equals_ask_allowed():
    assert check(quote=make_quote(at(10, 5, 2), bid=100.0, ask=100.0)).accepted


def test_wide_spread_points_and_pct():
    pts = check(quote=make_quote(at(10, 5, 2), bid=394.0, ask=400.0))  # 6 pts (1.5%)
    assert R.CROSSED_OR_WIDE_QUOTE in codes(pts)
    assert check(quote=make_quote(at(10, 5, 2), bid=395.0, ask=400.0)).accepted  # exactly 5 pts
    pct = check(quote=make_quote(at(10, 5, 2), bid=97.0, ask=100.0))  # 3 pts but 3.05%
    assert R.CROSSED_OR_WIDE_QUOTE in codes(pct)
    assert "%" in " ".join(r.detail for r in pct.rejections)


def test_top_of_book_quantity_is_warning_only():
    chk = check(quote=make_quote(at(10, 5, 2), ask_qty=50))
    assert chk.accepted
    assert any("TOP_OF_BOOK_QTY" in w for w in chk.warnings)
    assert "warnings" in chk.to_decision("S").detail
    assert not check().warnings


# --------------------------------------------------------------------------- premium allocation


def charges_fn_for(price):
    return lambda qty: costs.charges("BUY", TRADE_DATE, price, qty).total


def test_premium_budget_lots_formula_boundaries():
    g = gate()
    fn = charges_fn_for(330.5)
    # 2 lots (150 units): 330.5*150 = 49575 + charges ~45 -> within 50000
    assert g.premium_budget_lots(330.5, 75, fn, 250000.0) == 2
    # basis 200000 -> budget 40000 -> only 1 lot (24787.5 + charges)
    assert g.premium_budget_lots(330.5, 75, fn, 200000.0) == 1
    assert g.premium_budget_lots(330.5, 75, fn, 80000.0) == 0  # budget 16000 < 1 lot
    # capped by max_lots even with a giant budget
    assert g.premium_budget_lots(10.0, 75, charges_fn_for(10.0), 10_000_000.0) == 2


def test_premium_budget_includes_entry_charges_exactly():
    g = gate()
    price, lot = 133.0, 75
    base = price * lot * 2  # 19950
    ch = charges_fn_for(price)(150)
    # choose an equity basis whose 20% budget sits exactly between base and base+charges
    eq_between = (base + ch / 2) / 0.2
    assert g.premium_budget_lots(price, lot, charges_fn_for(price), eq_between) == 1
    eq_exact = (base + ch) / 0.2
    assert g.premium_budget_lots(price, lot, charges_fn_for(price), eq_exact + 0.01) == 2


def test_check_entry_one_lot_when_two_exceed_budget():
    chk = check(quote=make_quote(at(10, 5, 2), bid=396.0, ask=400.0))
    assert chk.accepted and chk.lots == 1 and chk.quantity == 75


def test_premium_budget_rejection_when_one_lot_exceeds_allocation():
    chk = check(quote=make_quote(at(10, 5, 2), bid=695.0, ask=700.0))  # 700.5*75 = 52537 > 50000
    assert codes(chk) == {R.PREMIUM_BUDGET}
    assert chk.lots == 0


def test_allocation_uses_session_open_equity_not_starting_capital():
    # prior-day loss: starting 250000 but session opens at 200000 -> budget 40000
    a = account(250000.0, cash=200000.0)
    assert a.session_open_equity == 200000.0
    q = make_quote(at(10, 5, 2), bid=326.0, ask=330.0)  # fill 330.5
    chk = check(acct=a, quote=q)
    assert chk.premium_budget == pytest.approx(40000.0)
    assert chk.lots == 1  # on 250000 it would have been 2 lots
    assert check(acct=account(), quote=q).lots == 2


def test_allocation_budget_not_moved_by_intraday_equity_drift():
    a = account()
    base = check(acct=a).premium_budget
    a.open("T1", 75, 100.0, 20.0)
    a.mark(150.0)
    a.close("T1", 150.0, 30.0)
    assert a.equity > 250000
    assert check(acct=a).premium_budget == pytest.approx(base)  # still 20% of session OPEN equity


def test_max_lots_config_respected():
    chk = check(g=gate(with_account(cfg(), max_lots=1)))
    assert chk.accepted and chk.lots == 1


def test_insufficient_cash():
    a = account(250000.0, cash=5000.0, session_equity=250000.0)
    chk = check(acct=a)
    assert R.INSUFFICIENT_CASH in codes(chk)


def test_invalid_contract_lot_size_rejected():
    chk = check(contract=make_contract(lot_size=0))
    assert R.NO_CONTRACT in codes(chk) and not chk.accepted


# --------------------------------------------------------------------------- daily loss


def test_daily_loss_boundary_with_open_position_marked_to_bid():
    g = gate()
    a = account()
    a.open("T1", 150, 400.0, 100.0)  # cost 60000, charges 100 -> cash 189900
    assert a.cash == pytest.approx(189900.0)
    # equity must fall to 225000 (-10% of 250000) -> position value 35100 -> bid 234.00
    assert g.daily_loss_breached(a, 150 * 234.00) is True  # exactly -25000 -> breach (<=)
    assert g.daily_loss_breached(a, 150 * 234.01) is False
    assert g.daily_loss_breached(a, 150 * 300.0) is False


def test_daily_loss_uses_account_marks_when_value_not_supplied():
    g = gate()
    a = account()
    a.open("T1", 150, 400.0, 100.0)
    a.mark(234.01)
    assert g.daily_loss_breached(a) is False
    a.mark(234.00)
    assert g.daily_loss_breached(a) is True


def test_daily_loss_on_account_state_boundary():
    g = gate()
    st = AccountState(250000.0, cash=225000.0, session_open_equity=250000.0)
    assert g.daily_loss_breached(st) is True  # == -25000
    st.cash = 225000.01
    assert g.daily_loss_breached(st) is False
    assert g.daily_loss_limit_inr(250000.0) == pytest.approx(25000.0)


def test_daily_loss_is_relative_to_session_open_equity_not_starting_capital():
    g = gate()
    # prior-day loss: session opened at 200000 -> limit 20000 (not 25000)
    st = AccountState(250000.0, cash=180000.0, session_open_equity=200000.0)
    assert g.daily_loss_breached(st) is True
    st.cash = 180000.01
    assert g.daily_loss_breached(st) is False


def test_daily_loss_includes_realized_winners_offset():
    g = gate()
    st = AccountState(250000.0, cash=240000.0, session_open_equity=250000.0)  # -4% net
    assert g.daily_loss_breached(st) is False


def test_check_entry_rejects_on_daily_loss():
    a = account(250000.0, cash=224000.0, session_equity=250000.0)
    chk = check(acct=a)
    assert R.DAILY_LOSS_LIMIT in codes(chk) and not chk.accepted


def test_config_overrides_flow_through():
    c = with_exec(with_account(cfg(), max_daily_loss_pct=5.0), max_quote_age_seconds=1.0)
    g = RiskGate(c)
    assert g.daily_loss_breached(
        AccountState(250000.0, cash=237500.0, session_open_equity=250000.0)
    )
    stale = check(g=g, now=at(10, 5, 3), quote=make_quote(at(10, 5, 1)))
    assert R.STALE_QUOTE in codes(stale)
