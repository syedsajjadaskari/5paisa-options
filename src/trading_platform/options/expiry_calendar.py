"""Historical option expiry calendar and filtering logic."""

from datetime import date
from enum import StrEnum


class ExpiryPolicy(StrEnum):
    """Expiry selection policy."""

    NEAREST_ELIGIBLE = "NEAREST_ELIGIBLE"
    NEXT_ELIGIBLE = "NEXT_ELIGIBLE"


def filter_eligible_expiries(
    trading_date: date,
    available_expiries: list[date],
    min_dte: int = 1,
    allow_expiry_day: bool = False,
) -> list[date]:
    """Filter available expiries based on trading date, min DTE, and expiry day policy.

    Args:
        trading_date: Current trading session date.
        available_expiries: List of available expiration dates on this trading date.
        min_dte: Minimum calendar days between trading date and expiry date.
        allow_expiry_day: If False, exclude expiries where trading_date == expiry_date (DTE = 0).

    Returns:
        Sorted list of eligible future expiration dates.
    """
    eligible: list[date] = []
    for exp in sorted(set(available_expiries)):
        dte = (exp - trading_date).days
        if dte < 0:
            continue
        if dte == 0 and not allow_expiry_day:
            continue
        if dte < min_dte:
            continue
        eligible.append(exp)
    return eligible


def select_target_expiry(
    trading_date: date,
    available_expiries: list[date],
    policy: ExpiryPolicy | str = ExpiryPolicy.NEAREST_ELIGIBLE,
    min_dte: int = 1,
    allow_expiry_day: bool = False,
) -> date:
    """Select the target expiration date given policy and eligibility constraints."""
    eligible = filter_eligible_expiries(
        trading_date=trading_date,
        available_expiries=available_expiries,
        min_dte=min_dte,
        allow_expiry_day=allow_expiry_day,
    )
    if not eligible:
        raise ValueError(
            f"No eligible expiries found on {trading_date} (min_dte={min_dte}, "
            f"allow_expiry_day={allow_expiry_day}) from {available_expiries}"
        )

    p = ExpiryPolicy(policy)
    if p == ExpiryPolicy.NEAREST_ELIGIBLE:
        return eligible[0]
    elif p == ExpiryPolicy.NEXT_ELIGIBLE:
        if len(eligible) < 2:
            # Fall back to nearest if next is not available, or raise
            return eligible[0]
        return eligible[1]

    return eligible[0]
