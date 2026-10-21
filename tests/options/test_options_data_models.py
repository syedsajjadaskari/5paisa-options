"""Unit tests for options data models, storage, and generic importer."""

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading_platform.core.enums import Timeframe
from trading_platform.data.contract_archive import ContractArchive, GenericOptionsDataImporter
from trading_platform.data.historical_options import HistoricalOptionsStorage
from trading_platform.data.option_chain import OptionBar, OptionContractMetadata

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


def test_option_contract_metadata_tradingsymbol():
    meta = OptionContractMetadata(
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=25000.0,
        option_type="CE",
        scrip_code=45123,
        lot_size=25,
    )
    assert meta.tradingsymbol == "NIFTY24OCT2425000CE"
    assert meta.lot_size == 25


def test_option_bar_validation():
    t0 = datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST)
    bar = OptionBar(
        timestamp=t0.astimezone(TZ_UTC),
        exchange_time=t0,
        trading_date=t0.date(),
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
        open=120.0,
        high=135.0,
        low=115.0,
        close=130.0,
        volume=5000,
        open_interest=250000,
        bid=129.5,
        ask=130.5,
    )
    assert bar.high >= bar.open
    assert bar.low <= bar.close
    assert bar.volume == 5000


def test_option_bar_invalid_ohlc_raises():
    t0 = datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST)
    with pytest.raises(ValueError):
        OptionBar(
            timestamp=t0.astimezone(TZ_UTC),
            exchange_time=t0,
            trading_date=t0.date(),
            underlying_symbol="NIFTY",
            expiry_date=date(2024, 10, 24),
            strike_price=24800.0,
            option_type="CE",
            scrip_code=45124,
            lot_size=25,
            open=120.0,
            high=110.0,  # High less than Open!
            low=105.0,
            close=115.0,
        )


def test_historical_options_storage(tmp_path: Path):
    storage = HistoricalOptionsStorage(
        base_dir=tmp_path / "options",
        duckdb_path=tmp_path / "options.duckdb",
    )
    t0 = datetime(2024, 10, 18, 9, 15, tzinfo=TZ_IST)
    bars = [
        OptionBar(
            timestamp=(t0 + timedelta(minutes=i)).astimezone(TZ_UTC),
            exchange_time=t0 + timedelta(minutes=i),
            trading_date=t0.date(),
            underlying_symbol="NIFTY",
            expiry_date=date(2024, 10, 24),
            strike_price=24800.0,
            option_type="CE",
            scrip_code=45124,
            lot_size=25,
            interval=Timeframe.M1,
            open=120.0 + i,
            high=125.0 + i,
            low=118.0 + i,
            close=122.0 + i,
            volume=1000 * (i + 1),
        )
        for i in range(5)
    ]

    saved_path = storage.save_option_bars(bars)
    assert saved_path.exists()

    queried = storage.query_option_bars(
        underlying="NIFTY",
        strike=24800.0,
        option_type="CE",
        interval="1m",
    )
    assert len(queried) == 5
    assert queried[0].close == 122.0
    storage.close()


def test_generic_options_importer_and_contract_archive(tmp_path: Path):
    csv_file = tmp_path / "mock_options.csv"
    csv_file.write_text(
        "timestamp,expiry,strike,option_type,open,high,low,close,volume,lot_size,scrip_code\n"
        "2024-10-18 09:15:00,2024-10-24,24800,CE,120.0,128.0,119.0,125.0,2500,25,98765\n"
        "2024-10-18 09:16:00,2024-10-24,24800,CE,125.0,130.0,124.0,129.0,3200,25,98765\n"
    )

    bars = GenericOptionsDataImporter.load_from_csv(csv_file)
    assert len(bars) == 2
    assert bars[0].strike_price == 24800.0
    assert bars[0].option_type == "CE"

    archive = ContractArchive()
    meta = OptionContractMetadata(
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=98765,
        lot_size=25,
    )
    archive.add_contract(meta)
    retrieved = archive.find_contract("NIFTY", date(2024, 10, 24), 24800.0, "CE")
    assert retrieved is not None
    assert retrieved.scrip_code == 98765
