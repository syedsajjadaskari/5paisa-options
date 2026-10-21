"""Unit tests for 5paisa read-only adapter and normalization logic."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.enums import OperationalMode, OrderSide, OrderType
from trading_platform.core.exceptions import BrokerError, LiveTradingPermissionError
from trading_platform.core.models import Order


def test_spot_scrip_code():
    assert FivePaisaBroker.get_nifty_spot_scrip_code() == 999920000


def test_parse_nifty_bars():
    broker = FivePaisaBroker(mode=OperationalMode.RESEARCH)
    raw_df = pd.DataFrame(
        [
            {
                "timestamp": "2024-10-18 09:15:00",
                "open": 24800.0,
                "high": 24825.0,
                "low": 24795.0,
                "close": 24810.0,
                "volume": 125000,
            },
            {
                "timestamp": "2024-10-18 09:20:00",
                "open": 24810.0,
                "high": 24835.0,
                "low": 24805.0,
                "close": 24830.0,
                "volume": 140000,
            },
        ]
    )
    bars = broker.parse_nifty_bars(raw_df, interval="5m")
    assert len(bars) == 2
    assert bars[0].symbol == "NIFTY 50"
    assert bars[0].instrument_id == "999920000"
    assert bars[0].open == 24800.0
    assert bars[1].close == 24830.0


def test_parse_option_bars():
    broker = FivePaisaBroker(mode=OperationalMode.RESEARCH)
    raw_df = pd.DataFrame(
        [
            {
                "timestamp": "2024-10-18 09:15:00",
                "open": 115.0,
                "high": 122.0,
                "low": 112.0,
                "close": 118.0,
                "volume": 1500,
                "open_interest": 45000,
            },
        ]
    )
    opt_bars = broker.parse_option_bars(
        raw_df,
        expiry=date(2024, 10, 24),
        strike=24800.0,
        option_type="CE",
        scrip_code=35008,
        lot_size=25,
        interval="1m",
    )
    assert len(opt_bars) == 1
    assert opt_bars[0].scrip_code == 35008
    assert opt_bars[0].strike_price == 24800.0
    assert opt_bars[0].option_type == "CE"
    assert opt_bars[0].lot_size == 25


def test_lookup_nifty_options_on_mock_scrip_master(tmp_path: Path):
    broker = FivePaisaBroker(mode=OperationalMode.RESEARCH, cache_dir=tmp_path)
    mock_scrip = pd.DataFrame(
        [
            {
                "Exch": "N",
                "ExchType": "D",
                "ScripCode": 35008,
                "Name": "NIFTY 24 OCT 2024 CE 24800.00",
                "Expiry": "2024-10-24",
                "ScripType": "CE",
                "StrikeRate": 24800.0,
                "SymbolRoot": "NIFTY",
                "LotSize": 25,
                "TickSize": 0.05,
            },
            {
                "Exch": "N",
                "ExchType": "D",
                "ScripCode": 35009,
                "Name": "NIFTY 24 OCT 2024 PE 24800.00",
                "Expiry": "2024-10-24",
                "ScripType": "PE",
                "StrikeRate": 24800.0,
                "SymbolRoot": "NIFTY",
                "LotSize": 25,
                "TickSize": 0.05,
            },
            {
                "Exch": "N",
                "ExchType": "D",
                "ScripCode": 35010,
                "Name": "BANKNIFTY 24 OCT 2024 CE 52000.00",
                "Expiry": "2024-10-24",
                "ScripType": "CE",
                "StrikeRate": 52000.0,
                "SymbolRoot": "BANKNIFTY",
                "LotSize": 15,
                "TickSize": 0.05,
            },
        ]
    )
    broker._scrip_df = mock_scrip

    # Filter NIFTY CE at 24800
    res = broker.lookup_nifty_options(strike=24800.0, option_type="CE")
    assert len(res) == 1
    assert res.iloc[0]["ScripCode"] == 35008

    # Filter NIFTY PE at 24800
    res_pe = broker.lookup_nifty_options(strike=24800.0, option_type="PE")
    assert len(res_pe) == 1
    assert res_pe.iloc[0]["ScripCode"] == 35009


def test_live_order_placement_strictly_blocked():
    broker = FivePaisaBroker(mode=OperationalMode.RESEARCH, live_approved=False)
    order = Order(
        order_id="ORD_1",
        client_order_id="CLI_1",
        symbol="NIFTY 24OCT24800CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=25,
        price=120.0,
        created_at=pd.Timestamp.now().to_pydatetime(),
        updated_at=pd.Timestamp.now().to_pydatetime(),
    )
    with pytest.raises(LiveTradingPermissionError):
        broker.place_order(order)


def test_fetch_historical_candles_unauthorized_raises():
    broker = FivePaisaBroker(mode=OperationalMode.RESEARCH)
    with pytest.raises(BrokerError, match="Authentication required"):
        broker.fetch_historical_candles()
