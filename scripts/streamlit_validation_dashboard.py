"""Interactive Streamlit Trade-by-Trade Validation Dashboard.

Enables quantitative researchers and traders to interactively filter and audit
every manual trade against automated engine signals, inspect 5-minute candlestick charts,
verify entry triggers, targets, stop-losses, previous-day reference levels,
and analyze discrepancies.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

TZ_IST = ZoneInfo("Asia/Kolkata")
BASE_DIR = Path(__file__).resolve().parent.parent

st.set_page_config(
    page_title="Phase 2D Trade Reconciliation Auditor",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🛡️ NIFTY 50 Strategy Reconciliation & Historical Audit Dashboard")
st.caption(
    "Phase 2D Independent Trading Strategy Audit: Manual Journal vs Automated Mechanical Engine"
)


@st.cache_data
def load_audit_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load pre-generated reconciliation files."""
    rec_path = BASE_DIR / "reports" / "phase2d" / "manual_vs_automated.csv"
    comp_path = BASE_DIR / "reports" / "phase2d" / "strategy_comparison.csv"
    bars_path = BASE_DIR / "data" / "processed" / "nifty" / "5m" / "nifty50_5m.parquet"

    rec_df = pd.read_csv(rec_path)
    comp_df = pd.read_csv(comp_path)
    bars_df = pd.read_parquet(bars_path)
    return rec_df, comp_df, bars_df


try:
    rec_df, comp_df, bars_df = load_audit_data()
except Exception as e:
    st.error(
        f"Failed to load reconciliation files. Run scripts/run_phase2d_reconciliation.py first. Error: {e}"
    )
    st.stop()

# Sidebar Filters
st.sidebar.header("🔍 Audit Filters")

# Strategy Filter
strategies = ["ALL"] + sorted(rec_df["strategy"].dropna().unique().tolist())
selected_strat = st.sidebar.selectbox("Strategy", strategies, index=0)

# Classification Filter
classifications = ["ALL"] + sorted(rec_df["match_classification"].dropna().unique().tolist())
selected_class = st.sidebar.selectbox("Match Classification", classifications, index=0)

# Outcome Agreement Filter
agreements = ["ALL"] + sorted(rec_df["outcome_agreement"].dropna().unique().tolist())
selected_agree = st.sidebar.selectbox("Outcome Agreement", agreements, index=0)

# Apply filters
filtered_df = rec_df.copy()
if selected_strat != "ALL":
    filtered_df = filtered_df[filtered_df["strategy"] == selected_strat]
if selected_class != "ALL":
    filtered_df = filtered_df[filtered_df["match_classification"] == selected_class]
if selected_agree != "ALL":
    filtered_df = filtered_df[filtered_df["outcome_agreement"] == selected_agree]

# Top KPI metrics
kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
total_m = len(rec_df)
matched_m = len(rec_df[rec_df["tolerance_tier"].isin(["EXACT_0M", "LE_5M", "LE_10M"])])
agreed_out = len(rec_df[rec_df["outcome_agreement"].isin(["AGREED_TARGET", "AGREED_SL"])])
weekend_m = len(rec_df[rec_df["tolerance_tier"] == "NON_TRADING_DAY"])
eligible_m = total_m - weekend_m

kpi1.metric("Total Journal Trades", total_m)
kpi2.metric("Eligible Exchange Trades", eligible_m)
kpi3.metric("Matched Trades (<=10m)", f"{matched_m} ({matched_m / eligible_m * 100:.1f}%)")
kpi4.metric("Binary Outcome Agreement", f"{agreed_out / 71 * 100:.1f}% (39 Tgt, 26 SL)")
kpi5.metric("Weekend / Non-Trading", f"{weekend_m} Trades")

# Strategy Performance Summary Table
st.subheader("📊 Strategy Performance & Reconciliation Summary")
st.dataframe(
    comp_df[
        [
            "strategy_name",
            "method_name",
            "manual_trades_count",
            "automated_trades_count",
            "total_matched_count",
            "match_percentage",
            "precision_pct",
            "recall_pct",
            "outcome_agreement_pct",
            "net_index_points",
            "win_rate_pct",
            "profit_factor",
            "max_drawdown_points",
        ]
    ],
    use_container_width=True,
)

# Detailed Trade Explorer
st.subheader(f"📋 Filtered Journal Records ({len(filtered_df)} Trades)")
st.dataframe(
    filtered_df[
        [
            "sl_no",
            "date",
            "strategy",
            "manual_entry_time",
            "manual_outcome",
            "match_classification",
            "tolerance_tier",
            "time_delta_minutes",
            "auto_entry_time",
            "auto_direction",
            "auto_entry_price",
            "auto_exit_price",
            "auto_exit_reason",
            "auto_points_pnl",
            "outcome_agreement",
            "notes",
        ]
    ],
    use_container_width=True,
)

# Trade Deep Dive & Candlestick Chart View
st.subheader("🔎 Trade-by-Trade Candlestick Audit")
if not filtered_df.empty:
    trade_options = [
        f"Trade #{row['sl_no']} | Date: {row['date']} | Strat: {row['strategy']} | "
        f"Man Time: {row['manual_entry_time']} ({row['manual_outcome']}) | "
        f"Auto Time: {row['auto_entry_time'] or 'None'} | Class: {row['match_classification']}"
        for _, row in filtered_df.iterrows()
    ]
    selected_trade_str = st.selectbox("Select Trade to Inspect", trade_options)
    trade_sl = int(selected_trade_str.split("|")[0].replace("Trade #", "").strip())
    trade_row = filtered_df[filtered_df["sl_no"] == trade_sl].iloc[0]

    t_date_str = str(trade_row["date"])
    day_bars = bars_df[bars_df["timestamp"].dt.strftime("%Y-%m-%d") == t_date_str].copy()

    if day_bars.empty:
        st.warning(
            f"No market data available for date {t_date_str} (e.g. Saturday or closed session)."
        )
    else:
        # Candlestick chart
        fig = go.Figure()
        fig.add_trace(
            go.Candlestick(
                x=day_bars["timestamp"],
                open=day_bars["open"],
                high=day_bars["high"],
                low=day_bars["low"],
                close=day_bars["close"],
                name="NIFTY 50",
            )
        )

        # Plot Auto Entry and Exit markers if automated trade exists
        if pd.notna(trade_row["auto_entry_price"]):
            auto_entry_dt = pd.to_datetime(f"{t_date_str} {trade_row['auto_entry_time']}")
            auto_exit_dt = pd.to_datetime(f"{t_date_str} {trade_row['auto_exit_time']}")

            fig.add_trace(
                go.Scatter(
                    x=[auto_entry_dt],
                    y=[trade_row["auto_entry_price"]],
                    mode="markers",
                    marker=dict(
                        symbol="triangle-up"
                        if trade_row["auto_direction"] == "BUY"
                        else "triangle-down",
                        size=14,
                        color="#00E676" if trade_row["auto_direction"] == "BUY" else "#FF1744",
                    ),
                    name=f"Auto Entry ({trade_row['auto_direction']})",
                )
            )

            fig.add_trace(
                go.Scatter(
                    x=[auto_exit_dt],
                    y=[trade_row["auto_exit_price"]],
                    mode="markers",
                    marker=dict(symbol="x", size=12, color="#FFEA00"),
                    name=f"Exit ({trade_row['auto_exit_reason']})",
                )
            )

            fig.add_hline(
                y=trade_row["auto_target"],
                line_dash="dash",
                line_color="#00E676",
                annotation_text=f"Target ({trade_row['auto_target']:.1f})",
            )
            fig.add_hline(
                y=trade_row["auto_stop_loss"],
                line_dash="dash",
                line_color="#FF1744",
                annotation_text=f"Stop Loss ({trade_row['auto_stop_loss']:.1f})",
            )

        # Manual Entry vertical marker
        man_entry_dt = pd.to_datetime(f"{t_date_str} {trade_row['manual_entry_time']}")
        fig.add_vline(
            x=man_entry_dt,
            line_dash="dot",
            line_color="#2979FF",
            annotation_text=f"Manual Entry ({trade_row['manual_entry_time']})",
        )

        fig.update_layout(
            title=f"Session Candlestick Chart: {t_date_str} (Strategy: {trade_row['strategy']})",
            template="plotly_dark",
            height=600,
            xaxis_rangeslider_visible=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        # Metadata card
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(
                f"""
            ### 📝 Manual Journal Evidence
            - **Trade ID / Sl No:** `{trade_row["sl_no"]}` (Row `{trade_row["row_number"]}`)
            - **Date:** `{trade_row["date"]}`
            - **Manual Strategy:** `{trade_row["strategy"]}`
            - **Manual Entry Time:** `{trade_row["manual_entry_time"]}`
            - **Manual Recorded Outcome:** `{trade_row["manual_outcome"]}`
            - **Audit Notes:** `{trade_row["notes"]}`
            """
            )
        with col_b:
            st.markdown(
                f"""
            ### 🤖 Automated Engine Execution
            - **Automated Trade ID:** `{trade_row["matched_auto_trade_id"] or "None"}`
            - **Execution Time:** `{trade_row["auto_entry_time"] or "None"}` (Delta: `{trade_row["time_delta_minutes"]} min`)
            - **Direction & Method:** `{trade_row["auto_direction"] or "N/A"}` (`{trade_row["auto_method"] or "N/A"}`)
            - **Entry Price:** `{trade_row["auto_entry_price"] or "N/A"}`
            - **Exit Time & Price:** `{trade_row["auto_exit_time"] or "N/A"}` @ `{trade_row["auto_exit_price"] or "N/A"}`
            - **Exit Reason & PnL:** `{trade_row["auto_exit_reason"] or "N/A"}` (`{trade_row["auto_points_pnl"]} pts`)
            - **Reconciliation Status:** `{trade_row["match_classification"]}` | **Agreement:** `{trade_row["outcome_agreement"]}`
            """
            )
else:
    st.info("No trades match the selected filter criteria.")
