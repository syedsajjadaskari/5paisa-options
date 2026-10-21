"""Unit tests for point-in-time option contract selector and moneyness logic."""

from datetime import date

import pytest

from trading_platform.core.enums import SignalDirection
from trading_platform.data.contract_archive import ContractArchive
from trading_platform.data.option_chain import OptionContractMetadata
from trading_platform.options.contract_selector import ContractSelectionError, ContractSelector
from trading_platform.options.contracts import (
    ContractMaturityType,
    NiftyLotSizeHistory,
    get_contract_lot_size,
)
from trading_platform.options.moneyness import Moneyness, find_atm_strike, get_target_strike


def create_mock_contracts(
    exp_date: date, strikes: list[float], opt_type: str = "CE", lot_size: int = 25
) -> list[OptionContractMetadata]:
    contracts = []
    for idx, k in enumerate(strikes):
        contracts.append(
            OptionContractMetadata(
                underlying_symbol="NIFTY",
                expiry_date=exp_date,
                strike_price=k,
                option_type=opt_type,  # type: ignore[arg-type]
                scrip_code=10000 + idx if opt_type == "CE" else 20000 + idx,
                lot_size=lot_size,
            )
        )
    return contracts


def test_atm_strike_calculation():
    strikes = [24700.0, 24750.0, 24800.0, 24850.0, 24900.0]
    assert find_atm_strike(24785.0, strikes) == 24800.0
    assert find_atm_strike(24770.0, strikes) == 24750.0
    assert find_atm_strike(24824.9, strikes) == 24800.0
    assert find_atm_strike(24825.1, strikes) == 24850.0


def test_moneyness_call_and_put():
    strikes = [24700.0, 24750.0, 24800.0, 24850.0, 24900.0]
    spot = 24810.0  # ATM is 24800

    # Call Options (CE)
    assert get_target_strike(spot, strikes, "CE", Moneyness.ATM) == 24800.0
    assert get_target_strike(spot, strikes, "CE", Moneyness.ITM_1) == 24750.0  # Lower strike
    assert get_target_strike(spot, strikes, "CE", Moneyness.OTM_1) == 24850.0  # Higher strike

    # Put Options (PE)
    assert get_target_strike(spot, strikes, "PE", Moneyness.ATM) == 24800.0
    assert get_target_strike(spot, strikes, "PE", Moneyness.ITM_1) == 24850.0  # Higher strike
    assert get_target_strike(spot, strikes, "PE", Moneyness.OTM_1) == 24750.0  # Lower strike


def test_contract_selector_bullish_and_bearish():
    archive = ContractArchive()
    trade_date = date(2024, 10, 18)
    exp_date = date(2024, 10, 24)
    strikes = [24700.0, 24750.0, 24800.0, 24850.0, 24900.0]

    for c in create_mock_contracts(exp_date, strikes, "CE", lot_size=25):
        archive.add_contract(c)
    for c in create_mock_contracts(exp_date, strikes, "PE", lot_size=25):
        archive.add_contract(c)

    selector = ContractSelector(archive=archive, moneyness=Moneyness.ATM)

    # Bullish signal -> Selects CE
    ce_contract = selector.select_contract(
        trade_date, spot_price=24805.0, signal_direction=SignalDirection.BUY
    )
    assert ce_contract.option_type == "CE"
    assert ce_contract.strike_price == 24800.0
    assert ce_contract.lot_size == 25

    # Bearish signal -> Selects PE
    pe_contract = selector.select_contract(
        trade_date, spot_price=24805.0, signal_direction=SignalDirection.SELL
    )
    assert pe_contract.option_type == "PE"
    assert pe_contract.strike_price == 24800.0


def test_contract_selector_rejects_missing_metadata():
    archive = ContractArchive()
    selector = ContractSelector(archive=archive)
    with pytest.raises(ContractSelectionError, match="No option contracts found"):
        selector.select_contract(
            date(2024, 10, 18), spot_price=24800.0, signal_direction=SignalDirection.BUY
        )


def test_historical_lot_size_schedule():
    """Verify historical regulatory milestones."""
    assert NiftyLotSizeHistory.get_lot_size(date(2020, 5, 10)) == 75
    assert NiftyLotSizeHistory.get_lot_size(date(2022, 1, 15)) == 50
    assert NiftyLotSizeHistory.get_lot_size(date(2024, 6, 1)) == 25
    assert NiftyLotSizeHistory.get_lot_size(date(2025, 1, 10)) == 75
    assert NiftyLotSizeHistory.get_lot_size(date(2026, 2, 15)) == 65


def test_contract_specific_lot_size_transitions_2026():
    """Verify NSE Circular 70616 transition for 2026 expiries (75 -> 65)."""
    # Weekly contract expiring Jan 06, 2026: lot size 65
    assert (
        get_contract_lot_size(
            trading_date=date(2025, 12, 15),
            expiry_date=date(2026, 1, 6),
            maturity_type=ContractMaturityType.WEEKLY,
        )
        == 65
    )

    # Weekly contract expiring Dec 23, 2025: lot size 75
    assert (
        get_contract_lot_size(
            trading_date=date(2025, 12, 15),
            expiry_date=date(2025, 12, 23),
            maturity_type=ContractMaturityType.WEEKLY,
        )
        == 75
    )

    # Quarterly contract expiring March 2026:
    # Traded at 75 prior to Dec 30, 2025 EOD
    assert (
        get_contract_lot_size(
            trading_date=date(2025, 11, 15),
            expiry_date=date(2026, 3, 26),
            maturity_type=ContractMaturityType.QUARTERLY,
        )
        == 75
    )
    # Automatically transitioned to 65 after Dec 30, 2025 EOD
    assert (
        get_contract_lot_size(
            trading_date=date(2026, 1, 5),
            expiry_date=date(2026, 3, 26),
            maturity_type=ContractMaturityType.QUARTERLY,
        )
        == 65
    )


def test_explicit_metadata_lot_size_priority():
    """Verify explicit metadata overrides inference."""
    assert (
        get_contract_lot_size(
            trading_date=date(2026, 2, 1),
            expiry_date=date(2026, 2, 26),
            metadata_lot_size=100,  # Explicitly verified custom metadata
        )
        == 100
    )
