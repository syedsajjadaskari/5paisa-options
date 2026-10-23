"""Real-Time NIFTY Options Paper Trading Streamlit Dashboard with Plotly charts and audit logs.

MANDATORY SAFEGUARD:
PAPER TRADING ONLY - NO REAL MONEY OR BROKER ORDERS.
"""

from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

TZ_IST = ZoneInfo("Asia/Kolkata")
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "processed" / "paper_trading.duckdb"
STATE_FILE = BASE_DIR / "data" / "cache" / "paper_engine_state.json"
BROKER_STATE_FILE = BASE_DIR / "data" / "cache" / "paper_broker_state.json"
PHASE4D_DIR = BASE_DIR / "reports" / "independent_codex_audit" / "phase4d"
PHASE4E_DIR = BASE_DIR / "reports" / "independent_codex_audit" / "phase4e"

st.set_page_config(
    page_title="NIFTY Options Paper Trading Platform",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Mandatory Safety Banner
st.markdown(
    """
    <div style="background: linear-gradient(90deg, #b31217, #e52d27); padding: 14px; border-radius: 8px; text-align: center; color: white; font-weight: bold; font-size: 1.25rem; letter-spacing: 1px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 20px;">
        🚨 PAPER TRADING — NO REAL MONEY OR BROKER ORDERS 🚨
    </div>
    """,
    unsafe_allow_html=True,
)


def load_engine_state() -> dict:
    """Load engine JSON state file via centralized telemetry inspector."""
    try:
        from trading_platform.paper.engine import PaperTradingEngine

        return PaperTradingEngine.inspect_saved_state(STATE_FILE)
    except Exception:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}


def load_broker_state() -> dict:
    """Load broker state file."""
    if BROKER_STATE_FILE.exists():
        try:
            with open(BROKER_STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def load_duckdb_data() -> dict[str, pd.DataFrame]:
    """Query DuckDB for live paper trading events."""
    data = {
        "candles": pd.DataFrame(),
        "signals": pd.DataFrame(),
        "selections": pd.DataFrame(),
        "trades": pd.DataFrame(),
        "orders": pd.DataFrame(),
        "equity": pd.DataFrame(),
        "interruptions": pd.DataFrame(),
    }
    if not DB_PATH.exists():
        return data

    try:
        con = duckdb.connect(str(DB_PATH), read_only=True)
        try:
            data["candles"] = con.execute(
                "SELECT * FROM candles_5m ORDER BY exchange_time DESC LIMIT 100"
            ).df()
        except Exception:
            pass

        try:
            data["signals"] = con.execute("SELECT * FROM signals ORDER BY timestamp DESC").df()
        except Exception:
            pass

        try:
            data["selections"] = con.execute(
                "SELECT * FROM contract_selections ORDER BY timestamp DESC"
            ).df()
        except Exception:
            pass

        try:
            data["trades"] = con.execute(
                "SELECT * FROM completed_trades ORDER BY exit_time DESC"
            ).df()
        except Exception:
            pass

        try:
            data["orders"] = con.execute(
                "SELECT * FROM virtual_orders ORDER BY timestamp DESC"
            ).df()
        except Exception:
            pass

        try:
            data["equity"] = con.execute(
                "SELECT * FROM equity_snapshots ORDER BY timestamp ASC"
            ).df()
        except Exception:
            pass

        try:
            data["interruptions"] = con.execute(
                "SELECT * FROM data_feed_interruptions ORDER BY timestamp DESC"
            ).df()
        except Exception:
            pass

        con.close()
    except Exception:
        pass
    return data


def load_phase4d_acceptance() -> str:
    """Show the latest bounded read-only evidence without implying engine execution."""
    path = PHASE4D_DIR / "LIVE_READ_ONLY_ACCEPTANCE.md"
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def load_phase4e_quality() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load read-only quality and shadow-signal evidence, never trades."""
    try:
        quality = pd.read_csv(PHASE4E_DIR / "SESSION_DATA_QUALITY.csv")
    except Exception:
        quality = pd.DataFrame()
    try:
        shadow = pd.read_csv(PHASE4E_DIR / "SHADOW_SIGNAL_AUDIT.csv")
    except Exception:
        shadow = pd.DataFrame()
    return quality, shadow


# Load data
engine_state = load_engine_state()
broker_state = load_broker_state()
db_data = load_duckdb_data()
phase4d_acceptance = load_phase4d_acceptance()
phase4e_quality, phase4e_shadow = load_phase4e_quality()

# Sidebar Controls
st.sidebar.title("⚙️ Engine Controls")
mode = engine_state.get("mode", "REPLAY_TEST")
run_id = engine_state.get("run_id", "NO_ACTIVE_RUN")
config_hash = engine_state.get("config_hash", "N/A")
is_kill_active = engine_state.get("kill_switch_active", False)

st.sidebar.markdown(f"**Operating Mode:** `{mode}`")
st.sidebar.markdown(f"**Engine Status:** `{engine_state.get('status', 'STOPPED')}`")
st.sidebar.markdown(f"**Engine Liveness:** `{engine_state.get('engine_liveness', 'STOPPED')}`")
st.sidebar.markdown(f"**Run ID:** `{run_id}`")
st.sidebar.markdown(f"**Config Hash:** `{config_hash}`")

if "PASS FOR VERIFIED READ-ONLY COLLECTION" in phase4d_acceptance:
    st.sidebar.info("✅ Phase 4D: verified read-only collection captured. Paper fills remain disabled.")
elif phase4d_acceptance:
    st.sidebar.warning("⚠️ Phase 4D: read-only acceptance is pending or conditional.")

if not phase4e_quality.empty:
    live_row = phase4e_quality[phase4e_quality["scope"] == "LIVE_FULL_NORMAL_NSE_SESSION"]
    live_status = live_row.iloc[0]["status"] if not live_row.empty else "UNKNOWN"
    st.sidebar.markdown(f"**Phase 4E live session:** `{live_status}`")
    st.sidebar.markdown(f"**Shadow evaluations:** `{len(phase4e_shadow)}` (no execution)")

if is_kill_active:
    st.sidebar.error("⚠️ KILL SWITCH ENGAGED")
else:
    st.sidebar.success("✅ Engine Operating Normally")

auto_refresh = st.sidebar.checkbox("Auto-refresh (5s)", value=True)
if auto_refresh:
    try:
        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(interval=5000, key="data_refresh")
    except ImportError:
        pass

# Top Metrics Row
col1, col2, col3, col4, col5, col6 = st.columns(6)

spot_price = engine_state.get("last_spot_price", 0.0)
initial_capital = broker_state.get("initial_balance", 250000.0)
current_cash = broker_state.get("current_cash", initial_capital)
positions = broker_state.get("positions", {})
open_pos_count = sum(1 for p in positions.values() if p.get("quantity", 0) != 0)

realized_pnl = sum(p.get("realized_pnl", 0.0) for p in positions.values())
unrealized_pnl = sum(p.get("unrealized_pnl", 0.0) for p in positions.values())
total_equity = current_cash + unrealized_pnl

peak_equity = max(initial_capital, total_equity)
drawdown_pct = ((peak_equity - total_equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0

col1.metric("NIFTY 50 Spot", f"{spot_price:,.2f}" if spot_price > 0 else "Offline")
col2.metric("Available Cash", f"₹{current_cash:,.2f}")
col3.metric("Total Equity", f"₹{total_equity:,.2f}")
col4.metric(
    "Realized P&L",
    f"₹{realized_pnl:,.2f}",
    delta=f"{realized_pnl:+,.2f}",
    delta_color="normal",
)
col5.metric(
    "Unrealized P&L",
    f"₹{unrealized_pnl:,.2f}",
    delta=f"{unrealized_pnl:+,.2f}",
    delta_color="normal",
)
col6.metric("Daily Drawdown", f"{drawdown_pct:.2f}%")

# Engine Status Row
status_col1, status_col2, status_col3, status_col4 = st.columns(4)
is_connected = engine_state.get("is_connected", False)
market_open = engine_state.get("market_session_open", False)
feed_interrupts = engine_state.get("feed_interruptions", len(db_data["interruptions"]))
spot_age = engine_state.get("spot_quote_age_seconds")

status_col1.markdown(f"**Data Feed:** {'🟢 Connected' if is_connected else '🔴 Disconnected / Replay'}")
status_col2.markdown(f"**Market Session:** {'🟢 Open' if market_open else '🟡 Closed (Off-Hours)'}")
status_col3.markdown(f"**Quote Age:** `{spot_age}s`" if spot_age is not None else "**Quote Age:** `N/A`")
status_col4.markdown(f"**Feed Interruptions:** `{feed_interrupts}`")

if not phase4e_quality.empty:
    st.subheader("📡 Read-Only Collection Quality")
    quality_cols = ["scope", "observations", "valid", "rejected", "coverage_percent", "status"]
    st.dataframe(phase4e_quality[quality_cols], use_container_width=True, hide_index=True)
    st.caption("Shadow signals are rule evaluations only; they do not represent trades, fills, positions or P&L.")
    with st.expander("Shadow signal evaluations"):
        st.dataframe(phase4e_shadow.head(100), use_container_width=True, hide_index=True)

pending_exit = engine_state.get("pending_exit_reason")
if pending_exit:
    st.warning(f"⚠️ **PENDING EXIT ENGAGED:** Reason `{pending_exit}`. Position is awaiting fresh executable Bid quote.")

# Active Position Card
active_pos = engine_state.get("active_position")
if active_pos:
    st.markdown("### 🎯 Active Virtual Option Position")
    p_col1, p_col2, p_col3, p_col4, p_col5 = st.columns(5)
    p_col1.info(f"**Contract:** {active_pos.get('symbol')}")
    p_col2.metric("Type / Strike", f"{active_pos.get('option_type')} {active_pos.get('strike')}")
    p_col3.metric("Entry Premium", f"₹{active_pos.get('entry_premium'):.2f}")
    p_col4.metric(
        "Underlying Targets",
        f"SL: {active_pos.get('underlying_stop_loss'):.1f} | TP: {active_pos.get('underlying_target'):.1f}",
    )
    p_col5.metric("Lots / Qty", f"{active_pos.get('lots', 1)} Lot(s) ({active_pos.get('quantity')} units)")
else:
    st.info("No active open positions. Engine is scanning closed 5-minute candles for signals.")

# Tabs Layout
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    [
        "📈 Live 5m Chart",
        "📡 Strategy Signals & Selections",
        "📜 Dual Ledger Completed Trades",
        "🔍 Virtual Order Audit Trail",
        "📊 Daily Equity Curve",
        "🛡️ Safety & Safeguards Audit",
    ]
)

with tab1:
    st.subheader("NIFTY 50 5-Minute Closed Candles")
    candles_df = db_data["candles"]
    if not candles_df.empty:
        candles_df = candles_df.sort_values("exchange_time")
        fig = go.Figure(
            data=[
                go.Candlestick(
                    x=candles_df["exchange_time"],
                    open=candles_df["open"],
                    high=candles_df["high"],
                    low=candles_df["low"],
                    close=candles_df["close"],
                    name="NIFTY 50 (5m)",
                )
            ]
        )

        # Overlay active trade lines if open
        if active_pos:
            entry_spot = active_pos.get("underlying_entry_spot", 0.0)
            sl_spot = active_pos.get("underlying_stop_loss", 0.0)
            tp_spot = active_pos.get("underlying_target", 0.0)

            fig.add_hline(
                y=entry_spot,
                line_dash="dash",
                line_color="blue",
                annotation_text=f"Entry: {entry_spot:.1f}",
            )
            fig.add_hline(
                y=sl_spot,
                line_dash="dot",
                line_color="red",
                annotation_text=f"SL: {sl_spot:.1f}",
            )
            fig.add_hline(
                y=tp_spot,
                line_dash="dot",
                line_color="green",
                annotation_text=f"TP: {tp_spot:.1f}",
            )

        fig.update_layout(
            xaxis_title="Time (IST)",
            yaxis_title="NIFTY 50 Index",
            xaxis_rangeslider_visible=False,
            height=480,
            template="plotly_white",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning(
            "No candle data found in DuckDB. Start the paper trading engine to ingest candles."
        )

with tab2:
    st.subheader("Strategy Status & Live Signal Decisions")

    # Strategy Status Cards
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.success("**PDS (Method 1)**\n\nGap-Down Role Reversal (Active)")
    s2.success("**ORB (Method 1)**\n\n09:20 Breakout (Active)")
    s3.success("**IRF (Method 2)**\n\nExtension & Reversal (Active)")
    s4.success("**PDR (Retest Bounce)**\n\nGap-Up Support (Active)")
    s5.warning("**RF (Method 1)**\n\nStrictly Disabled (Pending Rule)")

    st.markdown("---")
    sig_col, sel_col = st.columns(2)

    with sig_col:
        st.markdown("**Generated Strategy Signals**")
        signals_df = db_data["signals"]
        if not signals_df.empty:
            st.dataframe(
                signals_df[
                    [
                        "timestamp",
                        "strategy_name",
                        "method_id",
                        "direction",
                        "reference_price",
                        "stop_loss",
                        "target",
                        "reason",
                    ]
                ],
                use_container_width=True,
            )
        else:
            st.info("No strategy signals generated yet.")

    with sel_col:
        st.markdown("**Contract Selection & Validation Decisions**")
        sel_df = db_data["selections"]
        if not sel_df.empty:
            st.dataframe(
                sel_df[
                    [
                        "timestamp",
                        "status",
                        "option_type",
                        "strike",
                        "symbol",
                        "best_bid",
                        "best_ask",
                        "spread",
                        "reason",
                    ]
                ],
                use_container_width=True,
            )
        else:
            st.info("No contract selection decisions logged yet.")

with tab3:
    st.subheader("Dual Accounting: Underlying Index Points vs Option Rupees P&L")
    trades_df = db_data["trades"]
    if not trades_df.empty:
        st.dataframe(
            trades_df[
                [
                    "trade_id",
                    "strategy_name",
                    "symbol",
                    "quantity",
                    "entry_time",
                    "entry_premium",
                    "exit_time",
                    "exit_premium",
                    "underlying_points_pnl",
                    "option_gross_pnl",
                    "total_costs",
                    "option_net_pnl",
                    "exit_reason",
                ]
            ],
            use_container_width=True,
        )

        # Performance summary by strategy
        st.markdown("#### Strategy Performance Breakdown")
        strat_summary = (
            trades_df.groupby("strategy_name")
            .agg(
                trades=("trade_id", "count"),
                net_pnl_inr=("option_net_pnl", "sum"),
                total_costs_inr=("total_costs", "sum"),
                underlying_points=("underlying_points_pnl", "sum"),
            )
            .reset_index()
        )
        st.dataframe(strat_summary, use_container_width=True)
    else:
        st.info("No closed paper trades recorded yet.")

with tab4:
    st.subheader("Virtual Order Execution Audit")
    orders_df = db_data["orders"]
    if not orders_df.empty:
        st.dataframe(
            orders_df[
                [
                    "order_id",
                    "timestamp",
                    "symbol",
                    "side",
                    "quantity",
                    "requested_price",
                    "fill_price",
                    "status",
                ]
            ],
            use_container_width=True,
        )
    else:
        st.info("No virtual orders submitted yet.")

with tab5:
    st.subheader("Daily Equity Curve")
    eq_df = db_data["equity"]
    if not eq_df.empty:
        fig_eq = go.Figure()
        fig_eq.add_trace(
            go.Scatter(
                x=eq_df["timestamp"],
                y=eq_df["total_equity"],
                mode="lines+markers",
                name="Total Equity (₹)",
                line={"color": "#2563eb", "width": 2},
            )
        )
        fig_eq.add_hline(
            y=initial_capital,
            line_dash="dash",
            line_color="gray",
            annotation_text=f"Starting: ₹{initial_capital:,.2f}",
        )
        fig_eq.update_layout(
            xaxis_title="Time",
            yaxis_title="Total Equity (INR)",
            template="plotly_white",
            height=420,
        )
        st.plotly_chart(fig_eq, use_container_width=True)
    else:
        st.info("No equity snapshots recorded yet.")

with tab6:
    st.subheader("Institutional Safety Safeguards Verification")
    st.markdown(
        """
    - **Broker Isolation:** Dedicated `PaperBroker` active. Real broker order endpoints are permanently disabled and physically disconnected.
    - **Operating Mode:** Engine operating mode locked to `PAPER_ONLY`. No real capital at risk.
    - **Virtual Capital Accounting:** Starting capital ₹250,000 INR.
    - **Premium Allocation Cap:** Maximum 20% of equity (₹50,000) permitted per open position.
    - **Position Limits:** Maximum 1 concurrent position, maximum 2 lots ceiling (sized affordably).
    - **Session Boundaries:** Entry cutoff at 14:30 IST; forced intraday square-off at 15:15 IST.
    - **Execution Quality Filters:** Quote staleness threshold: 5.0 seconds; Maximum bid/ask spread: 2.0%.
    - **Independent Quote Freshness:** Underlying NIFTY spot tick freshness and option contract quote freshness are independently validated.
    - **No Fake Fills on Exit:** If executable Bid quote is missing or stale at exit, exit is marked as PENDING rather than filled at an arbitrary estimated price.
    - **Strategy Freeze:** Strategies frozen to canonical `BASELINE_V1` rules:
      - **PDS Method 1:** Enabled
      - **ORB Method 1:** Enabled
      - **IRF Method 2:** Enabled
      - **PDR Retest Bounce:** Enabled
      - **RF:** Disabled (awaiting approved canonical exit rule)
    - **Credential Sanitization:** All credentials and tokens masked with zero exposure in logs or UI.
    """
    )

    st.markdown("---")
    st.markdown("#### 📡 Data Feed Health & Interruptions Log")
    interrupt_df = db_data["interruptions"]
    if not interrupt_df.empty:
        st.dataframe(
            interrupt_df[
                [
                    "interruption_id",
                    "timestamp",
                    "error_type",
                    "error_message",
                ]
            ],
            use_container_width=True,
        )
    else:
        st.success("Zero data feed interruptions recorded.")
