"""Tests for the pure decision helpers in dashboard.views (SYNTHETIC data)."""

from __future__ import annotations

import subprocess
import sys
from datetime import date, datetime, timedelta

import pandas as pd
from test_dash_synthetic import (
    DAY,
    synthetic_snapshot,
    synthetic_trade,
    write_snapshot,
    write_trades_csv,
)

from trading_platform.dashboard import data, explain, views

DAY_D = date.fromisoformat(DAY)


def _bundle(tmp_path, snap=None, trades=None, source=data.SOURCE_LIVE, age=1.0):
    if snap is not None:
        snap = dict(snap)
        snap["written_at"] = (datetime.now() - timedelta(seconds=age)).strftime("%Y-%m-%dT%H:%M:%S") + "+05:30"
        write_snapshot(tmp_path / ("replay/live_state.json" if source == data.SOURCE_REPLAY else "live_state.json"), snap)
    if trades is not None:
        sub = "replay/journal" if source == data.SOURCE_REPLAY else "journal"
        write_trades_csv(tmp_path / sub / "trades.csv", trades)
    return data.load_source(tmp_path, source)


def _levels(banners):
    return [lvl for lvl, _ in banners]


def _text(banners):
    return " | ".join(t for _, t in banners)


def test_banner_no_session_reports_age(tmp_path) -> None:
    b = views.compute_banners(_bundle(tmp_path))
    assert "No active session" in _text(b) and _levels(b) == ["info"]
    stale = views.compute_banners(_bundle(tmp_path, synthetic_snapshot(), age=7200))
    assert "No active session" in _text(stale) and "Last snapshot age: 2.0h" in _text(stale)


def test_banner_engine_states(tmp_path) -> None:
    for state, level in (("BLOCKED", "error"), ("RECOVERY_REQUIRED", "error"), ("HALTED_RISK", "warning")):
        b = views.compute_banners(_bundle(tmp_path, synthetic_snapshot(engine_state=state)))
        assert (level, True) in [(lv, state in t) for lv, t in b], state


def test_banner_shadow_only_info(tmp_path) -> None:
    b = views.compute_banners(_bundle(tmp_path, synthetic_snapshot()))
    assert any(lv == "info" and "signals logged, no virtual fills" in t for lv, t in b)
    b2 = views.compute_banners(_bundle(tmp_path, synthetic_snapshot(tier="RESEARCH_PAPER")))
    assert not any("no virtual fills" in t for _, t in b2)


def test_banner_replay_and_exit_pending_and_connection(tmp_path) -> None:
    snap = synthetic_snapshot(mode="REPLAY", position=True)
    snap["position"]["status"] = "EXIT_PENDING"
    snap["connection"].update(status="DEGRADED", stale=True, candle_trusted=False)
    text = _text(views.compute_banners(_bundle(tmp_path, snap, source=data.SOURCE_REPLAY)))
    assert "REPLAY SIMULATED" in text and "EXIT_PENDING" in text
    assert "DEGRADED" in text and "Candle feed not trusted" in text


def test_banner_live_view_rejects_replay_snapshot(tmp_path) -> None:
    write_snapshot(tmp_path / "live_state.json", synthetic_snapshot(mode="REPLAY"))
    text = _text(views.compute_banners(data.load_source(tmp_path, data.SOURCE_LIVE)))
    assert "No active session" in text and "REPLAY" in text


def test_assemble_chart_uses_only_snapshot_candles_for_session(tmp_path) -> None:
    snap = synthetic_snapshot(position=True)
    snap["candles"] = snap["candles"][:5]  # restart: only 5 candles known
    hist5 = pd.DataFrame()
    ci = views.assemble_chart(_bundle(tmp_path, snap), DAY_D, ["ORB"], hist5, hist5)
    assert len(ci.candles) == 5 and ci.from_snapshot
    assert ci.levels["PDH"] == 25040.0
    kinds = sorted(m["kind"] for m in ci.markers)
    assert kinds == ["ENTRY", "SIGNAL"]


def test_assemble_chart_strategy_filter(tmp_path) -> None:
    ci = views.assemble_chart(_bundle(tmp_path, synthetic_snapshot()), DAY_D, ["PDS"],
                              pd.DataFrame(), pd.DataFrame())  # fmt: skip
    assert ci.markers == []  # the only synthetic signal is ORB


def _hist(day: str = "2026-10-20"):
    ts = pd.date_range(f"{day} 09:15", periods=8, freq="5min")
    h5 = pd.DataFrame({"timestamp": ts, "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0,
                       "volume": 0, "date": [t.date() for t in ts]})  # fmt: skip
    h1 = pd.DataFrame({"timestamp": pd.to_datetime(["2026-10-19"]), "open": 1.0, "high": 110.0,
                       "low": 90.0, "close": 100.0, "volume": 0, "date": [date(2026, 10, 19)]})  # fmt: skip
    return h5, h1


def test_assemble_chart_historical_day_with_trade_markers_only_when_candles(tmp_path) -> None:
    h5, h1 = _hist()
    trades = [synthetic_trade(1, day="2026-10-20", hhmm="09:20"),
              synthetic_trade(2, day="2026-10-21", hhmm="09:20")]  # fmt: skip
    b = _bundle(tmp_path, trades=trades)
    ci = views.assemble_chart(b, date(2026, 10, 20), ["ORB"], h5, h1)
    assert len(ci.candles) == 8 and not ci.from_snapshot
    assert ci.levels["PDH"] == 110.0 and ci.levels["PDL"] == 90.0 and "prev_date" not in ci.levels
    assert ci.levels["support_zone"] == [24950.0, 24960.0]  # recorded with the trade
    assert {m["trade_id"] for m in ci.markers} == {"T-SYNTH-001"}  # trade 2's date has no candles
    assert sorted(m["kind"] for m in ci.markers) == ["ENTRY", "EXIT"]
    # day without candles: nothing drawn, no markers
    ci2 = views.assemble_chart(b, date(2026, 10, 21), ["ORB"], h5, h1)
    assert ci2.candles.empty and ci2.markers == [] and "No validated candles" in ci2.caption


def test_assemble_chart_no_snapshot_no_day_is_empty(tmp_path) -> None:
    ci = views.assemble_chart(_bundle(tmp_path), None, ["ORB"], pd.DataFrame(), pd.DataFrame())
    assert ci.candles.empty and ci.markers == []


def test_trade_table_filters_and_columns(tmp_path) -> None:
    rows = [synthetic_trade(1), synthetic_trade(2, strategy="PDS", day="2026-10-27")]
    b = _bundle(tmp_path, trades=rows)
    t = views.trade_table(b.trades.df, ["ORB", "PDS"])
    assert list(t.columns) == views.HISTORY_COLUMNS and list(t["trade_id"]) == ["T-SYNTH-002", "T-SYNTH-001"]
    assert list(views.trade_table(b.trades.df, ["PDS"])["trade_id"]) == ["T-SYNTH-002"]
    t2 = views.trade_table(b.trades.df, ["ORB", "PDS"], (date(2026, 10, 26), date(2026, 10, 26)))
    assert list(t2["trade_id"]) == ["T-SYNTH-001"]
    assert views.trade_table(pd.DataFrame(), ["ORB"]).empty


def test_explanation_markdown_marks_missing_fields() -> None:
    row = synthetic_trade(1)
    row["trigger_candle_json"] = ""
    opened, closed = views.explanation_markdown(explain.explain_trade(row))
    assert "WHY THE TRADE OPENED" in opened and "WHY THE TRADE CLOSED" in closed
    assert "*not recorded*" in opened and "Final net P&L" in closed
    assert "close_above_PDH" in opened


def test_formatters() -> None:
    assert views.fmt_inr(1234.5, True) == "+₹1,234.50" and views.fmt_inr(-5) == "-₹5.00"
    assert views.fmt_inr(None) == "n/a" and views.fmt_pf(float("inf")).startswith("inf")
    assert views.fmt_pts(-3) == "-3.00 pts" and views.fmt_pct(None) == "n/a"


def test_dashboard_imports_no_broker_or_order_code() -> None:
    code = (
        "import sys\n"
        "import trading_platform.dashboard.data, trading_platform.dashboard.views\n"
        "import trading_platform.dashboard.charts, trading_platform.dashboard.explain\n"
        "bad=[m for m in sys.modules if m.startswith(('trading_platform.brokers',"
        "'trading_platform.execution','py5paisa','trading_platform.paper'))]\n"
        "print('BAD', bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={"PYTHONPATH": "src", "PATH": ""}, cwd=str(data.PROJECT_ROOT))  # fmt: skip
    assert out.stdout.strip() == "BAD []", out.stdout + out.stderr
