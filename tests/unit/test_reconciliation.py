"""Unit tests for TradeReconciler and manual trade reconciliation framework."""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from trading_platform.backtesting.engine import UnderlyingTradeRecord
from trading_platform.backtesting.reconciliation import (
    TradeReconciler,
)
from trading_platform.core.enums import SignalDirection


def test_reconciliation_no_manual_journal_provided() -> None:
    """When manual_trades is None, safely return NO_MANUAL_JOURNAL_PROVIDED report."""
    auto_trades = [
        UnderlyingTradeRecord(
            trade_id="AUTO_1",
            date=date(2024, 10, 18),
            strategy_name="IRF",
            direction=SignalDirection.BUY,
            method="Method1_BreakoutFailure",
            entry_time=datetime(2024, 10, 18, 9, 35),
            entry_price=24800.0,
            exit_time=datetime(2024, 10, 18, 10, 15),
            exit_price=24850.0,
            exit_reason="TARGET_HIT",
            points_pnl=50.0,
            is_win=True,
            target=24850.0,
            stop_loss=24765.0,
        )
    ]
    reconciler = TradeReconciler()
    report = reconciler.reconcile(automated_trades=auto_trades, manual_trades=None)

    assert report.status == "NO_MANUAL_JOURNAL_PROVIDED"
    assert report.total_automated_trades == 1
    assert report.total_manual_trades == 0
    assert report.matched_trades_count == 0
    assert "No manual reference journal" in report.discrepancies[0].reason


def test_reconciliation_empty_journal() -> None:
    """When manual_trades is empty, return EMPTY_JOURNAL."""
    reconciler = TradeReconciler()
    report = reconciler.reconcile(automated_trades=[], manual_trades=[])
    assert report.status == "EMPTY_JOURNAL"
    assert report.matched_trades_count == 0


def test_reconciliation_exact_and_partial_matches(tmp_path: Path) -> None:
    """Verify matching logic, price tolerance, missed trades, and markdown generation."""
    csv_file = tmp_path / "manual_journal.csv"
    with open(csv_file, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "trade_id",
                "date",
                "strategy_name",
                "method",
                "direction",
                "entry_time",
                "entry_price",
                "stop_loss",
                "target",
                "exit_time",
                "exit_price",
                "exit_reason",
                "points_pnl",
                "notes",
            ]
        )
        writer.writerow(
            [
                "MAN_1",
                "2024-10-18",
                "IRF",
                "Method1",
                "BUY",
                "09:35",
                "24800.0",
                "24765.0",
                "24850.0",
                "10:15",
                "24850.0",
                "TARGET_HIT",
                "50.0",
                "Perfect textbook entry",
            ]
        )
        writer.writerow(
            [
                "MAN_2",
                "2024-10-18",
                "ORB",
                "Method1",
                "SELL",
                "11:00",
                "24750.0",
                "24785.0",
                "24700.0",
                "11:45",
                "24700.0",
                "TARGET_HIT",
                "50.0",
                "Discretionary short",
            ]
        )

    reconciler = TradeReconciler()
    manual_trades = reconciler.load_manual_journal_csv(csv_file)
    assert len(manual_trades) == 2
    assert manual_trades[0].trade_id == "MAN_1"
    assert manual_trades[0].direction == SignalDirection.BUY

    # Automated trades: 1 matching MAN_1, 1 extra auto trade
    auto_trades = [
        UnderlyingTradeRecord(
            trade_id="AUTO_1",
            date=date(2024, 10, 18),
            strategy_name="IRF",
            direction=SignalDirection.BUY,
            method="Method1_BreakoutFailure",
            entry_time=datetime(2024, 10, 18, 9, 36),  # 1 min delta
            entry_price=24802.0,  # 2 pt delta
            exit_time=datetime(2024, 10, 18, 10, 15),
            exit_price=24850.0,
            exit_reason="TARGET_HIT",
            points_pnl=48.0,
            is_win=True,
            target=24850.0,
            stop_loss=24765.0,
        ),
        UnderlyingTradeRecord(
            trade_id="AUTO_2",
            date=date(2024, 10, 18),
            strategy_name="PDS",
            direction=SignalDirection.SELL,
            method="Method1_GoodRedCandle",
            entry_time=datetime(2024, 10, 18, 13, 0),
            entry_price=24720.0,
            exit_time=datetime(2024, 10, 18, 13, 30),
            exit_price=24740.0,
            exit_reason="SL_HIT",
            points_pnl=-20.0,
            is_win=False,
            target=24650.0,
            stop_loss=24740.0,
        ),
    ]

    report = reconciler.reconcile(
        automated_trades=auto_trades,
        manual_trades=manual_trades,
        time_tolerance_minutes=15.0,
        price_tolerance_points=10.0,
    )

    assert report.status == "COMPLETED"
    assert report.total_manual_trades == 2
    assert report.total_automated_trades == 2
    assert report.matched_trades_count == 1
    assert report.missed_manual_trades_count == 1  # MAN_2 missed by auto
    assert report.extra_automated_trades_count == 1  # AUTO_2 not in manual
    assert report.match_rate_pct == 50.0

    md = report.to_markdown()
    assert "# Trade Reconciliation Report" in md
    assert "MAN_1 / AUTO_1" in md
    assert "MISSED_MANUAL_TRADE" in md
    assert "EXTRA_AUTOMATED_TRADE" in md
