"""Pure plotly figure builders (no Streamlit, no I/O).

Design rules (accessible, restrained):
* Okabe-Ito colour-blind-safe hues.  Green/vermillion are used ONLY for profit/loss semantics
  (P&L bars, target/stop lines); candles are blue (up) / orange (down).
* Marker identity is carried by SHAPE as well as colour (entry = triangle, exit shape per reason).
* Nothing is invented: the candlestick trace holds exactly the candles passed in, markers outside
  the drawn candle span are dropped (and counted in ``fig.layout.meta``), levels are drawn only
  when a numeric value was supplied.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from trading_platform.dashboard import text as dash_text

CANDLE_MINUTES = 5

BLUE = "#0072B2"
SKY = "#56B4E9"
ORANGE = "#E69F00"
GREEN = "#009E73"  # profit / target only
VERMILLION = "#D55E00"  # loss / stop only
PURPLE = "#CC79A7"
GREY = "#7A7A7A"
GRID = "rgba(128,128,128,0.18)"

SAMPLED_SOURCES = {"SAMPLED_TICKS"}

# exit reason -> (plotly symbol, colour)
EXIT_STYLES: dict[str, tuple[str, str]] = {
    "TARGET": ("star", GREEN),
    "STOP": ("x", VERMILLION),
    "EOD": ("square", PURPLE),
    "RISK": ("diamond-x", VERMILLION),
    "STRATEGY_EXIT": ("pentagon", BLUE),
    "SHUTDOWN_UNRESOLVED": ("hourglass", GREY),
}
_DEFAULT_EXIT_STYLE = ("circle-x", GREY)

_LEVEL_STYLES: dict[str, tuple[str, str]] = {  # name -> (colour, dash)
    "PDH": (PURPLE, "dash"),
    "PDL": (PURPLE, "dash"),
    "PDC": (GREY, "dot"),
    "IRH": (BLUE, "dot"),
    "IRL": (BLUE, "dot"),
    "EMA200": (ORANGE, "dot"),
}
_ZONE_STYLES: dict[str, str] = {
    "support_zone": "rgba(0,158,115,0.14)",
    "resistance_zone": "rgba(213,94,0,0.14)",
}
_MAX_GENERIC_LEVELS = 10


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) or math.isinf(out) else out


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.to_pydatetime()


def _trigger_from_trade(trade: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Parsed ``trigger_candle_json`` of a trades.csv row (None when absent / unreadable)."""
    parsed = _parse_json_blob(trade.get("trigger_candle_json"))
    return parsed if isinstance(parsed, Mapping) else None


def _parse_json_blob(value: Any) -> Any | None:
    if isinstance(value, Mapping):
        return value
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        return json.loads(str(value))
    except ValueError:
        return None


def _base_layout(fig: go.Figure, title: str, height: int) -> None:
    fig.update_layout(
        title={"text": title, "x": 0.0, "xanchor": "left", "font": {"size": 15}},
        height=height,
        margin={"l": 56, "r": 96, "t": 56, "b": 40},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="closest",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "x": 0.0,
                "font": {"size": 11}},
        xaxis={"showgrid": False, "rangeslider": {"visible": False}},
        yaxis={"gridcolor": GRID, "zerolinecolor": GRID, "tickformat": ",.2f"},
    )  # fmt: skip


# --------------------------------------------------------------------------- levels


def _split_levels(
    levels: Mapping[str, Any] | None,
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """(scalar levels, zones) with only numeric, finite values."""
    scalars: dict[str, float] = {}
    zones: dict[str, tuple[float, float]] = {}
    for key, value in (levels or {}).items():
        name = str(key)
        if isinstance(value, Sequence) and not isinstance(value, str):
            nums = [_num(v) for v in value]
            if len(nums) == 2 and None not in nums:
                zones[name] = (min(nums), max(nums))  # type: ignore[type-var]
            continue
        n = _num(value)
        if n is not None and not isinstance(value, bool) and name.lower() not in {"notes", "note"}:
            scalars[name] = n
    ordered = sorted(scalars, key=lambda k: (k not in _LEVEL_STYLES, k))
    return {k: scalars[k] for k in ordered[: _MAX_GENERIC_LEVELS + len(_LEVEL_STYLES)]}, zones


# --------------------------------------------------------------------------- candlestick


def _marker_in_span(t: datetime | None, lo: datetime, hi: datetime) -> bool:
    return t is not None and lo <= t <= hi


def build_candlestick(
    candles: pd.DataFrame,
    *,
    levels: Mapping[str, Any] | None = None,
    markers: Iterable[Mapping[str, Any]] | None = None,
    trade: Mapping[str, Any] | None = None,
    title: str = "NIFTY 5-minute",
    height: int = 560,
    show_levels: bool = True,
    force_levels: Iterable[str] | None = None,
    trigger_candle: Mapping[str, Any] | None = None,
) -> go.Figure:
    """Candlestick (5m) with reference levels, zones, signal/entry/exit markers and trade lines.

    ``candles``: columns time (open label), open, high, low, close[, source].  Exactly these rows
    are drawn - no filling, resampling or interpolation.

    ``markers``: dicts with kind ('SIGNAL'|'ENTRY'|'EXIT'), t, price, optional strategy,
    trade_id, text, exit_reason, direction.  Markers outside the candle span are dropped and the
    number dropped is reported in ``fig.layout.meta['dropped_markers']``.

    ``trade``: optional mapping (a trades.csv row or snapshot position) providing stop_spot /
    target_spot (drawn as labelled lines) for the selected/active trade.

    ``force_levels``: level names (the levels recorded for the highlighted trade) that are ALWAYS
    drawn even when far from the candles: the axis is widened for a level within a few ranges of
    the price, otherwise an arrow annotation at the chart edge points to it.

    ``trigger_candle``: the recorded trigger candle (``time`` + o/h/l/c); defaults to the
    ``trigger_candle_json`` field of ``trade``.  Its 5m slot is highlighted.
    """
    fig = go.Figure()
    _base_layout(fig, title, height)
    fig.update_layout(meta={"dropped_markers": 0, "off_chart_levels": [], "candle_count": 0})

    if candles is None or candles.empty:
        fig.add_annotation(
            text="No validated candles to display", xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font={"size": 14, "color": GREY},
        )  # fmt: skip
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        return fig

    df = candles.sort_values("time").reset_index(drop=True)
    sources = df["source"] if "source" in df.columns else pd.Series(["UNKNOWN"] * len(df))
    sampled_mask = sources.isin(SAMPLED_SOURCES)
    solid, sampled = df[~sampled_mask], df[sampled_mask]
    hover = [
        f"{t:%d %b %H:%M} open<br>O {o:,.2f}  H {h:,.2f}<br>L {lo:,.2f}  C {c:,.2f}<br>{s}"
        for t, o, h, lo, c, s in zip(
            df["time"], df["open"], df["high"], df["low"], df["close"], sources, strict=True
        )
    ]
    hover_by_time = dict(zip(df["time"], hover, strict=True))

    def _candle_trace(part: pd.DataFrame, name: str, up: tuple[str, str], down: tuple[str, str]):
        return go.Candlestick(
            x=part["time"], open=part["open"], high=part["high"], low=part["low"],
            close=part["close"], name=name,
            increasing={"line": {"color": up[0], "width": 1}, "fillcolor": up[1]},
            decreasing={"line": {"color": down[0], "width": 1}, "fillcolor": down[1]},
            text=[hover_by_time[t] for t in part["time"]], hoverinfo="text",
        )  # fmt: skip

    if not solid.empty:
        fig.add_trace(_candle_trace(solid, "NIFTY 5m", (BLUE, SKY), (ORANGE, ORANGE)))
    if not sampled.empty:
        fig.add_trace(
            _candle_trace(sampled, "Sampled (non-authoritative)", (GREY, "rgba(0,0,0,0)"),
                          (GREY, "rgba(122,122,122,0.5)"))
        )  # fmt: skip

    first = df["time"].min().to_pydatetime()
    last_close = df["time"].max().to_pydatetime() + timedelta(minutes=CANDLE_MINUTES)
    y_lo, y_hi = float(df["low"].min()), float(df["high"].max())
    candle_range = max(y_hi - y_lo, 1.0)
    extend: list[float] = []
    meta = {"dropped_markers": 0, "off_chart_levels": [], "candle_count": len(df)}

    # ---- levels & zones
    forced = {str(n) for n in (force_levels or ())}
    edge_notes: list[tuple[str, float, bool]] = []  # (label, value, above) for far forced levels
    if show_levels:
        scalars, zones = _split_levels(levels)
        reach = max(100.0, candle_range)
        far_limit = max(400.0, 3.0 * candle_range)

        def _placement(name: str, lo: float, hi: float) -> str:
            """'draw' | 'edge' | 'skip' for a level spanning ``lo``..``hi``."""
            if hi >= y_lo - reach and lo <= y_hi + reach:
                return "draw"
            if name not in forced:
                return "skip"
            gap = (y_lo - hi) if hi < y_lo else (lo - y_hi)
            return "draw" if gap <= far_limit else "edge"

        for name, value in scalars.items():
            where = _placement(name, value, value)
            if where == "skip":
                meta["off_chart_levels"].append(f"{name} {value:,.2f}")
                continue
            if where == "edge":
                meta["off_chart_levels"].append(f"{name} {value:,.2f} (off chart)")
                edge_notes.append((f"{name} {value:,.2f}", value, value > y_hi))
                continue
            colour, dash = _LEVEL_STYLES.get(name, (GREY, "dot"))
            fig.add_shape(type="line", name=name, x0=first, x1=last_close, y0=value, y1=value,
                          line={"color": colour, "width": 1.2, "dash": dash})  # fmt: skip
            fig.add_annotation(x=last_close, y=value, text=f"{name} {value:,.1f}", xanchor="left",
                               showarrow=False, font={"size": 10, "color": colour}, xshift=4)  # fmt: skip
            extend.append(value)
        for name, (zlo, zhi) in zones.items():
            where = _placement(name, zlo, zhi)
            if where == "skip":
                meta["off_chart_levels"].append(f"{name} {zlo:,.2f}-{zhi:,.2f}")
                continue
            if where == "edge":
                meta["off_chart_levels"].append(f"{name} {zlo:,.2f}-{zhi:,.2f} (off chart)")
                edge_notes.append((f"{name.replace('_', ' ')} {zlo:,.0f}-{zhi:,.0f}", zlo, zlo > y_hi))
                continue
            fill = _ZONE_STYLES.get(name, "rgba(122,122,122,0.12)")
            fig.add_shape(type="rect", name=name, x0=first, x1=last_close, y0=zlo, y1=zhi,
                          fillcolor=fill, line={"width": 0}, layer="below")  # fmt: skip
            label = name.replace("_", " ")
            fig.add_annotation(x=last_close, y=(zlo + zhi) / 2, text=f"{label} {zlo:,.0f}-{zhi:,.0f}",
                               xanchor="left", showarrow=False, font={"size": 10, "color": GREY},
                               xshift=4)  # fmt: skip
            extend += [zlo, zhi]

    # ---- trade stop / target lines
    if trade:
        for key, label, colour in (
            ("stop_spot", "Stop", VERMILLION),
            ("target_spot", "Target", GREEN),
        ):
            value = _num(trade.get(key))
            if value is None:
                continue
            fig.add_shape(type="line", name=label, x0=first, x1=last_close, y0=value, y1=value,
                          line={"color": colour, "width": 1.6, "dash": "longdash"})  # fmt: skip
            fig.add_annotation(x=last_close, y=value, text=f"{label} {value:,.1f}", xanchor="left",
                               showarrow=False, font={"size": 10, "color": colour}, xshift=4)  # fmt: skip
            extend.append(value)

    # ---- markers
    signals: list[Mapping[str, Any]] = []
    entries: list[Mapping[str, Any]] = []
    exits: dict[str, list[Mapping[str, Any]]] = {}
    for m in markers or []:
        t, price = _as_dt(m.get("t")), _num(m.get("price"))
        kind = str(m.get("kind") or "").upper()
        if t is None or price is None or kind not in {"SIGNAL", "ENTRY", "EXIT"}:
            meta["dropped_markers"] += 1
            continue
        if not _marker_in_span(t, first, last_close):
            meta["dropped_markers"] += 1
            continue
        item = {**m, "t": t, "price": price}
        if kind == "SIGNAL":
            signals.append(item)
        elif kind == "ENTRY":
            entries.append(item)
        else:
            exits.setdefault(str(m.get("exit_reason") or "UNKNOWN").upper(), []).append(item)

    def _hover(m: Mapping[str, Any], head: str) -> str:
        bits = [head, f"{m['t']:%H:%M:%S}  @ {m['price']:,.2f}"]
        if m.get("strategy"):
            bits.append(str(m["strategy"]))
        if m.get("trade_id"):
            bits.append(f"trade {m['trade_id']}")
        if m.get("text"):
            bits.append(str(m["text"])[:120])
        return "<br>".join(bits)

    if signals:
        fig.add_trace(go.Scatter(
            x=[m["t"] for m in signals], y=[m["price"] for m in signals], mode="markers",
            name="Signal", marker={"symbol": "diamond-open", "size": 11, "color": PURPLE,
                                   "line": {"width": 2, "color": PURPLE}},
            text=[_hover(m, "SIGNAL") for m in signals], hoverinfo="text",
        ))  # fmt: skip
    if entries:
        symbols = [
            "triangle-down" if str(m.get("direction") or "").upper() in {"BEARISH", "SELL", "PE"}
            else "triangle-up"
            for m in entries
        ]  # fmt: skip
        fig.add_trace(go.Scatter(
            x=[m["t"] for m in entries], y=[m["price"] for m in entries], mode="markers",
            name="Entry", marker={"symbol": symbols, "size": 13, "color": BLUE,
                                  "line": {"width": 1.5, "color": "white"}},
            text=[_hover(m, "ENTRY") for m in entries], hoverinfo="text",
        ))  # fmt: skip
    for reason, items in sorted(exits.items()):
        symbol, colour = EXIT_STYLES.get(reason, _DEFAULT_EXIT_STYLE)
        fig.add_trace(go.Scatter(
            x=[m["t"] for m in items], y=[m["price"] for m in items], mode="markers",
            name=f"Exit: {reason}",
            marker={"symbol": symbol, "size": 13, "color": colour,
                    "line": {"width": 1.5, "color": "white" if "open" not in symbol else colour}},
            text=[_hover(m, f"EXIT ({reason})") for m in items], hoverinfo="text",
        ))  # fmt: skip
    extend += [m["price"] for m in signals + entries] + [m["price"] for v in exits.values() for m in v]

    # ---- trade annotations (strategy + entry / exit reason) for the highlighted trade
    if trade:
        strategy = str(trade.get("strategy") or "").strip()
        tid = trade.get("trade_id")
        short = dash_text.strategy_label_of(trade) if strategy else ""
        for m in entries:
            if tid and m.get("trade_id") not in (None, tid):
                continue
            sentence = dash_text.reason_sentence(trade)
            if sentence == dash_text.NOT_RECORDED:
                sentence = str(m.get("text") or "").strip()
            fig.add_annotation(
                x=m["t"], y=m["price"], text=f"{short} entry" + (f": {sentence[:70]}" if sentence else ""),
                showarrow=True, arrowhead=2, arrowcolor=BLUE, ax=-40, ay=-42,
                font={"size": 10}, bgcolor="rgba(255,255,255,0.7)", bordercolor=BLUE,
                borderwidth=1,
            )  # fmt: skip
        for reason_key, items in exits.items():
            for m in items:
                if tid and m.get("trade_id") not in (None, tid):
                    continue
                fig.add_annotation(
                    x=m["t"], y=m["price"], text=f"{short} exit: {reason_key}", showarrow=True,
                    arrowhead=2, arrowcolor=GREY, ax=40, ay=42, font={"size": 10},
                    bgcolor="rgba(255,255,255,0.7)", bordercolor=GREY, borderwidth=1,
                )  # fmt: skip

    # ---- trigger candle highlight (the candle the rule fired on)
    trigger = trigger_candle if trigger_candle is not None else (
        _trigger_from_trade(trade) if trade else None
    )
    trigger_t = _as_dt((trigger or {}).get("time") or (trigger or {}).get("t")) if trigger else None
    if trigger_t is not None and _marker_in_span(trigger_t, first, last_close):
        end = trigger_t + timedelta(minutes=CANDLE_MINUTES)
        fig.add_shape(type="rect", name="trigger candle", xref="x", yref="paper", x0=trigger_t, x1=end,
                      y0=0, y1=1, fillcolor="rgba(204,121,167,0.18)", line={"width": 0},
                      layer="below")  # fmt: skip
        fig.add_annotation(x=trigger_t + timedelta(minutes=CANDLE_MINUTES / 2), y=1, yref="paper",
                           text="trigger candle", showarrow=False, yanchor="bottom",
                           font={"size": 10, "color": PURPLE})  # fmt: skip
        meta["trigger_candle"] = trigger_t.isoformat()

    # ---- axes
    pad = max(candle_range * 0.08, 2.0)
    lo_axis = min([y_lo, *extend]) - pad
    hi_axis = max([y_hi, *extend]) + pad
    for text_, _value, above in edge_notes:  # far forced levels: arrow at the chart edge
        arrow = "▲" if above else "▼"
        fig.add_annotation(
            x=last_close, y=hi_axis if above else lo_axis, text=f"{arrow} {text_} (off chart)",
            showarrow=True, arrowhead=2, arrowcolor=GREY, ax=0, ay=26 if above else -26,
            xanchor="right", font={"size": 10, "color": GREY},
        )  # fmt: skip
    fig.update_yaxes(range=[lo_axis, hi_axis])
    if df["time"].dt.date.nunique() > 1:
        fig.update_xaxes(rangebreaks=[
            {"bounds": ["sat", "mon"]},
            {"bounds": [15.5, 9.25], "pattern": "hour"},
        ])  # fmt: skip
    else:
        fig.update_xaxes(range=[first - timedelta(minutes=5), last_close + timedelta(minutes=10)])
    fig.update_layout(meta=meta)
    return fig


# --------------------------------------------------------------------------- trade -> markers


def trade_markers(
    trades: pd.DataFrame, candles: pd.DataFrame, *, only_trade_id: str | None = None
) -> list[dict[str, Any]]:
    """Entry / exit markers from trades.csv rows - ONLY for trades whose session date has
    candles in ``candles`` (the chart never shows a marker without the candles it refers to)."""
    if trades is None or trades.empty or candles is None or candles.empty:
        return []
    days: set[date] = set(candles["time"].dt.date)
    out: list[dict[str, Any]] = []
    for _, r in trades.iterrows():
        if only_trade_id and r.get("trade_id") != only_trade_id:
            continue
        entry_t, exit_t = _as_dt(r.get("entry_time")), _as_dt(r.get("exit_time"))
        if entry_t is None or entry_t.date() not in days:
            continue
        entry_spot, exit_spot = _num(r.get("entry_spot")), _num(r.get("exit_spot"))
        common = {"strategy": str(r.get("strategy") or ""), "trade_id": r.get("trade_id")}
        if entry_spot is not None:
            out.append({**common, "kind": "ENTRY", "t": entry_t, "price": entry_spot,
                        "direction": str(r.get("direction") or ""),
                        "text": str(r.get("entry_reason") or "")})  # fmt: skip
        if exit_t is not None and exit_spot is not None and exit_t.date() in days:
            out.append({**common, "kind": "EXIT", "t": exit_t, "price": exit_spot,
                        "exit_reason": str(r.get("exit_reason") or "UNKNOWN"),
                        "text": f"net {_num(r.get('net_pnl')) or 0:,.2f}"})  # fmt: skip
    return out


def merge_markers(*groups: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Concatenate marker lists dropping duplicates (same kind + trade_id, or kind/time/price).

    The FIRST marker seen wins (pass the ledger markers first) and is *enriched*: fields it lacks
    (``exit_reason``, ``direction``, ``premium`` ...) are filled from the duplicate, so a
    snapshot marker never hides a richer ledger one and vice versa."""
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for group in groups:
        for m in group:
            kind = str(m.get("kind") or "").upper()
            tid = m.get("trade_id")
            key = (kind, tid) if tid and kind in {"ENTRY", "EXIT"} else (
                kind, str(m.get("t")), m.get("price"), m.get("strategy"))
            if key in seen:
                kept = seen[key]
                for field_, value in m.items():
                    if value in (None, "") or field_ == "text":
                        continue
                    if kept.get(field_) in (None, "", "UNKNOWN"):
                        kept[field_] = value
                continue
            kept = dict(m)
            seen[key] = kept
            out.append(kept)
    return out


# --------------------------------------------------------------------------- analytics charts


def build_equity_chart(
    curve: pd.DataFrame, baseline: float | None, *, title: str, unit: str, height: int = 340
) -> go.Figure:
    """Equity (or cumulative-points) line after each closed trade; dashed baseline = start."""
    fig = go.Figure()
    _base_layout(fig, title, height)
    fig.update_layout(margin={"l": 56, "r": 24, "t": 56, "b": 40})
    if curve is None or len(curve) < 2:
        fig.add_annotation(text="No closed trades yet", xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font={"size": 14, "color": GREY})  # fmt: skip
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        return fig
    x = list(range(len(curve)))
    labels = [str(t) for t in curve["trade_id"]]
    fig.add_trace(go.Scatter(
        x=x, y=curve["equity"], mode="lines+markers", name=unit,
        line={"color": BLUE, "width": 2}, marker={"size": 7, "color": BLUE,
                                                   "line": {"width": 2, "color": "white"}},
        text=[f"{lab}<br>{t}" for lab, t in zip(labels, curve["time"], strict=True)],
        hovertemplate="%{text}<br>%{y:,.2f}<extra></extra>",
    ))  # fmt: skip
    if baseline is not None:
        fig.add_hline(y=baseline, line={"color": GREY, "width": 1, "dash": "dash"},
                      annotation_text="start", annotation_position="bottom right")  # fmt: skip
    fig.update_xaxes(title="closed trade #", dtick=1 if len(curve) <= 25 else None)
    fig.update_yaxes(title=unit)
    return fig


def build_pnl_bars(
    df: pd.DataFrame, x: str, y: str, *, title: str, unit: str, height: int = 300
) -> go.Figure:
    """Bar chart of P&L by category/date: profit bars green, loss bars vermillion, zero line."""
    fig = go.Figure()
    _base_layout(fig, title, height)
    fig.update_layout(margin={"l": 56, "r": 24, "t": 56, "b": 40})
    if df is None or df.empty:
        fig.add_annotation(text="No data", xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font={"size": 14, "color": GREY})  # fmt: skip
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        return fig
    values = pd.to_numeric(df[y], errors="coerce").fillna(0.0)
    fig.add_trace(go.Bar(
        x=[str(v) for v in df[x]], y=values,
        marker={"color": [GREEN if v >= 0 else VERMILLION for v in values]},
        name=unit, hovertemplate="%{x}<br>%{y:,.2f}<extra></extra>",
    ))  # fmt: skip
    fig.update_yaxes(title=unit)
    fig.update_xaxes(type="category")
    return fig
