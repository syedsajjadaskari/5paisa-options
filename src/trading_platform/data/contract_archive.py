"""Historical contract metadata archive and provider-independent options import interface."""

from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.core.enums import DataSource, Timeframe
from trading_platform.data.option_chain import OptionBar, OptionContractMetadata

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


class ContractArchive:
    """Stores point-in-time historical contract metadata (strikes, expiries, historical lot sizes)."""

    def __init__(self) -> None:
        self._contracts: dict[int, OptionContractMetadata] = {}

    def add_contract(self, metadata: OptionContractMetadata) -> None:
        """Add or update contract metadata."""
        self._contracts[metadata.scrip_code] = metadata

    def get_contract(self, scrip_code: int) -> OptionContractMetadata | None:
        """Retrieve contract metadata by scrip code."""
        return self._contracts.get(scrip_code)

    def find_contract(
        self,
        underlying: str,
        expiry: date,
        strike: float,
        option_type: str,
    ) -> OptionContractMetadata | None:
        """Find contract matching underlying, expiry, strike and option type."""
        opt_type = option_type.upper()
        for c in self._contracts.values():
            if (
                c.underlying_symbol == underlying
                and c.expiry_date == expiry
                and abs(c.strike_price - strike) < 0.01
                and c.option_type == opt_type
            ):
                return c
        return None


class GenericOptionsDataImporter:
    """Provider-independent importer for external historical expired options data."""

    @staticmethod
    def load_from_csv(
        file_path: str | Path,
        underlying: str = "NIFTY",
        source_name: DataSource = DataSource.CSV,
    ) -> list[OptionBar]:
        """Ingest options candles from generic CSV containing historical expired data."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]

        required = {"timestamp", "expiry", "strike", "option_type", "open", "high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"CSV missing required columns for options ingestion: {missing}")

        bars: list[OptionBar] = []
        for _, row in df.iterrows():
            ts = pd.to_datetime(row["timestamp"])
            if ts.tzinfo is None:
                ist_dt = ts.replace(tzinfo=TZ_IST)
            else:
                ist_dt = ts.astimezone(TZ_IST)
            utc_dt = ist_dt.astimezone(TZ_UTC)

            exp_dt = pd.to_datetime(row["expiry"]).date()
            strike = float(row["strike"])
            opt_type = str(row["option_type"]).upper()

            lot_size = int(row.get("lot_size", 25))
            scrip_code = int(row.get("scrip_code", 0))

            bar = OptionBar(
                timestamp=utc_dt,
                exchange_time=ist_dt,
                trading_date=ist_dt.date(),
                underlying_symbol=underlying,
                expiry_date=exp_dt,
                strike_price=strike,
                option_type=opt_type,  # type: ignore[arg-type]
                scrip_code=scrip_code,
                exchange_segment=str(row.get("exchange_segment", "D")),
                lot_size=lot_size,
                tick_size=float(row.get("tick_size", 0.05)),
                interval=Timeframe(row.get("interval", "1m")),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row.get("volume", 0)),
                open_interest=int(row["open_interest"])
                if pd.notna(row.get("open_interest"))
                else None,
                bid=float(row["bid"]) if pd.notna(row.get("bid")) else None,
                ask=float(row["ask"]) if pd.notna(row.get("ask")) else None,
                data_source=source_name,
            )
            bars.append(bar)

        return bars
