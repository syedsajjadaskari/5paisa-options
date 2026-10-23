"""Live NIFTY Option Contract Selector with ATM strike selection, DTE validation, quote freshness and spread checks."""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.enums import SignalDirection
from trading_platform.data.live_market_feed import (
    MAX_PROVIDER_FUTURE_SKEW_SECONDS,
    Level2Quote,
    LiveMarketFeed,
)

logger = logging.getLogger(__name__)
TZ_IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class LiveContractSelectionResult:
    """Outcome of live option contract identification and Level 2 quote validation."""

    status: str  # 'APPROVED', 'REJECTED_NO_CONTRACT', 'REJECTED_NO_QUOTE', 'REJECTED_STALE_QUOTE', 'REJECTED_WIDE_SPREAD'
    option_type: str  # 'CE' or 'PE'
    strike: float
    expiry: date
    dte: int
    scrip_code: int
    symbol: str
    lot_size: int
    best_bid: float
    best_ask: float
    spread: float
    spread_pct: float
    reason: str
    selection_timestamp: datetime


class LiveContractSelector:
    """Identifies active ATM options contracts and validates live market depth."""

    def __init__(
        self,
        broker: FivePaisaBroker | None = None,
        feed: LiveMarketFeed | None = None,
        max_spread_points: float = 5.0,
        max_spread_pct: float = 5.0,
        max_staleness_seconds: float = 30.0,
        strike_step: float = 50.0,
    ) -> None:
        self.broker = broker or FivePaisaBroker()
        self.feed = feed or LiveMarketFeed(broker=self.broker)
        self.max_spread_points = max_spread_points
        self.max_spread_pct = max_spread_pct
        self.max_staleness_seconds = max_staleness_seconds
        self.strike_step = strike_step
        self._scrip_cache: pd.DataFrame | None = None

    def _get_nifty_options_master(self) -> pd.DataFrame:
        """Cache and return active NIFTY options from Scrip Master."""
        if self._scrip_cache is None or self._scrip_cache.empty:
            df = self.broker.get_scrip_master()
            mask = (df["Exch"] == "N") & (df["ExchType"] == "D") & (df["SymbolRoot"] == "NIFTY")
            self._scrip_cache = df[mask].copy()
            self._scrip_cache["ExpiryDate"] = self._scrip_cache["Expiry"].str.slice(0, 10)
        return self._scrip_cache

    def calculate_atm_strike(self, spot_price: float) -> float:
        """Calculate dynamic ATM strike rounded to nearest strike step (50 points)."""
        return round(spot_price / self.strike_step) * self.strike_step

    def select_contract(
        self,
        spot_price: float,
        signal_direction: SignalDirection | str,
        trading_date: date | None = None,
        mock_quote: Level2Quote | None = None,
    ) -> LiveContractSelectionResult:
        """Select ATM option contract and perform full quote validation.

        Steps:
        1. Determine CE (Bullish) or PE (Bearish).
        2. Compute ATM strike.
        3. Identify nearest eligible weekly expiry and DTE.
        4. Read dynamic lot size from instrument metadata.
        5. Fetch live Level 2 quote.
        6. Validate executable ask (for buy) and bid (for sell).
        7. Reject if quote is missing, stale, or spread exceeds limits.
        """
        now_dt = datetime.now(TZ_IST)
        t_date = trading_date or now_dt.date()

        # 1. Map direction
        sig_dir = (
            SignalDirection(signal_direction)
            if isinstance(signal_direction, str)
            else signal_direction
        )
        if sig_dir == SignalDirection.BUY:
            option_type: Literal["CE", "PE"] = "CE"
        elif sig_dir == SignalDirection.SELL:
            option_type = "PE"
        else:
            return self._rejected_result(
                "REJECTED_INVALID_DIRECTION",
                option_type="CE",
                strike=0.0,
                expiry=t_date,
                dte=0,
                reason=f"Invalid signal direction: {signal_direction}",
                timestamp=now_dt,
            )

        # 2. Compute ATM strike
        strike = self.calculate_atm_strike(spot_price)

        # 3. Locate contracts in scrip master
        df = self._get_nifty_options_master()
        t_str = t_date.isoformat()
        type_filtered = df[(df["ScripType"] == option_type) & (df["ExpiryDate"] >= t_str)]

        if type_filtered.empty:
            return self._rejected_result(
                "REJECTED_NO_CONTRACT",
                option_type=option_type,
                strike=strike,
                expiry=t_date,
                dte=0,
                reason=f"No active {option_type} options found on or after {t_date}",
                timestamp=now_dt,
            )

        # Identify nearest expiry
        available_expiries = sorted(type_filtered["ExpiryDate"].unique())
        target_expiry_str = available_expiries[0]
        target_expiry = date.fromisoformat(target_expiry_str)
        dte = (target_expiry - t_date).days

        # Locate exact strike
        matching_strike = type_filtered[
            (type_filtered["ExpiryDate"] == target_expiry_str)
            & (type_filtered["StrikeRate"] == strike)
        ]

        if matching_strike.empty:
            return self._rejected_result(
                "REJECTED_NO_CONTRACT",
                option_type=option_type,
                strike=strike,
                expiry=target_expiry,
                dte=dte,
                reason=f"No matching contract for strike {strike} {option_type} expiring {target_expiry}",
                timestamp=now_dt,
            )

        contract_row = matching_strike.iloc[0]
        scrip_code = int(contract_row["ScripCode"])
        lot_size = int(contract_row["LotSize"])
        symbol_name = str(contract_row["Name"])

        # 4. Fetch / Inject Level 2 Quote
        if mock_quote is not None:
            quote = mock_quote
        else:
            quote = self.feed.fetch_option_depth(scrip_code=scrip_code, symbol=symbol_name)

        # 5. Quote Validations
        if quote is None:
            return self._create_result(
                status="REJECTED_NO_QUOTE",
                option_type=option_type,
                strike=strike,
                expiry=target_expiry,
                dte=dte,
                scrip_code=scrip_code,
                symbol=symbol_name,
                lot_size=lot_size,
                best_bid=0.0,
                best_ask=0.0,
                spread=0.0,
                spread_pct=0.0,
                reason="Level 2 market depth unavailable from broker",
                timestamp=now_dt,
            )

        best_bid = quote.best_bid
        best_ask = quote.best_ask
        spread = quote.spread
        spread_pct = quote.spread_pct

        if not quote.provenance_verified:
            return self._create_result(
                status="REJECTED_NO_QUOTE",
                option_type=option_type,
                strike=strike,
                expiry=target_expiry,
                dte=dte,
                scrip_code=scrip_code,
                symbol=symbol_name,
                lot_size=lot_size,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                spread_pct=spread_pct,
                reason="Quote provenance is not independently verified",
                timestamp=now_dt,
            )

        # Check for valid executable quotes
        if best_ask <= 0 or best_bid <= 0:
            return self._create_result(
                status="REJECTED_NO_QUOTE",
                option_type=option_type,
                strike=strike,
                expiry=target_expiry,
                dte=dte,
                scrip_code=scrip_code,
                symbol=symbol_name,
                lot_size=lot_size,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                spread_pct=spread_pct,
                reason=f"Non-positive quote: Bid={best_bid}, Ask={best_ask}",
                timestamp=now_dt,
            )

        # Check crossed book (Bid > Ask)
        if best_bid > best_ask:
            return self._create_result(
                status="REJECTED_NO_QUOTE",
                option_type=option_type,
                strike=strike,
                expiry=target_expiry,
                dte=dte,
                scrip_code=scrip_code,
                symbol=symbol_name,
                lot_size=lot_size,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                spread_pct=spread_pct,
                reason=f"Crossed market depth: Bid {best_bid} > Ask {best_ask}",
                timestamp=now_dt,
            )

        # Independently recompute age from the immutable receipt/provider clocks.
        # Never trust a caller-supplied freshness flag by itself.
        raw_quote_age = (
            quote.ingestion_timestamp - quote.provider_timestamp
        ).total_seconds()
        if (
            raw_quote_age < -MAX_PROVIDER_FUTURE_SKEW_SECONDS
            or quote.is_stale
            or raw_quote_age > self.max_staleness_seconds
        ):
            return self._create_result(
                status="REJECTED_STALE_QUOTE",
                option_type=option_type,
                strike=strike,
                expiry=target_expiry,
                dte=dte,
                scrip_code=scrip_code,
                symbol=symbol_name,
                lot_size=lot_size,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                spread_pct=spread_pct,
                reason=(
                    f"Quote is stale or clock-skewed: age {raw_quote_age:.1f}s; "
                    f"absolute limit {self.max_staleness_seconds}s"
                ),
                timestamp=now_dt,
            )

        # Spread check
        if spread > self.max_spread_points:
            return self._create_result(
                status="REJECTED_WIDE_SPREAD",
                option_type=option_type,
                strike=strike,
                expiry=target_expiry,
                dte=dte,
                scrip_code=scrip_code,
                symbol=symbol_name,
                lot_size=lot_size,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                spread_pct=spread_pct,
                reason=f"Spread {spread:.2f} pts exceeds absolute limit {self.max_spread_points:.2f} pts",
                timestamp=now_dt,
            )

        if spread_pct > self.max_spread_pct:
            return self._create_result(
                status="REJECTED_WIDE_SPREAD",
                option_type=option_type,
                strike=strike,
                expiry=target_expiry,
                dte=dte,
                scrip_code=scrip_code,
                symbol=symbol_name,
                lot_size=lot_size,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                spread_pct=spread_pct,
                reason=f"Spread {spread_pct:.2f}% exceeds relative limit {self.max_spread_pct:.2f}%",
                timestamp=now_dt,
            )

        # Approved
        return self._create_result(
            status="APPROVED",
            option_type=option_type,
            strike=strike,
            expiry=target_expiry,
            dte=dte,
            scrip_code=scrip_code,
            symbol=symbol_name,
            lot_size=lot_size,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            spread_pct=spread_pct,
            reason="Verified executable Level 2 depth quote",
            timestamp=now_dt,
        )

    def _create_result(
        self,
        status: str,
        option_type: str,
        strike: float,
        expiry: date,
        dte: int,
        scrip_code: int,
        symbol: str,
        lot_size: int,
        best_bid: float,
        best_ask: float,
        spread: float,
        spread_pct: float,
        reason: str,
        timestamp: datetime,
    ) -> LiveContractSelectionResult:
        return LiveContractSelectionResult(
            status=status,
            option_type=option_type,
            strike=strike,
            expiry=expiry,
            dte=dte,
            scrip_code=scrip_code,
            symbol=symbol,
            lot_size=lot_size,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            spread_pct=spread_pct,
            reason=reason,
            selection_timestamp=timestamp,
        )

    def _rejected_result(
        self,
        status: str,
        option_type: str,
        strike: float,
        expiry: date,
        dte: int,
        reason: str,
        timestamp: datetime,
    ) -> LiveContractSelectionResult:
        return self._create_result(
            status=status,
            option_type=option_type,
            strike=strike,
            expiry=expiry,
            dte=dte,
            scrip_code=0,
            symbol="",
            lot_size=0,
            best_bid=0.0,
            best_ask=0.0,
            spread=0.0,
            spread_pct=0.0,
            reason=reason,
            timestamp=timestamp,
        )
