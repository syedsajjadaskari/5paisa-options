"""Unit tests for OptionExecutionSimulator, fill models, and audit logs."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from trading_platform.backtesting.option_execution import OptionExecutionSimulator
from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Signal
from trading_platform.data.option_chain import OptionBar, OptionContractMetadata
from trading_platform.options.premium_exits import ExitDecision

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


def test_simulate_entry_next_bar_open_fill():
    sim = OptionExecutionSimulator(slippage_points=0.50)
    sig_time = datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST)
    signal = Signal(
        strategy_name="IRF",
        method_id="TEST_IRF",
        reason="Test IRF Signal",
        symbol="NIFTY 50",
        direction=SignalDirection.BUY,
        timestamp=sig_time,
        reference_price=24800.0,
        stop_loss=24770.0,
        target=24860.0,
    )
    contract = OptionContractMetadata(
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
    )

    # Next bar (09:25 IST)
    next_bar_time = datetime(2024, 10, 18, 9, 25, tzinfo=TZ_IST)
    next_option_bar = OptionBar(
        timestamp=next_bar_time.astimezone(TZ_UTC),
        exchange_time=next_bar_time,
        trading_date=next_bar_time.date(),
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
        open=120.0,
        high=125.0,
        low=118.0,
        close=122.0,
        volume=5000,
    )

    fill_price, size_res, reason = sim.simulate_entry(signal, contract, next_option_bar)
    assert reason == "APPROVED"
    assert fill_price == 120.0 + 0.50  # Open + slippage
    assert size_res.is_approved is True
    assert size_res.quantity > 0
    assert len(sim.audit_logs) == 1
    assert sim.audit_logs[0].event_type == "ENTRY_FILL"


def test_simulate_entry_anti_lookahead_timing_rejection():
    """Verify order rejected if option bar time is before signal confirmation."""
    sim = OptionExecutionSimulator()
    sig_time = datetime(2024, 10, 18, 9, 25, tzinfo=TZ_IST)
    signal = Signal(
        strategy_name="ORB",
        method_id="TEST_ORB",
        reason="Test ORB Signal",
        symbol="NIFTY 50",
        direction=SignalDirection.BUY,
        timestamp=sig_time,
        reference_price=24800.0,
        stop_loss=24770.0,
        target=24860.0,
    )
    contract = OptionContractMetadata(
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
    )
    # Stale/Lookahead bar from 09:20 (earlier than signal time 09:25)
    stale_time = datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST)
    stale_bar = OptionBar(
        timestamp=stale_time.astimezone(TZ_UTC),
        exchange_time=stale_time,
        trading_date=stale_time.date(),
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
        open=120.0,
        high=125.0,
        low=118.0,
        close=122.0,
        volume=5000,
    )
    fill_price, size_res, reason = sim.simulate_entry(signal, contract, stale_bar)
    assert fill_price is None
    assert size_res.is_approved is False
    assert "REJECTED_LOOKAHEAD_VIOLATION" in reason


def test_no_artificial_capping_when_slippage_exceeds_high():
    """Verify buy fill price is NEVER capped down to bar High."""
    sim = OptionExecutionSimulator(slippage_points=5.0)  # Large 5.0 pt slippage
    sig_time = datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST)
    signal = Signal(
        strategy_name="ORB",
        method_id="TEST_ORB",
        reason="Test ORB Signal",
        symbol="NIFTY 50",
        direction=SignalDirection.BUY,
        timestamp=sig_time,
        reference_price=24800.0,
        stop_loss=24770.0,
        target=24860.0,
    )
    contract = OptionContractMetadata(
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
    )
    next_time = datetime(2024, 10, 18, 9, 25, tzinfo=TZ_IST)
    # Candle: Open 100.0, High 102.0, Low 99.0, Close 101.0
    # Open + slippage = 105.0 (> High 102.0)
    bar = OptionBar(
        timestamp=next_time.astimezone(TZ_UTC),
        exchange_time=next_time,
        trading_date=next_time.date(),
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=5000,
    )
    fill_price, size_res, reason = sim.simulate_entry(signal, contract, bar)
    assert reason == "APPROVED"
    # Never capped at 102.0 (which would artificially improve profitability). Fills strictly at 105.0
    assert fill_price == 105.0


def test_inverted_quote_stale_fallback_to_ohlc():
    """Verify inverted book (Bid > Ask) logs warning and falls back to conservative OHLC."""
    sim = OptionExecutionSimulator(slippage_points=0.50)
    sig_time = datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST)
    signal = Signal(
        strategy_name="ORB",
        method_id="TEST_ORB",
        reason="Test ORB Signal",
        symbol="NIFTY 50",
        direction=SignalDirection.BUY,
        timestamp=sig_time,
        reference_price=24800.0,
        stop_loss=24770.0,
        target=24860.0,
    )
    contract = OptionContractMetadata(
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
    )
    next_time = datetime(2024, 10, 18, 9, 25, tzinfo=TZ_IST)
    # Erroneous book: Bid 125.0 > Ask 120.0
    bar = OptionBar(
        timestamp=next_time.astimezone(TZ_UTC),
        exchange_time=next_time,
        trading_date=next_time.date(),
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
        open=119.0,
        high=126.0,
        low=118.0,
        close=121.0,
        volume=5000,
        bid=125.0,
        ask=120.0,
    )
    fill_price, size_res, reason = sim.simulate_entry(signal, contract, bar)
    assert reason == "APPROVED"
    # Falls back to Open + slippage = 119.0 + 0.50 = 119.50
    assert fill_price == 119.50
    assert any(log.event_type == "STALE_QUOTE_FALLBACK" for log in sim.audit_logs)


def test_simulate_entry_missing_price_rejection():
    sim = OptionExecutionSimulator()
    signal = Signal(
        strategy_name="IRF",
        method_id="TEST_IRF",
        reason="Test IRF Signal",
        symbol="NIFTY 50",
        direction=SignalDirection.BUY,
        timestamp=datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST),
        reference_price=24800.0,
        stop_loss=24770.0,
        target=24860.0,
    )
    contract = OptionContractMetadata(
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
    )

    fill_price, size_res, reason = sim.simulate_entry(signal, contract, next_option_bar=None)
    assert fill_price is None
    assert size_res.is_approved is False
    assert "REJECTED_MISSING_PRICE" in reason


def test_simulate_exit_lifecycle_and_costs():
    sim = OptionExecutionSimulator(slippage_points=0.50)
    contract = OptionContractMetadata(
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
    )
    entry_time = datetime(2024, 10, 18, 9, 25, tzinfo=TZ_IST)
    exit_time = datetime(2024, 10, 18, 10, 0, tzinfo=TZ_IST)

    exit_bar = OptionBar(
        timestamp=exit_time.astimezone(TZ_UTC),
        exchange_time=exit_time,
        trading_date=exit_time.date(),
        underlying_symbol="NIFTY",
        expiry_date=date(2024, 10, 24),
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
        open=155.0,
        high=162.0,
        low=152.0,
        close=160.0,
        volume=4000,
    )

    decision = ExitDecision(should_exit=True, exit_price=160.0, exit_reason="UNDERLYING_TARGET")
    result = sim.simulate_exit(
        exit_decision=decision,
        current_option_bar=exit_bar,
        entry_price=120.50,
        quantity=50,
        lots=2,
        contract=contract,
        entry_time=entry_time,
        trade_id="TRD_001",
    )

    assert result.gross_pnl > 0
    assert result.net_pnl < result.gross_pnl  # Costs deducted
    assert result.costs.total_transaction_costs > 0
    assert result.exit_reason == "UNDERLYING_TARGET"
    assert len(sim.audit_logs) == 1
    assert sim.audit_logs[0].event_type == "EXIT_FILL"
