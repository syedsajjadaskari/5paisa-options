"""Comprehensive Unit Test Suite for Production-Grade NIFTY Options Paper Trading Platform."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import pytest

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.enums import OrderSide, OrderType, SignalDirection
from trading_platform.core.exceptions import LiveTradingPermissionError
from trading_platform.core.models import Order, Signal
from trading_platform.data.live_market_feed import Level2Quote, SpotTick
from trading_platform.options.live_contract_selector import LiveContractSelector
from trading_platform.paper.broker import PaperBroker
from trading_platform.paper.engine import PaperEngineConfig, PaperTradingEngine
from trading_platform.paper.portfolio import VirtualPortfolio
from trading_platform.paper.risk import PaperRiskManager

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


@pytest.fixture
def base_date():
    return date(2026, 10, 21)


@pytest.fixture
def base_datetime(base_date):
    return datetime(2026, 10, 21, 9, 20, 0, tzinfo=TZ_IST)


# 1. Broker Order Endpoint Unreachable
def test_broker_order_endpoint_unreachable():
    paper_broker = PaperBroker()
    with pytest.raises(LiveTradingPermissionError):
        paper_broker.assert_live_allowed()
    assert paper_broker.mode == "PAPER"
    assert paper_broker.live_approved is False

    read_only_broker = FivePaisaBroker(live_approved=False)
    with pytest.raises(LiveTradingPermissionError):
        read_only_broker.place_order(
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


# 2. Read-Only Connectivity & Token Verification
def test_read_only_connectivity():
    broker = FivePaisaBroker()
    # Scrip code for spot index is canonical
    assert broker.get_nifty_spot_scrip_code() == 999920000


# 3. Valid NIFTY Contract Selection (Bullish CE / Bearish PE)
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


# 4. ₹250,000 Capital Accounting and 20% Premium Allocation
def test_capital_and_20pct_allocation_limit():
    portfolio = VirtualPortfolio(starting_capital=250000.0, max_allocation_pct=20.0)
    assert portfolio.starting_capital == 250000.0
    assert portfolio.available_cash == 250000.0
    assert portfolio.max_allowed_allocation_inr == 50000.0  # 20% of 250k

    # Test capacity check: ₹40,000 is allowed
    ok, reason = portfolio.check_allocation_capacity(40000.0)
    assert ok is True

    # Test capacity check: ₹55,000 exceeds 20% cap
    ok, reason = portfolio.check_allocation_capacity(55000.0)
    assert ok is False
    assert "REJECTED_ALLOCATION_LIMIT" in reason


# 5. Option Lot Sizing (Max 2 Lots Ceiling & Affordable Sizing)
def test_option_lot_sizing():
    risk_mgr = PaperRiskManager(
        starting_capital=250000.0,
        max_lots_per_trade=2,
        max_allocation_pct=20.0,
    )
    portfolio = VirtualPortfolio(starting_capital=250000.0, max_allocation_pct=20.0)

    # Scenario A: Premium = ₹100, Lot size = 65
    # 1 lot = ₹6,500; 2 lots = ₹13,000.
    # Max allocation = ₹50,000. Both fit! Permitted lots should be 2.
    res_a = risk_mgr.calculate_position_size(
        premium_ask=100.0,
        lot_size=65,
        portfolio=portfolio,
        check_time=datetime(2026, 10, 21, 10, 0, tzinfo=TZ_IST),
    )
    assert res_a.is_approved is True
    assert res_a.permitted_lots == 2
    assert res_a.quantity == 130  # 2 lots * 65

    # Scenario B: Expensive Option: Premium = ₹500, Lot size = 65
    # 1 lot = 500 * 65 = ₹32,500 (fits under ₹50,000 cap).
    # 2 lots = ₹65,000 (exceeds ₹50,000 cap!).
    # Permitted lots must drop to 1!
    res_b = risk_mgr.calculate_position_size(
        premium_ask=500.0,
        lot_size=65,
        portfolio=portfolio,
        check_time=datetime(2026, 10, 21, 10, 0, tzinfo=TZ_IST),
    )
    assert res_b.is_approved is True
    assert res_b.permitted_lots == 1
    assert res_b.quantity == 65

    # Scenario C: Very Expensive Option: Premium = ₹1000, Lot size = 65
    # 1 lot = ₹65,000 > ₹50,000 cap. Must be rejected!
    res_c = risk_mgr.calculate_position_size(
        premium_ask=1000.0,
        lot_size=65,
        portfolio=portfolio,
        check_time=datetime(2026, 10, 21, 10, 0, tzinfo=TZ_IST),
    )
    assert res_c.is_approved is False
    assert res_c.permitted_lots == 0
    assert "REJECTED_ALLOCATION_LIMIT" in res_c.reason


# 6. Daily Loss Limit (2.0% = ₹5,000)
def test_daily_loss_limit():
    risk_mgr = PaperRiskManager(
        starting_capital=250000.0,
        daily_loss_limit_pct=2.0,
    )
    portfolio = VirtualPortfolio(starting_capital=250000.0)

    # Incur ₹4,000 loss -> still under ₹5,000 limit
    risk_mgr.record_trade_exit(-4000.0)
    res1 = risk_mgr.calculate_position_size(100.0, 65, portfolio, datetime(2026, 10, 21, 10, 0, tzinfo=TZ_IST))
    assert res1.is_approved is True

    # Incur another ₹1,500 loss -> total loss ₹5,500 >= ₹5,000
    risk_mgr.record_trade_exit(-1500.0)
    res2 = risk_mgr.calculate_position_size(100.0, 65, portfolio, datetime(2026, 10, 21, 10, 30, tzinfo=TZ_IST))
    assert res2.is_approved is False
    assert "REJECTED_DAILY_LOSS_LIMIT" in res2.reason


# 7. Entry Cutoff at 14:30 IST
def test_entry_cutoff_time():
    risk_mgr = PaperRiskManager(entry_cutoff_time=dt_time(14, 30))
    portfolio = VirtualPortfolio(starting_capital=250000.0)

    # 14:29 IST -> allowed
    t_allowed = datetime(2026, 10, 21, 14, 29, 0, tzinfo=TZ_IST)
    res1 = risk_mgr.calculate_position_size(100.0, 65, portfolio, t_allowed)
    assert res1.is_approved is True

    # 14:30 IST -> blocked
    t_blocked = datetime(2026, 10, 21, 14, 30, 0, tzinfo=TZ_IST)
    res2 = risk_mgr.calculate_position_size(100.0, 65, portfolio, t_blocked)
    assert res2.is_approved is False
    assert "REJECTED_ENTRY_CUTOFF" in res2.reason


# 8. Quote Staleness (5 Seconds Threshold) & Spread Threshold (2.0%)
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


# 9. Concurrency & Restart State Recovery
def test_engine_concurrency_and_restart_recovery(tmp_path):
    db_file = str(tmp_path / "paper_test.duckdb")
    state_file = str(tmp_path / "engine_state.json")

    cfg1 = PaperEngineConfig(
        mode="REPLAY_TEST",
        starting_capital=250000.0,
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
    mock_q = Level2Quote(51344, "NIFTY 27 OCT 2026 CE 22800.00", 100.0, 100.5, 50, 50, 100.5, now, now)

    # 1. Open trade
    ok = engine1.execute_signal(sig, spot_price=22811.35, candle_time=now, mock_quote=mock_q)
    assert ok is True
    assert engine1.portfolio.active_position is not None
    saved_cash = engine1.portfolio.available_cash

    # 2. Concurrency: second signal rejected
    sig2 = Signal(
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
    ok2 = engine1.execute_signal(sig2, spot_price=22811.35, candle_time=now, mock_quote=mock_q)
    assert ok2 is False

    # 3. Restart recovery: instance 2 with same state file recovers position
    cfg2 = PaperEngineConfig(
        mode="REPLAY_TEST",
        starting_capital=250000.0,
        db_path=db_file,
        state_file=state_file,
    )
    engine2 = PaperTradingEngine(cfg2)
    assert engine2.portfolio.active_position is not None
    assert engine2.portfolio.available_cash == saved_cash

    # Concurrency maintained in recovered engine
    assert engine2.execute_signal(sig2, spot_price=22811.35, candle_time=now, mock_quote=mock_q) is False


# 10. Forced Intraday Square-Off at 15:15 IST
def test_forced_intraday_square_off(tmp_path):
    cfg = PaperEngineConfig(
        mode="REPLAY_TEST",
        starting_capital=250000.0,
        db_path=str(tmp_path / "paper_test.duckdb"),
        state_file=str(tmp_path / "engine_state.json"),
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
    mock_q = Level2Quote(51344, "NIFTY 27 OCT 2026 CE 22800.00", 100.0, 100.5, 50, 50, 100.5, now, now)
    engine.execute_signal(sig, spot_price=22811.35, candle_time=now, mock_quote=mock_q)
    assert engine.portfolio.active_position is not None

    # Tick arrives at 15:15:05 IST
    sq_time = datetime(2026, 10, 21, 15, 15, 5, tzinfo=TZ_IST)
    tick = SpotTick("NIFTY 50", 999920000, 22840.0, 22850.0, 22830.0, 22800.0, sq_time, sq_time)
    engine.on_tick(tick)

    # Position must be closed with exit_reason = EOD_SQUAREOFF
    assert engine.portfolio.active_position is None
    assert len(engine.completed_trades) == 1
    assert engine.completed_trades[0].exit_reason == "EOD_SQUAREOFF"
