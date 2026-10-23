"""Generate deterministic BASELINE_V1 versus CORRECTED_V2 trade reconciliation."""

from __future__ import annotations

import csv
import hashlib
import tempfile
from collections import Counter
from pathlib import Path

import pandas as pd

from trading_platform.backtesting.arbitration import PortfolioArbitrationPolicy, PortfolioArbitrator
from trading_platform.backtesting.reconciliation_audit import HistoricalReconciliationAuditor


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    baseline_path = repo / "reports/phase2d/backtest_trades.parquet"
    output_dir = repo / "reports/independent_codex_audit/remediation"
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline = pd.read_parquet(baseline_path)
    with tempfile.TemporaryDirectory() as tmp:
        auditor = HistoricalReconciliationAuditor(repo, slippage_points=0.50)
        auditor.reports_dir = Path(tmp)
        auditor.load_data()
        auditor.run_backtests()
        # RF is excluded from CORRECTED_V2 because the PDF leaves its exit open.
        corrected = [t for t in auditor.all_automated_trades if t.strategy_name != "RF"]

    def baseline_key(row: pd.Series) -> tuple[object, ...]:
        return (
            row.strategy_name,
            row.date,
            row.entry_time.to_pydatetime(),
            row.direction,
            row.method,
        )

    def corrected_key(trade: object) -> tuple[object, ...]:
        return (
            trade.strategy_name,
            trade.date,
            trade.entry_time,
            trade.direction.value,
            trade.method,
        )

    base_map = {baseline_key(row): row for _, row in baseline.iterrows()}
    corrected_map = {corrected_key(trade): trade for trade in corrected}
    common = base_map.keys() & corrected_map.keys()
    removed = base_map.keys() - corrected_map.keys()
    added = corrected_map.keys() - base_map.keys()

    detail_rows: list[dict[str, object]] = []
    for key in sorted(common, key=str):
        old = base_map[key]
        new = corrected_map[key]
        changes: list[str] = []
        if round(float(old.target), 2) != round(new.target, 2) or round(float(old.stop_loss), 2) != round(new.stop_loss, 2):
            changes.append("TARGET_STOP_CHANGE")
        if old.exit_time.to_pydatetime() != new.exit_time:
            changes.append("EXIT_CANDLE_CHANGE")
        if round(float(old.exit_price), 2) != round(new.exit_price, 2):
            changes.append("EXIT_PRICE_CHANGE")
        if round(float(old.points_pnl), 2) != round(new.points_pnl, 2):
            changes.append("PNL_CHANGE")
        detail_rows.append(
            {
                "match_key": "|".join(map(str, key)),
                "status": "PRESENT_BOTH",
                "baseline_trade_id": old.trade_id,
                "corrected_trade_id": new.trade_id,
                "strategy": new.strategy_name,
                "change_causes": ";".join(changes) or "UNCHANGED",
                "baseline_points": old.points_pnl,
                "corrected_points": new.points_pnl,
            }
        )
    for key in sorted(removed, key=str):
        old = base_map[key]
        detail_rows.append(
            {
                "match_key": "|".join(map(str, key)),
                "status": "REMOVED_FROM_CORRECTED",
                "baseline_trade_id": old.trade_id,
                "corrected_trade_id": "",
                "strategy": old.strategy_name,
                "change_causes": "NO_CORRECTED_SIGNAL",
                "baseline_points": old.points_pnl,
                "corrected_points": "",
            }
        )
    for key in sorted(added, key=str):
        new = corrected_map[key]
        detail_rows.append(
            {
                "match_key": "|".join(map(str, key)),
                "status": "ADDED_IN_CORRECTED",
                "baseline_trade_id": "",
                "corrected_trade_id": new.trade_id,
                "strategy": new.strategy_name,
                "change_causes": "NEW_CORRECTED_SIGNAL",
                "baseline_points": "",
                "corrected_points": new.points_pnl,
            }
        )

    detail_path = output_dir / "BACKTEST_TRADE_RECONCILIATION.csv"
    with detail_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(detail_rows[0]))
        writer.writeheader()
        writer.writerows(detail_rows)

    def metrics(name: str, trades: list[object]) -> dict[str, object]:
        wins = [t.points_pnl for t in trades if t.points_pnl > 0]
        losses = [t.points_pnl for t in trades if t.points_pnl <= 0]
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for trade in sorted(trades, key=lambda t: (t.entry_time, t.strategy_name, t.method, t.trade_id)):
            equity += trade.points_pnl
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        fifo = PortfolioArbitrator(PortfolioArbitrationPolicy.FIFO_SINGLE_POSITION).arbitrate(trades)
        return {
            "version": name,
            "scope": "UNARBITRATED",
            "trade_count": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "net_points": round(sum(t.points_pnl for t in trades), 2),
            "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else "NA",
            "max_drawdown": round(max_dd, 2),
            "fifo_trade_count": len(fifo.accepted_trades),
            "fifo_net_points": round(fifo.net_index_points, 2),
            "fifo_profit_factor": round(fifo.profit_factor, 4),
            "fifo_max_drawdown": round(fifo.max_drawdown_points, 2),
            "data_hash": file_hash(baseline_path) if name == "BASELINE_V1" else "CORRECTED_FROM_CURRENT_ENGINE_AND_BASELINE_DATA",
        }

    baseline_records = []
    from trading_platform.backtesting.engine import UnderlyingTradeRecord
    from trading_platform.core.enums import SignalDirection

    for _, row in baseline.iterrows():
        baseline_records.append(
            UnderlyingTradeRecord(
                trade_id=row.trade_id,
                date=row.date.date() if hasattr(row.date, "date") else row.date,
                strategy_name=row.strategy_name,
                direction=SignalDirection(row.direction),
                method=row.method,
                entry_time=row.entry_time.to_pydatetime(),
                entry_price=float(row.entry_price),
                exit_time=row.exit_time.to_pydatetime(),
                exit_price=float(row.exit_price),
                exit_reason=row.exit_reason,
                points_pnl=float(row.points_pnl),
                is_win=bool(row.is_win),
                target=float(row.target),
                stop_loss=float(row.stop_loss),
                is_uncertain_fill=bool(row.is_uncertain_fill),
            )
        )

    rows = [metrics("BASELINE_V1", baseline_records), metrics("CORRECTED_V2", corrected)]
    cause_counts = Counter()
    for row in detail_rows:
        for cause in str(row["change_causes"]).split(";"):
            cause_counts[cause] += 1
    for cause, count in sorted(cause_counts.items()):
        rows.append(
            {
                "version": "CHANGE_ATTRIBUTION",
                "scope": cause,
                "trade_count": count,
                "wins": "",
                "losses": "",
                "net_points": "",
                "profit_factor": "",
                "max_drawdown": "",
                "fifo_trade_count": "",
                "fifo_net_points": "",
                "fifo_profit_factor": "",
                "fifo_max_drawdown": "",
                "data_hash": "See BACKTEST_TRADE_RECONCILIATION.csv",
            }
        )

    comparison_path = output_dir / "BACKTEST_VERSION_COMPARISON.csv"
    with comparison_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
