"""Fill-price and charge tests (live/costs.py)."""

from __future__ import annotations

import pytest
from test_core_helpers import TRADE_DATE

from trading_platform.backtesting.indian_options_costs import IndianOptionsCostCalculator
from trading_platform.live.costs import (
    charges,
    entry_fill_price,
    exit_fill_price,
    round_trip_estimate,
)

CALC = IndianOptionsCostCalculator()


def test_entry_fill_is_ask_plus_slippage():
    assert entry_fill_price(100.0, 0.5) == 100.5
    assert entry_fill_price(100.05, 0.5) == 100.55  # no float noise
    assert entry_fill_price(100.0, 0.0) == 100.0


def test_exit_fill_is_bid_minus_slippage_with_floor():
    assert exit_fill_price(119.0, 0.5) == 118.5
    assert exit_fill_price(0.40, 0.5) == 0.05  # floored
    assert exit_fill_price(0.05, 0.5) == 0.05
    assert exit_fill_price(0.30, 0.5, floor=0.10) == 0.10
    assert exit_fill_price(0.60, 0.5) == 0.10


@pytest.mark.parametrize(
    "fn,args",
    [
        (entry_fill_price, (0.0, 0.5)),
        (entry_fill_price, (-1.0, 0.5)),
        (entry_fill_price, (100.0, -0.5)),
        (exit_fill_price, (0.0, 0.5)),
        (exit_fill_price, (-2.0, 0.5)),
        (exit_fill_price, (10.0, -1)),
    ],
)
def test_invalid_prices_raise(fn, args):
    with pytest.raises(ValueError):
        fn(*args)


def test_hand_computed_buy_leg():
    # qty 130 @ 100.50 on 2026-10-28: turnover 13065
    b = charges("BUY", TRADE_DATE, 100.50, 130)
    assert b.brokerage == 20.0
    assert b.exchange_charges == 4.58  # 13065 * 0.0003503 = 4.5768
    assert b.sebi_charges == 0.01  # 13065 * 1e-6 = 0.0131
    assert b.gst == 4.43  # 0.18 * (20 + 4.58 + 0.01) = 4.4262
    assert b.stamp_duty == 0.39  # 13065 * 3e-5 = 0.39195
    assert b.stt == 0.0
    assert b.total == 29.41


def test_hand_computed_sell_leg_and_round_trip():
    s = charges("SELL", TRADE_DATE, 119.50, 130)  # turnover 15535
    assert s.stt == 23.30  # 15535 * 0.0015 = 23.3025
    assert s.exchange_charges == 5.44
    assert s.sebi_charges == 0.02
    assert s.gst == 4.58
    assert s.stamp_duty == 0.0
    assert s.total == 53.34
    rt = round_trip_estimate(TRADE_DATE, 100.50, 119.50, 130)
    assert rt.entry_charges == 29.41 and rt.exit_charges == 53.34
    assert rt.total_charges == 82.75
    assert rt.gross_pnl == 2470.0
    assert rt.net_pnl == 2387.25


def test_matches_calculator_with_slippage_disabled():
    for side, prem, qty in (("BUY", 87.35, 75), ("SELL", 142.1, 150), ("BUY", 0.05, 75)):
        ours = charges(side, TRADE_DATE, prem, qty)
        theirs = CALC.calculate_single_leg_costs(TRADE_DATE, side, prem, qty, apply_slippage=False)
        assert ours.total == theirs.total_charges
        assert theirs.slippage_cost == 0.0


def test_round_trip_equals_calculator_round_trip():
    rt = round_trip_estimate(TRADE_DATE, 100.5, 119.5, 130)
    ref = CALC.calculate_costs(TRADE_DATE, 100.5, 119.5, 130, apply_slippage=False)
    assert rt.total_charges == pytest.approx(ref.total_transaction_costs, abs=0.011)


def test_slippage_is_not_double_counted():
    qty = 130
    with_slip = CALC.calculate_single_leg_costs(TRADE_DATE, "BUY", 100.5, qty)  # default True: trap
    ours = charges("BUY", TRADE_DATE, 100.5, qty)
    assert with_slip.total_charges - ours.total == pytest.approx(0.5 * qty)  # 65.00 extra
    assert ours.total == 29.41
    # slippage appears in the fill price only: net P&L moves by exactly slip*qty per side
    flat = round_trip_estimate(
        TRADE_DATE, entry_fill_price(100.0, 0.5), exit_fill_price(100.0, 0.5), qty
    )
    assert flat.gross_pnl == -130.0  # (99.5 - 100.5) * 130, i.e. slippage counted once, in prices


def test_stt_only_on_sell_stamp_only_on_buy():
    assert charges("BUY", TRADE_DATE, 50.0, 75).stt == 0.0
    assert charges("SELL", TRADE_DATE, 50.0, 75).stamp_duty == 0.0
    assert charges("sell", TRADE_DATE, 50.0, 75).stt > 0


@pytest.mark.parametrize(
    "args", [("HOLD", 10.0, 75), ("BUY", 10.0, 0), ("BUY", 0.0, 75), ("BUY", -1.0, 75)]
)
def test_charges_input_validation(args):
    with pytest.raises(ValueError):
        charges(args[0], TRADE_DATE, args[1], args[2])
