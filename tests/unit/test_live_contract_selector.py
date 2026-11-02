"""LiveContractSelector regression tests (ported from the retired Phase-4 safety/platform suites).

The selector itself is kept in ``options/live_contract_selector.py``; the paper engines that used
it were archived under ``archive/legacy_2026-10-28`` (see docs/CLEANUP_MANIFEST.md).
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading_platform.core.enums import SignalDirection
from trading_platform.data.live_market_feed import Level2Quote
from trading_platform.options.live_contract_selector import LiveContractSelector

TZ_IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def base_date():
    return date(2026, 10, 21)


@pytest.fixture
def base_datetime(base_date):
    return datetime(2026, 10, 21, 9, 20, 0, tzinfo=TZ_IST)



def test_bullish_ce_selection(base_date, base_datetime):
    selector = LiveContractSelector()
    quote = Level2Quote(
        scrip_code=51344,
        symbol="NIFTY 27 OCT 2026 CE 22800.00",
        best_bid=135.0,
        best_ask=135.5,
        bid_qty=100,
        ask_qty=100,
        last_price=135.5,
        provider_timestamp=base_datetime,
        ingestion_timestamp=base_datetime,
    )
    result = selector.select_contract(
        spot_price=22811.35,
        signal_direction=SignalDirection.BUY,
        trading_date=base_date,
        mock_quote=quote,
    )
    assert result.status == "APPROVED"
    assert result.option_type == "CE"
    assert result.strike == 22800.0
    assert result.scrip_code == 51344
    assert result.lot_size == 65


def test_bearish_pe_selection(base_date, base_datetime):
    selector = LiveContractSelector()
    quote = Level2Quote(
        scrip_code=51345,
        symbol="NIFTY 27 OCT 2026 PE 22800.00",
        best_bid=190.0,
        best_ask=190.5,
        bid_qty=100,
        ask_qty=100,
        last_price=190.5,
        provider_timestamp=base_datetime,
        ingestion_timestamp=base_datetime,
    )
    result = selector.select_contract(
        spot_price=22811.35,
        signal_direction=SignalDirection.SELL,
        trading_date=base_date,
        mock_quote=quote,
    )
    assert result.status == "APPROVED"
    assert result.option_type == "PE"
    assert result.strike == 22800.0
    assert result.scrip_code == 51345
    assert result.lot_size == 65


def test_wrong_option_contract_rejection(base_date, base_datetime):
    selector = LiveContractSelector()
    # Query an unrealistic strike where contracts do not exist in scrip master
    result = selector.select_contract(
        spot_price=99999.0,
        signal_direction=SignalDirection.BUY,
        trading_date=base_date,
    )
    assert result.status == "REJECTED_NO_CONTRACT"


def test_contract_expiration_validation(base_datetime):
    selector = LiveContractSelector()
    # Date far in the future after all active scrip master contracts expired
    future_date = date(2035, 1, 1)
    result = selector.select_contract(
        spot_price=22800.0,
        signal_direction=SignalDirection.BUY,
        trading_date=future_date,
    )
    assert result.status == "REJECTED_NO_CONTRACT"


def test_stale_quote_rejection(base_date, base_datetime):
    selector = LiveContractSelector(max_staleness_seconds=30.0)
    stale_timestamp = base_datetime - timedelta(seconds=45)
    stale_quote = Level2Quote(
        scrip_code=51344,
        symbol="NIFTY 27 OCT 2026 CE 22800.00",
        best_bid=135.0,
        best_ask=135.5,
        bid_qty=100,
        ask_qty=100,
        last_price=135.5,
        provider_timestamp=stale_timestamp,
        ingestion_timestamp=base_datetime,
        is_stale=True,
    )
    result = selector.select_contract(
        spot_price=22811.35,
        signal_direction=SignalDirection.BUY,
        trading_date=base_date,
        mock_quote=stale_quote,
    )
    assert result.status == "REJECTED_STALE_QUOTE"


def test_selector_recomputes_staleness_when_flag_is_false(base_date, base_datetime):
    """A false freshness flag cannot override provider/receipt timestamp evidence."""
    selector = LiveContractSelector(max_staleness_seconds=5.0)
    quote = Level2Quote(
        scrip_code=51344,
        symbol="NIFTY 27 OCT 2026 CE 22800.00",
        best_bid=135.0,
        best_ask=135.5,
        bid_qty=100,
        ask_qty=100,
        last_price=135.5,
        provider_timestamp=base_datetime - timedelta(seconds=5.1),
        ingestion_timestamp=base_datetime,
        is_stale=False,
    )
    result = selector.select_contract(
        spot_price=22811.35,
        signal_direction=SignalDirection.BUY,
        trading_date=base_date,
        mock_quote=quote,
    )
    assert result.status == "REJECTED_STALE_QUOTE"


def test_invalid_bid_ask_handling(base_date, base_datetime):
    selector = LiveContractSelector(max_spread_points=5.0)

    # 6a: Non-positive ask
    q_zero = Level2Quote(
        scrip_code=51344,
        symbol="NIFTY",
        best_bid=100.0,
        best_ask=0.0,
        bid_qty=10,
        ask_qty=0,
        last_price=100.0,
        provider_timestamp=base_datetime,
        ingestion_timestamp=base_datetime,
    )
    r1 = selector.select_contract(
        spot_price=22800.0,
        signal_direction=SignalDirection.BUY,
        trading_date=base_date,
        mock_quote=q_zero,
    )
    assert r1.status == "REJECTED_NO_QUOTE"

    # 6b: Crossed market depth (Bid > Ask)
    q_crossed = Level2Quote(
        scrip_code=51344,
        symbol="NIFTY",
        best_bid=140.0,
        best_ask=130.0,
        bid_qty=10,
        ask_qty=10,
        last_price=135.0,
        provider_timestamp=base_datetime,
        ingestion_timestamp=base_datetime,
    )
    r2 = selector.select_contract(
        spot_price=22800.0,
        signal_direction=SignalDirection.BUY,
        trading_date=base_date,
        mock_quote=q_crossed,
    )
    assert r2.status == "REJECTED_NO_QUOTE"

    # 6c: Wide spread exceeding threshold
    q_wide = Level2Quote(
        scrip_code=51344,
        symbol="NIFTY",
        best_bid=130.0,
        best_ask=140.0,
        bid_qty=10,
        ask_qty=10,
        last_price=135.0,
        provider_timestamp=base_datetime,
        ingestion_timestamp=base_datetime,
    )
    r3 = selector.select_contract(
        spot_price=22800.0,
        signal_direction=SignalDirection.BUY,
        trading_date=base_date,
        mock_quote=q_wide,
    )
    assert r3.status == "REJECTED_WIDE_SPREAD"


def test_valid_nifty_contract_selection(base_date, base_datetime):
    selector = LiveContractSelector(max_spread_pct=2.0, max_staleness_seconds=5.0)

    # Bullish -> CE
    q_ce = Level2Quote(
        51344, "NIFTY 27 OCT 2026 CE 22800.00", 135.0, 135.5, 100, 100, 135.5, base_datetime, base_datetime
    )
    res_ce = selector.select_contract(22811.35, SignalDirection.BUY, trading_date=base_date, mock_quote=q_ce)
    assert res_ce.status == "APPROVED"
    assert res_ce.option_type == "CE"
    assert res_ce.strike == 22800.0
    assert res_ce.lot_size == 65

    # Bearish -> PE
    q_pe = Level2Quote(
        51345, "NIFTY 27 OCT 2026 PE 22800.00", 200.0, 200.5, 100, 100, 200.5, base_datetime, base_datetime
    )
    res_pe = selector.select_contract(22811.35, SignalDirection.SELL, trading_date=base_date, mock_quote=q_pe)
    assert res_pe.status == "APPROVED"
    assert res_pe.option_type == "PE"
    assert res_pe.strike == 22800.0


def test_quote_staleness_and_spread_threshold(base_date, base_datetime):
    selector = LiveContractSelector(
        max_spread_pct=2.0,
        max_staleness_seconds=5.0,
    )

    # Stale Quote (> 5 seconds old)
    stale_ts = base_datetime - timedelta(seconds=6)
    q_stale = Level2Quote(51344, "NIFTY", 100.0, 100.5, 50, 50, 100.5, stale_ts, base_datetime, is_stale=True)
    res_stale = selector.select_contract(22800.0, SignalDirection.BUY, base_date, mock_quote=q_stale)
    assert res_stale.status == "REJECTED_STALE_QUOTE"

    # Wide Spread (> 2.0%)
    # Ask=100, Bid=97 -> Spread = 3.0 pts = 3.0% > 2.0%
    q_wide = Level2Quote(51344, "NIFTY", 97.0, 100.0, 50, 50, 100.0, base_datetime, base_datetime)
    res_wide = selector.select_contract(22800.0, SignalDirection.BUY, base_date, mock_quote=q_wide)
    assert res_wide.status == "REJECTED_WIDE_SPREAD"
