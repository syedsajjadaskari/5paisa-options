"""Dynamic strike selection and moneyness determination."""

from enum import StrEnum
from typing import Literal


class Moneyness(StrEnum):
    """Moneyness selection policy."""

    ATM = "ATM"
    ITM_1 = "ITM_1"
    OTM_1 = "OTM_1"


def find_atm_strike(spot_price: float, available_strikes: list[float]) -> float:
    """Find the strike closest to the current underlying spot price.

    Uses the actual set of available strikes for the contract date,
    avoiding any hardcoded strike step assumption.
    """
    if not available_strikes:
        raise ValueError("Available strikes list cannot be empty.")

    sorted_strikes = sorted(set(available_strikes))
    atm = min(sorted_strikes, key=lambda k: abs(k - spot_price))
    return atm


def get_target_strike(
    spot_price: float,
    available_strikes: list[float],
    option_type: Literal["CE", "PE"] | str,
    moneyness: Moneyness | str = Moneyness.ATM,
) -> float:
    """Determine target strike based on spot price, available strike grid, option type, and moneyness.

    For CE (Call):
    - ATM: closest strike
    - ITM_1: one strike lower than ATM (in-the-money)
    - OTM_1: one strike higher than ATM (out-of-the-money)

    For PE (Put):
    - ATM: closest strike
    - ITM_1: one strike higher than ATM (in-the-money)
    - OTM_1: one strike lower than ATM (out-of-the-money)
    """
    if not available_strikes:
        raise ValueError("Available strikes list cannot be empty.")

    sorted_strikes = sorted(set(available_strikes))
    atm = find_atm_strike(spot_price, sorted_strikes)
    idx = sorted_strikes.index(atm)

    m = Moneyness(moneyness)
    opt_type = option_type.upper()

    if m == Moneyness.ATM:
        return atm

    if opt_type == "CE":
        if m == Moneyness.ITM_1:
            if idx == 0:
                raise ValueError(f"No lower strike available for ITM Call below {atm}")
            return sorted_strikes[idx - 1]
        elif m == Moneyness.OTM_1:
            if idx >= len(sorted_strikes) - 1:
                raise ValueError(f"No higher strike available for OTM Call above {atm}")
            return sorted_strikes[idx + 1]
    elif opt_type == "PE":
        if m == Moneyness.ITM_1:
            if idx >= len(sorted_strikes) - 1:
                raise ValueError(f"No higher strike available for ITM Put above {atm}")
            return sorted_strikes[idx + 1]
        elif m == Moneyness.OTM_1:
            if idx == 0:
                raise ValueError(f"No lower strike available for OTM Put below {atm}")
            return sorted_strikes[idx - 1]

    raise ValueError(f"Unsupported moneyness '{moneyness}' or option type '{option_type}'")
