"""Regression tests for the verified terminal / dashboard truthfulness findings (SYNTHETIC data).

Every snapshot / trade here is made up in-test.  Rule ids and recorded-field shapes mirror what
the real engine writes (``method`` / ``entry_reason`` are rule ids, levels are recorded in the
strategy's own lower-case names) so the defects cannot hide behind demo-friendly strings.
"""

from __future__ import annotations

import json
import os
import tomllib
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from rich.console import Console
from test_dash_synthetic import (
    DAY,
    synthetic_candles,
    synthetic_snapshot,
    synthetic_trade,
    write_snapshot,
    write_trades_csv,
)

from trading_platform.dashboard import charts, data, explain, text, views
from trading_platform.live import terminal as term
from trading_platform.live.providers.base import CheckResult

NOW = datetime(2026, 10, 28, 10, 30, 0, tzinfo=term.IST)
ORB_RULE = "ORB_METHOD_1_PDH_BREAKOUT"
ORB_SENTENCE = "confirmation above the previous-day high (PDH)"
REPO = Path(__file__).resolve().parents[2]


def _lines(renderable, width: int = 120) -> list[str]:
    console = Console(record=True, width=width, force_terminal=False, color_system=None)
    console.print(renderable)
    return console.export_text().splitlines()


def _snap(kind: str = "open_profit", **position_updates) -> dict:
    snap = term.illustrative_snapshot(kind, NOW)
    if position_updates and snap["position"]:
        snap["position"].update(position_updates)
    return snap


# =================================================================== terminal: layout


def test_position_panel_is_directly_under_the_header() -> None:
    text_ = "\n".join(_lines(term.render_dashboard(_snap(), NOW)))
    assert text_.index("Active Position") < text_.index("Virtual Account")
    assert text_.index("Active Position") < text_.index("Strategies")
    assert text_.index("NIFTY OPTIONS") < text_.index("Active Position")


def test_frame_fits_normal_terminal_heights_with_position_visible() -> None:
    for height in (24, 30, 40, 50):
        for kind in ("open_profit", "exit_pending", "stale_bid"):
            lines = _lines(term.render_dashboard(_snap(kind), NOW, height=height, width=120))
            out = "\n".join(lines)
            assert len(lines) <= height - 1, (height, kind, len(lines))
            for needle in ("Active Position", "Stop NIFTY", "Target NIFTY", "Option BID", "To stop"):
                assert needle in out, (height, kind, needle)
            assert "NIFTY OPTIONS" in out  # header kept
    # the events panel is sized to what is left: absent when short, present when tall
    assert "Recent Events" not in "\n".join(
        _lines(term.render_dashboard(_snap(), NOW, height=24, width=120))
    )
    tall = _lines(term.render_dashboard(_snap(), NOW, height=60, width=120))
    assert "Recent Events" in "\n".join(tall) and "Virtual Account" in "\n".join(tall)


def test_events_panel_uses_remaining_height_only() -> None:
    snap = _snap("flat")
    snap["recent_events"] = [
        {"t": NOW.isoformat(), "kind": "INFO", "text": f"event-number-{i}"} for i in range(40)
    ]
    short = "\n".join(_lines(term.render_dashboard(snap, NOW, height=44, width=120)))
    tall = "\n".join(_lines(term.render_dashboard(snap, NOW, height=70, width=120)))
    assert short.count("event-number-") < tall.count("event-number-") <= term.MAX_EVENT_LINES
    assert "event-number-39" in short and "event-number-39" in tall


def test_live_frame_on_a_short_terminal_still_shows_the_position() -> None:
    console = Console(record=True, width=120, height=24, force_terminal=True, color_system="standard")
    clock = iter(range(1000, 100000, 10))
    ui = term.TerminalUI(console=console, now_fn=lambda: NOW, monotonic=lambda: float(next(clock)))
    with ui:
        ui.update(_snap(), [])
    out = console.export_text()
    assert "Active Position" in out and "Stop NIFTY" in out
    assert not any(line.strip() in {"...", "…"} for line in out.splitlines())  # no crop marker


def test_reference_levels_row_in_header() -> None:
    out = "\n".join(_lines(term.render_dashboard(_snap("flat"), NOW)))
    for needle in ("Levels:", "PDH 20,080.00", "PDL 19,920.00", "PDC 19,990.00", "EMA200 19,950.50",
                   "IRH 20,012.00", "IRL 19,985.00"):  # fmt: skip
        assert needle in out, needle
    snap = _snap("flat")
    snap["levels"] = {"PDH": 1.0}
    missing = "\n".join(_lines(term.render_dashboard(snap, NOW)))
    assert "PDH 1.00" in missing and "PDL n/a" in missing


# =================================================================== terminal: wording


def test_human_labels_everywhere_in_the_terminal() -> None:
    snap = _snap("open_profit", method=ORB_RULE, entry_reason=ORB_RULE)
    out = "\n".join(_lines(term.render_dashboard(snap, NOW)))
    assert "ORB M1" in out and ORB_SENTENCE in out
    assert "Orb Method" not in out and "Pdh Breakout" not in out
    assert f"(rule {ORB_RULE})" in out  # raw id only as a secondary detail
    opened = "\n".join(_lines(term.render_trade_opened(snap["position"] | {"remaining_cash": 1.0})))
    assert "ORB M1" in opened and ORB_SENTENCE in opened and "Orb Method" not in opened
    assert "Reason:" in opened and ORB_SENTENCE in opened
    closed = "\n".join(_lines(term.render_trade_closed(
        {"strategy": "IRF", "method": "IRF_METHOD_2_SUPPORT_BREAK", "direction": "BEARISH",
         "option_type": "PE", "exit_reason": "TARGET", "net_pnl": 10.0, "label": "REPLAY_SIMULATED"}
    )))
    assert "IRF M2" in closed and "Irf Method" not in closed and "BUY PE" in closed
    assert "Underlying NIFTY reached the target level" in closed
    assert "SIMULATED, NOT LIVE" in closed and "not a trading result" in closed


def test_engine_supplied_labels_win_and_strategy_table_uses_sentences() -> None:
    snap = _snap("open_profit", strategy_label="ORB M1", reason_text="custom sentence",
                 rule_id=ORB_RULE, direction_label="BUY CE")  # fmt: skip
    out = "\n".join(_lines(term.render_dashboard(snap, NOW)))
    assert "custom sentence" in out
    flat = _snap("flat")
    flat["strategies"][1]["last_signal"] = {"direction": "BULLISH", "candle_open_time": NOW.isoformat(),
                                            "reason": ORB_RULE}  # fmt: skip
    table = "\n".join(_lines(term.render_strategies(flat)))
    assert "SIGNAL BUY CE" in table and ORB_SENTENCE in table and ORB_RULE not in table


def test_done_for_day_strategy_says_not_met_not_waiting() -> None:
    row = {"state": "DONE_FOR_DAY", "conditions_failed": ["gap down (open < prev close)"]}
    assert "not met: gap down" in term._strategy_last_activity(row).plain
    row["state"] = "MONITORING"
    assert "waiting: gap down" in term._strategy_last_activity(row).plain


def test_event_kind_is_not_repeated_in_the_text() -> None:
    assert term.event_body("TRADE_OPENED", "VIRTUAL TRADE OPENED ORB M1 BUY CE") == "ORB M1 BUY CE"
    assert term.event_body("TRADE_CLOSED", "VIRTUAL TRADE CLOSED ORB M1 TARGET") == "ORB M1 TARGET"
    assert term.event_body("SIGNAL", "ORB BULLISH signal") == "ORB BULLISH signal"
    snap = _snap("flat")
    snap["recent_events"] = [{"t": NOW.isoformat(), "kind": "TRADE_OPENED",
                              "text": "VIRTUAL TRADE OPENED ORB M1 BUY CE"}]  # fmt: skip
    out = "\n".join(_lines(term.render_events(snap)))
    assert "TRADE_OPENED" in out and "VIRTUAL TRADE OPENED" not in out and "ORB M1 BUY CE" in out


def test_plain_mode_prints_panels_after_the_related_events() -> None:
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ui = term.TerminalUI(console=console, now_fn=lambda: NOW, monotonic=lambda: 0.0)

    def printed() -> str:
        return console.export_text(clear=False)

    snap = _snap("open_profit")
    events = [
        {"t": NOW.isoformat(), "kind": "SIGNAL", "text": "ORB BULLISH signal"},
        {"t": NOW.isoformat(), "kind": "TRADE_OPENED", "text": "VIRTUAL TRADE OPENED ORB M1"},
    ]
    ui.show_trade_opened(snap["position"])  # the app calls this BEFORE update(...)
    assert printed() == ""  # nothing printed yet: queued
    ui.update(snap, events)
    out = printed()
    assert out.index("ORB BULLISH signal") < out.index("TRADE_OPENED") < out.index("VIRTUAL TRADE OPENED")
    assert out.count("VIRTUAL TRADE OPENED") == 1  # prefix not duplicated on the event line
    ui.show_trade_closed(term.illustrative_trade(NOW))
    ui.show_message("bye")  # a later message flushes the queue first
    out = printed()
    assert out.index("VIRTUAL TRADE OPENED") < out.index("VIRTUAL TRADE CLOSED") < out.index("bye")


def test_pnl_labels_distinguish_net_estimate_from_gross_unrealized() -> None:
    out = "\n".join(_lines(term.render_dashboard(_snap(), NOW)))
    assert "P&L (net est.)" in out and "Unrealized (gross)" in out


def test_stale_bid_is_marked_and_pnl_flagged() -> None:
    out = "\n".join(_lines(term.render_dashboard(_snap("stale_bid"), NOW)))
    assert "Option BID:" in out and "87.90 (age 42s STALE)" in out
    assert "(stale bid)" in out
    fresh = "\n".join(_lines(term.render_dashboard(_snap("open_profit"), NOW)))
    assert "STALE)" not in fresh and "(stale bid)" not in fresh


def test_missing_bid_says_no_valid_bid() -> None:
    snap = _snap("open_profit", current_bid=None, net_pnl_estimate=None, gross_pnl=None, pnl_pct=None)
    out = "\n".join(_lines(term.render_dashboard(snap, NOW)))
    assert "n/a - no valid bid" in out
    explicit = _snap("open_profit", bid_is_stale=True, current_bid_age_seconds=9.0)
    assert "(age 9s STALE)" in "\n".join(_lines(term.render_dashboard(explicit, NOW)))


def test_status_line_marks_stale_bid_and_uses_net_label() -> None:
    line = term.status_line(_snap("stale_bid"), NOW)
    assert "pnl(net est.)" in line and "(stale bid)" in line and "ORB M1" in line


def test_market_closed_never_claims_market_open() -> None:
    snap = _snap("flat")
    snap["session"].update(market_status="CLOSED", engine_state="RUNNING",
                           state_reason="RUNNING (market open)")  # fmt: skip
    out = "\n".join(_lines(term.render_dashboard(snap, NOW)))
    assert "market open" not in out and "market closed" in out


# =================================================================== terminal: replay honesty


def _replay_snapshot() -> dict:
    snap = _snap("flat")
    snap["session"].update(mode="REPLAY", label="REPLAY_SIMULATED")
    # an OLD snapshot (or an engine that has not been updated yet) still claims a broker
    snap["connection"].update(broker="5paisa", status="CONNECTED")
    return snap


def test_replay_header_says_no_broker_and_never_connected() -> None:
    snap = _replay_snapshot()
    out = "\n".join(_lines(term.render_dashboard(snap, NOW)))
    assert "Broker: none (SIMULATED)" in out
    assert "SIMULATED (no broker)" in out
    assert "CONNECTED" not in out and "5paisa" not in out
    line = term.status_line(snap, NOW)
    assert "conn=SIMULATED" in line and "CONNECTED" not in line
    live = "\n".join(_lines(term.render_dashboard(_snap("flat"), NOW)))
    assert "Broker: 5paisa" in live and "CONNECTED" in live


def test_replay_ages_use_the_simulated_clock_not_the_wall_clock() -> None:
    snap = _replay_snapshot()
    sim = datetime(2025, 8, 18, 9, 47, 5, tzinfo=term.IST)
    snap["sim_time"] = sim.isoformat()
    snap["written_at"] = sim.isoformat()
    snap["connection"]["last_valid_quote_at"] = sim.isoformat()
    out = "\n".join(_lines(term.render_dashboard(snap, NOW)))  # NOW is 2026: wall clock
    assert "Sim time: 09:47:05 IST" in out
    assert "Data age: 0.0s" in out and "STALE" not in out and "SNAPSHOT STALE" not in out


def test_replay_tier_is_never_an_opt_in() -> None:
    snap = _replay_snapshot()
    snap["strategies"][1]["tier"] = "RESEARCH_PAPER"
    out = "\n".join(_lines(term.render_strategies(snap)))
    assert "REPLAY-SIM" in out and "RESEARCH-PAPER" not in out
    live = _snap("flat")
    assert "RESEARCH-PAPER" in "\n".join(_lines(term.render_strategies(live)))


def test_startup_warning_gets_a_warning_glyph_not_a_tick() -> None:
    out = "\n".join(_lines(term.render_startup_checks([
        CheckResult("spot", True, "WARNING (non-blocking): 0/2 option quotes fresh"),
        CheckResult("config", True, "fine"),
    ])))
    first, second = [ln for ln in out.splitlines() if "spot" in ln or "config" in ln]
    assert "!" in first and "✓" not in first and "✓" in second


# =================================================================== dashboard: freshness


def _write_replay(runtime: Path, *, engine_state: str, sim_day: str = "2025-08-18",
                  age_seconds: float = 1.0, position: bool = False) -> Path:
    snap = synthetic_snapshot(datetime(2025, 8, 18, 11, 1, 15), mode="REPLAY",
                              engine_state=engine_state, day=sim_day, position=position)  # fmt: skip
    snap["sim_time"] = "2025-08-18T11:01:15+05:30"
    path = runtime / "replay" / "live_state.json"
    write_snapshot(path, snap)
    mtime = datetime.now().timestamp() - age_seconds
    os.utime(path, (mtime, mtime))
    return path


def _replay_bundle(runtime: Path) -> data.SourceBundle:
    return data.load_source(runtime, data.SOURCE_REPLAY)


def test_replay_in_progress_is_active_not_stale(tmp_path) -> None:
    _write_replay(tmp_path, engine_state="RUNNING", position=True)
    bundle = _replay_bundle(tmp_path)
    assert bundle.snapshot.status == data.SNAPSHOT_OK and bundle.snapshot.active
    banners = " | ".join(t for _, t in views.compute_banners(bundle))
    assert "stale" not in banners.lower() and "No active session" not in banners
    assert "REPLAY SIMULATED" in banners


def test_finished_replay_says_replay_finished(tmp_path) -> None:
    _write_replay(tmp_path, engine_state="STOPPED", age_seconds=86400.0)
    bundle = _replay_bundle(tmp_path)
    assert bundle.snapshot.finished_replay and not bundle.snapshot.active
    banners = views.compute_banners(bundle)
    text_ = " | ".join(t for _, t in banners)
    assert "REPLAY FINISHED" in text_ and "stale" not in text_.lower()
    assert "No active session" not in text_


def test_replay_whose_process_died_is_stale_by_file_age(tmp_path) -> None:
    _write_replay(tmp_path, engine_state="RUNNING", age_seconds=3600.0)
    bundle = _replay_bundle(tmp_path)
    assert bundle.snapshot.status == data.SNAPSHOT_STALE
    text_ = " | ".join(t for _, t in views.compute_banners(bundle))
    assert "replay stopped updating" in text_ and "436" not in text_


def test_live_freshness_still_uses_written_at(tmp_path) -> None:
    write_snapshot(tmp_path / "live_state.json", synthetic_snapshot(datetime(2026, 10, 26, 8, 0, 0)))
    info = data.load_source(tmp_path, data.SOURCE_LIVE).snapshot
    assert info.status == data.SNAPSHOT_STALE and not info.active


def test_replay_hint_when_a_replay_exists_but_live_does_not(tmp_path) -> None:
    _write_replay(tmp_path, engine_state="STOPPED")
    live = data.load_source(tmp_path, data.SOURCE_LIVE)
    text_ = " | ".join(t for _, t in views.compute_banners(live))
    assert "runtime/replay" in text_ and "Replay (simulated)" in text_
    empty = tmp_path / "other"
    assert "runtime/replay" not in " | ".join(t for _, t in views.compute_banners(
        data.load_source(empty, data.SOURCE_LIVE)))  # fmt: skip


# =================================================================== dashboard: engine not updating


def test_stale_live_snapshot_with_open_position_is_a_top_level_error(tmp_path) -> None:
    for status in ("OPEN", "EXIT_PENDING"):
        snap = synthetic_snapshot(datetime(2026, 10, 26, 8, 0, 0), position=True)
        snap["position"]["status"] = status
        write_snapshot(tmp_path / "live_state.json", snap)
        banners = views.compute_banners(data.load_source(tmp_path, data.SOURCE_LIVE))
        level, msg = banners[0]
        assert level == "error"
        assert "Engine not updating" in msg and "T-SYNTH-OPEN" in msg and status in msg
        assert "NOT monitored" in msg and "Restart the app to recover" in msg


def test_no_error_for_fresh_flat_or_replay_positions(tmp_path) -> None:
    write_snapshot(tmp_path / "live_state.json", synthetic_snapshot(position=True))  # fresh
    fresh = views.compute_banners(data.load_source(tmp_path, data.SOURCE_LIVE))
    assert not any("Engine not updating" in t for _, t in fresh)
    write_snapshot(tmp_path / "live_state.json", synthetic_snapshot(datetime(2026, 10, 26, 8, 0, 0)))
    flat = views.compute_banners(data.load_source(tmp_path, data.SOURCE_LIVE))
    assert not any("Engine not updating" in t for _, t in flat)
    _write_replay(tmp_path, engine_state="RUNNING", age_seconds=3600.0, position=True)
    replay = views.compute_banners(_replay_bundle(tmp_path))
    assert not any("Engine not updating" in t for _, t in replay)


# =================================================================== dashboard: markers / chart


def _snap_markers(text_exit: str = "TARGET @ 25065.00") -> dict:
    return {"markers": [
        {"kind": "ENTRY", "t": f"{DAY}T09:50:10+05:30", "price": 25005.0, "strategy": "ORB",
         "trade_id": "T-SYNTH-001", "text": "ORB PE @ 74.45", "premium": 74.45},
        {"kind": "EXIT", "t": f"{DAY}T10:10:10+05:30", "price": 25065.0, "strategy": "ORB",
         "trade_id": "T-SYNTH-001", "text": text_exit, "premium": 107.45},
    ]}  # fmt: skip


def test_snapshot_exit_markers_carry_the_exit_reason_parsed_from_text() -> None:
    entry, exit_ = data.snapshot_markers(_snap_markers())
    assert exit_["exit_reason"] == "TARGET" and entry["direction"] == "PE"
    assert data.snapshot_markers(_snap_markers("STOP @ 1.0"))[1]["exit_reason"] == "STOP"
    assert "exit_reason" not in data.snapshot_markers(_snap_markers("???"))[1]
    explicit = _snap_markers()
    explicit["markers"][1]["exit_reason"] = "eod"
    assert data.snapshot_markers(explicit)[1]["exit_reason"] == "EOD"


def test_snapshot_only_chart_is_not_labelled_exit_unknown() -> None:
    candles = pd.DataFrame(synthetic_candles()).rename(
        columns={"t": "time", "o": "open", "h": "high", "l": "low", "c": "close"})
    candles["time"] = pd.to_datetime(candles["time"].str.slice(0, 19))
    fig = charts.build_candlestick(candles, markers=data.snapshot_markers(_snap_markers()))
    names = [t.name for t in fig.data]
    assert "Exit: TARGET" in names and not any("UNKNOWN" in str(n) for n in names)
    entry = next(t for t in fig.data if t.name == "Entry")
    assert entry.marker.symbol[0] == "triangle-down"  # PE -> down triangle


def test_merge_prefers_the_ledger_marker_and_enriches_by_trade_id() -> None:
    ledger = {"kind": "EXIT", "t": "a", "price": 1.0, "trade_id": "T1", "exit_reason": "STOP"}
    snap = {"kind": "EXIT", "t": "b", "price": 1.0, "trade_id": "T1", "premium": 3.0}
    merged = charts.merge_markers([ledger], [snap])
    assert len(merged) == 1 and merged[0]["exit_reason"] == "STOP" and merged[0]["premium"] == 3.0
    bare = {"kind": "EXIT", "t": "b", "price": 1.0, "trade_id": "T1", "exit_reason": "UNKNOWN"}
    assert charts.merge_markers([bare], [ledger])[0]["exit_reason"] == "STOP"


def test_assemble_chart_exit_marker_has_ledger_reason(tmp_path) -> None:
    snap = synthetic_snapshot()
    snap["markers"] += _snap_markers()["markers"]
    write_snapshot(tmp_path / "live_state.json", snap)
    write_trades_csv(tmp_path / "journal" / "trades.csv", [synthetic_trade(1, exit_reason="TARGET")])
    bundle = data.load_source(tmp_path, data.SOURCE_LIVE)
    ci = views.assemble_chart(bundle, date.fromisoformat(DAY), ["ORB"], pd.DataFrame(), pd.DataFrame())
    exits = [m for m in ci.markers if m["kind"] == "EXIT"]
    assert len(exits) == 1 and exits[0]["exit_reason"] == "TARGET"


def _flat_candles(n: int = 12, base: float = 24950.0) -> pd.DataFrame:
    times = pd.date_range(f"{DAY} 09:15", periods=n, freq="5min")
    return pd.DataFrame({"time": times, "open": base, "high": base + 10, "low": base - 10,
                         "close": base + 2, "source": "PROVIDER_COMPLETED"})  # fmt: skip


def test_recorded_level_names_are_normalised_for_the_chart() -> None:
    raw = {"pdh": 24673.65, "pdl": 24597.1, "prev_close": 24631.3, "ema_200": 24610.65,
           "level_basis": "PDH_PDL_WICK_EXTREME", "zone_low": 24597.1, "zone_top": 24629.25,
           "zone_bottom": 24657.85}  # fmt: skip
    pds = text.normalize_levels(raw, "PDS")
    assert pds["PDH"] == 24673.65 and pds["PDC"] == 24631.3 and pds["EMA200"] == 24610.65
    assert pds["support_zone"] == [24597.1, 24629.25] and "resistance_zone" not in pds
    pdr = text.normalize_levels(raw, "PDR")
    assert pdr["resistance_zone"] == [24657.85, 24629.25]
    canonical = text.normalize_levels({"PDH": 1.0, "pdh": 2.0})
    assert canonical["PDH"] == 1.0  # an alias never overwrites a canonical key


def test_chart_always_draws_the_levels_recorded_for_the_highlighted_trade() -> None:
    candles = _flat_candles()  # 24940-24960
    levels = {"PDH": 24700.0, "PDL": 24620.0, "support_zone": [24580.0, 24600.0], "IRH": 24960.0}
    plain = charts.build_candlestick(candles, levels=levels)
    assert any("PDH" in s for s in plain.layout.meta["off_chart_levels"])  # dropped without force
    fig = charts.build_candlestick(candles, levels=levels, force_levels=["PDH", "PDL", "support_zone"])
    drawn = {s.name for s in fig.layout.shapes}
    assert {"PDH", "PDL", "support_zone"} <= drawn  # within a few ranges: drawn, axis widened
    lo, hi = fig.layout.yaxis.range
    assert lo < 24580.0 and hi > 24960.0
    far = charts.build_candlestick(candles, levels={"PDH": 22000.0}, force_levels=["PDH"])
    assert any("PDH" in s and "off chart" in s for s in far.layout.meta["off_chart_levels"])
    arrows = [a for a in far.layout.annotations if "PDH 22,000.00" in str(a.text)]
    assert arrows and arrows[0].showarrow  # too far to widen: arrow annotation at the edge


def test_trigger_candle_is_highlighted_from_trigger_candle_json() -> None:
    candles = _flat_candles()
    trade = synthetic_trade(1)
    trade["trigger_candle_json"] = json.dumps({"time": f"{DAY}T09:25:00+05:30", "o": 1, "h": 2, "l": 0, "c": 1})
    fig = charts.build_candlestick(candles, trade=trade)
    shape = next(s for s in fig.layout.shapes if s.name == "trigger candle")
    assert pd.Timestamp(shape.x0) == pd.Timestamp(f"{DAY} 09:25:00")
    assert pd.Timestamp(shape.x1) == pd.Timestamp(f"{DAY} 09:30:00")
    assert any(a.text == "trigger candle" for a in fig.layout.annotations)
    trade["trigger_candle_json"] = ""
    assert not any(s.name == "trigger candle" for s in charts.build_candlestick(candles, trade=trade).layout.shapes)


def test_highlight_levels_uses_trade_levels_and_forces_them() -> None:
    trade = synthetic_trade(1, strategy="PDS")
    trade["levels_json"] = json.dumps({"pdl": 24597.1, "zone_low": 24597.1, "zone_top": 24629.25})
    levels, force = views.highlight_levels({"PDH": 1.0}, trade)
    assert levels["PDL"] == 24597.1 and levels["PDH"] == 1.0
    assert set(force) >= {"PDL", "support_zone"}
    assert views.highlight_levels({"PDH": 1.0}, None) == ({"PDH": 1.0}, [])


# =================================================================== dashboard: wording


def test_tier_captions_never_call_a_replay_an_opt_in() -> None:
    replay = views.tier_caption("RESEARCH_PAPER", "REPLAY_SIMULATED", replay=True)
    assert "opt-in" in replay and "not an opt-in" in replay and "explicit opt-in" not in replay
    live = views.tier_caption("RESEARCH_PAPER", "RESEARCH_PAPER_PROVISIONAL", replay=False)
    assert "explicit opt-in" in live
    assert "no virtual fills" in views.tier_caption("SHADOW_ONLY").lower()
    assert views.tier_caption("n/a") == "Execution tier not reported."


def _real_shaped_trade(n: int = 1) -> dict:
    row = synthetic_trade(n, strategy="IRF")
    row.update(
        method="IRF_METHOD_2_SUPPORT_BREAK", entry_reason="IRF_METHOD_2_SUPPORT_BREAK",
        direction="BEARISH", option_type="PE",
        entry_conditions_json=json.dumps({
            "failed": ["not_traded_back_above_resistance"],
            "satisfied": ["ir_marked_0915", "support_break_close_below_support"]}),
        levels_json=json.dumps({"irh": 24950.55, "irl": 24884.5, "support": 24980.4}),
        trigger_candle_json=json.dumps({"c": 24975.45, "close_time": f"{DAY}T10:30:00+05:30",
                                        "h": 24989.0, "l": 24971.7, "o": 24987.45,
                                        "time": f"{DAY}T10:25:00+05:30"}),
    )  # fmt: skip
    return row


def test_trade_table_shows_labels_and_sentences_not_rule_ids() -> None:
    trades = pd.DataFrame([_real_shaped_trade()])
    trades["session_date"] = date.fromisoformat(DAY)
    table = views.trade_table(trades, ["IRF"])
    assert list(table.columns) == views.HISTORY_COLUMNS
    assert table["strategy"][0] == "IRF M2"
    assert "IRF_METHOD_2" not in table["entry_reason"][0]
    assert "pullback support" in table["entry_reason"][0]


def test_explanation_is_readable_and_not_contradictory() -> None:
    exp = explain.explain_trade(_real_shaped_trade())
    opened, closed = views.explanation_markdown(exp)
    assert "IRF M2" in opened and "BUY PE" in opened
    assert "pullback support" in opened and "Rule id (detail):** IRF_METHOD_2_SUPPORT_BREAK" in opened
    assert "Entry conditions failed" not in opened
    assert "Other checks not met (not required for this setup)" in opened
    assert "not_traded_back_above_resistance" in opened
    assert "{" not in opened and '"o"' not in opened  # no raw JSON
    assert "O 24,987.45  H 24,989.00  L 24,971.70  C 24,975.45" in opened
    assert "opened 10:25" in opened and "closed 10:30" in opened
    assert "IRH 24,950.55" in opened and "IRL 24,884.50" in opened
    assert "TARGET - Underlying NIFTY reached the target level" in closed
    assert "Previous-day levels:** *not recorded*" in opened  # nothing to fill from here


def test_previous_day_levels_filled_from_other_sources_and_labelled(tmp_path) -> None:
    exp = explain.explain_trade(_real_shaped_trade())
    fb = ({"PDH": 24673.65, "PDL": 24597.1}, "verified daily dataset, session 2026-10-23")
    opened, _ = views.explanation_markdown(exp, fb)
    assert "PDH 24,673.65, PDL 24,597.10" in opened
    assert "not recorded with this trade; source: verified daily dataset" in opened
    # sourced from a sibling trade of the same day
    other = synthetic_trade(2)
    write_trades_csv(tmp_path / "journal" / "trades.csv", [_real_shaped_trade(1), other])
    bundle = data.load_source(tmp_path, data.SOURCE_LIVE)
    levels, source = views.previous_day_fallback(date.fromisoformat(DAY), bundle, pd.DataFrame())
    assert levels["PDH"] == 25000.0 and "T-SYNTH-002" in source
    # sourced from the verified daily dataset when nothing else knows
    solo = tmp_path / "solo"
    write_trades_csv(solo / "journal" / "trades.csv", [_real_shaped_trade(1)])
    h1 = pd.DataFrame({"date": [date(2026, 10, 23)], "high": [110.0], "low": [90.0], "close": [100.0]})
    levels, source = views.previous_day_fallback(
        date.fromisoformat(DAY), data.load_source(solo, data.SOURCE_LIVE), h1)
    assert levels == {"PDH": 110.0, "PDL": 90.0, "PDC": 100.0} and "verified daily dataset" in source


def test_recorded_previous_day_levels_in_strategy_names_are_recognised() -> None:
    row = synthetic_trade(1)
    row["levels_json"] = json.dumps({"pdh": 24673.65, "pdl": 24597.1, "prev_close": 24631.3})
    prev = explain.explain_trade(row)["opened"]["previous_day_levels"]
    assert prev == {"PDH": 24673.65, "PDL": 24597.1, "PDC": 24631.3}


def test_bid_view_marks_stale_and_missing_bids() -> None:
    assert views.bid_view({"current_bid": 87.9, "current_bid_age_seconds": 42.0}) == (
        "87.90 (age 42s STALE)", True)
    assert views.bid_view({"current_bid": 87.9, "current_bid_age_seconds": 0.5}) == ("87.90", False)
    assert views.bid_view({"current_bid": None}) == ("n/a - no valid bid", True)
    assert views.bid_view({"current_bid": 1.0, "bid_is_stale": True})[1] is True


def test_text_helpers() -> None:
    assert text.looks_like_rule_id(ORB_RULE) and not text.looks_like_rule_id("METHOD_1")
    assert text.strategy_label_of({"strategy": "ORB", "method": ORB_RULE}) == "ORB M1"
    assert text.strategy_label_of({"strategy": "PDR", "method": "PDR_RETEST_BOUNCE"}) == "PDR Retest"
    assert text.strategy_label_of({"strategy": "X", "strategy_label": "given"}) == "given"
    assert "previous-day high" in text.reason_sentence({"entry_reason": ORB_RULE})
    assert text.reason_sentence({"reason_text": "from engine", "entry_reason": ORB_RULE}) == "from engine"
    assert text.reason_sentence({"entry_reason": "free text sentence"}) == "free text sentence"
    assert text.exit_reason_from_text("TARGET @ 107.45") == "TARGET"
    assert text.exit_reason_from_text("whatever") is None
    assert text.format_candle("x") == text.NOT_RECORDED


# =================================================================== dashboard: config / scripts


def test_streamlit_config_binds_localhost_headless() -> None:
    cfg = tomllib.loads((REPO / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert cfg["server"]["address"] == "127.0.0.1" and cfg["server"]["headless"] is True


def test_dashboard_script_documents_localhost_and_runtime_override() -> None:
    src = (REPO / "scripts" / "paper_trading_dashboard.py").read_text(encoding="utf-8")
    assert "--server.address 127.0.0.1" in src and "PAPER_RUNTIME_DIR" in src


def test_dashboard_defaults_to_replay_when_only_a_replay_exists(monkeypatch, tmp_path) -> None:
    from streamlit.testing.v1 import AppTest

    _write_replay(tmp_path, engine_state="STOPPED")
    monkeypatch.setenv("PAPER_RUNTIME_DIR", str(tmp_path))
    at = AppTest.from_file(str(REPO / "scripts" / "paper_trading_dashboard.py"), default_timeout=90)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.sidebar.radio(key="source").value == data.SOURCE_REPLAY
    shown = "\n".join(str(e.value) for e in (*at.info, *at.warning, *at.error, *at.caption))
    assert "REPLAY FINISHED" in shown and "No active session" not in shown


def test_pending_entry_is_shown_without_claiming_a_position() -> None:
    snap = _snap("flat")
    snap["pending_entry"] = {"strategy": "ORB", "symbol": "NIFTY X CE", "detail": "waiting for ask"}
    out = "\n".join(_lines(term.render_dashboard(snap, NOW)))
    assert "Active Position: NONE" in out and "entry pending: ORB NIFTY X CE - waiting for ask" in out
