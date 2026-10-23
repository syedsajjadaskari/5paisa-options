"""Integration tests for Phase 2C Underlying and Options Backtest Engines across all 5 strategies."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from trading_platform.backtesting.engine import (
    OptionsBacktestEngine,
    UnderlyingBacktestEngine,
)
from trading_platform.core.enums import (
    DataSource,
    Exchange,
    InstrumentType,
    SignalDirection,
    Timeframe,
)
from trading_platform.core.models import Bar, Signal
from trading_platform.data.contract_archive import ContractArchive
from trading_platform.data.option_chain import OptionBar, OptionContractMetadata
from trading_platform.strategies.irf import IRFStrategy
from trading_platform.strategies.orb import ORBStrategy
from trading_platform.strategies.registry import registry

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


def make_day_bars(
    start_dt: datetime,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> list[Bar]:
    bars = []
    for i in range(len(opens)):
        bar_time = start_dt + timedelta(minutes=5 * i)
        bars.append(
            Bar(
                timestamp=bar_time.astimezone(TZ_UTC),
                exchange_time=bar_time,
                symbol="NIFTY 50",
                instrument_id="999920000",
                exchange=Exchange.NSE,
                instrument_type=InstrumentType.INDEX,
                interval=Timeframe.M5,
                open=opens[i],
                high=highs[i],
                low=lows[i],
                close=closes[i],
                volume=10000,
                source=DataSource.SYNTHETIC,
            )
        )
    return bars


def test_strategy_registry_contains_all_5_strategies():
    """Verify registry has IRF, ORB, PDR, PDS, and RF registered."""
    strategies = registry.list_strategies()
    assert "IRF" in strategies
    assert "ORB" in strategies
    assert "PDR" in strategies
    assert "PDS" in strategies
    assert "RF" in strategies


def test_orb_underlying_backtest_lifecycle():
    """Verify ORB strategy generates signal on 09:20 bar, enters on 09:25 open, and hits target."""
    engine = UnderlyingBacktestEngine(slippage_points=0.50)
    strategy = ORBStrategy()

    # Day 1: 09:15 to 09:40
    start_dt = datetime(2024, 10, 18, 9, 15, tzinfo=TZ_IST)
    # Candle 0 (09:15-09:20): Breakout above PDH 24800 -> Closes 24820
    # Candle 1 (09:20-09:25): Entry bar (Open 24820)
    # Candle 2 (09:25-09:30): Rallies towards target (24820 + 60 = 24880) -> High 24885
    opens = [24800.0, 24820.0, 24840.0]
    highs = [24825.0, 24840.0, 24885.0]
    lows = [24795.0, 24815.0, 24835.0]
    closes = [24820.0, 24835.0, 24880.0]

    bars = make_day_bars(start_dt, opens, highs, lows, closes)
    day_states = {
        "2024-10-18": {"pdh": 24800.0, "pdl": 24700.0, "ema_200": 24750.0}
    }

    report = engine.run(strategy, bars, day_context_states=day_states)

    assert report.total_trades == 1
    assert report.winning_trades == 1
    assert report.trades[0].method == "ORB_METHOD_1_PDH_BREAKOUT"
    assert report.trades[0].exit_reason == "TARGET"
    assert report.trades[0].points_pnl > 0


def test_irf_underlying_backtest_lifecycle():
    """Verify IRF Method 1: C1 closes > IRH, C2 closes < IRH -> Short signal -> Hit target."""
    engine = UnderlyingBacktestEngine(slippage_points=0.50)
    strategy = IRFStrategy()

    start_dt = datetime(2024, 10, 18, 9, 15, tzinfo=TZ_IST)
    # Candle 0 (09:15): IR Candle: High 24820, Low 24780
    # Candle 1 (09:20): C1 closes > IRH (High 24835, Close 24830)
    # Candle 2 (09:25): C2 closes < IRH (Close 24810 < 24820) -> Short Signal emitted
    # Candle 3 (09:30): Entry on Open 24810. Drops to Target (24810 - 60 = 24750) -> Low 24740
    opens = [24790.0, 24820.0, 24830.0, 24810.0]
    highs = [24820.0, 24835.0, 24832.0, 24812.0]
    lows = [24780.0, 24815.0, 24805.0, 24740.0]
    closes = [24810.0, 24830.0, 24810.0, 24745.0]

    bars = make_day_bars(start_dt, opens, highs, lows, closes)
    report = engine.run(strategy, bars)

    assert report.total_trades == 1
    assert report.winning_trades == 1
    assert report.trades[0].direction == SignalDirection.SELL
    assert report.trades[0].method == "IRF_METHOD_1_IMMEDIATE_FAILURE"
    assert report.trades[0].exit_reason == "TARGET"


def test_options_backtest_data_availability_gate_rejection():
    """Verify OptionsBacktestEngine rejects signals when genuine historical option data is missing."""
    archive = ContractArchive()  # Empty archive
    engine = OptionsBacktestEngine(archive=archive)

    signals = [
        Signal(
            strategy_name="ORB",
            method_id="ORB_METHOD_1",
            reason="ORB Breakout",
            symbol="NIFTY 50",
            direction=SignalDirection.BUY,
            timestamp=datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST),
            reference_price=24800.0,
            stop_loss=24770.0,
            target=24860.0,
        )
    ]

    report = engine.run(
        signals=signals,
        option_bars_by_contract={},
        underlying_bars_by_time={},
    )

    # Strictly 0% coverage and zero trades executed
    assert report.data_coverage_pct == 0.0
    assert report.executable_options_trades_count == 0
    assert len(report.rejection_reasons) == 1
    assert "CONTRACT_SELECTION_FAILED" in report.rejection_reasons[0]


def test_options_backtest_execution_with_valid_option_data():
    """Verify options backtest execution when genuine historical option candles are supplied."""
    archive = ContractArchive()
    trade_date = date(2024, 10, 18)
    exp_date = date(2024, 10, 24)

    contract = OptionContractMetadata(
        underlying_symbol="NIFTY",
        expiry_date=exp_date,
        strike_price=24800.0,
        option_type="CE",
        scrip_code=45124,
        lot_size=25,
    )
    archive.add_contract(contract)

    engine = OptionsBacktestEngine(archive=archive, slippage_points=0.50)

    sig_time = datetime(2024, 10, 18, 9, 20, tzinfo=TZ_IST)
    signal = Signal(
        strategy_name="ORB",
        method_id="ORB_METHOD_1",
        reason="ORB Breakout",
        symbol="NIFTY 50",
        direction=SignalDirection.BUY,
        timestamp=sig_time,
        reference_price=24800.0,
        stop_loss=24770.0,
        target=24860.0,
    )

    t_entry = datetime(2024, 10, 18, 9, 25, tzinfo=TZ_IST)
    t_exit = datetime(2024, 10, 18, 9, 30, tzinfo=TZ_IST)

    opt_bars = [
        # This is the signal candle and must never be selected as the entry bar.
        OptionBar(
            timestamp=sig_time.astimezone(TZ_UTC),
            exchange_time=sig_time,
            trading_date=trade_date,
            underlying_symbol="NIFTY",
            expiry_date=exp_date,
            strike_price=24800.0,
            option_type="CE",
            scrip_code=45124,
            lot_size=25,
            open=80.0,
            high=90.0,
            low=78.0,
            close=85.0,
            volume=5000,
        ),
        OptionBar(
            timestamp=t_entry.astimezone(TZ_UTC),
            exchange_time=t_entry,
            trading_date=trade_date,
            underlying_symbol="NIFTY",
            expiry_date=exp_date,
            strike_price=24800.0,
            option_type="CE",
            scrip_code=45124,
            lot_size=25,
            open=120.0,
            high=130.0,
            low=118.0,
            close=125.0,
            volume=5000,
        ),
        OptionBar(
            timestamp=t_exit.astimezone(TZ_UTC),
            exchange_time=t_exit,
            trading_date=trade_date,
            underlying_symbol="NIFTY",
            expiry_date=exp_date,
            strike_price=24800.0,
            option_type="CE",
            scrip_code=45124,
            lot_size=25,
            open=150.0,
            high=185.0,
            low=148.0,
            close=180.0,
            volume=5000,
        ),
    ]

    und_bars = {
        t_entry: Bar(
            timestamp=t_entry.astimezone(TZ_UTC),
            exchange_time=t_entry,
            symbol="NIFTY 50",
            instrument_id="999920000",
            exchange=Exchange.NSE,
            instrument_type=InstrumentType.INDEX,
            interval=Timeframe.M5,
            open=24820,
            high=24835,
            low=24815,
            close=24830,
            volume=10000,
            source=DataSource.SYNTHETIC,
        ),
        t_exit: Bar(
            timestamp=t_exit.astimezone(TZ_UTC),
            exchange_time=t_exit,
            symbol="NIFTY 50",
            instrument_id="999920000",
            exchange=Exchange.NSE,
            instrument_type=InstrumentType.INDEX,
            interval=Timeframe.M5,
            open=24830,
            high=24870,  # Underlying target (24860) reached!
            low=24825,
            close=24865,
            volume=10000,
            source=DataSource.SYNTHETIC,
        ),
    }

    report = engine.run(
        signals=[signal],
        option_bars_by_contract={contract.tradingsymbol: opt_bars},
        underlying_bars_by_time=und_bars,
    )

    assert report.data_coverage_pct == 100.0
    assert report.executable_options_trades_count == 1
    trade = report.trades[0]
    assert trade.entry_time == t_entry
    assert trade.entry_premium == 120.5
    # Slippage is already embedded in entry/exit fill prices and must not be
    # charged again as a synthetic transaction-cost line item.
    assert trade.costs.slippage_cost == 0.0
    assert trade.net_pnl > 0
    assert trade.costs.total_transaction_costs > 0
    assert trade.exit_reason == "UNDERLYING_TARGET"
