"""Unit tests for dashboard.charts (SYNTHETIC candles)."""

from __future__ import annotations

import pandas as pd
import pytest
from test_dash_synthetic import DAY, synthetic_candles, synthetic_trade

from trading_platform.dashboard import charts, data


def _candles(n: int = 12, source: str | None = None) -> pd.DataFrame:
    snap = {"candles": synthetic_candles(n=n)}
    df = data.snapshot_candles(snap)
    if source:
        df["source"] = source
    return df


def _traces(fig, name):
    return [t for t in fig.data if t.name == name]


def test_exactly_the_given_candles_are_drawn() -> None:
    c = _candles(12)
    fig = charts.build_candlestick(c)
    (trace,) = _traces(fig, "NIFTY 5m")
    assert len(trace.x) == 12
    assert list(trace.close) == list(c["close"])
    assert fig.layout.meta["candle_count"] == 12


def test_empty_candles_draw_nothing_and_say_so() -> None:
    fig = charts.build_candlestick(pd.DataFrame(columns=["time", "open", "high", "low", "close"]),
                                   levels={"PDH": 25000.0})
    assert len(fig.data) == 0 and len(fig.layout.shapes) == 0
    assert any("No validated candles" in a.text for a in fig.layout.annotations)


def test_sampled_candles_use_separate_non_authoritative_trace() -> None:
    c = _candles(4)
    c.loc[3, "source"] = "SAMPLED_TICKS"
    fig = charts.build_candlestick(c)
    assert len(_traces(fig, "NIFTY 5m")[0].x) == 3
    assert len(_traces(fig, "Sampled (non-authoritative)")[0].x) == 1


def test_level_lines_and_zones_have_named_shapes() -> None:
    levels = {"PDH": 25040.0, "PDL": 24950.0, "IRH": 25020.0, "EMA200": None,
              "support_zone": [24955.0, 24965.0], "resistance_zone": [25035.0, 25045.0],
              "notes": "ignored text"}  # fmt: skip
    fig = charts.build_candlestick(_candles(), levels=levels)
    shapes = {s.name: s for s in fig.layout.shapes}
    assert {"PDH", "PDL", "IRH", "support_zone", "resistance_zone"} <= set(shapes)
    assert "EMA200" not in shapes and "notes" not in shapes
    assert shapes["PDH"].type == "line" and shapes["PDH"].y0 == 25040.0
    assert shapes["support_zone"].type == "rect"
    assert (shapes["support_zone"].y0, shapes["support_zone"].y1) == (24955.0, 24965.0)


def test_far_off_levels_are_not_drawn_but_reported() -> None:
    fig = charts.build_candlestick(_candles(), levels={"PDH": 30000.0})
    assert "PDH" not in {s.name for s in fig.layout.shapes}
    assert any("PDH" in x for x in fig.layout.meta["off_chart_levels"])


def test_marker_counts_and_shapes_by_exit_reason() -> None:
    markers = [
        {"kind": "SIGNAL", "t": f"{DAY} 09:30:00", "price": 25005.0, "strategy": "ORB"},
        {"kind": "SIGNAL", "t": f"{DAY} 09:35:00", "price": 25006.0, "strategy": "PDS"},
        {"kind": "ENTRY", "t": f"{DAY} 09:40:05", "price": 25005.0, "direction": "BULLISH"},
        {"kind": "ENTRY", "t": f"{DAY} 09:50:05", "price": 25010.0, "direction": "BEARISH"},
        {"kind": "EXIT", "t": f"{DAY} 09:45:00", "price": 25065.0, "exit_reason": "TARGET"},
        {"kind": "EXIT", "t": f"{DAY} 09:55:00", "price": 24975.0, "exit_reason": "STOP"},
        {"kind": "EXIT", "t": f"{DAY} 10:00:00", "price": 24980.0, "exit_reason": "STOP"},
    ]
    fig = charts.build_candlestick(_candles(), markers=markers)
    assert len(_traces(fig, "Signal")[0].x) == 2
    entry = _traces(fig, "Entry")[0]
    assert len(entry.x) == 2 and list(entry.marker.symbol) == ["triangle-up", "triangle-down"]
    assert len(_traces(fig, "Exit: TARGET")[0].x) == 1
    assert len(_traces(fig, "Exit: STOP")[0].x) == 2
    assert _traces(fig, "Exit: TARGET")[0].marker.symbol != _traces(fig, "Exit: STOP")[0].marker.symbol
    assert fig.layout.meta["dropped_markers"] == 0


def test_markers_outside_candle_span_or_malformed_are_dropped() -> None:
    markers = [
        {"kind": "ENTRY", "t": "2026-10-26 15:00:00", "price": 25005.0},  # no candle there
        {"kind": "ENTRY", "t": "2026-10-25 09:40:00", "price": 25005.0},  # other day
        {"kind": "ENTRY", "t": None, "price": 25005.0},
        {"kind": "BOGUS", "t": f"{DAY} 09:40:00", "price": 25005.0},
        {"kind": "ENTRY", "t": f"{DAY} 09:40:00", "price": None},
        {"kind": "ENTRY", "t": f"{DAY} 09:40:00", "price": 25005.0},
    ]
    fig = charts.build_candlestick(_candles(), markers=markers)
    assert len(_traces(fig, "Entry")[0].x) == 1
    assert fig.layout.meta["dropped_markers"] == 5


def test_trade_adds_stop_target_lines_and_annotations() -> None:
    row = synthetic_trade(1)
    c = _candles()
    markers = charts.trade_markers(pd.DataFrame([{**row, "entry_time": pd.Timestamp(f"{DAY} 09:50:10"),
                                                   "exit_time": pd.Timestamp(f"{DAY} 10:10:10"),
                                                   "session_date": pd.Timestamp(DAY).date()}]), c)
    fig = charts.build_candlestick(c, markers=markers, trade=row)
    names = {s.name: s for s in fig.layout.shapes}
    assert names["Stop"].y0 == 24975.0 and names["Target"].y0 == 25065.0
    texts = [a.text for a in fig.layout.annotations]
    assert any("ORB M1 entry" in t and "synthetic breakout" in t for t in texts)
    assert any("ORB M1 exit: TARGET" in t for t in texts)


def test_trade_markers_only_for_dates_with_candles() -> None:
    c = _candles()
    t_in = {**synthetic_trade(1), "entry_time": pd.Timestamp(f"{DAY} 09:50:10"),
            "exit_time": pd.Timestamp(f"{DAY} 10:10:10")}  # fmt: skip
    t_out = {**synthetic_trade(2, day="2026-10-20"), "entry_time": pd.Timestamp("2026-10-20 09:50:10"),
             "exit_time": pd.Timestamp("2026-10-20 10:10:10")}  # fmt: skip
    ms = charts.trade_markers(pd.DataFrame([t_in, t_out]), c)
    assert {m["trade_id"] for m in ms} == {"T-SYNTH-001"}
    assert sorted(m["kind"] for m in ms) == ["ENTRY", "EXIT"]
    assert charts.trade_markers(pd.DataFrame([t_in]), c.iloc[0:0]) == []


def test_merge_markers_dedups_same_trade_and_same_signal() -> None:
    a = [{"kind": "ENTRY", "trade_id": "x", "t": "t1", "price": 1.0},
         {"kind": "SIGNAL", "t": "t0", "price": 2.0, "strategy": "ORB"}]  # fmt: skip
    b = [{"kind": "ENTRY", "trade_id": "x", "t": "t1b", "price": 1.1},
         {"kind": "SIGNAL", "t": "t0", "price": 2.0, "strategy": "ORB"},
         {"kind": "SIGNAL", "t": "t9", "price": 3.0, "strategy": "ORB"}]  # fmt: skip
    assert len(charts.merge_markers(a, b)) == 3


def test_multi_day_candles_get_rangebreaks_single_day_does_not() -> None:
    one = charts.build_candlestick(_candles())
    assert not one.layout.xaxis.rangebreaks
    c2 = pd.concat([_candles(3), _candles(3).assign(time=lambda d: d["time"] + pd.Timedelta(days=1))])
    assert charts.build_candlestick(c2).layout.xaxis.rangebreaks


def test_equity_and_bar_figures() -> None:
    curve = pd.DataFrame({"time": [None, 1, 2], "equity": [100.0, 120.0, 110.0],
                          "pnl": [0, 20, -10], "trade_id": ["START", "a", "b"]})  # fmt: skip
    fig = charts.build_equity_chart(curve, 100.0, title="t", unit="₹")
    assert len(fig.data) == 1 and list(fig.data[0].y) == [100.0, 120.0, 110.0]
    assert len(charts.build_equity_chart(curve.iloc[:1], 100.0, title="t", unit="₹").data) == 0
    bars = charts.build_pnl_bars(pd.DataFrame({"k": ["a", "b"], "v": [5.0, -3.0]}), "k", "v",
                                 title="t", unit="₹")
    assert list(bars.data[0].marker.color) == [charts.GREEN, charts.VERMILLION]
    assert len(charts.build_pnl_bars(pd.DataFrame(), "k", "v", title="t", unit="₹").data) == 0


@pytest.mark.parametrize("reason", list(charts.EXIT_STYLES))
def test_every_exit_reason_has_a_style(reason: str) -> None:
    fig = charts.build_candlestick(
        _candles(), markers=[{"kind": "EXIT", "t": f"{DAY} 09:45:00", "price": 25000.0,
                              "exit_reason": reason}])  # fmt: skip
    assert len(_traces(fig, f"Exit: {reason}")) == 1
