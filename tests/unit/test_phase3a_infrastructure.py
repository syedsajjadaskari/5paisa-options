"""Unit tests for Phase 3A infrastructure: SessionNormalizer, PortfolioArbitrator, RobustnessTester, and WalkForwardSplitter."""

from datetime import date, datetime, time

from trading_platform.analytics.robustness import RobustnessTester
from trading_platform.backtesting.arbitration import (
    PortfolioArbitrationPolicy,
    PortfolioArbitrator,
)
from trading_platform.backtesting.engine import UnderlyingTradeRecord
from trading_platform.core.enums import DataSource
from trading_platform.core.models import Bar
from trading_platform.data.session_normalizer import (
    SessionNormalizationMode,
    SessionNormalizer,
)
from trading_platform.optimization.walk_forward import WalkForwardSplitter


def make_dummy_bar(dt: datetime, open_p: float = 24000.0, close_p: float = 24010.0) -> Bar:
    return Bar(
        timestamp=dt,
        exchange_time=dt,
        symbol="NIFTY",
        instrument_id="NIFTY_INDEX",
        open=open_p,
        high=open_p + 20.0,
        low=open_p - 10.0,
        close=close_p,
        volume=1000,
        source=DataSource.FIVEPAISA_HISTORICAL,
    )


def test_session_normalizer_filtering():
    """Verify that continuous trading mode removes 15:30 settlement bars but keeps 09:15-15:25."""
    test_date = date(2026, 1, 5)
    bars = [
        make_dummy_bar(datetime.combine(test_date, time(9, 15))),
        make_dummy_bar(datetime.combine(test_date, time(12, 0))),
        make_dummy_bar(datetime.combine(test_date, time(15, 25))),
        make_dummy_bar(datetime.combine(test_date, time(15, 30))),  # Settlement snapshot
    ]

    # Continuous trading mode
    norm_bars = SessionNormalizer.normalize_bars(
        bars, mode=SessionNormalizationMode.CONTINUOUS_TRADING
    )
    assert len(norm_bars) == 3
    assert all(b.exchange_time.time() <= time(15, 25) for b in norm_bars)

    # Raw mode
    raw_bars = SessionNormalizer.normalize_bars(bars, mode=SessionNormalizationMode.RAW)
    assert len(raw_bars) == 4

    # Audit integrity
    audit = SessionNormalizer.audit_session_integrity(
        bars, mode=SessionNormalizationMode.CONTINUOUS_TRADING
    )
    assert audit.total_raw_bars == 4
    assert audit.total_normalized_bars == 3
    assert audit.post_market_bars_removed == 1


def test_portfolio_arbitrator_fifo_capacity_one():
    """Verify FIFO single-position arbitrator rejects overlapping signals and records blocking trade."""
    trades = [
        UnderlyingTradeRecord(
            trade_id="T1",
            date=date(2026, 1, 5),
            strategy_name="ORB",
            direction="BUY",
            method="METHOD_1",
            entry_time=time(9, 20),
            entry_price=24000.0,
            exit_time=time(10, 30),
            exit_price=24060.0,
            exit_reason="TARGET_HIT",
            points_pnl=59.0,
            is_win=True,
            target=24060.0,
            stop_loss=23970.0,
        ),
        # Overlapping trade while T1 is active (starts at 09:35, T1 exits at 10:30)
        UnderlyingTradeRecord(
            trade_id="T2",
            date=date(2026, 1, 5),
            strategy_name="IRF",
            direction="SELL",
            method="METHOD_1",
            entry_time=time(9, 35),
            entry_price=24020.0,
            exit_time=time(10, 0),
            exit_price=23960.0,
            exit_reason="TARGET_HIT",
            points_pnl=59.0,
            is_win=True,
            target=23960.0,
            stop_loss=24055.0,
        ),
        # Non-overlapping trade (starts at 10:35, after T1 exits at 10:30)
        UnderlyingTradeRecord(
            trade_id="T3",
            date=date(2026, 1, 5),
            strategy_name="PDS",
            direction="SELL",
            method="METHOD_1",
            entry_time=time(10, 35),
            entry_price=24050.0,
            exit_time=time(11, 15),
            exit_price=23990.0,
            exit_reason="TARGET_HIT",
            points_pnl=59.0,
            is_win=True,
            target=23990.0,
            stop_loss=24085.0,
        ),
    ]

    arbitrator = PortfolioArbitrator(
        policy=PortfolioArbitrationPolicy.FIFO_SINGLE_POSITION, max_capacity=1
    )
    report = arbitrator.arbitrate(trades)

    assert report.total_signals_received == 3
    assert report.accepted_trades_count == 2
    assert report.rejected_signals_count == 1
    assert [t.trade_id for t in report.accepted_trades] == ["T1", "T3"]

    decisions = {d.trade_id: d for d in report.decision_records}
    assert decisions["T2"].status == "REJECTED"
    assert decisions["T2"].blocking_trade_id == "T1"
    assert "PORTFOLIO_CAPACITY_LIMIT_REACHED" in decisions["T2"].rejection_reason


def test_portfolio_arbitrator_tie_breaker():
    """Verify deterministic tie-breaker when two trades arrive at identical timestamps."""
    t_date = date(2026, 1, 5)
    trades = [
        # Strategy B arrives at 09:20
        UnderlyingTradeRecord(
            trade_id="T_PDR",
            date=t_date,
            strategy_name="PDR",
            direction="BUY",
            method="METHOD_1",
            entry_time=time(9, 20),
            entry_price=24000.0,
            exit_time=time(10, 0),
            exit_price=24060.0,
            exit_reason="TARGET_HIT",
            points_pnl=59.0,
            is_win=True,
            target=24060.0,
            stop_loss=23970.0,
        ),
        # Strategy A arrives at 09:20
        UnderlyingTradeRecord(
            trade_id="T_IRF",
            date=t_date,
            strategy_name="IRF",
            direction="SELL",
            method="METHOD_1",
            entry_time=time(9, 20),
            entry_price=24000.0,
            exit_time=time(10, 0),
            exit_price=23960.0,
            exit_reason="TARGET_HIT",
            points_pnl=59.0,
            is_win=True,
            target=23960.0,
            stop_loss=24055.0,
        ),
    ]

    arbitrator = PortfolioArbitrator(
        policy=PortfolioArbitrationPolicy.FIFO_SINGLE_POSITION, max_capacity=1
    )
    report = arbitrator.arbitrate(trades)

    # In FIFO lexicographical tie-break: IRF < PDR alphabetically, so IRF is accepted first
    assert report.accepted_trades_count == 1
    assert report.accepted_trades[0].trade_id == "T_IRF"
    assert report.decision_records[1].trade_id == "T_PDR"
    assert report.decision_records[1].status == "REJECTED"


def test_robustness_tester_bootstrap_and_monte_carlo():
    """Verify bootstrap confidence intervals and Monte Carlo drawdown metrics."""
    trades = [
        UnderlyingTradeRecord(
            trade_id=f"T_{i}",
            date=date(2026, 1, 1 + (i % 20)),
            strategy_name="PDS",
            direction="SELL",
            method="METHOD_1",
            entry_time=time(9, 30),
            entry_price=24000.0,
            exit_time=time(10, 0),
            exit_price=23940.0 if i % 2 == 0 else 24035.0,
            exit_reason="TARGET_HIT" if i % 2 == 0 else "STOP_LOSS",
            points_pnl=59.0 if i % 2 == 0 else -36.0,
            is_win=i % 2 == 0,
            target=23940.0,
            stop_loss=24035.0,
        )
        for i in range(20)
    ]

    boot_res = RobustnessTester.bootstrap_confidence_intervals(trades, n_resamples=100)
    boot_map = {b.metric_name: b for b in boot_res}
    assert "Win Rate (%)" in boot_map
    assert "Net Index Points" in boot_map
    assert boot_map["Win Rate (%)"].point_estimate == 50.0
    boot_block = RobustnessTester.session_block_bootstrap(trades, block_size=2, n_resamples=100)
    boot_block_map = {b.metric_name: b for b in boot_block}
    assert "Net Index Points" in boot_block_map
    assert (
        boot_block_map["Net Index Points"].ci_lower
        <= boot_block_map["Net Index Points"].point_estimate
        <= boot_block_map["Net Index Points"].ci_upper
    )

    mc_dd = RobustnessTester.monte_carlo_drawdown_reshuffle(trades, n_permutations=100)
    assert mc_dd.actual_max_drawdown >= 0.0
    assert mc_dd.p95_drawdown >= mc_dd.p50_drawdown
    assert mc_dd.worst_case_drawdown >= mc_dd.p95_drawdown

    slip = RobustnessTester.slippage_sensitivity_analysis(trades, steps=[0.5, 1.0, 1.5])
    assert len(slip) == 3
    assert slip[0].net_index_points > slip[1].net_index_points > slip[2].net_index_points


def test_walk_forward_splitter():
    """Verify quarterly fold splitting logic."""
    bars = []
    # Create daily bars spanning 12 months: Sep 2025 to Aug 2026
    for month in [9, 10, 11, 12]:
        bars.append(make_dummy_bar(datetime(2025, month, 15, 10, 0)))
    for month in [1, 2, 3, 4, 5, 6, 7, 8]:
        bars.append(make_dummy_bar(datetime(2026, month, 15, 10, 0)))

    folds = WalkForwardSplitter.generate_expanding_folds(bars)
    assert len(folds) == 3
    for fold in folds:
        assert fold.train_start < fold.train_end <= fold.test_start < fold.test_end
        assert (fold.test_end - fold.test_start).days >= 60
