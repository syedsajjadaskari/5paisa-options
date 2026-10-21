"""Historical point-in-time option contract selector."""

from datetime import date
from typing import Literal

from trading_platform.core.enums import SignalDirection
from trading_platform.data.contract_archive import ContractArchive
from trading_platform.data.option_chain import OptionContractMetadata
from trading_platform.options.contracts import get_contract_lot_size
from trading_platform.options.expiry_calendar import ExpiryPolicy, select_target_expiry
from trading_platform.options.moneyness import Moneyness, get_target_strike


class ContractSelectionError(Exception):
    """Raised when no valid or verifiable contract satisfies the selection criteria."""

    pass


class ContractSelector:
    """Selects valid historical option contracts based on signal and policy."""

    def __init__(
        self,
        archive: ContractArchive | None = None,
        moneyness: Moneyness | str = Moneyness.ATM,
        expiry_policy: ExpiryPolicy | str = ExpiryPolicy.NEAREST_ELIGIBLE,
        min_dte: int = 1,
        allow_expiry_day: bool = False,
    ) -> None:
        self.archive = archive or ContractArchive()
        self.moneyness = Moneyness(moneyness)
        self.expiry_policy = ExpiryPolicy(expiry_policy)
        self.min_dte = min_dte
        self.allow_expiry_day = allow_expiry_day

    def select_contract(
        self,
        trading_date: date,
        spot_price: float,
        signal_direction: SignalDirection | str,
        available_contracts: list[OptionContractMetadata] | None = None,
    ) -> OptionContractMetadata:
        """Select an option contract matching the signal, moneyness, and expiry rules.

        Args:
            trading_date: Date of the signal.
            spot_price: Current underlying NIFTY spot price.
            signal_direction: Signal intent (BUY -> bullish -> CE, SELL -> bearish -> PE).
            available_contracts: Optional list of contracts available on that date.
                                 If None, pulls from the internal archive.

        Returns:
            The selected point-in-time OptionContractMetadata.
        """
        # 1. Map direction to option type
        sig_dir = SignalDirection(signal_direction)
        if sig_dir in (SignalDirection.BUY,):
            option_type: Literal["CE", "PE"] = "CE"
        elif sig_dir in (SignalDirection.SELL,):
            option_type = "PE"
        else:
            raise ContractSelectionError(
                f"Cannot select option contract for signal direction '{signal_direction}'"
            )

        # 2. Gather candidates
        candidates = available_contracts or list(self.archive._contracts.values())
        matching_candidates = [
            c for c in candidates if c.option_type == option_type and c.expiry_date >= trading_date
        ]

        if not matching_candidates:
            raise ContractSelectionError(
                f"No option contracts found for {option_type} on or after {trading_date}"
            )

        # 3. Select target expiry
        available_expiries = [c.expiry_date for c in matching_candidates]
        try:
            target_expiry = select_target_expiry(
                trading_date=trading_date,
                available_expiries=available_expiries,
                policy=self.expiry_policy,
                min_dte=self.min_dte,
                allow_expiry_day=self.allow_expiry_day,
            )
        except ValueError as e:
            raise ContractSelectionError(f"Expiry selection failed on {trading_date}: {e}") from e

        # 4. Filter contracts for this target expiry
        expiry_contracts = [c for c in matching_candidates if c.expiry_date == target_expiry]
        available_strikes = [c.strike_price for c in expiry_contracts]

        if not available_strikes:
            raise ContractSelectionError(
                f"No strikes available for {option_type} expiring on {target_expiry}"
            )

        # 5. Determine target strike using dynamic strike grid
        try:
            target_strike = get_target_strike(
                spot_price=spot_price,
                available_strikes=available_strikes,
                option_type=option_type,
                moneyness=self.moneyness,
            )
        except ValueError as e:
            raise ContractSelectionError(
                f"Strike selection failed around spot {spot_price}: {e}"
            ) from e

        # 6. Locate contract metadata
        selected = None
        for c in expiry_contracts:
            if abs(c.strike_price - target_strike) < 0.01:
                selected = c
                break

        if selected is None:
            raise ContractSelectionError(
                f"Matching contract for strike {target_strike} {option_type} expiring {target_expiry} not found in archive"
            )

        # 7. Validate and bind point-in-time regulatory lot size
        resolved_lot_size = get_contract_lot_size(
            trading_date=trading_date,
            expiry_date=selected.expiry_date,
            metadata_lot_size=selected.lot_size,
        )
        if resolved_lot_size != selected.lot_size:
            # Return updated metadata with date-accurate regulatory lot size
            selected = OptionContractMetadata(
                underlying_symbol=selected.underlying_symbol,
                expiry_date=selected.expiry_date,
                strike_price=selected.strike_price,
                option_type=selected.option_type,
                scrip_code=selected.scrip_code,
                exchange=selected.exchange,
                exchange_segment=selected.exchange_segment,
                lot_size=resolved_lot_size,
                tick_size=selected.tick_size,
            )

        return selected
