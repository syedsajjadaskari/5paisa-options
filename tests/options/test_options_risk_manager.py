"""Unit tests for options risk management, position sizing, and exposure controls."""

from trading_platform.execution.options_risk_manager import OptionsRiskManager, OptionsRiskPolicy


def test_position_sizing_by_capital_cap():
    # Capital: 100,000, Max Allocation: 10% = 10,000 max per trade
    policy = OptionsRiskPolicy(
        initial_capital=100000.0,
        max_position_capital_pct=10.0,
        max_open_positions=1,
    )
    rm = OptionsRiskManager(policy=policy)

    # Premium = 100, Lot size = 25 -> Cost per lot = 2,500
    # Max allocation 10,000 // 2,500 = 4 lots = 100 units
    res = rm.calculate_position_size(entry_premium=100.0, lot_size=25, contract_id="NIFTY_TEST")
    assert res.is_approved is True
    assert res.lots == 4
    assert res.quantity == 100
    assert res.capital_required == 10000.0


def test_position_sizing_by_premium_risk():
    # Capital: 100,000, Max Risk: 1% = 1,000 max risk per trade
    # Premium = 100, Lot size = 25 -> Cost per lot = 2,500
    # Stop-Loss: 20% -> Risk per lot = 2,500 * 0.20 = 500
    # Max risk 1,000 // 500 = 2 lots
    policy = OptionsRiskPolicy(
        initial_capital=100000.0,
        max_position_capital_pct=20.0,  # 20,000 cap
        max_risk_per_trade_pct=1.0,  # 1,000 risk cap
    )
    rm = OptionsRiskManager(policy=policy)
    res = rm.calculate_position_size(
        entry_premium=100.0,
        lot_size=25,
        contract_id="NIFTY_TEST",
        premium_stop_pct=0.20,
    )
    assert res.is_approved is True
    assert res.lots == 2
    assert res.quantity == 50


def test_position_sizing_rejects_expensive_single_lot():
    # Capital: 100,000, Max Allocation: 10% = 10,000
    # Premium = 500, Lot size = 25 -> Cost per lot = 12,500 (> 10,000)
    policy = OptionsRiskPolicy(initial_capital=100000.0, max_position_capital_pct=10.0)
    rm = OptionsRiskManager(policy=policy)
    res = rm.calculate_position_size(entry_premium=500.0, lot_size=25, contract_id="NIFTY_EXP")
    assert res.is_approved is False
    assert "REJECTED_CAPITAL_EXCEEDED" in res.reason


def test_insufficient_total_capital_rejection():
    """Verify order rejected if account capital cannot afford 1 single lot."""
    policy = OptionsRiskPolicy(initial_capital=2000.0, max_position_capital_pct=100.0)
    rm = OptionsRiskManager(policy=policy)
    # 1 lot = 100 * 25 = 2,500 > 2,000 total capital
    res = rm.calculate_position_size(entry_premium=100.0, lot_size=25, contract_id="NIFTY_INSUFF")
    assert res.is_approved is False
    assert "REJECTED_INSUFFICIENT_CAPITAL" in res.reason


def test_max_daily_loss_enforcement():
    # Daily loss limit: 2% of 100,000 = 2,000
    policy = OptionsRiskPolicy(initial_capital=100000.0, max_daily_loss_pct=2.0)
    rm = OptionsRiskManager(policy=policy)

    # First trade loses 2,100
    rm.register_position_opened("TRADE_1")
    rm.record_trade_completion(net_pnl=-2100.0, contract_id="TRADE_1")

    # Second trade attempt must be rejected
    res = rm.calculate_position_size(entry_premium=100.0, lot_size=25, contract_id="TRADE_2")
    assert res.is_approved is False
    assert "REJECTED_MAX_DAILY_LOSS" in res.reason


def test_no_averaging_down_rejection():
    policy = OptionsRiskPolicy(max_open_positions=2, no_averaging_down=True)
    rm = OptionsRiskManager(policy=policy)
    rm.register_position_opened("NIFTY_24OCT_25000CE")

    # Attempting to size same contract again must be rejected
    res = rm.calculate_position_size(
        entry_premium=100.0, lot_size=25, contract_id="NIFTY_24OCT_25000CE"
    )
    assert res.is_approved is False
    assert "REJECTED_AVERAGING_DOWN" in res.reason
