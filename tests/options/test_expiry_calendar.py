"""Unit tests for option expiry filtering and selection policies."""

from datetime import date

import pytest

from trading_platform.options.expiry_calendar import (
    ExpiryPolicy,
    filter_eligible_expiries,
    select_target_expiry,
)


def test_expiry_filtering_min_dte_and_expiry_day():
    trading_date = date(2024, 10, 18)  # Friday
    all_expiries = [
        date(2024, 10, 17),  # Past
        date(2024, 10, 18),  # Today (DTE = 0)
        date(2024, 10, 24),  # Thursday (DTE = 6)
        date(2024, 10, 31),  # Next Thursday (DTE = 13)
        date(2024, 11, 7),  # Further (DTE = 20)
    ]

    # Exclude expiry day (DTE = 0)
    eligible = filter_eligible_expiries(
        trading_date, all_expiries, min_dte=1, allow_expiry_day=False
    )
    assert date(2024, 10, 18) not in eligible
    assert eligible[0] == date(2024, 10, 24)

    # Allow expiry day
    eligible_same_day = filter_eligible_expiries(
        trading_date, all_expiries, min_dte=0, allow_expiry_day=True
    )
    assert date(2024, 10, 18) in eligible_same_day
    assert eligible_same_day[0] == date(2024, 10, 18)


def test_select_target_expiry_policies():
    trading_date = date(2024, 10, 18)
    all_expiries = [
        date(2024, 10, 24),  # Nearest (DTE = 6)
        date(2024, 10, 31),  # Next (DTE = 13)
        date(2024, 11, 7),
    ]

    nearest = select_target_expiry(trading_date, all_expiries, policy=ExpiryPolicy.NEAREST_ELIGIBLE)
    assert nearest == date(2024, 10, 24)

    next_exp = select_target_expiry(trading_date, all_expiries, policy=ExpiryPolicy.NEXT_ELIGIBLE)
    assert next_exp == date(2024, 10, 31)


def test_select_target_expiry_no_candidates_raises():
    trading_date = date(2024, 10, 18)
    past_expiries = [date(2024, 10, 10), date(2024, 10, 17)]
    with pytest.raises(ValueError, match="No eligible expiries found"):
        select_target_expiry(trading_date, past_expiries)
