"""Streamlit rendering of each dashboard section (reader only - no trading, no broker code).

Pure helpers (``compute_banners``, ``assemble_chart``, ``trade_table``, ``fmt_*``) hold the
decisions so they can be unit tested without a Streamlit runtime; the ``render_*`` functions only
lay things out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from trading_platform.dashboard import charts, data, explain, metrics
from trading_platform.dashboard import text as dash_text
from trading_platform.live import labels

STRATEGY_NAMES = ["PDS", "ORB", "IRF", "PDR"]
PAPER_BANNER = "PAPER TRADING ONLY - virtual fills on read-only data. No real orders are ever placed."

REPLAY_DIR_NAME = "replay"


def tier_caption(tier: str, auth_label: str | None = None, *, replay: bool = False) -> str:
    """Caption for a strategy's tier.  A replay is never described as an 'explicit opt-in'."""
    if not tier or tier == "n/a":
        return "Execution tier not reported."
    text = labels.tier_label(tier, auth_label, replay=replay)
    if replay:
        return f"{text} - simulated fills on PROVISIONAL rules inside a replay."
    return {
        "DISABLED": f"{text} - never evaluated, never fills.",
        "AUTHOR_APPROVED": f"{text} - every rule confirmed by the strategy author.",
    }.get(tier.upper(), text)


NR = explain.NOT_RECORDED


# --------------------------------------------------------------------------- formatting


def fmt_inr(value: Any, signed: bool = False) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if v != v:
        return "n/a"
    sign = "+" if signed and v > 0 else ("-" if v < 0 else "")
    return f"{sign}₹{abs(v):,.2f}"


def fmt_pts(value: Any, signed: bool = True) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if v != v:
        return "n/a"
    return f"{v:+,.2f} pts" if signed else f"{v:,.2f} pts"


def fmt_num(value: Any, digits: int = 2) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if v != v else f"{v:,.{digits}f}"


def fmt_pf(value: float | None) -> str:
    if value is None:
        return "n/a"
    return "inf (no losses)" if value == float("inf") else f"{value:.2f}"


def fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def fmt_dt(value: Any, fmt: str = "%d %b %H:%M:%S") -> str:
    ts = data.parse_ts(value)
    return ts.strftime(fmt) if ts else "n/a"


def _get(d: Any, *keys: str, default: Any = None) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


# --------------------------------------------------------------------------- banners (pure)


def _is_simulated_connection(bundle: data.SourceBundle, snap: dict[str, Any] | None) -> bool:
    conn = (snap or {}).get("connection") or {}
    broker = str(conn.get("broker") or "").upper()
    return (bundle.is_replay or str(conn.get("status") or "").upper() == "SIMULATED"
            or "SIMULATED" in broker)  # fmt: skip


def unmonitored_position(bundle: data.SourceBundle, snap: dict[str, Any] | None) -> dict[str, Any] | None:
    """The LIVE virtual position that nobody is monitoring (stale / stopped snapshot), if any."""
    if bundle.is_replay or snap is None or bundle.snapshot.active:
        return None
    pos = snap.get("position")
    if isinstance(pos, dict) and str(pos.get("status") or "").upper() in {"OPEN", "EXIT_PENDING"}:
        return pos
    return None


def replay_available(bundle: data.SourceBundle) -> bool:
    """True when a replay snapshot exists next to the LIVE runtime (a hint for the user)."""
    if bundle.is_replay:
        return False
    return (bundle.root / REPLAY_DIR_NAME / "live_state.json").exists()


def compute_banners(bundle: data.SourceBundle) -> list[tuple[str, str]]:
    """Ordered ``(level, text)`` banners; level in {error, warning, info}."""
    out: list[tuple[str, str]] = []
    info = bundle.snapshot
    snap = data.effective_snapshot(info)
    orphan = unmonitored_position(bundle, snap)
    if orphan is not None:
        out.append(("error", f"Engine not updating - virtual position {orphan.get('trade_id', '?')} is "
                    f"{str(orphan.get('status')).upper()} and NOT monitored (no stop/target). "
                    "Restart the app to recover."))  # fmt: skip
    if bundle.is_replay:
        out.append(("warning", "REPLAY SIMULATED - historical data replayed through the engine. "
                    "Nothing here is live market data or live paper performance."))  # fmt: skip
    if bundle.excluded_rows:
        out.append(("warning", f"{bundle.excluded_rows} ledger row(s) labelled REPLAY/SIMULATED "
                    "were excluded from the LIVE view."))  # fmt: skip
    if info.status == data.SNAPSHOT_WRONG_MODE:
        out.append(("warning", f"No active session - {info.message}. It is not shown as {bundle.source}."))
    elif info.finished_replay:
        out.append(("info", "REPLAY FINISHED - the simulated session has ended; values are its final "
                    f"state (simulated time {fmt_dt(info.sim_time)})."))  # fmt: skip
    elif not info.active:
        age = f" Last snapshot age: {info.age_text()}." if info.age_seconds is not None else ""
        why = {
            data.SNAPSHOT_MISSING: "no snapshot file found",
            data.SNAPSHOT_CORRUPT: "the snapshot file is unreadable",
            data.SNAPSHOT_STALE: "the snapshot is stale (trading process not updating it)"
            if not bundle.is_replay else "the replay stopped updating (process ended or interrupted)",
            data.SNAPSHOT_STOPPED: "the session has stopped",
        }.get(info.status, info.message)
        out.append(("info", f"No active session - {why}.{age}"))
    if replay_available(bundle) and not info.active:
        out.append(("info", "A replay run (SIMULATED) exists in runtime/replay - choose Source: "
                    f"'{data.SOURCE_REPLAY}' in the sidebar to view it."))  # fmt: skip
    if snap is None:
        return out
    session = snap.get("session") or {}
    state = str(session.get("engine_state") or "")
    reason = str(session.get("state_reason") or "").strip()
    suffix = f": {reason}" if reason else ""
    if state == "BLOCKED":
        out.append(("error", f"BLOCKED{suffix}. No new entries (fail-closed)."))
    elif state == "RECOVERY_REQUIRED":
        out.append(("error", f"RECOVERY_REQUIRED{suffix}. Uncertain restart - no new entries until resolved."))
    elif state == "HALTED_RISK":
        out.append(("warning", f"HALTED_RISK{suffix}. Risk limit reached - no new entries."))
    pos = snap.get("position")
    if isinstance(pos, dict) and str(pos.get("status")) == "EXIT_PENDING":
        out.append(("error", "EXIT_PENDING - exit triggered but no valid bid yet; "
                    "the position stays open and new entries are blocked."))  # fmt: skip
    shadow = [
        str(s.get("name")) for s in snap.get("strategies") or []
        if isinstance(s, dict) and str(s.get("tier")) == "SHADOW_ONLY"
    ]  # fmt: skip
    if shadow:
        out.append(("info", f"SHADOW_ONLY ({', '.join(shadow)}): signals logged, no virtual fills."))
    conn = snap.get("connection") or {}
    conn_status = str(conn.get("status", "CONNECTED")).upper()
    if info.active and (conn.get("stale") or conn_status not in {"CONNECTED", "SIMULATED"}):
        out.append(("warning", f"Data connection {conn.get('status', 'unknown')}"
                    + (" - quotes are stale." if conn.get("stale") else ".")))  # fmt: skip
    if info.active and conn.get("candle_trusted") is False:
        out.append(("warning", "Candle feed not trusted - strategies are not evaluating on this feed."))
    return out


# --------------------------------------------------------------------------- chart assembly (pure)


@dataclass
class ChartInputs:
    candles: pd.DataFrame
    levels: dict[str, Any]
    markers: list[dict[str, Any]]
    caption: str
    day: date | None = None
    from_snapshot: bool = False
    trades_on_day: pd.DataFrame = field(default_factory=pd.DataFrame)


def trade_levels(trade: dict[str, Any] | None) -> dict[str, Any]:
    """Levels recorded with one trade (``levels_json``), under the chart's level names.

    For the ACTIVE position (no ledger row yet) the monitored strategy's ``levels`` carried in
    the snapshot are used instead, if the caller put them in ``trade['levels']``."""
    if not trade:
        return {}
    parsed = explain.parse_json_field(trade.get("levels_json"))
    if not isinstance(parsed, dict):
        parsed = trade.get("levels") if isinstance(trade.get("levels"), dict) else None
    if not isinstance(parsed, dict):
        return {}
    return dash_text.normalize_levels(parsed, str(trade.get("strategy") or ""))


def _recorded_levels(trades_day: pd.DataFrame) -> dict[str, Any]:
    """Levels recorded in the day's trades (levels_json) - first recorded value wins."""
    merged: dict[str, Any] = {}
    if trades_day is None or trades_day.empty:
        return merged
    for row in trades_day.to_dict("records"):
        for k, v in trade_levels(row).items():
            merged.setdefault(k, v)
    return merged


def assemble_chart(
    bundle: data.SourceBundle,
    day: date | None,
    strategies: list[str],
    hist5: pd.DataFrame,
    hist1d: pd.DataFrame,
) -> ChartInputs:
    """Choose validated candles, levels and markers for one session date.

    * Snapshot candles are used when the snapshot belongs to ``day`` (the only source of a live
      session's candles); otherwise verified historical candles for ``day``.
    * Marker/trade overlays are included only for dates that actually have candles.
    * Nothing is back-filled: a restart shows only the candles present in the snapshot.
    """
    snap = data.effective_snapshot(bundle.snapshot)
    snap_day = data.parse_ts(_get(snap, "session", "date"))
    snap_date = snap_day.date() if snap_day else None
    if day is None:
        day = snap_date
    trades = bundle.trades.df
    in_sel = trades[trades["strategy"].isin(strategies)] if not trades.empty else trades
    day_trades = in_sel[in_sel["session_date"] == day] if (day and not in_sel.empty) else in_sel.iloc[0:0]

    from_snapshot = bool(snap is not None and snap_date == day)
    snap_candles = data.snapshot_candles(snap) if from_snapshot else pd.DataFrame()
    levels: dict[str, Any] = {}
    markers: list[dict[str, Any]] = []
    if from_snapshot and not snap_candles.empty:
        candles = snap_candles
        levels = dict(snap.get("levels") or {})
        markers += data.snapshot_markers(snap)
        label = "snapshot" + (" (stale)" if not bundle.snapshot.active else "")
        srcs = ", ".join(sorted(set(candles["source"])))
        caption = f"Candles: {label}, source {srcs} - only candles present in the snapshot are drawn."
    else:
        candles = data.candles_for_date(hist5, day) if day else data.candles_for_date(hist5, date.min)
        if not candles.empty:
            levels = dict(data.previous_day_levels(hist1d, day))
            levels.pop("prev_date", None)
            for k, v in _recorded_levels(day_trades).items():  # recorded zones / strategy levels
                levels.setdefault(k, v)
            caption = ("Candles: verified historical dataset (HISTORICAL_FILE). PDH/PDL/PDC from the "
                       "verified daily dataset; zones/other levels only if recorded with a trade.")
        else:
            caption = "No validated candles exist for this date - nothing is drawn."
    if not candles.empty:
        # ledger markers first: merge_markers keeps the first and enriches it from the rest
        markers = charts.trade_markers(day_trades, candles) + markers
        markers += data.signal_markers_from_events(data.load_events(bundle.events_dir, day)) if day else []
        markers = [m for m in markers if not m.get("strategy") or m["strategy"] in strategies]
        markers = charts.merge_markers(markers)
    else:
        markers = []
    return ChartInputs(candles, levels, markers, caption, day, from_snapshot and not snap_candles.empty,
                       day_trades)  # fmt: skip


def highlight_levels(
    levels: dict[str, Any], trade: dict[str, Any] | None, snap: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """(levels to draw, names that must always be drawn) for the highlighted trade.

    The levels recorded for the trade (or, for the active position, the monitoring strategy's
    levels in the snapshot) override the day's levels and are forced onto the chart even when
    they are far from the visible candles."""
    if not trade:
        return levels, []
    own = trade_levels(trade)
    if not own:
        for row in (snap or {}).get("strategies") or []:
            if isinstance(row, dict) and row.get("name") == trade.get("strategy") and row.get("levels"):
                own = trade_levels({"strategy": row["name"], "levels": row["levels"]})
                break
    return {**levels, **own}, list(own)


def active_trade_for_chart(snap: dict[str, Any] | None) -> dict[str, Any] | None:
    pos = (snap or {}).get("position")
    return dict(pos) if isinstance(pos, dict) else None


# --------------------------------------------------------------------------- trade table (pure)

HISTORY_COLUMNS = [
    "trade_id", "session_date", "strategy", "entry_time", "exit_time", "option_type", "strike",
    "quantity", "entry_premium", "exit_premium", "entry_reason", "exit_reason", "gross_pnl",
    "total_charges", "net_pnl",
]  # fmt: skip


def trade_table(
    trades: pd.DataFrame,
    strategies: list[str],
    date_range: tuple[date, date] | None = None,
) -> pd.DataFrame:
    """Filtered Trade History frame (newest first) with the user-spec columns.

    ``strategy`` shows the human label (``ORB M1``) and ``entry_reason`` a sentence; the raw rule
    id stays in the Trade explanation tab as a secondary detail."""
    if trades.empty:
        return trades.reindex(columns=HISTORY_COLUMNS)
    df = trades[trades["strategy"].isin(strategies)]
    if date_range:
        lo, hi = date_range
        df = df[df["session_date"].apply(lambda d: d is not None and lo <= d <= hi)]
    df = df.sort_values("exit_time", ascending=False, kind="stable").reset_index(drop=True)
    records = df.to_dict("records")
    out = df[HISTORY_COLUMNS].copy()
    out["strategy"] = [dash_text.strategy_label_of(r) for r in records]
    out["entry_reason"] = [dash_text.reason_sentence(r) for r in records]
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------- Market overview


def render_banners(bundle: data.SourceBundle) -> None:
    st.caption(PAPER_BANNER)
    for level, text in compute_banners(bundle):
        getattr(st, level)(text)
    for problem in bundle.trades.problems:
        st.warning(f"Ledger: {problem}")


def render_market_overview(bundle: data.SourceBundle) -> None:
    info = bundle.snapshot
    snap = data.effective_snapshot(info)
    st.subheader("Market overview")
    if snap is None:
        st.markdown("**No active session.** " + (f"Last snapshot age: {info.age_text()}."
                    if info.age_seconds is not None else "No snapshot has been written."))  # fmt: skip
        return
    if info.finished_replay:
        st.caption(f"REPLAY FINISHED - final simulated state at {fmt_dt(info.sim_time)}. Not live.")
    elif not info.active:
        st.caption(f"STALE - last known values from {fmt_dt(info.written_at)} ({info.age_text()} ago). "
                   "Not live.")  # fmt: skip
    session, conn, spot = (snap.get(k) or {} for k in ("session", "connection", "spot"))
    simulated = _is_simulated_connection(bundle, snap)
    price, prev = spot.get("price"), spot.get("prev_close")
    delta = None
    try:
        if price is not None and prev:
            delta = f"{float(price) - float(prev):+,.2f} vs prev close"
    except (TypeError, ValueError):
        delta = None
    reported_age = conn.get("data_age_seconds")
    age = None
    if reported_age is not None and simulated:
        age = float(reported_age)  # a replay's clock is simulated: no wall-clock add-on
    elif reported_age is not None and info.age_seconds is not None:
        age = float(reported_age) + max(info.age_seconds, 0.0)
    fresh = "n/a" if age is None else f"{data.format_age(age)} old" + (" - STALE" if conn.get("stale") else "")
    c = st.columns(5)
    c[0].metric("NIFTY 50", fmt_num(price), delta)
    c[1].metric("Market", str(session.get("market_status") or "n/a"))
    c[2].metric("Connection", "SIMULATED (no broker)" if simulated else str(conn.get("status") or "n/a"))
    c[3].metric("Simulated time" if simulated else "Data time",
                fmt_dt(snap.get("sim_time") if simulated and snap.get("sim_time") else spot.get("exchange_time"),
                       "%H:%M:%S"), fresh, delta_color="off")  # fmt: skip
    c[4].metric("Engine", str(session.get("engine_state") or "n/a"),
                str(session.get("mode") or ""), delta_color="off")  # fmt: skip
    cc = snap.get("current_candle")
    left, right = st.columns([3, 2])
    with left:
        if isinstance(cc, dict) and cc:
            auth = bool(cc.get("authoritative"))
            flag = "PROVIDER-COMPLETED" if auth else "SAMPLED / non-authoritative (forming)"
            st.markdown(
                f"**Current 5m candle** ({fmt_dt(cc.get('open_time'), '%H:%M')} open) - "
                f"O {fmt_num(cc.get('o'))}  H {fmt_num(cc.get('h'))}  "
                f"L {fmt_num(cc.get('l'))}  C {fmt_num(cc.get('c'))}"
            )
            st.caption(f"Source {cc.get('source', 'n/a')} - {flag}. Strategies evaluate only provider-completed candles.")
        else:
            st.markdown("**Current 5m candle:** not available")
    with right:
        quotes = snap.get("quotes") or {}
        for side in ("CE", "PE"):
            q = quotes.get(side)
            if isinstance(q, dict) and q:
                st.markdown(
                    f"**{side}** {q.get('strike', '')} - bid {fmt_num(q.get('bid'))} / "
                    f"ask {fmt_num(q.get('ask'))}"
                )
            else:
                st.markdown(f"**{side}:** no quote")
    notes = conn.get("notes") or []
    if notes:
        st.caption("Notes: " + "; ".join(str(n) for n in notes[:5]))
    ev = snap.get("last_event")
    if isinstance(ev, dict) and ev.get("text"):
        st.caption(f"Last event [{ev.get('kind', '')}] {fmt_dt(ev.get('t'), '%H:%M:%S')}: {ev.get('text')}")


# --------------------------------------------------------------------------- Trading chart


def _date_options(bundle: data.SourceBundle, hist5: pd.DataFrame) -> list[date]:
    days = set(data.historical_dates(hist5))
    if not bundle.trades.df.empty:
        days |= {d for d in bundle.trades.df["session_date"] if d is not None}
    snap = data.effective_snapshot(bundle.snapshot)
    sd = data.parse_ts(_get(snap, "session", "date"))
    if sd:
        days.add(sd.date())
    return sorted(days, reverse=True)[:400]


def resolve_chart_day(choice: str | date, bundle: data.SourceBundle) -> date | None:
    """'Current session' -> the snapshot session date (None when there is no snapshot)."""
    if isinstance(choice, date):
        return choice
    snap = data.effective_snapshot(bundle.snapshot)
    sd = data.parse_ts(_get(snap, "session", "date"))
    return sd.date() if sd else None


def render_chart_tab(bundle: data.SourceBundle, day_choice: str | date, strategies: list[str],
                     hist5: pd.DataFrame, hist1d: pd.DataFrame) -> None:  # fmt: skip
    st.subheader("Trading chart (NIFTY 5m)")
    day = resolve_chart_day(day_choice, bundle)
    snap = data.effective_snapshot(bundle.snapshot)
    ci = assemble_chart(bundle, day, strategies, hist5, hist1d)
    options: dict[str, dict[str, Any] | None] = {"None (levels only)": None}
    active = active_trade_for_chart(snap)
    if active and ci.from_snapshot:
        options[f"ACTIVE {active.get('trade_id', '')} {active.get('strategy', '')}"] = active
    for _, r in ci.trades_on_day.iterrows():
        options[f"{r['trade_id']} {r['strategy']} {fmt_dt(r['entry_time'], '%H:%M')}"] = r.to_dict()
    choice = st.selectbox("Highlight trade (stop / target lines)", list(options), index=min(1, len(options) - 1),
                          key="chart_trade")  # fmt: skip
    trade = options.get(choice)
    if trade and not ci.candles.empty and trade.get("trade_id") and "exit_time" in trade:
        # closed trade: show only this trade's entry/exit with annotations
        ci.markers = [m for m in ci.markers if m["kind"] == "SIGNAL" or m.get("trade_id") in (None, trade["trade_id"])]
    title = f"NIFTY 5m - {day:%d %b %Y}" if day else "NIFTY 5m"
    levels, force = highlight_levels(ci.levels, trade, snap)
    fig = charts.build_candlestick(ci.candles, levels=levels, markers=ci.markers, trade=trade, title=title,
                                   force_levels=force)  # fmt: skip
    st.plotly_chart(fig, width="stretch", key="chart_main")
    st.caption(ci.caption)
    meta = fig.layout.meta or {}
    if meta.get("off_chart_levels"):
        st.caption("Levels outside the visible range: " + ", ".join(meta["off_chart_levels"]))
    st.caption("Markers - diamond: signal | triangle up/down: entry (CE/PE) | exit shapes: star target, "
               "x stop, square end-of-day, diamond-x risk. A shaded band marks the trigger candle of the "
               "highlighted trade.")  # fmt: skip


# --------------------------------------------------------------------------- Active trade


def bid_view(pos: dict[str, Any], max_age: float = 5.0) -> tuple[str, bool]:
    """(text, stale) for the position's option bid.  Never shows an old bid as current."""
    bid = data._num(pos.get("current_bid"))
    if bid is None:
        return "n/a - no valid bid", True
    age = data._num(pos.get("current_bid_age_seconds"))
    flag = pos.get("bid_is_stale")
    stale = flag if isinstance(flag, bool) else (age is not None and age > max_age)
    if stale:
        return f"{bid:,.2f} (age {'n/a' if age is None else f'{age:.0f}s'} STALE)", True
    return f"{bid:,.2f}", False


def render_active_trade(bundle: data.SourceBundle) -> None:
    info = bundle.snapshot
    snap = data.effective_snapshot(info)
    st.subheader("Active trade")
    pos = (snap or {}).get("position")
    if not isinstance(pos, dict) or not pos:
        st.info("No open position.")
        pending = (snap or {}).get("pending_entry")
        if isinstance(pending, dict) and pending:
            st.warning(f"Entry pending: {pending.get('strategy', '?')} {pending.get('symbol', '')} - "
                       f"{pending.get('detail') or 'waiting for a fill'} (not filled yet; no position exists).")  # fmt: skip
        last = (snap or {}).get("last_closed_trade")
        if isinstance(last, dict) and last:
            st.caption(f"Last closed trade: {last.get('trade_id')} {dash_text.strategy_label_of(last)} "
                       f"{last.get('exit_reason')} net {fmt_inr(last.get('net_pnl'), True)}")  # fmt: skip
        return
    if unmonitored_position(bundle, snap) is not None:
        st.error(f"Engine not updating - this virtual position is {pos.get('status')} and NOT monitored "
                 "(no stop/target). Restart the app to recover.")  # fmt: skip
    elif info.finished_replay:
        st.caption("Replay finished - this is the final simulated state, not a live position.")
    elif not info.active:
        st.warning(f"Last known position from a stale snapshot ({info.age_text()} old). "
                   "Do not treat these numbers as live.")  # fmt: skip
    if str(pos.get("status")) == "EXIT_PENDING":
        st.error("EXIT_PENDING - exit triggered, waiting for a valid bid.")
    if "REPLAY" in str(pos.get("label", "")).upper():
        st.warning("REPLAY SIMULATED position.")
    bid_text, bid_stale = bid_view(pos)
    top = st.columns(4)
    top[0].metric("Strategy", dash_text.strategy_label_of(pos))
    top[1].metric("Contract", f"{pos.get('strike', 'n/a')} {pos.get('option_type', '')}")
    top[2].metric("Unrealized (gross)", fmt_inr(pos.get("gross_pnl"), True), fmt_pct(pos.get("pnl_pct")), delta_color="off")
    top[3].metric("P&L (net est.)", fmt_inr(pos.get("net_pnl_estimate"), True),
                  "stale bid" if bid_stale else None, delta_color="off")  # fmt: skip
    rid = dash_text.rule_id_of(pos)
    rows = {
        "Status": pos.get("status"),
        "Symbol": pos.get("symbol"),
        "Direction": dash_text.direction_text(pos),
        "Strike": pos.get("strike"),
        "Expiry": pos.get("expiry"),
        "Lots / quantity": f"{pos.get('lots', 'n/a')} / {pos.get('quantity', 'n/a')}",
        "Entry time": fmt_dt(pos.get("entry_time")),
        "Entry premium (ask + slippage)": fmt_num(pos.get("entry_premium")),
        "Entry bid / ask": f"{fmt_num(pos.get('entry_bid'))} / {fmt_num(pos.get('entry_ask'))}",
        "Current bid": bid_text,
        "Underlying at entry": fmt_num(pos.get("entry_spot")),
        "Stop (underlying)": fmt_num(pos.get("stop_spot")),
        "Target (underlying)": fmt_num(pos.get("target_spot")),
        "Distance to stop / target": f"{fmt_num(pos.get('dist_to_stop'))} / {fmt_num(pos.get('dist_to_target'))}",
        "Holding": data.format_age(pos.get("holding_seconds")),
        "Entry reason": dash_text.reason_sentence(pos),
        "Rule id (detail)": rid,
        "Label": pos.get("label") or NR,
    }
    warnings = pos.get("entry_warnings")
    if isinstance(warnings, list) and warnings:
        rows["Entry warnings"] = "; ".join(str(w) for w in warnings[:3])
    left = st.columns(2)
    items = [(k, v) for k, v in rows.items() if not (k == "Rule id (detail)" and not v)]
    half = (len(items) + 1) // 2
    for col, chunk in zip(left, (items[:half], items[half:]), strict=True):
        with col:
            for k, v in chunk:
                st.markdown(f"**{k}:** {v if v not in (None, '') else NR}")


# --------------------------------------------------------------------------- Strategy monitor


def render_strategy_monitor(bundle: data.SourceBundle) -> None:
    st.subheader("Strategy monitor")
    snap = data.effective_snapshot(bundle.snapshot)
    monitors = {str(s.get("name")): s for s in (snap or {}).get("strategies") or [] if isinstance(s, dict)}
    if bundle.snapshot.finished_replay:
        st.caption("Replay finished - showing the final simulated strategy state.")
    elif not bundle.snapshot.active:
        st.caption("No active session - showing last known strategy state." if monitors
                   else "No active session - no strategy state available.")  # fmt: skip
    for name in STRATEGY_NAMES:
        s = monitors.get(name, {})
        tier = str(s.get("tier") or "n/a")
        state = str(s.get("state") or "n/a")
        title = str(s.get("label") or name)
        with st.expander(f"{title} - {state} - tier {tier}", expanded=name in monitors):
            st.caption(tier_caption(tier, s.get("auth_label"), replay=bundle.is_replay))
            if not s:
                st.write("No status reported for this strategy.")
            c = st.columns(4)
            c[0].metric("Last evaluated candle", fmt_dt(s.get("last_evaluated_candle"), "%H:%M") if s else "n/a")
            c[1].metric("Trades today", s.get("trades_today", 0) if s else "n/a")
            c[2].metric("Strategy P&L today", fmt_inr(s.get("pnl_today")) if s else "n/a")
            c[3].metric("Latest signal", _signal_text(s.get("last_signal")))
            sig = s.get("last_signal")
            if isinstance(sig, dict) and sig:
                rid = dash_text.rule_id_of(sig)
                st.caption(f"Latest signal: {dash_text.reason_sentence(sig)}" + (f" (rule {rid})" if rid else ""))
            if s.get("last_rejection"):
                st.warning(f"Entry rejection: {s['last_rejection']}")
            sat, fail = s.get("conditions_satisfied") or [], s.get("conditions_failed") or []
            cc = st.columns(2)
            cc[0].markdown("**Conditions satisfied**\n\n" + ("\n".join(f"- {x}" for x in sat) or "- none"))
            cc[1].markdown("**Conditions not met (yet)**\n\n" + ("\n".join(f"- {x}" for x in fail) or "- none"))
            if s.get("levels"):
                st.markdown("**Levels**")
                st.json(s["levels"], expanded=False)
            if s.get("note"):
                st.caption(f"Note: {s['note']}")
            _render_disclosure(name)
    st.caption("RF is permanently disabled and is not evaluated.")


def _signal_text(sig: Any) -> str:
    if not isinstance(sig, dict) or not sig:
        return "none"
    direction = labels.direction_label(str(sig.get("direction") or "")) if sig.get("direction") else ""
    return f"{direction} {fmt_dt(sig.get('candle_open_time') or sig.get('t'), '%H:%M')}".strip()


def _render_disclosure(name: str) -> None:
    d = data.strategy_disclosure(name)
    with st.expander(f"{name} provisional assumptions (not author-approved)"):
        if not d["manifest"] and not d["rules"]:
            st.write("No provisional-assumption disclosure available for this strategy.")
            return
        if d["rules"]:
            st.markdown("**Provisional rules**\n\n" + "\n".join(f"- {r}" for r in d["rules"]))
        if d["manifest"]:
            st.dataframe(pd.DataFrame(d["manifest"]).astype(str), width="stretch", hide_index=True)


# --------------------------------------------------------------------------- Trade history


def render_trade_history(bundle: data.SourceBundle, strategies: list[str]) -> str | None:
    """Render the history table; returns the selected trade id (or None)."""
    st.subheader("Trade history")
    trades = bundle.trades.df
    if trades.empty:
        st.info("No closed trades in this ledger yet.")
        return None
    days = [d for d in trades["session_date"] if d is not None]
    rng = None
    if days:
        lo, hi = min(days), max(days)
        picked = st.date_input("Date range", value=(lo, hi), min_value=lo, max_value=hi, key="hist_range")
        if isinstance(picked, tuple | list) and len(picked) == 2:
            rng = (picked[0], picked[1])
    table = trade_table(trades, strategies, rng)
    if table.empty:
        st.info("No trades match the current filters.")
        return None
    cfg = {
        "session_date": st.column_config.DateColumn("Date"),
        "entry_time": st.column_config.DatetimeColumn("Entry time", format="HH:mm:ss"),
        "exit_time": st.column_config.DatetimeColumn("Exit time", format="HH:mm:ss"),
        "option_type": "CE/PE",
        "entry_premium": st.column_config.NumberColumn("Entry premium", format="%.2f"),
        "exit_premium": st.column_config.NumberColumn("Exit premium", format="%.2f"),
        "gross_pnl": st.column_config.NumberColumn("Gross P&L", format="%.2f"),
        "total_charges": st.column_config.NumberColumn("Charges", format="%.2f"),
        "net_pnl": st.column_config.NumberColumn("Net P&L", format="%.2f"),
    }
    event = st.dataframe(table, width="stretch", hide_index=True, column_config=cfg,
                         on_select="rerun", selection_mode="single-row", key="history_table")  # fmt: skip
    rows = (event.selection.rows if event is not None else []) or []
    selected = str(table.iloc[rows[0]]["trade_id"]) if rows else None
    if selected:
        st.session_state["selected_trade_id"] = selected
        st.caption(f"Selected {selected} - open the Trade explanation tab.")
    st.download_button("Download shown rows (CSV)", table.to_csv(index=False), "trades_view.csv", "text/csv")
    return selected


# --------------------------------------------------------------------------- Performance


def _paper_capital(bundle: data.SourceBundle) -> tuple[float | None, str]:
    snap = data.effective_snapshot(bundle.snapshot)
    cap = data._num(_get(snap, "account", "starting_capital"))
    if cap:
        return cap, "snapshot"
    df = bundle.trades.df
    if not df.empty and df["balance_after"].notna().any() and df["net_pnl"].notna().any():
        first = df.sort_values("exit_time").iloc[0]
        return float(first["balance_after"] - first["net_pnl"]), "inferred from first ledger row"
    cfg = data.configured_starting_capital()
    return (cfg, "configs/live_paper.yaml") if cfg else (None, "unknown")


def render_paper_performance(bundle: data.SourceBundle, strategies: list[str]) -> None:
    unit = "REPLAY SIMULATED paper (₹)" if bundle.is_replay else "LIVE PAPER (₹)"
    st.markdown(f"#### {unit}")
    st.caption("Virtual rupee P&L from the paper ledger - includes slippage and charges. "
               "Not comparable with the historical backtest, which is in index points.")  # fmt: skip
    trades = bundle.trades.df
    trades = trades[trades["strategy"].isin(strategies)] if not trades.empty else trades
    cap, cap_src = _paper_capital(bundle)
    snap = data.effective_snapshot(bundle.snapshot)
    active = bundle.snapshot.active or bundle.snapshot.finished_replay
    unreal = data._num(_get(snap, "account", "unrealized_pnl")) if active else 0.0
    equity = data._num(_get(snap, "account", "equity")) if active else None
    s = metrics.summarize_paper(trades, starting_capital=cap, unrealized=unreal or 0.0,
                                current_equity=equity)  # fmt: skip
    if not active and bundle.snapshot.data is not None:
        st.caption("No active session: equity is starting capital + realized P&L; unrealized shown as 0.")
    r1 = st.columns(5)
    r1[0].metric("Starting capital", fmt_inr(s["starting_capital"]), cap_src, delta_color="off")
    r1[1].metric("Current equity", fmt_inr(s["current_equity"]))
    r1[2].metric("Realized P&L", fmt_inr(s["realized_pnl"], True))
    r1[3].metric("Unrealized P&L", fmt_inr(s["unrealized_pnl"], True))
    r1[4].metric("Charges paid", fmt_inr(s["total_charges"]))
    r2 = st.columns(5)
    r2[0].metric("Total trades", s["total_trades"])
    r2[1].metric("Wins / losses", f"{s['wins']} / {s['losses']}")
    r2[2].metric("Win rate", fmt_pct(s["win_rate"]))
    r2[3].metric("Profit factor", fmt_pf(s["profit_factor"]))
    r2[4].metric("Max drawdown (closed trades)", fmt_inr(s["max_drawdown"]), fmt_pct(s["max_drawdown_pct"]), delta_color="off")
    st.caption("Max drawdown is measured on closed trades only (equity after each exit); moves inside "
               "an open trade are not included.")  # fmt: skip
    if trades.empty:
        st.info("No closed trades yet - analytics will appear after the first virtual exit.")
        return
    curve = metrics.equity_curve(trades, cap if cap is not None else 0.0)
    if cap is None:
        st.caption("Starting capital unknown: the curve shows cumulative net P&L from 0.")
    st.plotly_chart(charts.build_equity_chart(curve, cap if cap is not None else 0.0,
                                              title="Equity curve (₹, after each closed trade)", unit="₹"),
                    width="stretch", key="paper_equity")  # fmt: skip
    sp = metrics.strategy_pnl(trades)
    dp = metrics.daily_pnl(trades)
    a, b = st.columns(2)
    a.plotly_chart(charts.build_pnl_bars(sp, "strategy", "pnl", title="Net P&L by strategy (₹)", unit="₹"),
                   width="stretch", key="paper_strategy")  # fmt: skip
    b.plotly_chart(charts.build_pnl_bars(dp, "date", "pnl", title="Daily net P&L (₹)", unit="₹"),
                   width="stretch", key="paper_daily")  # fmt: skip
    st.dataframe(sp, width="stretch", hide_index=True)
    st.dataframe(dp, width="stretch", hide_index=True)


def render_backtest_performance(strategies: list[str]) -> None:
    st.markdown("#### HISTORICAL BACKTEST (index points)")
    st.caption("Underlying NIFTY index points only: no rupees, no option premium, no charges, no "
               "slippage. Never compare with the live paper rupee figures.")  # fmt: skip
    root = data.PROJECT_ROOT / "reports" / "backtest"
    runs = data.list_backtest_runs(root)
    legacy = data.legacy_portfolio_csv_path()
    options = [*runs] + (["Legacy frozen: COMBINED_PORTFOLIO_TRADES.csv"] if legacy.exists() else [])
    if not options:
        st.info("No backtest outputs found (reports/backtest/<run_id>/portfolio_trades.csv).")
        return
    choice = st.selectbox("Backtest source", options, key="bt_run")
    summary: dict[str, Any] = {}
    if choice in runs:
        run = data.load_backtest_run(root, choice)
        trades, problems, summary = run.trades, run.problems, run.summary
    else:
        trades, problems = data.load_points_trades_csv(legacy)
    for p in problems:
        st.warning(p)
    if not trades.empty:
        trades = trades[trades["strategy"].isin(strategies)]
    s = metrics.summarize_points(trades)
    c = st.columns(5)
    c[0].metric("Trades", s["total_trades"])
    c[1].metric("Wins / losses", f"{s['wins']} / {s['losses']}")
    c[2].metric("Win rate", fmt_pct(s["win_rate"]))
    c[3].metric("Profit factor", fmt_pf(s["profit_factor"]))
    c[4].metric("Total", fmt_pts(s["total_points"]), f"max DD {fmt_num(s['max_drawdown_points'])} pts", delta_color="off")
    if trades.empty:
        st.info("No backtest trades for the selected strategies.")
    else:
        curve = metrics.equity_curve(trades, 0.0, pnl_col="points_pnl")
        st.plotly_chart(charts.build_equity_chart(curve, 0.0, title="Cumulative points (backtest)", unit="points"),
                        width="stretch", key="bt_equity")  # fmt: skip
        sp = metrics.strategy_pnl(trades, pnl_col="points_pnl")
        dp = metrics.daily_pnl(trades, pnl_col="points_pnl", date_col="date")
        a, b = st.columns(2)
        a.plotly_chart(charts.build_pnl_bars(sp, "strategy", "pnl", title="Points by strategy", unit="points"),
                       width="stretch", key="bt_strategy")  # fmt: skip
        b.plotly_chart(charts.build_pnl_bars(dp, "date", "pnl", title="Daily points", unit="points"),
                       width="stretch", key="bt_daily")  # fmt: skip
        st.dataframe(sp, width="stretch", hide_index=True)
    if summary:
        with st.expander("Run summary.json"):
            st.json(summary, expanded=False)


def render_performance(bundle: data.SourceBundle, strategies: list[str]) -> None:
    st.subheader("Performance analytics")
    paper_label = "Replay simulated (₹)" if bundle.is_replay else "Live paper (₹)"
    paper_tab, bt_tab = st.tabs([paper_label, "Historical backtest (points)"])
    with paper_tab:
        render_paper_performance(bundle, strategies)
    with bt_tab:
        render_backtest_performance(strategies)


# --------------------------------------------------------------------------- Trade explanation


def _kv(label: str, value: Any) -> str:
    if isinstance(value, float):
        value = f"{value:,.2f}"
    text = NR if value in (None, "") else str(value)
    return f"- **{label}:** " + (f"*{text}*" if text == NR else text)


def previous_day_fallback(
    day: date | None,
    bundle: data.SourceBundle,
    hist1d: pd.DataFrame,
) -> tuple[dict[str, Any], str]:
    """Previous-day levels for a trade that did not record them, with where they came from.

    Order: another trade of the same day that recorded PDH/PDL/PDC, the snapshot's levels (when
    it is that day's session), the verified daily dataset.  Empty dict when none applies."""
    if day is not None:
        df = bundle.trades.df
        if not df.empty:
            for row in df[df["session_date"] == day].to_dict("records"):
                got = {k: v for k, v in trade_levels(row).items() if k in dash_text.PREVIOUS_DAY_KEYS}
                if got:
                    return got, f"levels recorded with trade {row.get('trade_id')} on the same day"
        snap = data.effective_snapshot(bundle.snapshot)
        snap_day = data.parse_ts(_get(snap, "session", "date"))
        if snap is not None and snap_day and snap_day.date() == day:
            got = {k: snap["levels"][k] for k in dash_text.PREVIOUS_DAY_KEYS
                   if isinstance(snap.get("levels"), dict) and data._num(snap["levels"].get(k)) is not None}  # fmt: skip
            if got:
                return got, "reference levels of that session (runtime snapshot)"
        daily = data.previous_day_levels(hist1d, day)
        if daily:
            return ({k: daily[k] for k in dash_text.PREVIOUS_DAY_KEYS if k in daily},
                    f"verified daily dataset, session {daily.get('prev_date')}")  # fmt: skip
    return {}, ""


def explanation_markdown(
    exp: dict[str, Any], prev_fallback: tuple[dict[str, Any], str] | None = None
) -> tuple[str, str]:
    """Markdown for the (opened, closed) panes from an ``explain_trade`` result.

    ``prev_fallback`` ``(levels, source)`` fills the previous-day levels when the trade did not
    record them; the source is always printed next to such values."""
    o, c = exp["opened"], exp["closed"]
    strategy = o.get("strategy_label") or o["strategy"]
    lines = [f"### {o['title']}", _kv("Strategy", strategy),
             _kv("Direction", o.get("direction_text") or o["direction"]),
             _kv("Entry reason", o.get("reason_text") or o["entry_reason"])]  # fmt: skip
    if o.get("rule_id"):
        lines.append(_kv("Rule id (detail)", o["rule_id"]))
    lines.append(_kv("Signal time", o["signal_time"]))
    tc = o["trigger_candle"]
    lines.append(_kv("Trigger candle", NR if tc == NR else dash_text.format_candle(tc)))
    cond = o["entry_conditions"]
    if cond == NR:
        lines.append(_kv("Entry conditions", NR))
    else:
        lines.append("- **Entry conditions satisfied:** " + (", ".join(cond["satisfied"]) or NR))
        if cond["failed"]:
            lines.append("- **Other checks not met (not required for this setup):** "
                         + ", ".join(cond["failed"]))  # fmt: skip
        if cond["details"]:
            lines.append("- **Condition details:** " + dash_text.format_details(cond["details"]))
    pdl = o["previous_day_levels"]
    if pdl != NR:
        lines.append(_kv("Previous-day levels", dash_text.format_levels(pdl) + " (recorded with the trade)"))
    elif prev_fallback and prev_fallback[0]:
        lines.append(_kv("Previous-day levels", dash_text.format_levels(prev_fallback[0])
                         + f" (not recorded with this trade; source: {prev_fallback[1]})"))  # fmt: skip
    else:
        lines.append(_kv("Previous-day levels", NR))
    if o["levels"] != NR and o["levels"]["other"]:
        lines.append("- **Other levels:** " + dash_text.format_levels(o["levels"]["other"]))
    if o["confirmation_rule"] != NR:
        lines.append(_kv("Confirmation rule", o["confirmation_rule"]))
    k = o["contract"]
    lines.append(_kv("Option contract", NR if k["symbol"] == NR else
                     f"{k['symbol']} ({k['option_type']} {k['strike']}, expiry {k['expiry']}, qty {k['quantity']})"))
    f = o["fill"]
    lines.append(_kv("Virtual fill", f"{f['entry_time']} spot {f['entry_spot']} bid {f['entry_bid']} "
                     f"ask {f['entry_ask']} slippage {f['entry_slippage']} -> premium {f['entry_premium']}"))
    lines.append(_kv("Label", o["label"]))
    exit_word = c.get("exit_sentence")
    closed = [f"### {c['title']}",
              _kv("Exit reason", f"{c['exit_reason']} - {exit_word}" if exit_word else c["exit_reason"]),
              _kv("Exit condition", c["exit_condition"]), _kv("Exit time", c["exit_time"]),
              _kv("Underlying at trigger", c["underlying_at_trigger"]),
              _kv("Original stop", c["original_stop"]), _kv("Original target", c["original_target"]),
              _kv("Option exit bid", c["option_exit_bid"]),
              _kv("Exit slippage / premium", f"{c['exit_slippage']} / {c['exit_premium']}"),
              _kv("Charges (entry / exit / total)",
                  f"{c['entry_charges']} / {c['exit_charges']} / {c['total_charges']}"),
              _kv("Gross P&L", c["gross_pnl"]), _kv("Final net P&L", c["net_pnl"])]  # fmt: skip
    if c["exit_detail"] != NR:
        closed.append("- **Recorded exit detail:** " + json.dumps(c["exit_detail"], default=str))
    return "\n".join(lines), "\n".join(closed)


def render_trade_explanation(bundle: data.SourceBundle, strategies: list[str],
                             hist5: pd.DataFrame, hist1d: pd.DataFrame) -> None:  # fmt: skip
    st.subheader("Trade explanation")
    trades = bundle.trades.df
    trades = trades[trades["strategy"].isin(strategies)] if not trades.empty else trades
    if trades.empty:
        st.info("No closed trades to explain yet.")
        return
    ids = list(trades.sort_values("exit_time", ascending=False)["trade_id"])
    pre = st.session_state.get("selected_trade_id")
    idx = ids.index(pre) if pre in ids else 0
    tid = st.selectbox("Trade", ids, index=idx, key="explain_trade")
    row = trades[trades["trade_id"] == tid].iloc[0]
    exp = explain.explain_trade(row.to_dict())
    day = row["session_date"]
    left, right = st.columns(2)
    opened_md, closed_md = explanation_markdown(exp, previous_day_fallback(day, bundle, hist1d))
    left.markdown(opened_md)
    right.markdown(closed_md)
    if exp["missing"]:
        st.caption(f"{len(exp['missing'])} field(s) were not recorded for this trade; they are shown as "
                   f"'{NR}' and are never guessed.")  # fmt: skip
    sub = data.SourceBundle(bundle.source, bundle.root, bundle.snapshot,
                            data.TradesData(trades[trades["trade_id"] == tid], bundle.trades.path, True),
                            bundle.events_dir)  # fmt: skip
    ci = assemble_chart(sub, day, strategies, hist5, hist1d)
    trade = row.to_dict()
    ci.markers = [m for m in ci.markers if m["kind"] == "SIGNAL" or m.get("trade_id") == tid]
    levels, force = highlight_levels(ci.levels, trade, data.effective_snapshot(bundle.snapshot))
    fig = charts.build_candlestick(ci.candles, levels=levels, markers=ci.markers, trade=trade,
                                   title=f"{tid} - {dash_text.strategy_label_of(trade)} - {day}",
                                   force_levels=force)  # fmt: skip
    st.plotly_chart(fig, width="stretch", key="explain_chart")
    st.caption(ci.caption)
    meta = fig.layout.meta or {}
    if meta.get("off_chart_levels"):
        st.caption("Levels outside the visible range: " + ", ".join(meta["off_chart_levels"]))


# --------------------------------------------------------------------------- cached loaders


@st.cache_data(show_spinner=False)
def cached_historical(path: str, mtime: float) -> pd.DataFrame:  # noqa: ARG001 - mtime keys the cache
    return data.load_historical(path)


def load_hist(path: Path) -> pd.DataFrame:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return cached_historical(str(path), mtime)


def day_options(bundle: data.SourceBundle, hist5: pd.DataFrame) -> list[date]:
    return _date_options(bundle, hist5)
