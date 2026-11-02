"""Plotly comparison dashboard for the manual-vs-V3 reconciliation (single self-contained HTML).

Only real data is drawn: candles come from the verified historical 5-minute dataset; levels are
the strategy's own reference levels for that day; manual markers use the journal's recorded
time only (the journal has no price, stop or target, so none is drawn for the manual side).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from trading_platform.backtesting.manual_diagnostics import DayReplayer
from trading_platform.backtesting.manual_reconciliation_report import Reconciliation

# colour-blind-safe, restrained
C_MANUAL = "#7b3294"
C_SIGNAL = "#0072b2"
C_ENTRY = "#009e73"
C_EXIT = "#d55e00"
C_LEVEL = "#6c757d"
C_GOOD = "#009e73"
C_BAD = "#d55e00"
C_NEUTRAL = "#9aa5b1"
LAYOUT = {
    "template": "plotly_white",
    "font": {"family": "Inter, Helvetica, Arial, sans-serif", "size": 13},
}


def _counts_figure(rec: Reconciliation) -> go.Figure:
    s = rec.strategy_summary
    s = s[s.strategy.isin(["PDS", "ORB", "IRF", "PDR", "RF"])]
    fig = go.Figure()
    for col, name, color in (
        ("manual_trades", "Manual journal", C_MANUAL),
        ("automated_signals_in_journal_range", "V3 raw signals", C_SIGNAL),
        ("automated_standalone_trades_in_range", "V3 standalone trades", "#56b4e9"),
        ("automated_portfolio_trades_in_range", "V3 portfolio (FIFO) trades", C_ENTRY),
    ):
        fig.add_bar(x=s.strategy, y=s[col], name=name, marker_color=color)
    fig.update_layout(
        title="Trade counts: manual journal vs V3 (RF has no automated strategy)",
        barmode="group",
        yaxis_title="count",
        **LAYOUT,
    )
    return fig


def _match_rate_figure(rec: Reconciliation) -> go.Figure:
    s = rec.strategy_summary
    s = s[s.strategy.isin(["PDS", "ORB", "IRF", "PDR", "TOTAL"])]
    fig = go.Figure()
    for col, name, color in (
        ("exact", "EXACT", C_GOOD),
        ("close_le_5min", "CLOSE (<=5 min)", "#56b4e9"),
        ("near_le_10min", "NEAR (<=10 min)", "#f0e442"),
        ("different_gt_10min", "DIFFERENT (>10 min)", "#e69f00"),
        ("unmatched", "UNMATCHED", C_BAD),
    ):
        pct = (s[col] / s.eligible_manual_trades.where(s.eligible_manual_trades > 0) * 100).round(1)
        fig.add_bar(
            x=s.strategy,
            y=pct,
            name=name,
            marker_color=color,
            customdata=s[col],
            hovertemplate="%{y}% (%{customdata} records)",
        )
    fig.update_layout(
        title="Share of ELIGIBLE manual trades by match category (signal level)",
        barmode="stack",
        yaxis_title="% of eligible",
        **LAYOUT,
    )
    return fig


def _category_figure(rec: Reconciliation) -> go.Figure:
    t = rec.all_trades
    order = ["EXACT", "CLOSE", "NEAR", "DIFFERENT", "UNMATCHED"]
    elig = t[t.eligibility == "ELIGIBLE"]
    vals = [int((elig.match_category == c).sum()) for c in order]
    extra = [
        ("RF (unsupported)", int((t.eligibility == "UNSUPPORTED_RF").sum())),
        ("No session / date", int(t.eligibility.isin(["NON_TRADING_DAY", "NO_FIRST_BAR"]).sum())),
    ]
    x = order + [e[0] for e in extra]
    y = vals + [e[1] for e in extra]
    colors = [C_GOOD, "#56b4e9", "#f0e442", "#e69f00", C_BAD, C_NEUTRAL, C_NEUTRAL]
    fig = go.Figure(go.Bar(x=x, y=y, marker_color=colors, text=y, textposition="outside"))
    fig.update_layout(
        title="All 190 manual records by outcome of matching", yaxis_title="records", **LAYOUT
    )
    return fig


def _matrix_figure(rec: Reconciliation) -> go.Figure:
    m = rec.outcome_matrix
    m = m[m.scope == "ALL"]
    rows = ["Target", "SL", "BE"]
    cols = ["TARGET", "STOP", "EOD", "NO_AUTOMATED_EXIT"]
    z = [
        [int(m[(m.manual_outcome == r) & (m.automated_outcome == c)]["count"].sum()) for c in cols]
        for r in rows
    ]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=["Auto TARGET", "Auto STOP", "Auto EOD", "No automated exit"],
            y=["Manual Target", "Manual SL", "Manual BE"],
            text=z,
            texttemplate="%{text}",
            colorscale=[[0, "#f7f7f7"], [1, "#0072b2"]],
            showscale=False,
        )
    )
    fig.update_layout(
        title="Outcome agreement matrix (valid signal matches <= 10 min). Diagonal Target/Target and SL/STOP = agreement; the engine has no break-even exit",
        **LAYOUT,
    )
    return fig


def _reasons_figure(rec: Reconciliation) -> go.Figure:
    r = rec.reasons
    fig = go.Figure()
    if not r.empty:
        pv = r.groupby(["root_cause", "manual_strategy"]).size().unstack(fill_value=0)
        palette = {
            "ORB": "#0072b2",
            "IRF": "#e69f00",
            "PDR": "#009e73",
            "PDS": "#cc79a7",
            "RF": C_NEUTRAL,
        }
        for strat in pv.columns:
            fig.add_bar(
                y=pv.index,
                x=pv[strat],
                name=strat,
                orientation="h",
                marker_color=palette.get(strat, C_NEUTRAL),
            )
    fig.update_layout(
        title="Why manual trades are not reproduced as accepted V3 trades (root cause x strategy)",
        barmode="stack",
        xaxis_title="records",
        yaxis={"autorange": "reversed"},
        **LAYOUT,
    )
    return fig


def _offset_figure(rec: Reconciliation) -> go.Figure:
    t = rec.all_trades
    d = t[t.timestamp_diff_min_manual_minus_signal_open != ""].copy()
    d["off"] = d.timestamp_diff_min_manual_minus_signal_open.astype(int)
    fig = go.Figure()
    palette = {"ORB": "#0072b2", "IRF": "#e69f00", "PDR": "#009e73", "PDS": "#cc79a7"}
    for strat, g in d.groupby("manual_strategy"):
        fig.add_histogram(
            x=g.off.clip(-60, 60),
            name=strat,
            xbins={"start": -62.5, "end": 62.5, "size": 5},
            marker_color=palette.get(strat, C_NEUTRAL),
        )
    fig.update_layout(
        title="Timing convention check: manual time minus V3 signal-candle OPEN label (minutes, clipped to +/-60). A fill-time convention would pile up at +5; the pile-up is at 0",
        barmode="stack",
        xaxis_title="minutes (manual - automated signal candle open)",
        yaxis_title="records",
        **LAYOUT,
    )
    return fig


def _pick_cases(rec: Reconciliation) -> list[tuple[str, pd.Series]]:
    t = rec.all_trades
    reasons = rec.reasons.set_index("manual_serial") if not rec.reasons.empty else pd.DataFrame()
    picks: list[tuple[str, pd.Series]] = []

    def first(df: pd.DataFrame, label: str) -> None:
        if not df.empty:
            picks.append((label, df.iloc[0]))

    valid = t[t.valid_match_le_10min == "YES"]
    first(
        valid[
            (valid.manual_strategy == "ORB")
            & (valid.outcome_agreement == "AGREE_BOTH_TARGET")
            & (valid.match_category == "EXACT")
        ],
        "Exact ORB match, both reach target",
    )
    first(
        valid[
            (valid.manual_strategy == "ORB")
            & (valid.outcome_agreement == "MANUAL_TARGET_AUTO_STOP")
        ],
        "Exact ORB match, outcomes DISAGREE (manual target, automated stop)",
    )
    first(
        valid[
            (valid.manual_strategy == "ORB") & (valid.outcome_agreement == "MANUAL_BE_AUTO_STOP")
        ],
        "ORB match, manual break-even vs automated stop",
    )
    first(
        valid[(valid.manual_strategy == "IRF") & (valid.match_category == "CLOSE")],
        "IRF match one candle apart (CLOSE)",
    )
    first(
        valid[(valid.manual_strategy == "IRF") & (valid.match_category == "NEAR")],
        "IRF match two candles apart (NEAR)",
    )
    first(valid[(valid.manual_strategy == "PDR")], "PDR matched")
    first(valid[(valid.manual_strategy == "PDS")], "PDS matched")
    rej = t[(t.valid_match_le_10min == "YES") & (t.portfolio_accepted == "NO")]
    first(rej, "Matched signal rejected by the one-position portfolio")
    if not reasons.empty:

        def by_cause(cause: str, strat: str, label: str) -> None:
            ids = reasons[(reasons.root_cause == cause) & (reasons.manual_strategy == strat)].index
            sel = t[t.manual_serial.isin(ids)]
            first(sel, label)

        by_cause(
            "Strategy-method mismatch",
            "ORB",
            "UNMATCHED ORB: strong in-trend candle but previous-day level not broken (consistent with Method 2)",
        )
        by_cause(
            "Manual discretionary entry",
            "ORB",
            "UNMATCHED ORB: no level broken and candle fails the quality / trend tests",
        )
        by_cause(
            "Rulebook ambiguity",
            "ORB",
            "UNMATCHED ORB: would qualify under a different reading of the previous-day level",
        )
        by_cause(
            "Strategy-method mismatch",
            "IRF",
            "UNMATCHED IRF: pattern is IRF Method 1, not the portfolio's Method 2",
        )
        by_cause(
            "Different candle timing",
            "IRF",
            "IRF: V3 signalled the same day but far from the manual time",
        )
        by_cause(
            "Manual discretionary entry",
            "IRF",
            "UNMATCHED IRF: no close above the initial-range high had occurred",
        )
        by_cause(
            "Rulebook ambiguity", "PDR", "UNMATCHED PDR: provisional 'open above zone top' gate"
        )
        by_cause(
            "Strategy-method mismatch", "PDS", "UNMATCHED PDS: only another PDS method signals"
        )
    return picks


def _case_figure(replayer: DayReplayer, label: str, row: pd.Series) -> go.Figure | None:
    day = date.fromisoformat(row.date)
    bars = replayer.by_day.get(day)
    if not bars:
        return None
    regular = [b for b in bars if "09:15" <= b.exchange_time.strftime("%H:%M") <= "15:25"]
    df = pd.DataFrame(
        {
            "t": [b.exchange_time.replace(tzinfo=None) for b in regular],
            "o": [b.open for b in regular],
            "h": [b.high for b in regular],
            "l": [b.low for b in regular],
            "c": [b.close for b in regular],
        }
    )
    fig = go.Figure(
        go.Candlestick(
            x=df.t,
            open=df.o,
            high=df.h,
            low=df.l,
            close=df.c,
            name="NIFTY 5m",
            increasing_line_color="#2a9d8f",
            decreasing_line_color="#e76f51",
        )
    )
    first_bar = next((b for b in regular if b.exchange_time.strftime("%H:%M") == "09:15"), None)
    state = replayer.ctx[first_bar.exchange_time].strategy_state() if first_bar else {}
    x0, x1 = df.t.min(), df.t.max() + pd.Timedelta(minutes=5)
    notes: list[str] = []

    def hline(y: float | None, name: str, dash: str = "dot") -> None:
        if y is not None:
            fig.add_shape(
                type="line",
                x0=x0,
                x1=x1,
                y0=y,
                y1=y,
                line={"color": C_LEVEL, "width": 1, "dash": dash},
            )
            fig.add_annotation(
                x=x0,
                y=y,
                text=f"{name} {y:,.1f}",
                showarrow=False,
                xanchor="left",
                yanchor="bottom",
                font={"size": 10, "color": C_LEVEL},
            )

    hline(state.get("pdh"), "PDH")
    hline(state.get("pdl"), "PDL")
    if (
        row.manual_strategy == "PDR"
        and state.get("pd_res_body") is not None
        and state.get("pdh") is not None
    ):
        fig.add_hrect(
            y0=state["pd_res_body"],
            y1=state["pdh"],
            fillcolor="#e69f00",
            opacity=0.15,
            line_width=0,
            annotation_text="previous-day resistance zone",
            annotation_position="top left",
        )
    if (
        row.manual_strategy == "PDS"
        and state.get("pdl") is not None
        and state.get("pd_supp_body_top") is not None
    ):
        fig.add_hrect(
            y0=state["pdl"],
            y1=state["pd_supp_body_top"],
            fillcolor="#e69f00",
            opacity=0.15,
            line_width=0,
            annotation_text="previous-day support zone",
            annotation_position="bottom left",
        )
    if row.manual_strategy == "IRF" and first_bar is not None:
        hline(first_bar.high, "IRH", "dash")
        hline(first_bar.low, "IRL", "dash")

    mt = pd.Timestamp(f"{row.date} {row.manual_entry_time}")
    fig.add_vline(x=mt, line={"color": C_MANUAL, "dash": "dash", "width": 2})
    fig.add_annotation(
        x=mt,
        y=df.h.max(),
        text=f"MANUAL {row.manual_entry_time} ({row.manual_outcome})",
        showarrow=True,
        ay=-30,
        font={"color": C_MANUAL},
        bgcolor="white",
    )
    if row.automated_signal_time:
        st = pd.Timestamp(row.automated_signal_time)
        fig.add_vline(x=st, line={"color": C_SIGNAL, "dash": "dot", "width": 2})
        fig.add_annotation(
            x=st,
            y=df.l.min(),
            text=f"V3 SIGNAL {st:%H:%M} ({row.automated_method})",
            showarrow=True,
            ay=30,
            font={"color": C_SIGNAL},
            bgcolor="white",
        )
    if row.automated_trade_entry_time and row.automated_nifty_entry_price != "":
        et, xt = pd.Timestamp(row.automated_trade_entry_time), pd.Timestamp(row.automated_exit_time)
        fig.add_trace(
            go.Scatter(
                x=[et],
                y=[float(row.automated_nifty_entry_price)],
                mode="markers",
                marker={"symbol": "triangle-up", "size": 13, "color": C_ENTRY},
                name="V3 entry",
            )
        )
        xp = None
        d = next(
            (b for b in regular if b.exchange_time.replace(tzinfo=None) == xt.to_pydatetime()), None
        )
        if d is not None and row.automated_exit_reason == "TARGET" and row.automated_target != "":
            xp = float(row.automated_target)
        elif (
            d is not None
            and row.automated_exit_reason in ("STOP_LOSS", "INTRABAR_SL_CONFLICT")
            and row.automated_stop_loss != ""
        ):
            xp = float(row.automated_stop_loss)
        if xp is None:
            xp = float(row.automated_nifty_entry_price) + float(
                row.automated_net_index_points or 0
            ) * (1 if row.automated_direction == "BUY" else -1)
        fig.add_trace(
            go.Scatter(
                x=[xt],
                y=[xp],
                mode="markers",
                marker={"symbol": "x", "size": 12, "color": C_EXIT},
                name=f"V3 exit ({row.automated_exit_reason})",
            )
        )
        for y, nm in ((row.automated_stop_loss, "stop"), (row.automated_target, "target")):
            if y != "":
                fig.add_shape(
                    type="line",
                    x0=et,
                    x1=xt,
                    y0=float(y),
                    y1=float(y),
                    line={
                        "color": C_EXIT if nm == "stop" else C_ENTRY,
                        "width": 1.5,
                        "dash": "dash",
                    },
                )
                fig.add_annotation(
                    x=xt,
                    y=float(y),
                    text=f"{nm} {float(y):,.1f}",
                    showarrow=False,
                    xanchor="left",
                    font={"size": 10},
                )
        notes.append(f"V3 exit: {row.automated_exit_reason}, {row.automated_net_index_points} pts")
    if row.portfolio_accepted == "NO" and row.valid_match_le_10min == "YES":
        notes.append(f"NOT in portfolio: {row.rejection_reason}")
    if row.mismatch_explanation:
        notes.append(row.mismatch_explanation[:230])
    title = f"#{row.manual_serial} {row.date} {row.manual_strategy} - {label}"
    subtitle = " | ".join(
        [f"category {row.match_category}", f"outcome {row.outcome_agreement}"] + notes
    )
    fig.update_layout(
        title={"text": f"{title}<br><sup>{subtitle}</sup>"},
        xaxis_rangeslider_visible=False,
        height=560,
        legend={"orientation": "h"},
        **LAYOUT,
    )
    return fig


def build_dashboard(
    rec: Reconciliation,
    replayer: DayReplayer,
    run_id: str,
    v1_reference: dict[str, Any] | None = None,
) -> str:
    h = rec.headline
    parts: list[str] = []
    first = True

    def add(fig: go.Figure) -> None:
        nonlocal first
        parts.append(fig.to_html(full_html=False, include_plotlyjs=True if first else False))
        first = False

    kpis = [
        ("Manual trades", f"{h['manual_trades']}"),
        (
            "Eligible",
            f"{h['eligible']}  (RF {h['excluded_rf']}, no session {h['excluded_no_session']})",
        ),
        (
            "Valid signal matches (<=10 min)",
            f"{h['valid']}/{h['eligible']} = {h['signal_match_rate_pct']}%",
        ),
        ("Exact", f"{h['exact']}/{h['eligible']} = {h['exact_match_rate_pct']}%"),
        (
            "Outcome agreement",
            f"{h['outcome_agreement_fraction']} = {h['outcome_agreement_pct']}% ({h['outcome_agreement_pct_excluding_be']}% excl. manual BE)",
        ),
        (
            "Accepted by FIFO portfolio",
            f"{h['portfolio_accepted']}/{h['valid']} = {h['portfolio_acceptance_pct_of_matched']}%",
        ),
    ]
    cards = "".join(
        f"<div class='card'><div class='k'>{k}</div><div class='v'>{v}</div></div>" for k, v in kpis
    )
    figs = [
        _counts_figure(rec),
        _match_rate_figure(rec),
        _category_figure(rec),
        _matrix_figure(rec),
        _reasons_figure(rec),
        _offset_figure(rec),
    ]
    body = []
    for f in figs:
        add(f)
        body.append(parts.pop())
    cases = []
    for label, row in _pick_cases(rec):
        f = _case_figure(replayer, label, row)
        if f is not None:
            add(f)
            cases.append(parts.pop())
    v1_html = ""
    if v1_reference:
        v1_html = (
            "<h2>Reference baseline (frozen V1 ledger, same matcher; reference only)</h2>"
            f"<p>V1 (IRF Method 1 portfolio, 353 trades, legacy engine): valid matches {v1_reference['valid']}/{v1_reference['eligible']} "
            f"= {v1_reference['rate']}%; exact {v1_reference['exact']}. Shown to put V3 in context only.</p>"
        )
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Manual journal vs V3 backtest</title>
<style>:root{{--bg:#fff;--fg:#1d2733;--card:#f4f6f8;--mut:#5b6b7b}}
@media (prefers-color-scheme: dark){{:root{{--bg:#12171d;--fg:#e6edf3;--card:#1c242d;--mut:#9fb0c0}}}}
body{{background:var(--bg);color:var(--fg);font-family:Inter,Helvetica,Arial,sans-serif;margin:0 auto;max-width:1200px;padding:16px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:16px 0}}
.card{{background:var(--card);border-radius:10px;padding:12px 14px}}.k{{color:var(--mut);font-size:12px}}.v{{font-size:18px;font-weight:600;margin-top:4px}}
.note{{background:var(--card);border-left:4px solid #e69f00;padding:10px 14px;border-radius:6px;margin:12px 0;font-size:14px}}
h1{{font-size:24px}}h2{{font-size:18px;margin-top:28px}}</style></head><body>
<h1>Manual trading journal vs V3 automated backtest</h1>
<p style='color:var(--mut)'>Run <code>{run_id}</code>. NIFTY index points only; the journal contains no direction, method, prices, stops or targets (all UNKNOWN). Times are IST candle-open labels.</p>
<div class='note'><b>Read before interpreting:</b> a match means the V3 engine produced a signal for the same date and strategy within 10 minutes of the journal time. It does not prove the manual trade followed the same rule or level. The engine has no break-even exit, so manual BE outcomes (23% of the journal) can never agree.</div>
<div class='cards'>{cards}</div>
{"".join(body)}
<h2>Selected days (real candles; manual time, V3 signal, entry/exit, stop/target and previous-day levels)</h2>
{"".join(cases)}
{v1_html}
</body></html>"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
