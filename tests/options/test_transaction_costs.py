"""Unit tests for Indian options transaction costs and time-versioned regulatory taxes."""

from datetime import date

from trading_platform.backtesting.indian_options_costs import IndianOptionsCostCalculator


def test_transaction_costs_pre_oct_2024():
    """Verify pre-October 2024 tax regime (STT: 0.0625%, NSE txn fee: 0.0505%)."""
    calc = IndianOptionsCostCalculator(brokerage_per_order=20.0, slippage_points_per_unit=0.50)
    trade_date = date(2024, 9, 15)
    entry_prem = 100.0
    exit_prem = 120.0
    qty = 50

    costs = calc.calculate_costs(
        trade_date=trade_date,
        entry_premium=entry_prem,
        exit_premium=exit_prem,
        quantity=qty,
        apply_slippage=True,
    )

    # Turnover: Buy = 5000, Sell = 6000, Total = 11000
    assert costs.brokerage == 40.00
    # STT @ 0.0625% on 6000 = 3.75
    assert costs.stt == 3.75
    # Exchange fee @ 0.0505% on 11000 = 5.56
    assert costs.exchange_charges == 5.56
    # Stamp duty @ 0.003% on 5000 = 0.15
    assert costs.stamp_duty == 0.15
    # SEBI turnover @ 0.0001% on 11000 = 0.01
    assert costs.sebi_charges == 0.01
    # GST @ 18% on (40 + 5.56 + 0.01) = 8.20
    assert costs.gst == 8.20
    # Slippage: 1.0 * 50 = 50.0
    assert costs.slippage_cost == 50.0
    assert costs.total_transaction_costs == 107.67


def test_transaction_costs_oct_2024_to_mar_2026():
    """Verify Oct 1, 2024 to Mar 31, 2026 regime (STT: 0.10%, NSE txn fee: 0.03503%)."""
    calc = IndianOptionsCostCalculator(brokerage_per_order=20.0, slippage_points_per_unit=0.50)
    trade_date = date(2025, 2, 10)
    entry_prem = 120.0
    exit_prem = 150.0
    qty = 50

    costs = calc.calculate_costs(
        trade_date=trade_date,
        entry_premium=entry_prem,
        exit_premium=exit_prem,
        quantity=qty,
        apply_slippage=True,
    )

    # Turnover: Buy = 6000, Sell = 7500, Total = 13500
    assert costs.brokerage == 40.00
    # STT @ 0.10% on 7500 = 7.50
    assert costs.stt == 7.50
    # Exchange fee @ 0.03503% on 13500 = 4.73
    assert costs.exchange_charges == 4.73
    # Stamp duty @ 0.003% on 6000 = 0.18
    assert costs.stamp_duty == 0.18
    # SEBI fee @ 0.0001% on 13500 = 0.01
    assert costs.sebi_charges == 0.01
    # GST @ 18% on (40 + 4.73 + 0.01 = 44.74) = 8.05
    assert costs.gst == 8.05
    assert costs.slippage_cost == 50.0
    assert costs.total_transaction_costs == 110.47


def test_transaction_costs_post_apr_2026_stt_hike():
    """Verify April 1, 2026 Union Budget revision (STT: 0.15% on sell premium)."""
    calc = IndianOptionsCostCalculator(brokerage_per_order=20.0, slippage_points_per_unit=0.0)
    trade_date = date(2026, 4, 15)
    entry_prem = 100.0
    exit_prem = 200.0
    qty = 65  # 2026 lot size

    costs = calc.calculate_costs(
        trade_date=trade_date,
        entry_premium=entry_prem,
        exit_premium=exit_prem,
        quantity=qty,
        apply_slippage=False,
    )

    # Sell Turnover: 65 * 200 = 13,000
    # STT @ 0.15% on 13,000 = 19.50
    assert costs.stt == 19.50
    assert IndianOptionsCostCalculator.get_stt_rate(trade_date) == 0.0015


def test_exercise_stt_modeling():
    """Verify exercise-related STT modeled separately on intrinsic settlement value."""
    calc = IndianOptionsCostCalculator(brokerage_per_order=20.0)
    trade_date = date(2025, 10, 15)

    # Option exercised with spot 25200, strike 25000 -> Intrinsic = 200
    costs = calc.calculate_costs(
        trade_date=trade_date,
        entry_premium=150.0,
        exit_premium=200.0,
        quantity=50,
        is_exercised=True,
        settlement_price=25200.0,
        strike_price=25000.0,
    )

    # Intrinsic = 200 * 50 = 10,000. Exercise STT @ 0.125% = 12.50
    assert costs.exercise_stt == 12.50
    assert costs.stt == 12.50


def test_account_specific_brokerage_rate():
    """Verify custom/account-specific brokerage plans (e.g. ₹10 Ultra Trader vs ₹20 standard)."""
    calc = IndianOptionsCostCalculator(brokerage_per_order=10.0, slippage_points_per_unit=0.0)
    trade_date = date(2025, 1, 10)
    costs = calc.calculate_costs(trade_date, 100.0, 110.0, 25, apply_slippage=False)
    # Buy + Sell @ ₹10 = ₹20
    assert costs.brokerage == 20.00
