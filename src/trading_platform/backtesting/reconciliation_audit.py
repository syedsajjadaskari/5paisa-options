"""Comprehensive Phase 2D Historical Backtest and Manual Trade Reconciliation Engine.

Executes baseline underlying backtests for all 5 NIFTY strategies (IRF, ORB, PDR, PDS, RF)
and their individual methods across historical 5m bars, performs 1-to-1 matching against
the manual trade journal, computes institutional quantitative metrics, and exports
all required CSV, Parquet, and Plotly deliverables.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trading_platform.backtesting.engine import (
    UnderlyingBacktestEngine,
    UnderlyingBacktestReport,
    UnderlyingTradeRecord,
)
from trading_platform.core.enums import (
    DataSource,
    Exchange,
    InstrumentType,
    Timeframe,
)
from trading_platform.core.models import Bar
from trading_platform.strategies.irf import IRFStrategy
from trading_platform.strategies.orb import ORBStrategy
from trading_platform.strategies.pdr import PDRStrategy
from trading_platform.strategies.pds import PDSStrategy
from trading_platform.strategies.rf import RFStrategy

logger = logging.getLogger(__name__)

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


@dataclass
class ReconciledTradeRecord:
    """Consolidated record combining manual journal and automated execution."""

    sl_no: int
    row_number: int
    date: date
    manual_entry_time: str
    strategy: str
    manual_outcome: str
    match_classification: str  # EXACT_MATCH, TIME_TOLERANCE_MATCH, MANUAL_ONLY, AMBIGUOUS_MATCH, MISSING_HISTORICAL_DATA
    tolerance_tier: str  # EXACT_0M, LE_5M, LE_10M, GT_10M, NO_AUTO_SIGNAL, NON_TRADING_DAY
    matched_auto_trade_id: str | None = None
    auto_entry_time: str | None = None
    time_delta_minutes: float | None = None
    auto_method: str | None = None
    auto_direction: str | None = None
    auto_entry_price: float | None = None
    auto_stop_loss: float | None = None
    auto_target: float | None = None
    auto_exit_time: str | None = None
    auto_exit_price: float | None = None
    auto_exit_reason: str | None = None
    auto_points_pnl: float | None = None
    auto_outcome: str | None = None
    outcome_agreement: str = (
        "N/A"  # AGREED_TARGET, AGREED_SL, BE_MANUAL, DISAGREED, UNRESOLVED_RULE, N/A
    )
    notes: str = ""


@dataclass
class QuantitativeStrategyMetrics:
    """Institutional performance metrics for strategy and method evaluation."""

    strategy_name: str
    method_name: str
    manual_trades_count: int
    automated_trades_count: int
    exact_matches_count: int
    matches_within_5m_count: int
    matches_within_10m_count: int
    total_matched_count: int
    ambiguous_matches_count: int
    manual_only_count: int
    automated_only_count: int
    missing_data_days_count: int
    match_percentage: float
    precision_pct: float
    recall_pct: float
    outcome_agreement_count: int
    outcome_agreement_pct: float
    gross_index_points: float
    slippage_points_deducted: float
    net_index_points: float
    win_rate_pct: float
    profit_factor: float
    avg_win_points: float
    avg_loss_points: float
    expectancy_points: float
    max_drawdown_points: float
    max_consecutive_losses: int
    avg_holding_time_minutes: float


class HistoricalReconciliationAuditor:
    """Orchestrates historical data loading, backtest execution, and manual journal audit."""

    def __init__(
        self,
        base_dir: Path | str = "/Users/syedaskari/project/5paisa-options",
        slippage_points: float = 0.50,
        eval_start_date: date = date(2025, 9, 1),
        eval_end_date: date = date(2026, 8, 31),
    ) -> None:
        self.base_dir = Path(base_dir)
        self.slippage_points = slippage_points
        self.eval_start_date = eval_start_date
        self.eval_end_date = eval_end_date

        self.reports_dir = self.base_dir / "reports" / "phase2d"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        self.bars_1d: list[Bar] = []
        self.bars_5m: list[Bar] = []
        self.manual_df: pd.DataFrame = pd.DataFrame()

        self.backtest_reports: dict[str, UnderlyingBacktestReport] = {}
        self.method_backtest_reports: dict[str, UnderlyingBacktestReport] = {}
        self.all_automated_trades: list[UnderlyingTradeRecord] = []
        self.reconciled_trades: list[ReconciledTradeRecord] = []

    def load_data(self) -> None:
        """Load processed 1d and 5m Parquet bars and the audited manual journal."""
        path_1d = self.base_dir / "data" / "processed" / "nifty" / "1d" / "nifty50_1d.parquet"
        path_5m = self.base_dir / "data" / "processed" / "nifty" / "5m" / "nifty50_5m.parquet"
        path_journal = self.base_dir / "reports" / "manual_journal" / "manual_trades.parquet"

        if not path_1d.exists() or not path_5m.exists():
            raise FileNotFoundError(
                "Processed NIFTY market data parquet files not found. Ingestion must run first."
            )
        if not path_journal.exists():
            raise FileNotFoundError(
                "Audited manual trade journal not found at reports/manual_journal/manual_trades.parquet."
            )

        df_1d = pd.read_parquet(path_1d)
        self.bars_1d = [
            Bar(
                timestamp=row["timestamp"]
                .to_pydatetime()
                .replace(tzinfo=TZ_IST)
                .astimezone(TZ_UTC),
                exchange_time=row["timestamp"].to_pydatetime().replace(tzinfo=TZ_IST),
                symbol="NIFTY",
                instrument_id="999920000",
                exchange=Exchange.NSE,
                instrument_type=InstrumentType.INDEX,
                interval=Timeframe.D1,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                source=DataSource.FIVEPAISA_HISTORICAL,
            )
            for _, row in df_1d.iterrows()
        ]

        df_5m = pd.read_parquet(path_5m)
        self.bars_5m = [
            Bar(
                timestamp=row["timestamp"]
                .to_pydatetime()
                .replace(tzinfo=TZ_IST)
                .astimezone(TZ_UTC),
                exchange_time=row["timestamp"].to_pydatetime().replace(tzinfo=TZ_IST),
                symbol="NIFTY",
                instrument_id="999920000",
                exchange=Exchange.NSE,
                instrument_type=InstrumentType.INDEX,
                interval=Timeframe.M5,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                source=DataSource.FIVEPAISA_HISTORICAL,
            )
            for _, row in df_5m.iterrows()
        ]

        self.manual_df = pd.read_parquet(path_journal)
        logger.info(
            "Data loaded successfully: %d 1D bars, %d 5M bars, %d manual trades.",
            len(self.bars_1d),
            len(self.bars_5m),
            len(self.manual_df),
        )

    def run_backtests(self) -> None:
        """Execute baseline and method-by-method backtests across the historical bars."""
        engine = UnderlyingBacktestEngine(slippage_points=self.slippage_points)

        # 1. Canonical Strategy Baselines
        canonical_strategies = {
            "IRF": IRFStrategy({"method": "METHOD_1"}),
            "ORB": ORBStrategy({"method": "METHOD_1"}),
            "PDR": PDRStrategy(),
            "PDS": PDSStrategy({"method": "METHOD_1"}),
            "RF": RFStrategy({"strict_rulebook_mode": False}),
        }

        self.all_automated_trades = []
        for name, strat in canonical_strategies.items():
            rep = engine.run(strat, self.bars_5m, daily_history_bars=self.bars_1d)
            self.backtest_reports[name] = rep
            for t in rep.trades:
                if self.eval_start_date <= t.date <= self.eval_end_date:
                    self.all_automated_trades.append(t)

        # 2. Individual Method Backtests
        method_variants = {
            "IRF_Method_1": IRFStrategy({"method": "METHOD_1"}),
            "IRF_Method_2": IRFStrategy({"method": "METHOD_2"}),
            "IRF_Method_3": IRFStrategy({"method": "METHOD_3"}),
            "ORB_Method_1": ORBStrategy({"method": "METHOD_1"}),
            "ORB_Method_2": ORBStrategy({"method": "METHOD_2"}),
            "ORB_Method_3": ORBStrategy({"method": "METHOD_3"}),
            "PDR_Method_1": PDRStrategy(),
            "PDS_Method_1": PDSStrategy({"method": "METHOD_1"}),
            "PDS_Method_2": PDSStrategy({"method": "METHOD_2"}),
            "PDS_Method_3": PDSStrategy({"method": "METHOD_3"}),
            "RF_Canonical": RFStrategy({"strict_rulebook_mode": False}),
        }

        for key, strat in method_variants.items():
            rep = engine.run(strat, self.bars_5m, daily_history_bars=self.bars_1d)
            self.method_backtest_reports[key] = rep

    def reconcile_manual_trades(self) -> None:
        """Perform 1-to-1 matching and outcome comparison against the manual journal."""
        self.reconciled_trades = []
        matched_auto_ids: set[str] = set()

        for _, m_row in self.manual_df.iterrows():
            m_sl = int(m_row["sl_no"])
            m_row_num = int(m_row["row_number"])
            m_date = pd.to_datetime(m_row["date"]).date()
            m_strat = str(m_row["strategy"]).strip().upper()
            m_entry_time_str = str(m_row["entry_time"]).strip()
            m_out = str(m_row["outcome"]).strip()
            m_time_dt = pd.to_datetime(m_row["entry_timestamp_ist"]).replace(tzinfo=None)

            # Check 1: Non-trading exchange days (Saturdays/holidays)
            if m_date.weekday() >= 5:
                self.reconciled_trades.append(
                    ReconciledTradeRecord(
                        sl_no=m_sl,
                        row_number=m_row_num,
                        date=m_date,
                        manual_entry_time=m_entry_time_str,
                        strategy=m_strat,
                        manual_outcome=m_out,
                        match_classification="MISSING_HISTORICAL_DATA",
                        tolerance_tier="NON_TRADING_DAY",
                        notes="Weekend journal entry recorded on Saturday when NSE was closed; zero historical bars.",
                    )
                )
                continue

            # Candidate automated trades of the same strategy on the same date
            candidates = [
                a
                for a in self.all_automated_trades
                if a.strategy_name == m_strat
                and a.date == m_date
                and a.trade_id not in matched_auto_ids
            ]

            if not candidates:
                self.reconciled_trades.append(
                    ReconciledTradeRecord(
                        sl_no=m_sl,
                        row_number=m_row_num,
                        date=m_date,
                        manual_entry_time=m_entry_time_str,
                        strategy=m_strat,
                        manual_outcome=m_out,
                        match_classification="MANUAL_ONLY",
                        tolerance_tier="NO_AUTO_SIGNAL",
                        notes="Automated engine rejected setup or emitted no signal matching rulebook criteria.",
                    )
                )
                continue

            # Find closest automated trade by timestamp
            best_a: UnderlyingTradeRecord | None = None
            min_dt = float("inf")
            for a in candidates:
                dt_min = abs((a.entry_time.replace(tzinfo=None) - m_time_dt).total_seconds()) / 60.0
                if dt_min < min_dt:
                    min_dt = dt_min
                    best_a = a

            assert best_a is not None

            # Classification by tolerance tier
            if min_dt == 0.0:
                match_class = "EXACT_MATCH"
                tier = "EXACT_0M"
            elif min_dt <= 5.0:
                match_class = "TIME_TOLERANCE_MATCH"
                tier = "LE_5M"
            elif min_dt <= 10.0:
                match_class = "TIME_TOLERANCE_MATCH"
                tier = "LE_10M"
            else:
                match_class = "AMBIGUOUS_MATCH"
                tier = "GT_10M"

            # Reserve this automated trade for 1-to-1 matching if within tolerance
            if min_dt <= 10.0:
                matched_auto_ids.add(best_a.trade_id)

            # Evaluate outcome agreement
            auto_reason = best_a.exit_reason
            auto_out_str = (
                "TARGET"
                if "TARGET" in auto_reason
                else "STOP_LOSS"
                if ("STOP_LOSS" in auto_reason or "SL" in auto_reason)
                else "EOD_SQUAREOFF"
            )

            agreement = "DISAGREED"
            if m_strat == "RF":
                agreement = "UNRESOLVED_RULE"  # Rulebook target is marked OPEN in PDF
            elif m_out == "Target" and auto_out_str == "TARGET":
                agreement = "AGREED_TARGET"
            elif m_out == "SL" and auto_out_str == "STOP_LOSS":
                agreement = "AGREED_SL"
            elif m_out == "BE":
                agreement = "BE_MANUAL"

            notes = []
            if min_dt == 5.0 and m_strat == "ORB" and m_entry_time_str == "09:15":
                notes.append(
                    "Manual entered at candle open 09:15; mechanical strategy entered at 09:20 candle close."
                )
            elif min_dt > 0.0:
                notes.append(f"Timing delta: {min_dt:+.1f} min.")

            if m_out == "BE":
                notes.append("Manual break-even exit reflects discretionary trailing stop.")

            self.reconciled_trades.append(
                ReconciledTradeRecord(
                    sl_no=m_sl,
                    row_number=m_row_num,
                    date=m_date,
                    manual_entry_time=m_entry_time_str,
                    strategy=m_strat,
                    manual_outcome=m_out,
                    match_classification=match_class,
                    tolerance_tier=tier,
                    matched_auto_trade_id=best_a.trade_id,
                    auto_entry_time=best_a.entry_time.strftime("%H:%M"),
                    time_delta_minutes=min_dt,
                    auto_method=best_a.method,
                    auto_direction=best_a.direction.value,
                    auto_entry_price=best_a.entry_price,
                    auto_stop_loss=best_a.stop_loss,
                    auto_target=best_a.target,
                    auto_exit_time=best_a.exit_time.strftime("%H:%M"),
                    auto_exit_price=best_a.exit_price,
                    auto_exit_reason=best_a.exit_reason,
                    auto_points_pnl=best_a.points_pnl,
                    auto_outcome=auto_out_str,
                    outcome_agreement=agreement,
                    notes="; ".join(notes) if notes else "Full alignment.",
                )
            )

    def calculate_strategy_metrics(self) -> list[QuantitativeStrategyMetrics]:
        """Compute institutional quantitative performance metrics for all strategies and methods."""
        metrics_list: list[QuantitativeStrategyMetrics] = []

        method_configs = [
            (
                "IRF",
                "Method 1: Immediate Failure",
                "IRF_Method_1",
                self.method_backtest_reports["IRF_Method_1"],
            ),
            (
                "IRF",
                "Method 2: Extension & Reversal",
                "IRF_Method_2",
                self.method_backtest_reports["IRF_Method_2"],
            ),
            (
                "IRF",
                "Method 3: Liquidity Sweep",
                "IRF_Method_3",
                self.method_backtest_reports["IRF_Method_3"],
            ),
            (
                "ORB",
                "Method 1: PDH/PDL Breakout",
                "ORB_Method_1",
                self.method_backtest_reports["ORB_Method_1"],
            ),
            (
                "ORB",
                "Method 2: Inner Level Breakout",
                "ORB_Method_2",
                self.method_backtest_reports["ORB_Method_2"],
            ),
            (
                "ORB",
                "Method 3: 200 DMA Gap",
                "ORB_Method_3",
                self.method_backtest_reports["ORB_Method_3"],
            ),
            (
                "PDR",
                "Method 1: Retest Bounce",
                "PDR_Method_1",
                self.method_backtest_reports["PDR_Method_1"],
            ),
            (
                "PDS",
                "Method 1: Strong Red Candle",
                "PDS_Method_1",
                self.method_backtest_reports["PDS_Method_1"],
            ),
            (
                "PDS",
                "Method 2: Red Pin Bar HPR",
                "PDS_Method_2",
                self.method_backtest_reports["PDS_Method_2"],
            ),
            (
                "PDS",
                "Method 3: Support Break Fallback",
                "PDS_Method_3",
                self.method_backtest_reports["PDS_Method_3"],
            ),
            (
                "RF",
                "Canonical Reclaim (Unresolved Target)",
                "RF_Canonical",
                self.method_backtest_reports["RF_Canonical"],
            ),
        ]

        reconciled_df = pd.DataFrame([asdict(r) for r in self.reconciled_trades])

        for strat_name, method_display, _, rep in method_configs:
            eval_trades = [
                t for t in rep.trades if self.eval_start_date <= t.date <= self.eval_end_date
            ]
            man_strat_df = reconciled_df[reconciled_df["strategy"] == strat_name]
            total_manual = len(man_strat_df)
            weekend_manual = len(man_strat_df[man_strat_df["tolerance_tier"] == "NON_TRADING_DAY"])
            eligible_manual = total_manual - weekend_manual

            # Matched subset from canonical reconciliation for this strategy
            strat_matched = man_strat_df[
                man_strat_df["tolerance_tier"].isin(["EXACT_0M", "LE_5M", "LE_10M"])
            ]
            exact_m = len(strat_matched[strat_matched["tolerance_tier"] == "EXACT_0M"])
            le_5m = len(strat_matched[strat_matched["tolerance_tier"] == "LE_5M"])
            le_10m = len(strat_matched[strat_matched["tolerance_tier"] == "LE_10M"])
            total_matched = len(strat_matched)
            ambiguous_m = len(man_strat_df[man_strat_df["tolerance_tier"] == "GT_10M"])
            man_only = len(man_strat_df[man_strat_df["tolerance_tier"] == "NO_AUTO_SIGNAL"])

            auto_count = len(eval_trades)
            auto_only = max(0, auto_count - total_matched)

            match_pct = (total_matched / eligible_manual * 100.0) if eligible_manual > 0 else 0.0
            precision_pct = (total_matched / auto_count * 100.0) if auto_count > 0 else 0.0
            recall_pct = (total_matched / eligible_manual * 100.0) if eligible_manual > 0 else 0.0

            # Outcome agreement
            agreed = strat_matched[
                strat_matched["outcome_agreement"].isin(["AGREED_TARGET", "AGREED_SL"])
            ]
            outcome_agreed_count = len(agreed)
            binary_evaluated = strat_matched[
                strat_matched["outcome_agreement"].isin(["AGREED_TARGET", "AGREED_SL", "DISAGREED"])
            ]
            outcome_agreement_pct = (
                (outcome_agreed_count / len(binary_evaluated) * 100.0)
                if len(binary_evaluated) > 0
                else 0.0
            )

            # Financial metrics over eval_trades
            wins = [t for t in eval_trades if t.is_win]
            losses = [t for t in eval_trades if not t.is_win]
            win_rate = (len(wins) / len(eval_trades) * 100.0) if eval_trades else 0.0

            net_pts = sum(t.points_pnl for t in eval_trades)
            # Round-trip slippage was 2 * 0.50 = 1.00 pt per trade
            slippage_total = len(eval_trades) * (2 * self.slippage_points)
            gross_pts = net_pts + slippage_total

            gross_wins = sum(t.points_pnl for t in wins)
            gross_losses = abs(sum(t.points_pnl for t in losses))
            pf = (
                (gross_wins / gross_losses)
                if gross_losses > 0
                else (99.0 if gross_wins > 0 else 0.0)
            )

            avg_win = (sum(t.points_pnl for t in wins) / len(wins)) if wins else 0.0
            avg_loss = (abs(sum(t.points_pnl for t in losses)) / len(losses)) if losses else 0.0
            expectancy = ((win_rate / 100.0) * avg_win) - (((100.0 - win_rate) / 100.0) * avg_loss)

            # Max drawdown and consecutive losses
            max_dd = 0.0
            cum_pts = 0.0
            peak_pts = 0.0
            max_consec_losses = 0
            curr_consec_losses = 0

            hold_times = []
            for t in eval_trades:
                cum_pts += t.points_pnl
                if cum_pts > peak_pts:
                    peak_pts = cum_pts
                dd = peak_pts - cum_pts
                if dd > max_dd:
                    max_dd = dd

                if not t.is_win:
                    curr_consec_losses += 1
                    if curr_consec_losses > max_consec_losses:
                        max_consec_losses = curr_consec_losses
                else:
                    curr_consec_losses = 0

                hold_times.append((t.exit_time - t.entry_time).total_seconds() / 60.0)

            avg_holding = (sum(hold_times) / len(hold_times)) if hold_times else 0.0

            metrics_list.append(
                QuantitativeStrategyMetrics(
                    strategy_name=strat_name,
                    method_name=method_display,
                    manual_trades_count=total_manual,
                    automated_trades_count=auto_count,
                    exact_matches_count=exact_m,
                    matches_within_5m_count=le_5m,
                    matches_within_10m_count=le_10m,
                    total_matched_count=total_matched,
                    ambiguous_matches_count=ambiguous_m,
                    manual_only_count=man_only,
                    automated_only_count=auto_only,
                    missing_data_days_count=weekend_manual,
                    match_percentage=round(match_pct, 2),
                    precision_pct=round(precision_pct, 2),
                    recall_pct=round(recall_pct, 2),
                    outcome_agreement_count=outcome_agreed_count,
                    outcome_agreement_pct=round(outcome_agreement_pct, 2),
                    gross_index_points=round(gross_pts, 2),
                    slippage_points_deducted=round(slippage_total, 2),
                    net_index_points=round(net_pts, 2),
                    win_rate_pct=round(win_rate, 2),
                    profit_factor=round(pf, 2),
                    avg_win_points=round(avg_win, 2),
                    avg_loss_points=round(avg_loss, 2),
                    expectancy_points=round(expectancy, 2),
                    max_drawdown_points=round(max_dd, 2),
                    max_consecutive_losses=max_consec_losses,
                    avg_holding_time_minutes=round(avg_holding, 1),
                )
            )

        return metrics_list

    def export_all_deliverables(self) -> None:
        """Export all CSV, Parquet, HTML and Markdown deliverables required by Phase 2D."""
        reconciled_df = pd.DataFrame([asdict(r) for r in self.reconciled_trades])
        metrics_list = self.calculate_strategy_metrics()
        metrics_df = pd.DataFrame([asdict(m) for m in metrics_list])

        # 1. reports/phase2d/strategy_comparison.csv
        metrics_df.to_csv(self.reports_dir / "strategy_comparison.csv", index=False)

        # 2. reports/phase2d/manual_vs_automated.csv
        reconciled_df.to_csv(self.reports_dir / "manual_vs_automated.csv", index=False)

        # 3. reports/phase2d/matched_trades.csv
        matched_df = reconciled_df[
            reconciled_df["tolerance_tier"].isin(["EXACT_0M", "LE_5M", "LE_10M"])
        ].copy()
        matched_df.to_csv(self.reports_dir / "matched_trades.csv", index=False)

        # 4. reports/phase2d/missed_manual_trades.csv
        missed_df = reconciled_df[
            reconciled_df["tolerance_tier"].isin(["NO_AUTO_SIGNAL", "NON_TRADING_DAY"])
        ].copy()
        missed_df.to_csv(self.reports_dir / "missed_manual_trades.csv", index=False)

        # 5. reports/phase2d/extra_automated_signals.csv
        matched_auto_ids = set(matched_df["matched_auto_trade_id"].dropna().unique())
        extra_auto_records = [
            {
                "trade_id": t.trade_id,
                "date": t.date,
                "strategy": t.strategy_name,
                "method": t.method,
                "direction": t.direction.value,
                "entry_time": t.entry_time.strftime("%H:%M"),
                "entry_price": t.entry_price,
                "exit_time": t.exit_time.strftime("%H:%M"),
                "exit_price": t.exit_price,
                "exit_reason": t.exit_reason,
                "points_pnl": t.points_pnl,
                "is_win": t.is_win,
                "target": t.target,
                "stop_loss": t.stop_loss,
            }
            for t in self.all_automated_trades
            if t.trade_id not in matched_auto_ids
        ]
        extra_auto_df = pd.DataFrame(extra_auto_records)
        extra_auto_df.to_csv(self.reports_dir / "extra_automated_signals.csv", index=False)

        # 6. reports/phase2d/outcome_disagreements.csv
        disagreements_df = matched_df[
            matched_df["outcome_agreement"].isin(["DISAGREED", "BE_MANUAL", "UNRESOLVED_RULE"])
        ].copy()
        disagreements_df.to_csv(self.reports_dir / "outcome_disagreements.csv", index=False)

        # 7. reports/phase2d/backtest_trades.parquet
        auto_trades_df = pd.DataFrame(
            [
                {
                    "trade_id": t.trade_id,
                    "date": t.date,
                    "strategy_name": t.strategy_name,
                    "method": t.method,
                    "direction": t.direction.value,
                    "entry_time": t.entry_time,
                    "entry_price": t.entry_price,
                    "exit_time": t.exit_time,
                    "exit_price": t.exit_price,
                    "exit_reason": t.exit_reason,
                    "points_pnl": t.points_pnl,
                    "is_win": t.is_win,
                    "target": t.target,
                    "stop_loss": t.stop_loss,
                    "is_uncertain_fill": t.is_uncertain_fill,
                }
                for t in self.all_automated_trades
            ]
        )
        auto_trades_df.to_parquet(self.reports_dir / "backtest_trades.parquet", index=False)

        # 8. reports/phase2d/equity_curves.html
        self.generate_equity_curves_html()

        # 9. reports/phase2d/validation_dashboard.html
        self.generate_validation_dashboard_html()

        logger.info("Successfully exported all Phase 2D deliverables to %s.", self.reports_dir)

    def generate_equity_curves_html(self) -> None:
        """Generate interactive Plotly visualization for cumulative points, drawdowns, and monthly returns."""
        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            subplot_titles=(
                "Cumulative Net Index Points (1.0 pt Slippage Subtracted)",
                "System Drawdown (Index Points)",
                "Combined Multi-Strategy Portfolio Equity Curve",
            ),
            row_heights=[0.45, 0.25, 0.30],
        )

        colors = {
            "IRF": "#EF5350",
            "ORB": "#42A5F5",
            "PDR": "#FFA726",
            "PDS": "#66BB6A",
            "RF": "#AB47BC",
        }

        # Multi-strategy curves
        date_range = pd.date_range(self.eval_start_date, self.eval_end_date, freq="D")
        combined_daily_pnl: dict[date, float] = {d.date(): 0.0 for d in date_range}

        for strat_name, rep in self.backtest_reports.items():
            strat_trades = [
                t for t in rep.trades if self.eval_start_date <= t.date <= self.eval_end_date
            ]
            strat_trades.sort(key=lambda x: x.entry_time)

            if not strat_trades:
                continue

            cum_pnl = 0.0
            x_times = []
            y_cum = []
            y_dd = []
            peak = 0.0

            for t in strat_trades:
                cum_pnl += t.points_pnl
                peak = max(peak, cum_pnl)
                dd = cum_pnl - peak
                x_times.append(t.exit_time)
                y_cum.append(cum_pnl)
                y_dd.append(dd)
                combined_daily_pnl[t.date] = combined_daily_pnl.get(t.date, 0.0) + t.points_pnl

            fig.add_trace(
                go.Scatter(
                    x=x_times,
                    y=y_cum,
                    mode="lines+markers",
                    name=f"{strat_name} (Net: {cum_pnl:+.1f} pts)",
                    line=dict(color=colors.get(strat_name, "#FFFFFF"), width=2),
                ),
                row=1,
                col=1,
            )

            fig.add_trace(
                go.Scatter(
                    x=x_times,
                    y=y_dd,
                    mode="lines",
                    name=f"{strat_name} DD",
                    line=dict(color=colors.get(strat_name, "#FFFFFF"), width=1.5, dash="dot"),
                    showlegend=False,
                ),
                row=2,
                col=1,
            )

        # Combined Portfolio Curve
        comb_dates = sorted(list(combined_daily_pnl.keys()))
        comb_cum = np.cumsum([combined_daily_pnl[d] for d in comb_dates])
        fig.add_trace(
            go.Scatter(
                x=[datetime.combine(d, datetime.min.time()) for d in comb_dates],
                y=comb_cum,
                mode="lines",
                name=f"Combined Portfolio (Total: {comb_cum[-1]:+.1f} pts)",
                line=dict(color="#00E676", width=3),
                fill="tozeroy",
                fillcolor="rgba(0, 230, 118, 0.1)",
            ),
            row=3,
            col=1,
        )

        fig.update_layout(
            title="NIFTY Strategy Historical Performance & Drawdown Architecture (2025–2026)",
            template="plotly_dark",
            height=950,
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )

        fig.write_html(str(self.reports_dir / "equity_curves.html"))

    def generate_validation_dashboard_html(self) -> None:
        """Generate interactive Plotly candlestick validation dashboard for trade audits."""
        # Select representative trades across exact match, tolerance match, missed, and disagreement
        reconciled_df = pd.DataFrame([asdict(r) for r in self.reconciled_trades])
        sample_rows = pd.concat(
            [
                reconciled_df[reconciled_df["match_classification"] == "EXACT_MATCH"].head(2),
                reconciled_df[reconciled_df["tolerance_tier"] == "LE_5M"].head(3),
                reconciled_df[reconciled_df["outcome_agreement"] == "DISAGREED"].head(2),
                reconciled_df[reconciled_df["match_classification"] == "MANUAL_ONLY"].head(2),
            ]
        ).drop_duplicates()

        fig = make_subplots(
            rows=len(sample_rows),
            cols=1,
            shared_xaxes=False,
            vertical_spacing=0.06,
            subplot_titles=[
                f"Trade #{r['sl_no']} | Date: {r['date']} | Strat: {r['strategy']} | "
                f"Class: {r['match_classification']} | Outcome: Man={r['manual_outcome']} vs Auto={r['auto_outcome'] or 'None'}"
                for _, r in sample_rows.iterrows()
            ],
        )

        row_idx = 1
        for _, r in sample_rows.iterrows():
            t_date = r["date"]
            day_bars = [b for b in self.bars_5m if b.exchange_time.date() == t_date]
            if not day_bars:
                continue

            fig.add_trace(
                go.Candlestick(
                    x=[b.exchange_time for b in day_bars],
                    open=[b.open for b in day_bars],
                    high=[b.high for b in day_bars],
                    low=[b.low for b in day_bars],
                    close=[b.close for b in day_bars],
                    name=f"NIFTY {t_date}",
                    showlegend=False,
                ),
                row=row_idx,
                col=1,
            )

            # Overlay levels if automated trade exists
            if pd.notna(r["auto_entry_price"]):
                entry_dt = datetime.strptime(
                    f"{t_date} {r['auto_entry_time']}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=TZ_IST)
                exit_dt = datetime.strptime(
                    f"{t_date} {r['auto_exit_time']}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=TZ_IST)

                fig.add_trace(
                    go.Scatter(
                        x=[entry_dt],
                        y=[r["auto_entry_price"]],
                        mode="markers",
                        marker=dict(
                            symbol="triangle-up"
                            if r["auto_direction"] == "BUY"
                            else "triangle-down",
                            size=14,
                            color="#00E676" if r["auto_direction"] == "BUY" else "#FF1744",
                        ),
                        name=f"Auto Entry ({r['auto_direction']})",
                        showlegend=False,
                    ),
                    row=row_idx,
                    col=1,
                )

                fig.add_trace(
                    go.Scatter(
                        x=[exit_dt],
                        y=[r["auto_exit_price"]],
                        mode="markers",
                        marker=dict(symbol="x", size=12, color="#FFEA00"),
                        name=f"Exit ({r['auto_exit_reason']})",
                        showlegend=False,
                    ),
                    row=row_idx,
                    col=1,
                )

                # Target and SL horizontal lines
                fig.add_hline(
                    y=r["auto_target"],
                    line_dash="dash",
                    line_color="#00E676",
                    annotation_text=f"Target ({r['auto_target']:.1f})",
                    row=row_idx,
                    col=1,
                )
                fig.add_hline(
                    y=r["auto_stop_loss"],
                    line_dash="dash",
                    line_color="#FF1744",
                    annotation_text=f"SL ({r['auto_stop_loss']:.1f})",
                    row=row_idx,
                    col=1,
                )

            # Manual entry marker
            man_dt = datetime.strptime(
                f"{t_date} {r['manual_entry_time']}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=TZ_IST)
            fig.add_vline(
                x=man_dt,
                line_dash="dot",
                line_color="#2979FF",
                annotation_text=f"Manual Entry ({r['manual_entry_time']})",
                row=row_idx,
                col=1,
            )

            row_idx += 1

        fig.update_layout(
            title="Trade-by-Trade Validation Audit Dashboard (Candlestick Overlays)",
            template="plotly_dark",
            height=300 * len(sample_rows),
            xaxis_rangeslider_visible=False,
        )

        fig.write_html(str(self.reports_dir / "validation_dashboard.html"))
