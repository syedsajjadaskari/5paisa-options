"""Manual vs Automated Trade Reconciliation Framework.

Reconciles manually journaled trades against automated engine trade signals,
identifying timing deltas, price deltas, missed trades, false positives,
and structural discrepancies.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from trading_platform.backtesting.engine import UnderlyingTradeRecord
from trading_platform.core.enums import SignalDirection


@dataclass(frozen=True)
class ManualTradeRecord:
    """Manual reference trade journal entry from discretionary or human execution."""

    trade_id: str
    date: date
    strategy_name: str
    method: str
    direction: SignalDirection
    entry_time: datetime
    entry_price: float
    stop_loss: float
    target: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    points_pnl: float | None = None
    notes: str = ""


@dataclass(frozen=True)
class TradeMatch:
    """Pair of matched manual and automated trade executions."""

    manual_trade: ManualTradeRecord
    automated_trade: UnderlyingTradeRecord
    time_delta_minutes: float
    entry_price_delta: float
    exit_price_delta: float | None = None
    direction_matches: bool = True
    notes: str = ""


@dataclass(frozen=True)
class TradeDiscrepancy:
    """Discrepancy identified between manual journal and automated backtest."""

    discrepancy_type: str  # "MISSED_MANUAL_TRADE", "EXTRA_AUTOMATED_TRADE", "PRICE_MISMATCH", "TIMING_MISMATCH", "EXIT_MISMATCH"
    reason: str
    manual_trade: ManualTradeRecord | None = None
    automated_trade: UnderlyingTradeRecord | None = None


@dataclass
class ReconciliationReport:
    """Comprehensive reconciliation output between manual journal and automated trades."""

    status: str  # "COMPLETED", "NO_MANUAL_JOURNAL_PROVIDED", "EMPTY_JOURNAL"
    total_manual_trades: int
    total_automated_trades: int
    matched_trades_count: int
    missed_manual_trades_count: int
    extra_automated_trades_count: int
    discrepancies: list[TradeDiscrepancy] = field(default_factory=list)
    matches: list[TradeMatch] = field(default_factory=list)
    match_rate_pct: float = 0.0
    summary: str = ""

    def to_markdown(self) -> str:
        """Format reconciliation report into a human-readable GitHub-style markdown table."""
        lines = [
            "# Trade Reconciliation Report",
            f"**Status:** `{self.status}`",
            f"**Summary:** {self.summary}",
            "",
            "## Summary Metrics",
            f"- **Total Manual Trades:** {self.total_manual_trades}",
            f"- **Total Automated Trades:** {self.total_automated_trades}",
            f"- **Matched Trades:** {self.matched_trades_count}",
            f"- **Missed Manual Trades (False Negatives):** {self.missed_manual_trades_count}",
            f"- **Extra Automated Trades (False Positives):** {self.extra_automated_trades_count}",
            f"- **Match Rate:** {self.match_rate_pct:.1f}%",
            "",
        ]

        if self.matches:
            lines.extend(
                [
                    "## Matched Trades",
                    "| Trade ID | Strategy | Direction | Man Entry | Auto Entry | Time Δ (min) | Price Δ (pts) | Man PnL | Auto PnL |",
                    "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
                ]
            )
            for m in self.matches:
                man = m.manual_trade
                auto = m.automated_trade
                man_pnl_str = f"{man.points_pnl:+.1f}" if man.points_pnl is not None else "N/A"
                auto_pnl_str = f"{auto.points_pnl:+.1f}"
                lines.append(
                    f"| {man.trade_id} / {auto.trade_id} | {auto.strategy_name} | {auto.direction.value} | "
                    f"{man.entry_time.strftime('%H:%M')} | {auto.entry_time.strftime('%H:%M')} | "
                    f"{m.time_delta_minutes:+.1f} | {m.entry_price_delta:+.2f} | {man_pnl_str} | {auto_pnl_str} |"
                )
            lines.append("")

        if self.discrepancies:
            lines.extend(
                [
                    "## Discrepancies & Disagreements",
                    "| Type | Reference Trade | Reason |",
                    "| :--- | :--- | :--- |",
                ]
            )
            for d in self.discrepancies:
                ref_id = "N/A"
                if d.manual_trade:
                    ref_id = f"Manual {d.manual_trade.trade_id} ({d.manual_trade.strategy_name})"
                elif d.automated_trade:
                    ref_id = (
                        f"Auto {d.automated_trade.trade_id} ({d.automated_trade.strategy_name})"
                    )
                lines.append(f"| `{d.discrepancy_type}` | {ref_id} | {d.reason} |")
            lines.append("")

        return "\n".join(lines)


class TradeReconciler:
    """Reconciles manual trade logs against automated engine signals and executions."""

    @staticmethod
    def load_manual_journal_csv(path: Path | str) -> list[ManualTradeRecord]:
        """Parse manual trade journal CSV file into a list of ManualTradeRecord.

        Expected CSV columns:
        trade_id, date, strategy_name, method, direction, entry_time, entry_price,
        stop_loss, target, [exit_time], [exit_price], [exit_reason], [points_pnl], [notes]
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Manual trade journal not found at {path}")

        records: list[ManualTradeRecord] = []
        with open(p, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trade_date = date.fromisoformat(row["date"].strip())

                # Parse direction
                dir_str = row["direction"].strip().upper()
                direction = (
                    SignalDirection.BUY if dir_str in ("BUY", "LONG") else SignalDirection.SELL
                )

                # Parse entry time
                entry_str = row["entry_time"].strip()
                if "T" in entry_str or " " in entry_str:
                    entry_time = datetime.fromisoformat(entry_str)
                else:
                    # HH:MM or HH:MM:SS format
                    parts = [int(x) for x in entry_str.split(":")]
                    entry_time = datetime(
                        trade_date.year,
                        trade_date.month,
                        trade_date.day,
                        parts[0],
                        parts[1],
                        parts[2] if len(parts) > 2 else 0,
                    )

                exit_time = None
                exit_str = row.get("exit_time", "").strip()
                if exit_str:
                    if "T" in exit_str or " " in exit_str:
                        exit_time = datetime.fromisoformat(exit_str)
                    else:
                        parts = [int(x) for x in exit_str.split(":")]
                        exit_time = datetime(
                            trade_date.year,
                            trade_date.month,
                            trade_date.day,
                            parts[0],
                            parts[1],
                            parts[2] if len(parts) > 2 else 0,
                        )

                exit_price = float(row["exit_price"]) if row.get("exit_price", "").strip() else None
                points_pnl = float(row["points_pnl"]) if row.get("points_pnl", "").strip() else None

                record = ManualTradeRecord(
                    trade_id=row.get("trade_id", f"MAN_{len(records) + 1}").strip(),
                    date=trade_date,
                    strategy_name=row["strategy_name"].strip(),
                    method=row.get("method", "Method1").strip(),
                    direction=direction,
                    entry_time=entry_time,
                    entry_price=float(row["entry_price"]),
                    stop_loss=float(row["stop_loss"]),
                    target=float(row["target"]),
                    exit_time=exit_time,
                    exit_price=exit_price,
                    exit_reason=row.get("exit_reason", "").strip() or None,
                    points_pnl=points_pnl,
                    notes=row.get("notes", "").strip(),
                )
                records.append(record)

        return records

    def reconcile(
        self,
        automated_trades: list[UnderlyingTradeRecord],
        manual_trades: list[ManualTradeRecord] | None = None,
        time_tolerance_minutes: float = 15.0,
        price_tolerance_points: float = 25.0,
    ) -> ReconciliationReport:
        """Compare automated engine trades against manual reference journal.

        If manual_trades is None, safely returns NO_MANUAL_JOURNAL_PROVIDED report.
        """
        if manual_trades is None:
            return ReconciliationReport(
                status="NO_MANUAL_JOURNAL_PROVIDED",
                total_manual_trades=0,
                total_automated_trades=len(automated_trades),
                matched_trades_count=0,
                missed_manual_trades_count=0,
                extra_automated_trades_count=len(automated_trades),
                discrepancies=[
                    TradeDiscrepancy(
                        discrepancy_type="NO_MANUAL_JOURNAL_PROVIDED",
                        reason=(
                            "No manual reference journal was provided for reconciliation. "
                            f"Automated backtest engine emitted {len(automated_trades)} trades. "
                            "Discretionary trade validation cannot be asserted without manual journal data."
                        ),
                    )
                ],
                summary="Reconciliation bypassed: manual reference trade journal was not provided.",
            )

        if not manual_trades:
            return ReconciliationReport(
                status="EMPTY_JOURNAL",
                total_manual_trades=0,
                total_automated_trades=len(automated_trades),
                matched_trades_count=0,
                missed_manual_trades_count=0,
                extra_automated_trades_count=len(automated_trades),
                summary="Manual trade journal is empty. No trades to reconcile.",
            )

        matched_manual_indices: set[int] = set()
        matched_auto_indices: set[int] = set()
        matches: list[TradeMatch] = []
        discrepancies: list[TradeDiscrepancy] = []

        # Find best pairs
        for a_idx, auto in enumerate(automated_trades):
            best_m_idx = None
            best_time_diff = float("inf")

            for m_idx, man in enumerate(manual_trades):
                if m_idx in matched_manual_indices:
                    continue

                # Check date and strategy/direction compatibility
                if man.date != auto.date:
                    continue

                if man.direction != auto.direction:
                    continue

                # Check time delta
                dt = abs((auto.entry_time - man.entry_time).total_seconds()) / 60.0
                if dt <= time_tolerance_minutes and dt < best_time_diff:
                    best_time_diff = dt
                    best_m_idx = m_idx

            if best_m_idx is not None:
                man = manual_trades[best_m_idx]
                matched_manual_indices.add(best_m_idx)
                matched_auto_indices.add(a_idx)

                price_delta = auto.entry_price - man.entry_price
                exit_price_delta = (
                    auto.exit_price - man.exit_price
                    if (man.exit_price is not None and auto.exit_price is not None)
                    else None
                )

                notes = []
                if abs(price_delta) > price_tolerance_points:
                    notes.append(
                        f"Price delta {price_delta:+.2f} exceeds tolerance {price_tolerance_points} pts"
                    )
                    discrepancies.append(
                        TradeDiscrepancy(
                            discrepancy_type="PRICE_MISMATCH",
                            reason=f"Entry price delta {price_delta:+.2f} exceeds tolerance {price_tolerance_points} pts (Auto: {auto.entry_price:.2f}, Man: {man.entry_price:.2f})",
                            manual_trade=man,
                            automated_trade=auto,
                        )
                    )

                if (
                    man.exit_reason
                    and auto.exit_reason
                    and man.exit_reason.upper() != auto.exit_reason.upper()
                ):
                    discrepancies.append(
                        TradeDiscrepancy(
                            discrepancy_type="EXIT_MISMATCH",
                            reason=f"Exit reason disagreement: Auto={auto.exit_reason}, Manual={man.exit_reason}",
                            manual_trade=man,
                            automated_trade=auto,
                        )
                    )

                matches.append(
                    TradeMatch(
                        manual_trade=man,
                        automated_trade=auto,
                        time_delta_minutes=best_time_diff,
                        entry_price_delta=price_delta,
                        exit_price_delta=exit_price_delta,
                        direction_matches=True,
                        notes="; ".join(notes) if notes else "Full match within tolerances",
                    )
                )

        # Unmatched manual trades -> False Negatives / Missed trades
        for m_idx, man in enumerate(manual_trades):
            if m_idx not in matched_manual_indices:
                discrepancies.append(
                    TradeDiscrepancy(
                        discrepancy_type="MISSED_MANUAL_TRADE",
                        reason=(
                            f"Manual trade {man.trade_id} on {man.date} at {man.entry_time.strftime('%H:%M')} "
                            f"({man.direction.value} {man.strategy_name}) was not detected by automated engine."
                        ),
                        manual_trade=man,
                    )
                )

        # Unmatched automated trades -> False Positives / Extra trades
        for a_idx, auto in enumerate(automated_trades):
            if a_idx not in matched_auto_indices:
                discrepancies.append(
                    TradeDiscrepancy(
                        discrepancy_type="EXTRA_AUTOMATED_TRADE",
                        reason=(
                            f"Automated trade {auto.trade_id} on {auto.date} at {auto.entry_time.strftime('%H:%M')} "
                            f"({auto.direction.value} {auto.strategy_name}) has no corresponding manual trade in journal."
                        ),
                        automated_trade=auto,
                    )
                )

        total_manual = len(manual_trades)
        matched_count = len(matches)
        match_rate = (matched_count / total_manual * 100.0) if total_manual > 0 else 0.0

        summary = (
            f"Reconciliation completed: {matched_count}/{total_manual} manual trades matched "
            f"({match_rate:.1f}%). {len(manual_trades) - matched_count} missed, "
            f"{len(automated_trades) - matched_count} extra automated trades."
        )

        return ReconciliationReport(
            status="COMPLETED",
            total_manual_trades=total_manual,
            total_automated_trades=len(automated_trades),
            matched_trades_count=matched_count,
            missed_manual_trades_count=total_manual - matched_count,
            extra_automated_trades_count=len(automated_trades) - matched_count,
            discrepancies=discrepancies,
            matches=matches,
            match_rate_pct=match_rate,
            summary=summary,
        )
