"""Unit tests for StrategyVisualizer and Plotly chart reporting."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from trading_platform.backtesting.engine import (
    UnderlyingBacktestReport,
    UnderlyingTradeRecord,
)
from trading_platform.backtesting.reporting import StrategyVisualizer
from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar


def test_visualizer_empty_bars() -> None:
    """StrategyVisualizer safely handles empty bar list."""
    viz = StrategyVisualizer()
    fig = viz.build_chart(bars=[])
    assert fig is not None
    assert "No Bar Data Available" in fig.layout.title.text


def test_visualizer_chart_generation_and_export(tmp_path: Path) -> None:
    """StrategyVisualizer correctly builds candle chart with reference levels and trades."""
    base_time = datetime(2024, 10, 18, 9, 15)
    bars = []
    price = 24800.0
    for i in range(15):
        t = base_time + timedelta(minutes=5 * i)
        o = price
        h = price + 15.0
        low_val = price - 10.0
        c = price + 5.0
        bars.append(
            Bar(
                symbol="NIFTY",
                instrument_id="NIFTY50",
                timestamp=t,
                exchange_time=t,
                open=o,
                high=h,
                low=low_val,
                close=c,
                volume=10000,
            )
        )
        price = c

    report = UnderlyingBacktestReport(
        strategy_name="IRF",
        total_trades=1,
        winning_trades=1,
        losing_trades=0,
        win_rate_pct=100.0,
        total_points=35.0,
        profit_factor=float("inf"),
        max_drawdown_points=0.0,
        avg_points_per_trade=35.0,
        trades=[
            UnderlyingTradeRecord(
                trade_id="T1",
                date=date(2024, 10, 18),
                strategy_name="IRF",
                direction=SignalDirection.BUY,
                method="Method1_BreakoutFailure",
                entry_time=base_time + timedelta(minutes=20),
                entry_price=24820.0,
                exit_time=base_time + timedelta(minutes=45),
                exit_price=24855.0,
                exit_reason="TARGET_HIT",
                points_pnl=35.0,
                is_win=True,
                target=24855.0,
                stop_loss=24785.0,
            )
        ],
    )

    viz = StrategyVisualizer()
    fig = viz.build_chart(bars=bars, report=report)
    assert fig is not None
    assert len(fig.data) > 0  # Candlestick + Volume + levels + markers

    out_file = tmp_path / "chart.html"
    exported = viz.export_html(fig, out_file)
    assert exported.exists()
    assert exported.stat().st_size > 500  # Valid HTML output

    # Check convenience method
    report_chart = viz.generate_strategy_report_chart(
        bars=bars, report=report, output_path=tmp_path / "report_chart.html"
    )
    assert report_chart.exists()
