"""Phase 4B Comprehensive Supervised Paper Trading Validation Test Suite.

NOTE ON DATA PROVENANCE:
INFRASTRUCTURE TESTS ONLY - SYNTHETIC DATA FIXTURES (NOT FOR HISTORICAL RESEARCH).
All option prices, bid/ask spreads, and underlying spot paths in this module are
deterministic test doubles used solely for infrastructure and lifecycle validation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading_platform.core.enums import SignalDirection
from trading_platform.core.exceptions import LiveTradingPermissionError
from trading_platform.core.models import Signal
from trading_platform.data.live_market_feed import Level2Quote, SpotTick
from trading_platform.paper.engine import PaperEngineConfig, PaperTradingEngine

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


@pytest.fixture
def test_config(tmp_path):
    """Isolated PaperEngineConfig using temporary directories."""
    db_file = tmp_path / "paper_test.duckdb"
    state_file = tmp_path / "cache" / "paper_engine_state.json"
    reports_dir = tmp_path / "reports"
    return PaperEngineConfig(
        mode="REPLAY_TEST",
        starting_capital=250000.0,
        maximum_lots_per_trade=2,
        maximum_concurrent_positions=1,
        max_allocation_percent=20.0,
        daily_loss_limit_percent=2.0,
        slippage_points=0.50,
        brokerage_per_order=20.0,
        db_path=db_file,
        state_file=state_file,
        reports_dir=reports_dir,
    )


# -------------------------------------------------------------------------
# 1. ORB Bullish Signal -> Virtual CE Purchase -> Virtual Target Exit
# -------------------------------------------------------------------------
def test_orb_bullish_ce_lifecycle(test_config):
    """Validate full lifecycle: ORB Bullish Signal -> CE Selection -> Fill -> Target Exit."""
    engine = PaperTradingEngine(test_config)
    eval_time = datetime(2026, 10, 21, 9, 25, 0, tzinfo=TZ_IST)

    # 1. ORB Bullish Signal: Spot breakouts at 24500, SL=24450, TP=24600
    signal = Signal(
        symbol="NIFTY",
        strategy_name="ORB",
        method_id="M1",
        direction=SignalDirection.BUY,
        timestamp=eval_time,
        reference_price=24500.0,
        stop_loss=24450.0,
        target=24600.0,
        reason="ORB_BULLISH_BREAKOUT",
    )

    mock_quote = Level2Quote(
        scrip_code=51101,
        symbol="NIFTY 27 OCT 2026 CE 24500.00",
        best_bid=150.0,
        best_ask=150.5,
        bid_qty=500,
        ask_qty=500,
        last_price=150.5,
        provider_timestamp=eval_time,
        ingestion_timestamp=eval_time,
    )

    # Execute Signal
    executed = engine.execute_signal(
        signal=signal,
        spot_price=24500.0,
        candle_time=eval_time,
        mock_quote=mock_quote,
    )
    assert executed is True

    # Validate active position state
    pos = engine.portfolio.active_position
    assert pos is not None
    assert pos.symbol == "NIFTY 27 OCT 2026 CE 24500.00"
    assert pos.option_type == "CE"
    assert pos.strike == 24500.0
    assert pos.lots == 2
    assert pos.quantity == 130  # 2 lots * 65
    assert pos.entry_premium == 151.0  # 150.5 ask + 0.5 slippage
    assert engine.portfolio.available_cash < 250000.0

    # 2. Underlying moves to Target (24600.0) -> Exit triggered
    exit_time = eval_time + timedelta(minutes=15)
    exit_spot = 24605.0  # Above 24600 target
    mock_exit_bid = 210.0

    hit = engine.evaluate_exits(
        current_spot=exit_spot,
        tick_time=exit_time,
        mock_bid=mock_exit_bid,
    )
    assert hit is True
    assert engine.portfolio.active_position is None
    assert len(engine.completed_trades) == 1

    trade = engine.completed_trades[0]
    assert trade.exit_reason == "TARGET"
    assert trade.exit_price == 209.5  # 210.0 bid - 0.5 slippage
    assert trade.net_pnl > 0.0
    assert trade.gross_pnl == (209.5 - 151.0) * 130
    assert engine.portfolio.total_equity > 250000.0
    # Account equity, recorded trade P&L, and the paper broker cash ledger must reconcile.
    assert engine.portfolio.total_equity == pytest.approx(
        test_config.starting_capital + trade.net_pnl,
        abs=0.01,
    )
    assert engine.portfolio.total_equity == pytest.approx(engine.broker.current_cash, abs=0.01)
    assert engine.portfolio.realized_pnl == pytest.approx(trade.net_pnl, abs=0.01)


# -------------------------------------------------------------------------
# 2. PDS Bearish Signal -> Virtual PE Purchase -> Virtual Stop Loss Exit
# -------------------------------------------------------------------------
def test_pds_bearish_pe_lifecycle(test_config):
    """Validate full lifecycle: PDS Bearish Signal -> PE Selection -> Fill -> SL Exit."""
    engine = PaperTradingEngine(test_config)
    eval_time = datetime(2026, 10, 21, 9, 30, 0, tzinfo=TZ_IST)

    signal = Signal(
        symbol="NIFTY",
        strategy_name="PDS",
        method_id="M1",
        direction=SignalDirection.SELL,
        timestamp=eval_time,
        reference_price=24400.0,
        stop_loss=24450.0,
        target=24300.0,
        reason="PDS_BEARISH_ROLE_REVERSAL",
    )

    mock_quote = Level2Quote(
        scrip_code=51102,
        symbol="NIFTY 27 OCT 2026 PE 24400.00",
        best_bid=130.0,
        best_ask=130.8,
        bid_qty=500,
        ask_qty=500,
        last_price=130.8,
        provider_timestamp=eval_time,
        ingestion_timestamp=eval_time,
    )

    executed = engine.execute_signal(
        signal=signal,
        spot_price=24400.0,
        candle_time=eval_time,
        mock_quote=mock_quote,
    )
    assert executed is True

    pos = engine.portfolio.active_position
    assert pos is not None
    assert pos.option_type == "PE"
    assert pos.strike == 24400.0
    assert pos.quantity == 130

    # Underlying hits Stop Loss: spot rises to 24455 (>= 24450)
    exit_time = eval_time + timedelta(minutes=10)
    exit_spot = 24455.0
    mock_exit_bid = 95.0

    hit = engine.evaluate_exits(
        current_spot=exit_spot,
        tick_time=exit_time,
        mock_bid=mock_exit_bid,
    )
    assert hit is True
    assert engine.portfolio.active_position is None

    trade = engine.completed_trades[0]
    assert trade.exit_reason == "STOP_LOSS"
    assert trade.net_pnl < 0.0
    assert engine.risk_manager.consecutive_losses == 1


# -------------------------------------------------------------------------
# 3. IRF Bearish Signal -> Virtual PE Purchase -> Virtual Exit
# -------------------------------------------------------------------------
def test_irf_bearish_pe_lifecycle(test_config):
    """Validate full lifecycle: IRF Bearish Signal -> PE Selection -> Fill -> Exit."""
    engine = PaperTradingEngine(test_config)
    eval_time = datetime(2026, 10, 21, 10, 15, 0, tzinfo=TZ_IST)

    signal = Signal(
        symbol="NIFTY",
        strategy_name="IRF",
        method_id="M2",
        direction=SignalDirection.SELL,
        timestamp=eval_time,
        reference_price=24600.0,
        stop_loss=24650.0,
        target=24500.0,
        reason="IRF_EXTENSION_REVERSAL",
    )

    mock_quote = Level2Quote(
        scrip_code=51103,
        symbol="NIFTY 27 OCT 2026 PE 24600.00",
        best_bid=140.0,
        best_ask=140.5,
        bid_qty=500,
        ask_qty=500,
        last_price=140.5,
        provider_timestamp=eval_time,
        ingestion_timestamp=eval_time,
    )

    executed = engine.execute_signal(
        signal=signal,
        spot_price=24600.0,
        candle_time=eval_time,
        mock_quote=mock_quote,
    )
    assert executed is True

    pos = engine.portfolio.active_position
    assert pos.symbol == "NIFTY 27 OCT 2026 PE 24600.00"

    # Spot drops to 24490 (target hit)
    exit_time = eval_time + timedelta(minutes=20)
    mock_exit_bid = 195.0
    hit = engine.evaluate_exits(
        current_spot=24490.0,
        tick_time=exit_time,
        mock_bid=mock_exit_bid,
    )
    assert hit is True
    assert engine.portfolio.active_position is None
    assert engine.completed_trades[-1].exit_reason == "TARGET"


# -------------------------------------------------------------------------
# 4. PDR Bullish Signal -> Virtual CE Purchase -> Virtual Exit
# -------------------------------------------------------------------------
def test_pdr_bullish_ce_lifecycle(test_config):
    """Validate full lifecycle: PDR Retest Bounce -> CE Selection -> Fill -> Exit."""
    engine = PaperTradingEngine(test_config)
    eval_time = datetime(2026, 10, 21, 11, 0, 0, tzinfo=TZ_IST)

    signal = Signal(
        symbol="NIFTY",
        strategy_name="PDR",
        method_id="RETEST_BOUNCE",
        direction=SignalDirection.BUY,
        timestamp=eval_time,
        reference_price=24350.0,
        stop_loss=24300.0,
        target=24450.0,
        reason="PDR_SUPPORT_RETEST_BOUNCE",
    )

    mock_quote = Level2Quote(
        scrip_code=51104,
        symbol="NIFTY 27 OCT 2026 CE 24350.00",
        best_bid=160.0,
        best_ask=160.6,
        bid_qty=500,
        ask_qty=500,
        last_price=160.6,
        provider_timestamp=eval_time,
        ingestion_timestamp=eval_time,
    )

    executed = engine.execute_signal(
        signal=signal,
        spot_price=24350.0,
        candle_time=eval_time,
        mock_quote=mock_quote,
    )
    assert executed is True
    assert engine.portfolio.active_position.option_type == "CE"

    # Spot advances to 24460 (target hit)
    exit_time = eval_time + timedelta(minutes=25)
    mock_exit_bid = 215.0
    hit = engine.evaluate_exits(
        current_spot=24460.0,
        tick_time=exit_time,
        mock_bid=mock_exit_bid,
    )
    assert hit is True
    assert engine.completed_trades[-1].exit_reason == "TARGET"


# -------------------------------------------------------------------------
# 5. Independent Staleness & Timestamp Segregation
# -------------------------------------------------------------------------
def test_independent_timestamp_staleness_check(test_config):
    """Verify that spot quote freshness and option quote freshness are checked independently."""
    engine = PaperTradingEngine(test_config)
    now = datetime(2026, 10, 21, 10, 0, 0, tzinfo=TZ_IST)

    # Spot is fresh (2.0s old)
    spot_tick = SpotTick(
        symbol="NIFTY 50",
        scrip_code=999920000,
        last_rate=24500.0,
        high=24550.0,
        low=24450.0,
        prev_close=24400.0,
        provider_timestamp=now - timedelta(seconds=2.0),
        ingestion_timestamp=now,
    )
    assert spot_tick.is_stale is False

    # Option quote is stale (15.0s old, exceeding 5.0s threshold)
    stale_option_quote = Level2Quote(
        scrip_code=51101,
        symbol="NIFTY 27 OCT 2026 CE 24500.00",
        best_bid=150.0,
        best_ask=150.5,
        bid_qty=100,
        ask_qty=100,
        last_price=150.5,
        provider_timestamp=now - timedelta(seconds=15.0),
        ingestion_timestamp=now,  # Local receipt was recent, but exchange quote is old!
        is_stale=True,
    )
    assert stale_option_quote.is_stale is True

    # Validate independent checker
    is_valid, reason = engine.feed.validate_spot_and_option_freshness(
        spot_tick=spot_tick,
        option_quote=stale_option_quote,
    )
    assert is_valid is False
    assert "STALE_OPTION_QUOTE" in reason


def test_stale_spot_tick_is_rejected_before_state_or_candle_updates(test_config):
    """A stale provider tick cannot drive exits, candles, or the last-price state."""
    engine = PaperTradingEngine(test_config)
    now = datetime(2026, 10, 21, 10, 0, 0, tzinfo=TZ_IST)
    stale_tick = SpotTick(
        symbol="NIFTY 50",
        scrip_code=999920000,
        last_rate=24500.0,
        high=24510.0,
        low=24490.0,
        prev_close=24400.0,
        provider_timestamp=now - timedelta(seconds=5.1),
        ingestion_timestamp=now,
        is_stale=True,
    )

    assert engine.on_tick(stale_tick) is None
    assert engine.last_spot_price == 0.0
    assert engine.feed.aggregator.current_bar_open_time is None
    assert engine.closed_bars == []


def test_spot_tick_age_is_recomputed_when_stale_flag_is_false(test_config):
    """Engine rejects stale clock evidence even if an upstream flag is incorrect."""
    engine = PaperTradingEngine(test_config)
    now = datetime(2026, 10, 21, 10, 0, 0, tzinfo=TZ_IST)
    tick = SpotTick(
        symbol="NIFTY 50",
        scrip_code=999920000,
        last_rate=24500.0,
        high=24510.0,
        low=24490.0,
        prev_close=24400.0,
        provider_timestamp=now - timedelta(seconds=5.1),
        ingestion_timestamp=now,
        is_stale=False,
    )

    assert engine.on_tick(tick) is None
    assert engine.last_spot_price == 0.0


def test_paper_execution_requires_explicit_enabled_gate(test_config):
    """PAPER_TRADE cannot simulate a fill while the independent safety gate is off."""
    test_config.mode = "PAPER_TRADE"
    test_config.paper_execution_enabled = False
    engine = PaperTradingEngine(test_config)
    eval_time = datetime(2026, 10, 21, 10, 0, 0, tzinfo=TZ_IST)
    signal = Signal(
        symbol="NIFTY",
        strategy_name="ORB",
        method_id="M1",
        direction=SignalDirection.BUY,
        timestamp=eval_time,
        reference_price=24500.0,
        stop_loss=24470.0,
        target=24560.0,
        reason="SAFETY_GATE_TEST",
    )

    assert engine.execute_signal(signal, 24500.0, eval_time) is False
    assert engine.rejections_log[-1]["status"] == "PAPER_EXECUTION_DISABLED"


def test_live_strategy_context_never_fabricates_reference_levels(test_config):
    """Missing prior-session/indicator data stays missing instead of being invented."""
    engine = PaperTradingEngine(test_config)
    ctx = engine.strategy_contexts["ORB"]
    engine._update_strategy_context_levels(ctx, object())
    assert ctx.state == {}


# -------------------------------------------------------------------------
# 6. Rejection of Virtual Entries When Market Is Closed (PAPER_TRADE mode)
# -------------------------------------------------------------------------
def test_market_closed_rejection_in_paper_trade(test_config):
    """Ensure that in PAPER_TRADE mode, entries outside NSE hours are strictly rejected."""
    test_config.mode = "PAPER_TRADE"
    engine = PaperTradingEngine(test_config)

    # 21:58 IST is after market close (15:30 IST)
    eval_time = datetime(2026, 10, 21, 21, 58, 0, tzinfo=TZ_IST)
    signal = Signal(
        symbol="NIFTY",
        strategy_name="ORB",
        method_id="M1",
        direction=SignalDirection.BUY,
        timestamp=eval_time,
        reference_price=24500.0,
        stop_loss=24450.0,
        target=24600.0,
        reason="AFTER_HOURS_SIGNAL",
    )

    executed = engine.execute_signal(
        signal=signal,
        spot_price=24500.0,
        candle_time=eval_time,
    )
    assert executed is False
    assert engine.portfolio.active_position is None
    assert len(engine.rejections_log) >= 1
    assert "MARKET_CLOSED" in engine.rejections_log[-1]["status"]


# -------------------------------------------------------------------------
# 7. Missing or Stale Bid Quote on Exit Marks Exit As Pending (No Fake Fills)
# -------------------------------------------------------------------------
def test_missing_quote_marks_exit_as_pending(test_config):
    """Strictly verify that missing or stale quote on exit DOES NOT fabricate a fake exit fill."""
    engine = PaperTradingEngine(test_config)
    eval_time = datetime(2026, 10, 21, 9, 30, 0, tzinfo=TZ_IST)

    # Create active position
    mock_quote = Level2Quote(
        scrip_code=51101,
        symbol="NIFTY 27 OCT 2026 CE 24500.00",
        best_bid=150.0,
        best_ask=150.5,
        bid_qty=500,
        ask_qty=500,
        last_price=150.5,
        provider_timestamp=eval_time,
        ingestion_timestamp=eval_time,
    )
    signal = Signal(
        symbol="NIFTY",
        strategy_name="ORB",
        method_id="M1",
        direction=SignalDirection.BUY,
        timestamp=eval_time,
        reference_price=24500.0,
        stop_loss=24450.0,
        target=24600.0,
        reason="ORB_ENTRY",
    )
    engine.execute_signal(signal, 24500.0, eval_time, mock_quote=mock_quote)
    assert engine.portfolio.active_position is not None

    # Underlying hits target at 24610, but option depth is missing in PAPER_TRADE mode
    engine.config.mode = "PAPER_TRADE"
    engine.feed.fetch_option_depth = lambda scrip_code, symbol=None: None
    exit_spot = 24610.0
    exit_time = eval_time + timedelta(minutes=10)

    # Calling evaluate_exits with mock_bid=None triggers live depth lookup which returns None
    engine._close_active_position(
        exit_reason="TARGET",
        exit_time=exit_time,
        exit_spot=exit_spot,
        mock_bid=None,  # No mock provided -> fetch live depth
    )

    # Assert position was NOT closed! Pending exit was engaged!
    assert engine.portfolio.active_position is not None
    assert engine.pending_exit_reason == "TARGET"
    assert len(engine.completed_trades) == 0

    # Next cycle: Fresh valid executable bid quote arrives!
    fresh_depth = Level2Quote(
        scrip_code=51101,
        symbol="NIFTY 27 OCT 2026 CE 24500.00",
        best_bid=220.0,
        best_ask=220.5,
        bid_qty=500,
        ask_qty=500,
        last_price=220.0,
        provider_timestamp=exit_time + timedelta(seconds=2),
        ingestion_timestamp=exit_time + timedelta(seconds=2),
    )
    engine.feed.fetch_option_depth = lambda scrip_code, symbol=None: fresh_depth

    # Position now successfully exits with valid quote
    closed = engine._close_active_position(
        exit_reason=engine.pending_exit_reason,
        exit_time=exit_time + timedelta(seconds=2),
        exit_spot=exit_spot,
        mock_bid=None,
    )
    assert closed is True
    assert engine.portfolio.active_position is None
    assert engine.pending_exit_reason is None
    assert len(engine.completed_trades) == 1
    assert engine.completed_trades[0].exit_price == 219.5  # 220.0 - 0.5 slippage


# -------------------------------------------------------------------------
# 8. Crash Recovery Preserves Run ID and Engine State
# -------------------------------------------------------------------------
def test_state_persistence_and_run_id_consistency(test_config):
    """Verify that restarting the engine reloads existing run_id, active position, and signal keys."""
    engine1 = PaperTradingEngine(test_config)
    initial_run_id = engine1.run_id

    # Place trade and set pending exit
    eval_time = datetime(2026, 10, 21, 9, 30, 0, tzinfo=TZ_IST)
    mock_quote = Level2Quote(
        scrip_code=51101,
        symbol="NIFTY 27 OCT 2026 CE 24500.00",
        best_bid=150.0,
        best_ask=150.5,
        bid_qty=500,
        ask_qty=500,
        last_price=150.5,
        provider_timestamp=eval_time,
        ingestion_timestamp=eval_time,
    )
    signal = Signal(
        symbol="NIFTY",
        strategy_name="ORB",
        method_id="M1",
        direction=SignalDirection.BUY,
        timestamp=eval_time,
        reference_price=24500.0,
        stop_loss=24450.0,
        target=24600.0,
        reason="ORB_PERSIST_TEST",
    )
    engine1.execute_signal(signal, 24500.0, eval_time, mock_quote=mock_quote)
    engine1.pending_exit_reason = "STOP_LOSS"
    engine1.save_state()

    # Re-initialize engine from the same state file (simulating crash / restart)
    engine2 = PaperTradingEngine(test_config)
    assert engine2.run_id == initial_run_id
    assert engine2.recorder.run_id == initial_run_id
    assert engine2.portfolio.active_position is not None
    assert engine2.portfolio.active_position.symbol == "NIFTY 27 OCT 2026 CE 24500.00"
    assert engine2.pending_exit_reason == "STOP_LOSS"
    assert len(engine2.executed_signal_keys) == 1

    # Attempting to execute the exact same signal again is rejected as duplicate!
    duplicate_executed = engine2.execute_signal(
        signal=signal,
        spot_price=24500.0,
        candle_time=eval_time,
        mock_quote=mock_quote,
    )
    assert duplicate_executed is False


# -------------------------------------------------------------------------
# 9. Broker Isolation Safety Gate
# -------------------------------------------------------------------------
def test_broker_order_prohibition_safety():
    """Verify live broker orders cannot be executed under any circumstance."""
    from trading_platform.brokers.fivepaisa import FivePaisaBroker

    broker = FivePaisaBroker()
    with pytest.raises(LiveTradingPermissionError):
        broker.assert_live_allowed()
