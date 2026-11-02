"""Daily and Cumulative Reporting Engine for NIFTY Options Paper Trading."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)
TZ_IST = ZoneInfo("Asia/Kolkata")


class PaperTradingReporter:
    """Compiles daily trading journals, strategy performance, dual accounting, and fee attribution."""

    def __init__(
        self,
        db_path: str | Path = "data/processed/paper_trading.duckdb",
        reports_dir: str | Path = "reports/paper_trading",
    ) -> None:
        self.db_path = Path(db_path)
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_daily_journal(self, run_id: str | None = None) -> pd.DataFrame:
        """Fetch completed round-trip paper trades with full attribution."""
        if not self.db_path.exists():
            return pd.DataFrame()

        con = duckdb.connect(str(self.db_path), read_only=True)
        query = "SELECT * FROM completed_trades"
        params: list[str] = []
        if run_id:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY exit_time DESC"

        try:
            df = con.execute(query, params).df()
            con.close()
            return df
        except Exception:
            con.close()
            return pd.DataFrame()

    def generate_strategy_performance(self, run_id: str | None = None) -> pd.DataFrame:
        """Compile strategy-wise win rates, points, and rupee P&L."""
        df = self.generate_daily_journal(run_id)
        if df.empty:
            return pd.DataFrame(
                columns=[
                    "strategy_name",
                    "trades",
                    "wins",
                    "losses",
                    "win_rate_pct",
                    "underlying_points",
                    "gross_pnl_inr",
                    "total_costs_inr",
                    "net_pnl_inr",
                    "profit_factor",
                ]
            )

        summary_rows = []
        for strat, grp in df.groupby("strategy_name"):
            trades_cnt = len(grp)
            wins_cnt = (grp["option_net_pnl"] > 0).sum()
            losses_cnt = (grp["option_net_pnl"] <= 0).sum()
            win_rate = (wins_cnt / trades_cnt) * 100.0 if trades_cnt > 0 else 0.0

            total_points = round(grp["underlying_points_pnl"].sum(), 2)
            gross_pnl = round(grp["option_gross_pnl"].sum(), 2)
            costs = round(grp["total_costs"].sum(), 2)
            net_pnl = round(grp["option_net_pnl"].sum(), 2)

            gross_wins = grp.loc[grp["option_net_pnl"] > 0, "option_net_pnl"].sum()
            gross_losses = abs(grp.loc[grp["option_net_pnl"] < 0, "option_net_pnl"].sum())
            profit_factor = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 999.0

            summary_rows.append(
                {
                    "strategy_name": strat,
                    "trades": trades_cnt,
                    "wins": wins_cnt,
                    "losses": losses_cnt,
                    "win_rate_pct": round(win_rate, 1),
                    "underlying_points": total_points,
                    "gross_pnl_inr": gross_pnl,
                    "total_costs_inr": costs,
                    "net_pnl_inr": net_pnl,
                    "profit_factor": profit_factor,
                }
            )

        return pd.DataFrame(summary_rows)

    def generate_dual_accounting_summary(self, run_id: str | None = None) -> pd.DataFrame:
        """Compare underlying points captured against realized options rupees."""
        df = self.generate_daily_journal(run_id)
        if df.empty:
            return pd.DataFrame()

        cols = [
            "trade_id",
            "strategy_name",
            "symbol",
            "quantity",
            "underlying_points_pnl",
            "option_gross_pnl",
            "total_costs",
            "option_net_pnl",
            "exit_reason",
        ]
        return df[cols].copy()

    def generate_html_report(self, run_id: str) -> str:
        """Generate a clean styled HTML report of paper trading session."""
        journal = self.generate_daily_journal(run_id)
        strat_df = self.generate_strategy_performance(run_id)
        dual_df = self.generate_dual_accounting_summary(run_id)

        now_str = datetime.now(TZ_IST).strftime("%Y-%m-%d %H:%M:%S IST")
        strat_html = strat_df.to_html(classes="table", index=False) if not strat_df.empty else "<p>No completed trades recorded for this run.</p>"
        journal_html = journal.to_html(classes="table", index=False) if not journal.empty else "<p>No trades executed.</p>"
        dual_html = dual_df.to_html(classes="table", index=False) if not dual_df.empty else "<p>No dual accounting records.</p>"

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Paper Trading Session Report - {run_id}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 30px; background-color: #f8fafc; color: #1e293b; line-height: 1.5; }}
h1, h2 {{ color: #0f172a; margin-top: 1.5rem; }}
.badge {{ display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 0.85rem; font-weight: bold; }}
.badge-paper {{ background: #fee2e2; color: #991b1b; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 25px; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #e2e8f0; font-size: 0.88rem; }}
th {{ background-color: #f1f5f9; font-weight: 600; color: #334155; }}
tr:hover {{ background-color: #f8fafc; }}
</style>
</head>
<body>
<h1>🛡️ NIFTY Options Paper Trading Report</h1>
<p><span class="badge badge-paper">SIMULATION / STRICTLY PAPER TRADING ONLY</span></p>
<p><strong>Run ID:</strong> {run_id} | <strong>Generated:</strong> {now_str}</p>

<h2>Strategy Performance Summary</h2>
{strat_html}

<h2>Completed Trades Journal</h2>
{journal_html}

<h2>Dual Accounting (Underlying Points vs Option Rupees)</h2>
{dual_html}
</body>
</html>
"""

    def generate_session_validation_df(self, run_id: str | None = None) -> pd.DataFrame:
        """Compile session validation metrics dataframe."""
        if not self.db_path.exists():
            return pd.DataFrame()

        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            run_filter = " WHERE run_id = ?" if run_id else ""
            params = [run_id] if run_id else []
            trades_df = con.execute(
                f"SELECT * FROM completed_trades{run_filter}", params
            ).df()
            eq_df = con.execute(
                f"SELECT * FROM equity_snapshots{run_filter} "
                "ORDER BY timestamp DESC LIMIT 1",
                params,
            ).df()
            try:
                interrupt_cnt = con.execute(
                    f"SELECT count(*) FROM data_feed_interruptions{run_filter}", params
                ).fetchone()[0]
            except Exception:
                interrupt_cnt = 0
            con.close()

            completed_count = len(trades_df)
            realized_pnl = round(trades_df["option_net_pnl"].sum(), 2) if not trades_df.empty else 0.0
            total_brokerage = round(trades_df["total_costs"].sum(), 2) if not trades_df.empty else 0.0
            total_points = round(trades_df["underlying_points_pnl"].sum(), 2) if not trades_df.empty else 0.0
            latest_equity = eq_df["total_equity"].iloc[0] if not eq_df.empty else 250000.0

            row = {
                "timestamp": datetime.now(TZ_IST).isoformat(),
                "run_id": run_id or "RUN_ALL",
                "virtual_starting_capital": 250000.0,
                "latest_equity": latest_equity,
                "realized_pnl_inr": realized_pnl,
                "underlying_points_pnl": total_points,
                "total_transaction_costs_inr": total_brokerage,
                "completed_trades": completed_count,
                "feed_interruptions": interrupt_cnt,
                "broker_order_endpoints_disabled": True,
                "validation_status": (
                    "EVIDENCE_RECORDED_NOT_INDEPENDENTLY_VALIDATED"
                    if completed_count
                    else "NOT_VERIFIED_NO_COMPLETED_TRADES"
                ),
            }
            return pd.DataFrame([row])
        except Exception:
            con.close()
            return pd.DataFrame()

    def generate_quote_quality_df(self) -> pd.DataFrame:
        """Compile quote freshness and execution depth metrics dataframe."""
        if not self.db_path.exists():
            return pd.DataFrame()

        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            sel_df = con.execute("SELECT * FROM contract_selections ORDER BY timestamp DESC").df()
            con.close()
            if sel_df.empty:
                return pd.DataFrame(
                    columns=[
                        "timestamp",
                        "symbol",
                        "scrip_code",
                        "best_bid",
                        "best_ask",
                        "spread",
                        "status",
                        "reason",
                        "quote_freshness_status",
                    ]
                )
            sel_df["quote_freshness_status"] = sel_df["status"].apply(
                lambda s: "FRESH_EXECUTABLE" if s in ("APPROVED", "SELECTED") else "REJECTED_OR_STALE"
            )
            return sel_df
        except Exception:
            con.close()
            return pd.DataFrame()

    def generate_signal_audit_df(self) -> pd.DataFrame:
        """Compile complete audit log of all strategy signals and arbitration outcomes."""
        if not self.db_path.exists():
            return pd.DataFrame()

        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            sig_df = con.execute("SELECT * FROM signals ORDER BY timestamp DESC").df()
            con.close()
            return sig_df
        except Exception:
            con.close()
            return pd.DataFrame()

    def export_reports(self, run_id: str) -> dict[str, Path]:
        """Export CSV, Parquet, and HTML summary reports to reports/paper_trading/."""
        exported = {}
        today_str = datetime.now(TZ_IST).strftime("%Y%m%d")

        # 1. Daily Journal
        journal = self.generate_daily_journal(run_id)
        journal_csv = self.reports_dir / f"paper_journal_{today_str}_{run_id}.csv"
        journal_parquet = self.reports_dir / f"paper_journal_{today_str}_{run_id}.parquet"
        journal.to_csv(journal_csv, index=False)
        journal.to_parquet(journal_parquet, compression="zstd")
        exported["journal_csv"] = journal_csv
        exported["journal_parquet"] = journal_parquet

        # 2. Strategy Performance
        strat_df = self.generate_strategy_performance(run_id)
        strat_csv = self.reports_dir / f"strategy_performance_{today_str}_{run_id}.csv"
        strat_df.to_csv(strat_csv, index=False)
        exported["strategy_performance_csv"] = strat_csv

        # 3. Dual Accounting
        dual_df = self.generate_dual_accounting_summary(run_id)
        dual_csv = self.reports_dir / f"dual_accounting_{today_str}_{run_id}.csv"
        dual_df.to_csv(dual_csv, index=False)
        exported["dual_accounting_csv"] = dual_csv

        # 4. HTML Report
        html_content = self.generate_html_report(run_id)
        html_file = self.reports_dir / f"paper_journal_{today_str}_{run_id}.html"
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        exported["journal_html"] = html_file

        # 5. Dedicated Phase 4B Audits
        sess_df = self.generate_session_validation_df(run_id)
        sess_csv = self.reports_dir / "session_validation.csv"
        sess_df.to_csv(sess_csv, index=False)
        exported["session_validation_csv"] = sess_csv

        quote_df = self.generate_quote_quality_df()
        quote_csv = self.reports_dir / "quote_quality.csv"
        quote_df.to_csv(quote_csv, index=False)
        exported["quote_quality_csv"] = quote_csv

        signal_df = self.generate_signal_audit_df()
        signal_csv = self.reports_dir / "signal_audit.csv"
        signal_df.to_csv(signal_csv, index=False)
        exported["signal_audit_csv"] = signal_csv

        return exported
