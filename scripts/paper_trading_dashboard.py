"""NIFTY options VIRTUAL paper-trading dashboard (Streamlit, read-only).

Run (from the repository root, bound to localhost only):

    streamlit run scripts/paper_trading_dashboard.py --server.address 127.0.0.1

``.streamlit/config.toml`` sets the same address (and headless mode), so the plain
``streamlit run scripts/paper_trading_dashboard.py`` is also localhost-only when launched from
the repository root.  Do not expose the page on a network: it shows your virtual account.

PAPER TRADING ONLY - NO REAL MONEY OR BROKER ORDERS.

This page is a pure READER.  It never starts a trading process and imports no broker/order code.
It reads ``$PAPER_RUNTIME_DIR`` (default ``./runtime``; set it when the app was started with a
custom ``--runtime-dir``, e.g. ``PAPER_RUNTIME_DIR=/path/to/dir streamlit run ...``):

* ``live_state.json``                 snapshot written ~1 Hz by the trading process
* ``journal/trades.csv``              closed virtual trades (authoritative P&L ledger)
* ``journal/events_YYYYMMDD.jsonl``   compact decision events
* ``replay/...``                      REPLAY (simulated) output, shown only when selected

plus the verified historical datasets (data/processed/nifty) and backtest outputs
(reports/backtest) for historical-only views.  Start the trading app separately:
``python scripts/run_paper_trading.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import streamlit as st  # noqa: E402

from trading_platform.dashboard import data, views  # noqa: E402

st.set_page_config(
    page_title="NIFTY Options Paper Trading",
    page_icon=":material/query_stats:",
    layout="wide",
    initial_sidebar_state="expanded",
)

RUNTIME_DIR = data.resolve_runtime_dir()
REFRESH_CHOICES = {"1 s": 1, "2 s": 2, "5 s": 5, "10 s": 10, "30 s": 30}


def _default_source() -> str:
    """Open on the replay when only a replay has run (no LIVE snapshot, a replay one exists)."""
    live = RUNTIME_DIR / "live_state.json"
    replay = RUNTIME_DIR / "replay" / "live_state.json"
    return data.SOURCE_REPLAY if (not live.exists() and replay.exists()) else data.SOURCE_LIVE


def sidebar() -> dict:
    st.session_state.setdefault("source", _default_source())
    st.sidebar.title("Paper trading")
    st.sidebar.caption("Read-only viewer. Starting/stopping the engine is done in the terminal.")
    source = st.sidebar.radio("Source", list(data.SOURCES), key="source",
                              help="Replay is SIMULATED and kept strictly separate from live paper.")  # fmt: skip
    strategies = st.sidebar.multiselect("Strategies", views.STRATEGY_NAMES,
                                        default=views.STRATEGY_NAMES, key="strategies")  # fmt: skip
    st.sidebar.markdown("---")
    auto = st.sidebar.toggle("Auto-refresh", value=True, key="auto_refresh")
    label = st.sidebar.select_slider("Refresh interval", list(REFRESH_CHOICES), value="2 s",
                                     key="refresh_interval", disabled=not auto)  # fmt: skip
    st.sidebar.caption(f"Runtime dir: `{RUNTIME_DIR}` (override: `PAPER_RUNTIME_DIR`)")
    return {
        "source": source,
        "strategies": strategies or views.STRATEGY_NAMES,
        "interval": REFRESH_CHOICES[label] if auto else None,
    }


def body(source: str, strategies: list[str]) -> None:
    """Everything that refreshes: re-reads the runtime files on every run."""
    bundle = data.load_source(RUNTIME_DIR, source)
    hist5 = views.load_hist(data.historical_5m_path())
    hist1d = views.load_hist(data.historical_1d_path())

    views.render_banners(bundle)
    views.render_market_overview(bundle)
    st.divider()

    days = views.day_options(bundle, hist5)
    day_labels: list = ["Current session", *days]
    choice = st.selectbox(
        "Chart / explanation date", day_labels, key="chart_day",
        format_func=lambda v: v if isinstance(v, str) else f"{v:%a %d %b %Y}",
    )  # fmt: skip

    t_chart, t_active, t_strat, t_hist, t_perf, t_explain = st.tabs(
        ["Trading chart", "Active trade", "Strategy monitor", "Trade history",
         "Performance", "Trade explanation"]
    )  # fmt: skip
    with t_chart:
        views.render_chart_tab(bundle, choice, strategies, hist5, hist1d)
    with t_active:
        views.render_active_trade(bundle)
    with t_strat:
        views.render_strategy_monitor(bundle)
    with t_hist:
        views.render_trade_history(bundle, strategies)
    with t_perf:
        views.render_performance(bundle, strategies)
    with t_explain:
        views.render_trade_explanation(bundle, strategies, hist5, hist1d)


def main() -> None:
    st.title("NIFTY options paper trading")
    cfg = sidebar()
    st.fragment(run_every=cfg["interval"])(body)(cfg["source"], cfg["strategies"])


main()
