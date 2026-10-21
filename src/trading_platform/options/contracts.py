"""Official historical NSE contract metadata and lot-size schedule for NIFTY 50 derivatives.

Regulatory Reference Framework:
1. NSE Circular NSE/FAOP/70616 (Dated: Oct 03, 2025, Effective: Oct 28, 2025 EOD):
   - Periodic revision of lot sizes based on SEBI contract value guidelines.
   - NIFTY lot size revised from 75 to 65.
   - Weekly & Monthly contracts expiring up to Dec 30, 2025: Lot size 75.
   - First revised weekly contract: Jan 06, 2026 expiry (Lot size 65).
   - First revised monthly contract: Jan 27, 2026 expiry (Lot size 65).
   - Quarterly and Half-Yearly contracts expiring beyond Dec 30, 2025: Traded at 75 until Dec 30, 2025 EOD,
     transitioned to 65 on Dec 30, 2025 EOD.
   Source: https://www.nseindia.com (Download Ref No: 70616)

2. NSE Circular NSE/FAOP/64625 (Dated: Oct 18, 2024, Effective: Nov 20, 2024):
   - Regulatory revision under SEBI index derivative framework.
   - NIFTY lot size revised from 25 to 75.
   - Existing weekly & monthly contracts expiring up to Dec 26, 2024: Lot size 25 until expiry.
   - Quarterly and Half-Yearly contracts: Traded at 25 until Dec 26, 2024 EOD, transitioned to 75 on Dec 26, 2024 EOD.
   - New contracts introduced Nov 20, 2024 onwards: Lot size 75.
   Source: https://www.nseindia.com (Download Ref No: 64625)

3. NSE Circular NSE/FAOP/61794 (Dated: April 2024, Effective: Apr 26, 2024):
   - NIFTY lot size revised from 50 to 25.

4. NSE Circular NSE/FAOP/48792 (Dated: July 2021, Effective: Oct 29, 2021):
   - NIFTY lot size revised from 75 to 50.
"""

from datetime import date
from enum import StrEnum


class ContractMaturityType(StrEnum):
    """Maturity classification for index derivative contracts."""

    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    HALF_YEARLY = "HALF_YEARLY"


class NiftyLotSizeHistory:
    """Historical regulatory schedule and contract-specific transition resolver for NIFTY 50."""

    # Key transition milestone dates
    DATE_OCT_2021_CUTOFF = date(2021, 10, 29)
    DATE_APR_2024_CUTOFF = date(2024, 4, 26)

    # Circular 64625 milestones (25 -> 75)
    CIRCULAR_64625_EFFECTIVE = date(2024, 11, 20)
    CIRCULAR_64625_TRANSITION_EOD = date(2024, 12, 26)

    # Circular 70616 milestones (75 -> 65)
    CIRCULAR_70616_EFFECTIVE = date(2025, 10, 28)
    CIRCULAR_70616_TRANSITION_EOD = date(2025, 12, 30)
    FIRST_65_WEEKLY_EXPIRY = date(2026, 1, 6)
    FIRST_65_MONTHLY_EXPIRY = date(2026, 1, 27)

    @classmethod
    def get_trade_date_default_lot_size(cls, trading_date: date) -> int:
        """Return the prevailing standard lot size on a trading date when contract metadata is absent."""
        if trading_date < cls.DATE_OCT_2021_CUTOFF:
            return 75
        elif trading_date < cls.DATE_APR_2024_CUTOFF:
            return 50
        elif trading_date < cls.CIRCULAR_64625_EFFECTIVE:
            return 25
        elif trading_date <= cls.CIRCULAR_70616_TRANSITION_EOD:
            return 75
        else:
            return 65

    @classmethod
    def get_lot_size(cls, trading_date: date) -> int:
        """Alias for get_trade_date_default_lot_size for backward compatibility."""
        return cls.get_trade_date_default_lot_size(trading_date)

    @classmethod
    def resolve_contract_lot_size(
        cls,
        trading_date: date,
        expiry_date: date | None = None,
        maturity_type: ContractMaturityType | str = ContractMaturityType.WEEKLY,
        metadata_lot_size: int | None = None,
    ) -> int:
        """Resolve exact point-in-time lot size for a contract.

        Hierarchy:
        1. Verifiable contract metadata lot size (always highest priority).
        2. Contract-specific expiry date and maturity transition rules.
        3. Prevailing regulatory lot size schedule on trading date.
        """
        if metadata_lot_size is not None and metadata_lot_size > 0:
            return metadata_lot_size

        if expiry_date is None:
            return cls.get_trade_date_default_lot_size(trading_date)

        mat_type = (
            ContractMaturityType(maturity_type) if isinstance(maturity_type, str) else maturity_type
        )

        # Rule Set 1: 2025/2026 Revision (Circular 70616: 75 -> 65)
        if expiry_date >= cls.FIRST_65_WEEKLY_EXPIRY:
            if mat_type in (ContractMaturityType.QUARTERLY, ContractMaturityType.HALF_YEARLY):
                # Long-dated contracts traded at 75 until Dec 30, 2025 EOD, then converted to 65
                if trading_date <= cls.CIRCULAR_70616_TRANSITION_EOD:
                    return 75
                return 65
            elif mat_type == ContractMaturityType.MONTHLY:
                if expiry_date >= cls.FIRST_65_MONTHLY_EXPIRY:
                    return 65
                return 75
            else:  # WEEKLY
                return 65

        # Contracts expiring on or before Dec 30, 2025 under the 75 regime
        if expiry_date > cls.CIRCULAR_64625_TRANSITION_EOD:
            return 75

        # Rule Set 2: Late 2024 Revision (Circular 64625: 25 -> 75)
        if expiry_date <= cls.CIRCULAR_64625_TRANSITION_EOD:
            if mat_type in (ContractMaturityType.QUARTERLY, ContractMaturityType.HALF_YEARLY):
                # Long-dated contracts traded at 25 until Dec 26, 2024 EOD, then converted to 75
                if trading_date <= cls.CIRCULAR_64625_TRANSITION_EOD:
                    return 25
                return 75
            # Existing weekly and monthly contracts expiring up to Dec 26, 2024 traded at 25
            if trading_date < cls.CIRCULAR_64625_EFFECTIVE:
                return 25
            # Contracts expiring by Dec 26, 2024 were already existing at 25
            return 25

        # Fallback to trade date schedule
        return cls.get_trade_date_default_lot_size(trading_date)


def get_contract_lot_size(
    trading_date: date,
    expiry_date: date | None = None,
    maturity_type: ContractMaturityType | str = ContractMaturityType.WEEKLY,
    metadata_lot_size: int | None = None,
) -> int:
    """Convenience functional interface for resolving lot size."""
    return NiftyLotSizeHistory.resolve_contract_lot_size(
        trading_date=trading_date,
        expiry_date=expiry_date,
        maturity_type=maturity_type,
        metadata_lot_size=metadata_lot_size,
    )
