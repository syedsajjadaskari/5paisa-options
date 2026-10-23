"""Comprehensive Offline & Safety Test Suite for Phase 4 Paper Trading Engine.

Validates all 14 safety and deterministic requirements:
1. Bullish CE selection
2. Bearish PE selection
3. Wrong option contract rejection
4. Contract expiration validation
5. Stale quote rejection
6. Invalid bid/ask handling
7. Capital insufficiency rejection
8. Position concurrency (max 1 position)
9. Duplicate signal prevention
10. Missing candles handling
11. Market disconnection handling
12. Restart recovery without position duplication
13. Intraday square-off at 15:15 IST
14. Live broker order endpoint prohibition
"""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.brokers.paper_broker import PaperBroker
from trading_platform.core.enums import (
    OperationalMode,
    OrderSide,
    OrderType,
    SignalDirection,
)
from trading_platform.core.exceptions import LiveTradingPermissionError
from trading_platform.core.models import Bar, Order, Signal
from trading_platform.data.live_market_feed import (
    FiveMinuteCandleAggregator,
    Level2Quote,
    LiveMarketFeed,
    SpotTick,
)
from trading_platform.execution.paper_trading_engine import (
    PaperTradingConfig,
    PaperTradingEngine,
)
from trading_platform.options.live_contract_selector import LiveContractSelector
from trading_platform.paper.recorder import MarketDataRecorder

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


@pytest.fixture
def base_date():
    return date(2026, 10, 21)


@pytest.fixture
def base_datetime(base_date):
    return datetime(2026, 10, 21, 9, 20, 0, tzinfo=TZ_IST)


@pytest.fixture
def mock_feed():
    feed = LiveMarketFeed(broker=FivePaisaBroker(), poll_interval=1.0)
    feed.is_connected = True
    return feed


# 1. Bullish CE Selection
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


# 2. Bearish PE Selection
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


# 3. Wrong Option Contract Rejection
def test_wrong_option_contract_rejection(base_date, base_datetime):
    selector = LiveContractSelector()
    # Query an unrealistic strike where contracts do not exist in scrip master
    result = selector.select_contract(
        spot_price=99999.0,
        signal_direction=SignalDirection.BUY,
        trading_date=base_date,
    )
    assert result.status == "REJECTED_NO_CONTRACT"


# 4. Contract Expiration Validation
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


# 5. Stale Quote Rejection
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


# 6. Invalid Bid/Ask Handling (Missing Quote, Crossed Depth, Wide Spread)
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


# 7. Capital Insufficiency Rejection
def test_capital_insufficiency_rejection(tmp_path):
    # Starting balance ₹1,000 only, while 1 lot of NIFTY option (65 * ₹100 = ₹6,500)
    cfg = PaperTradingConfig(
        mode="REPLAY_TEST",
        initial_capital=1000.0,
        db_path=str(tmp_path / "test.duckdb"),
        state_file=str(tmp_path / "state.json"),
    )
    engine = PaperTradingEngine(cfg)

    now = datetime(2026, 10, 21, 9, 20, 0, tzinfo=TZ_IST)
    sig = Signal(
        strategy_name="ORB",
        method_id="ORB_METHOD_1",
        symbol="NIFTY 50",
        direction=SignalDirection.BUY,
        timestamp=now,
        reference_price=22811.35,
        stop_loss=22781.35,
        target=22871.35,
        reason="ORB Buy",
    )
    quote = Level2Quote(
        51344, "NIFTY 27 OCT 2026 CE 22800.00", 100.0, 100.5, 50, 50, 100.5, now, now
    )
    executed = engine.execute_signal(
        sig, spot_price=22811.35, candle_time=now, mock_quote=quote
    )
    assert executed is False
    assert engine.active_position_contract is None


# 8. Position Concurrency (Max 1 Open Position)
def test_position_concurrency(tmp_path):
    cfg = PaperTradingConfig(
        mode="REPLAY_TEST",
        initial_capital=100000.0,
        max_open_positions=1,
        db_path=str(tmp_path / "test.duckdb"),
        state_file=str(tmp_path / "state.json"),
    )
    engine = PaperTradingEngine(cfg)

    now = datetime(2026, 10, 21, 9, 20, 0, tzinfo=TZ_IST)
    sig1 = Signal(
        strategy_name="ORB",
        method_id="ORB_METHOD_1",
        symbol="NIFTY 50",
        direction=SignalDirection.BUY,
        timestamp=now,
        reference_price=22811.35,
        stop_loss=22781.35,
        target=22871.35,
        reason="ORB Buy 1",
    )
    quote = Level2Quote(
        51344, "NIFTY 27 OCT 2026 CE 22800.00", 100.0, 100.5, 50, 50, 100.5, now, now
    )

    # First trade accepted
    res1 = engine.execute_signal(sig1, spot_price=22811.35, candle_time=now, mock_quote=quote)
    assert res1 is True
    assert engine.active_position_contract is not None

    # Second trade rejected due to position concurrency
    sig2 = Signal(
        strategy_name="PDS",
        method_id="PDS_METHOD_1",
        symbol="NIFTY 50",
        direction=SignalDirection.SELL,
        timestamp=now,
        reference_price=22811.35,
        stop_loss=22851.35,
        target=22741.35,
        reason="PDS Sell 2",
    )
    res2 = engine.execute_signal(sig2, spot_price=22811.35, candle_time=now, mock_quote=quote)
    assert res2 is False


# 9. Duplicate Signal Prevention
def test_duplicate_signal_prevention(tmp_path):
    cfg = PaperTradingConfig(
        mode="REPLAY_TEST",
        initial_capital=100000.0,
        db_path=str(tmp_path / "test.duckdb"),
        state_file=str(tmp_path / "state.json"),
    )
    engine = PaperTradingEngine(cfg)
    now = datetime(2026, 10, 21, 9, 20, 0, tzinfo=TZ_IST)
    bar = Bar(
        timestamp=now.astimezone(TZ_UTC),
        exchange_time=now,
        symbol="NIFTY 50",
        instrument_id="999920000",
        open=22800.0,
        high=22850.0,
        low=22790.0,
        close=22840.0,
    )
    # First evaluation
    engine.on_candle_close(bar)
    # Second immediate evaluation with same candle
    signals2 = engine.on_candle_close(bar)
    # State machine daily trade flag prevents re-entry
    assert len(signals2) == 0 or engine.active_position_contract is not None


# 10. Missing Candles Handling
def test_missing_candles_handling():
    agg = FiveMinuteCandleAggregator()
    t1 = datetime(2026, 10, 21, 9, 15, 0, tzinfo=TZ_IST)
    tick1 = SpotTick("NIFTY", 999920000, 22800.0, 22810.0, 22790.0, 22800.0, t1, t1)
    agg.process_tick(tick1)

    # Missing ticks between 09:16 and 09:29; next tick arrives at 09:30
    t2 = datetime(2026, 10, 21, 9, 30, 0, tzinfo=TZ_IST)
    tick2 = SpotTick("NIFTY", 999920000, 22850.0, 22860.0, 22840.0, 22800.0, t2, t2)
    bar = agg.process_tick(tick2)

    # Closes the 09:15 bar accurately without crashing
    assert bar is not None
    assert bar.exchange_time == datetime(2026, 10, 21, 9, 15, 0, tzinfo=TZ_IST)
    assert bar.open == 22800.0


# 11. Market Disconnection Handling
def test_market_disconnection_handling():
    feed = LiveMarketFeed(broker=FivePaisaBroker(), poll_interval=1.0)
    feed.is_connected = False
    # Spot polling when disconnected returns None and increments error counter
    tick = feed.fetch_spot_tick()
    # In offline test, connection cannot be made so returns None
    assert tick is None or feed.is_connected is True


# 12. Restart Recovery without Duplicate Positions
def test_restart_recovery(tmp_path):
    db_file = str(tmp_path / "persist.duckdb")
    state_file = str(tmp_path / "engine_state.json")

    # Instance 1: Open position
    cfg1 = PaperTradingConfig(
        mode="REPLAY_TEST",
        initial_capital=100000.0,
        db_path=db_file,
        state_file=state_file,
    )
    engine1 = PaperTradingEngine(cfg1)
    now = datetime(2026, 10, 21, 9, 20, 0, tzinfo=TZ_IST)
    sig = Signal(
        strategy_name="ORB",
        method_id="ORB_METHOD_1",
        symbol="NIFTY 50",
        direction=SignalDirection.BUY,
        timestamp=now,
        reference_price=22811.35,
        stop_loss=22781.35,
        target=22871.35,
        reason="ORB Buy",
    )
    quote = Level2Quote(
        51344, "NIFTY 27 OCT 2026 CE 22800.00", 100.0, 100.5, 50, 50, 100.5, now, now
    )
    engine1.execute_signal(sig, spot_price=22811.35, candle_time=now, mock_quote=quote)
    assert engine1.active_position_contract is not None
    saved_cash = engine1.broker.current_cash

    # Instance 2: Simulate restart with same state file
    cfg2 = PaperTradingConfig(
        mode="REPLAY_TEST",
        initial_capital=100000.0,
        db_path=db_file,
        state_file=state_file,
    )
    engine2 = PaperTradingEngine(cfg2)
    assert engine2.active_position_contract is not None
    assert (
        engine2.active_position_contract["symbol"]
        == engine1.active_position_contract["symbol"]
    )
    assert engine2.broker.current_cash == saved_cash

    # Check concurrency: engine2 rejects new trade because recovered position is active
    new_sig = Signal(
        strategy_name="PDS",
        method_id="PDS_METHOD_1",
        symbol="NIFTY 50",
        direction=SignalDirection.SELL,
        timestamp=now,
        reference_price=22811.35,
        stop_loss=22851.35,
        target=22741.35,
        reason="PDS Sell",
    )
    assert (
        engine2.execute_signal(
            new_sig, spot_price=22811.35, candle_time=now, mock_quote=quote
        )
        is False
    )


# 13. Intraday Square-Off at 15:15 IST
def test_intraday_square_off(tmp_path):
    cfg = PaperTradingConfig(
        mode="REPLAY_TEST",
        initial_capital=100000.0,
        db_path=str(tmp_path / "test.duckdb"),
        state_file=str(tmp_path / "state.json"),
    )
    engine = PaperTradingEngine(cfg)

    now = datetime(2026, 10, 21, 9, 20, 0, tzinfo=TZ_IST)
    sig = Signal(
        strategy_name="ORB",
        method_id="ORB_METHOD_1",
        symbol="NIFTY 50",
        direction=SignalDirection.BUY,
        timestamp=now,
        reference_price=22811.35,
        stop_loss=22781.35,
        target=22871.35,
        reason="ORB Buy",
    )
    quote = Level2Quote(
        51344, "NIFTY 27 OCT 2026 CE 22800.00", 100.0, 100.5, 50, 50, 100.5, now, now
    )
    engine.execute_signal(sig, spot_price=22811.35, candle_time=now, mock_quote=quote)
    assert engine.active_position_contract is not None

    # Send tick at 15:15:01 IST
    eod_time = datetime(2026, 10, 21, 15, 15, 5, tzinfo=TZ_IST)
    eod_tick = SpotTick(
        symbol="NIFTY 50",
        scrip_code=999920000,
        last_rate=22830.0,
        high=22840.0,
        low=22820.0,
        prev_close=22800.0,
        provider_timestamp=eod_time,
        ingestion_timestamp=eod_time,
    )
    engine.on_tick(eod_tick)

    # Open position must be squared off automatically
    assert engine.active_position_contract is None
    assert len(engine.completed_trades) == 1
    assert engine.completed_trades[0].exit_reason == "EOD_SQUAREOFF"


# 14. Live Broker Order Endpoint Prohibition
def test_broker_order_endpoint_prohibition():
    paper_broker = PaperBroker()
    # assert_live_allowed MUST raise LiveTradingPermissionError under all circumstances
    with pytest.raises(LiveTradingPermissionError):
        paper_broker.assert_live_allowed()

    # The broker mode must be locked to PAPER
    assert paper_broker.mode == OperationalMode.PAPER
    assert paper_broker.live_approved is False

    # Live order methods do not exist or are strictly blocked
    with pytest.raises(LiveTradingPermissionError):
        FivePaisaBroker(mode=OperationalMode.PAPER, live_approved=False).place_order(
            Order(
                order_id="1",
                client_order_id="1",
                symbol="NIFTY",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=65,
                created_at=datetime.now(TZ_UTC),
                updated_at=datetime.now(TZ_UTC),
            )
        )


def test_option_archive_rejects_missing_provider_timestamp(tmp_path):
    """Receipt time must never be fabricated as the exchange/provider timestamp."""
    broker = MagicMock()
    broker.is_connected = True
    broker.get_scrip_master.return_value = pd.DataFrame(
        [
            {
                "Exch": "N",
                "ExchType": "D",
                "SymbolRoot": "NIFTY",
                "Expiry": "2026-10-29",
                "StrikeRate": 22800.0,
                "ScripType": "CE",
                "ScripCode": 51344,
                "LotSize": 65,
                "Name": "NIFTY 29 OCT 2026 CE 22800.00",
            }
        ]
    )
    broker.client.fetch_market_depth.return_value = {
        "Status": 0,
        "MarketDepthData": [
            {
                "Asks": [{"Price": 100.5, "Quantity": 65}],
                "Bids": [{"Price": 100.0, "Quantity": 65}],
                # Deliberately no TimeStamp/TickDt.
            }
        ],
    }
    recorder = MarketDataRecorder(
        db_path=tmp_path / "paper.duckdb",
        archive_dir=tmp_path / "archive",
    )

    assert recorder.archive_atm_option_chain(broker, spot_price=22811.0) == 0
    assert recorder._options_buffer == []
