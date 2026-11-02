"""ORB Method 1 conformance tests against ORB_Strategy_Rulebook-2.pdf.

All bars are SYNTHETIC (clearly labelled test fixtures, not market data).  Bars are
open-stamped (``exchange_time`` = bar OPEN label): the first candle of the session is the bar
labelled 09:15 and its close is known at 09:20.

Rules tagged PROVISIONAL are not defined by the PDF (see configs/strategy_provisional/orb.yaml)
and are tested only to prove they are explicit, config-driven and fail closed.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta

import pytest

from trading_platform.core.enums import SignalDirection, Timeframe
from trading_platform.core.models import Bar
from trading_platform.strategies.base import StrategyContext
from trading_platform.strategies.orb import ORBStrategy
from trading_platform.strategies.provisional import load_provisional, provisional_manifest

D1 = date(2025, 3, 10)
D2 = date(2025, 3, 11)

PDH, PDL, EMA = 24800.0, 24700.0, 24750.0


def _bar(
    d: date, hh: int, mm: int, o: float, h: float, low: float, c: float, interval=Timeframe.M5
) -> Bar:
    t = datetime(d.year, d.month, d.day, hh, mm)
    return Bar(
        symbol="NIFTY",
        instrument_id="SYNTHETIC",
        timestamp=t,
        exchange_time=t,
        interval=interval,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1,
    )


def _ctx(pdh=PDH, pdl=PDL, ema=EMA, **extra) -> StrategyContext:
    ctx = StrategyContext()
    ctx.state.update({"pdh": pdh, "pdl": pdl, "ema_200": ema, **extra})
    return ctx


# SYNTHETIC first candles ------------------------------------------------------------
def long_bar(d=D1, hh=9, mm=15) -> Bar:  # green, body .73, upper wick .18, close > PDH
    return _bar(d, hh, mm, 24790.0, 24840.0, 24785.0, 24830.0)


def short_bar(d=D1, hh=9, mm=15) -> Bar:  # red, body .82, lower wick .09, close < PDL
    return _bar(d, hh, mm, 24710.0, 24715.0, 24660.0, 24665.0)


def _strat(**cfg) -> ORBStrategy:
    return ORBStrategy(config={"method": "METHOD_1", **cfg})


# ------------------------------------------------------------------ entry (MATCH)
def test_long_signal_on_0915_candle_with_rich_metadata() -> None:
    strat, ctx = _strat(), _ctx()
    sig = strat.on_bar(ctx, long_bar())
    assert sig is not None
    assert sig.direction == SignalDirection.BUY
    assert sig.method_id == "ORB_METHOD_1_PDH_BREAKOUT"
    assert sig.reference_price == 24830.0
    assert sig.stop_loss == 24800.0 and sig.target == 24890.0  # entry-30 / entry+60 (PDF p11)
    md = sig.metadata
    assert md["stop_points"] == 30.0 and md["target_points"] == 60.0
    assert md["levels"]["pdh"] == PDH and md["levels"]["pdl"] == PDL
    assert md["levels"]["ema_200"] == EMA
    assert md["trigger_candle"] == {
        "time": "2025-03-10T09:15:00",
        "o": 24790.0,
        "h": 24840.0,
        "l": 24785.0,
        "c": 24830.0,
    }
    assert "CLOSE_ABOVE_PDH" in md["conditions_satisfied"]
    assert "STRONG_BODY" in md["conditions_satisfied"]
    assert md["conditions_failed"] == []
    assert md["bar_open_time"] == "2025-03-10T09:15:00"
    assert md["decision_time"] == "2025-03-10T09:20:00"  # known at candle CLOSE
    assert md["risk_unit"] == "NIFTY_INDEX_POINTS"
    assert md["provisional_rules"]


def test_short_signal_mirrors_long() -> None:
    sig = _strat().on_bar(_ctx(ema=24760.0), short_bar())
    assert sig is not None and sig.direction == SignalDirection.SELL
    assert sig.method_id == "ORB_METHOD_1_PDL_BREAKDOWN"
    assert sig.stop_loss == 24665.0 + 30.0 and sig.target == 24665.0 - 60.0
    assert "CLOSE_BELOW_PDL" in sig.metadata["conditions_satisfied"]


def test_no_gap_requirement_for_method_1() -> None:
    """PDF: gap is 'not mandatory' for Method 1 - no prev_close needed."""
    ctx = _ctx()
    assert "prev_close" not in ctx.state
    assert _strat().on_bar(ctx, long_bar()) is not None


def test_close_must_be_strictly_beyond_level() -> None:
    b = _bar(D1, 9, 15, 24790.0, 24805.0, 24785.0, 24800.0)  # close == PDH
    assert _strat().on_bar(_ctx(), b) is None


def test_close_inside_range_has_no_setup_and_reports_failed_conditions() -> None:
    strat, ctx = _strat(), _ctx()
    b = _bar(D1, 9, 15, 24740.0, 24760.0, 24735.0, 24755.0)
    assert strat.on_bar(ctx, b) is None
    snap = strat.evaluation_snapshot(D1.isoformat())
    assert snap["disposition"] == "NO_SETUP"
    assert "CLOSE_ABOVE_PDH" in snap["conditions_failed"]
    assert "CLOSE_BELOW_PDL" in snap["conditions_failed"]


# ------------------------------------------------------------------ first-candle timing (FIXED)
def test_0920_bar_is_second_candle_and_never_a_decision_candle() -> None:
    """Defect fixed: open-stamped 09:20 bar used to be accepted as the 'first candle'."""
    strat, ctx = _strat(), _ctx()
    assert strat.on_bar(ctx, long_bar(hh=9, mm=20)) is None
    snap = strat.evaluation_snapshot(D1)
    assert snap["disposition"] == "FIRST_BAR_MISSING_OR_LATE"
    assert snap["state"] == "BLOCKED"
    assert "FIRST_CANDLE_0915_AVAILABLE" in snap["conditions_failed"]


def test_missing_0915_keeps_strategy_silent_all_day_even_if_0915_arrives_late() -> None:
    strat, ctx = _strat(), _ctx()
    assert strat.on_bar(ctx, long_bar(hh=9, mm=20)) is None
    assert strat.on_bar(ctx, long_bar(hh=9, mm=25)) is None
    assert strat.on_bar(ctx, long_bar(hh=9, mm=15)) is None  # out-of-order late first bar
    assert strat.evaluation_snapshot(D1)["signal_emitted"] is False


def test_non_five_minute_bar_is_ignored() -> None:
    strat, ctx = _strat(), _ctx()
    daily = _bar(D1, 9, 15, 24790.0, 24840.0, 24785.0, 24830.0, interval=Timeframe.D1)
    assert strat.on_bar(ctx, daily) is None
    assert strat.on_bar(ctx, long_bar()) is not None  # day was not consumed by the daily bar


# ------------------------------------------------------------------ one decision per day / reset
def test_one_signal_per_setup_no_duplicates() -> None:
    strat, ctx = _strat(), _ctx()
    assert strat.on_bar(ctx, long_bar()) is not None
    assert strat.on_bar(ctx, long_bar(hh=9, mm=15)) is None  # same candle re-delivered
    assert strat.on_bar(ctx, long_bar(hh=9, mm=20)) is None
    assert strat.on_bar(ctx, long_bar(hh=10, mm=0)) is None


def test_driver_that_already_added_the_bar_does_not_duplicate_history() -> None:
    strat, ctx = _strat(), _ctx()
    b = long_bar()
    ctx.add_bar(b)
    assert strat.on_bar(ctx, b) is not None
    assert len(ctx.historical_bars) == 1


def test_per_day_state_resets_by_date() -> None:
    strat, ctx = _strat(), _ctx()
    assert strat.on_bar(ctx, long_bar(D1)) is not None
    # Next day: caller sets NEW levels explicitly.
    ctx.state.update({"pdh": 24900.0, "pdl": 24780.0, "ema_200": 24810.0, "levels_date": D2})
    b = _bar(D2, 9, 15, 24890.0, 24945.0, 24885.0, 24935.0)
    sig = strat.on_bar(ctx, b)
    assert sig is not None and sig.metadata["levels"]["pdh"] == 24900.0


def test_missing_first_bar_yesterday_does_not_affect_today() -> None:
    strat, ctx = _strat(), _ctx()
    assert strat.on_bar(ctx, long_bar(D1, 9, 30)) is None
    ctx.state.update({"pdh": 24790.0, "pdl": 24650.0, "ema_200": 24700.0})
    assert strat.on_bar(ctx, long_bar(D2)) is not None


# ------------------------------------------------------------------ levels: missing / stale (FIXED)
@pytest.mark.parametrize("missing", ["pdh", "pdl", "ema_200"])
def test_missing_level_means_no_signal_and_day_not_revived_later(missing: str) -> None:
    strat, ctx = _strat(), _ctx()
    ctx.state[missing] = None
    assert strat.on_bar(ctx, long_bar()) is None
    snap = strat.evaluation_snapshot(D1)
    assert snap["disposition"] == "MISSING_LEVELS" and snap["state"] == "BLOCKED"
    assert missing in snap["note"]
    # Levels showing up one bar late must not retro-fire a 09:20 signal on a later bar.
    ctx.state[missing] = {"pdh": PDH, "pdl": PDL, "ema_200": EMA}[missing]
    assert strat.on_bar(ctx, long_bar(hh=9, mm=20)) is None


def test_nan_and_bool_levels_are_missing() -> None:
    assert _strat().on_bar(_ctx(pdh=float("nan")), long_bar()) is None
    assert _strat().on_bar(_ctx(ema=True), long_bar()) is None


def test_stale_levels_date_is_rejected() -> None:
    strat = _strat()
    ctx = _ctx(levels_date="2025-03-07")  # levels belong to another day
    assert strat.on_bar(ctx, long_bar()) is None
    assert strat.evaluation_snapshot(D1)["disposition"] == "STALE_LEVELS"


def test_levels_date_matching_today_is_accepted() -> None:
    assert _strat().on_bar(_ctx(levels_date=D1), long_bar()) is not None


def test_previous_day_levels_left_in_context_are_detected_as_stale() -> None:
    """Context keys not refreshed for the new day (identical PDH/PDL pair) => no signal."""
    strat, ctx = _strat(), _ctx()
    assert strat.on_bar(ctx, long_bar(D1)) is not None
    assert strat.on_bar(ctx, long_bar(D2)) is None  # nothing was refreshed for D2
    assert strat.evaluation_snapshot(D2)["disposition"] == "STALE_LEVELS"


def test_stale_ema_alone_is_detected() -> None:
    strat, ctx = _strat(), _ctx()
    assert strat.on_bar(ctx, long_bar(D1)) is not None
    ctx.state.update({"pdh": 24790.0, "pdl": 24650.0})  # EMA left untouched
    assert strat.on_bar(ctx, long_bar(D2)) is None
    assert strat.evaluation_snapshot(D2)["disposition"] == "STALE_LEVELS"


def test_reset_state_clears_day_memory_and_stale_memory() -> None:
    strat, ctx = _strat(), _ctx()
    assert strat.on_bar(ctx, long_bar(D1)) is not None
    strat.reset_state()
    assert strat.on_bar(StrategyContext(state=dict(ctx.state)), long_bar(D1)) is not None


def test_ema_warmup_incomplete_fails_closed_with_explicit_disposition() -> None:
    strat = _strat()
    assert strat.on_bar(_ctx(ema=None, has_sufficient_ema_warmup=False), long_bar()) is None
    assert strat.evaluation_snapshot(D1)["disposition"] == "EMA_WARMUP_INSUFFICIENT"


def test_pdh_below_pdl_is_rejected() -> None:
    assert _strat().on_bar(_ctx(pdh=24700.0, pdl=24800.0), long_bar()) is None


# ------------------------------------------------------------------ trend filter (strict MATCH + PROVISIONAL basis)
def test_trend_is_strictly_above_ema_for_long() -> None:
    """PDF: 'price above the 200 EMA'.  Equality used to pass (>=)."""
    assert _strat().on_bar(_ctx(ema=24830.0), long_bar()) is None
    assert _strat().on_bar(_ctx(ema=24831.0), long_bar()) is None
    snap_strat = _strat()
    snap_strat.on_bar(_ctx(ema=24830.0), long_bar())
    snap = snap_strat.evaluation_snapshot(D1)
    assert "TREND_CLOSE_ABOVE_EMA200(PROVISIONAL)" in snap["conditions_failed"]
    assert "CLOSE_ABOVE_PDH" in snap["conditions_satisfied"]


def test_trend_is_strictly_below_ema_for_short() -> None:
    assert _strat().on_bar(_ctx(ema=24665.0), short_bar()) is None
    assert _strat().on_bar(_ctx(ema=24600.0), short_bar()) is None


# ------------------------------------------------------------------ candle quality
def test_strong_opposing_wick_is_rejected() -> None:
    b = _bar(D1, 9, 15, 24790.0, 24860.0, 24788.0, 24830.0)  # upper wick .42 of range
    assert _strat().on_bar(_ctx(), b) is None


def test_weak_body_without_pin_bar_is_rejected() -> None:
    b = _bar(D1, 9, 15, 24795.0, 24830.0, 24780.0, 24805.0)  # body .2, lower wick .3
    assert _strat().on_bar(_ctx(), b) is None


def test_pin_bar_with_close_in_trade_direction_is_conditionally_accepted() -> None:
    b = _bar(D1, 9, 15, 24795.0, 24812.0, 24760.0, 24808.0)  # green, lower wick .67, upper .08
    sig = _strat().on_bar(_ctx(), b)
    assert sig is not None
    assert "PIN_BAR_CONDITIONAL" in sig.metadata["conditions_satisfied"]


def test_wrong_colour_pin_bar_is_no_longer_accepted() -> None:
    """Defect fixed: pin-bar shape alone (red candle, close barely above PDH) used to give BUY."""
    b = _bar(D1, 9, 15, 24808.0, 24812.0, 24760.0, 24801.0)
    assert _strat().on_bar(_ctx(), b) is None


def test_pin_bar_with_strong_opposing_wick_is_rejected() -> None:
    b = _bar(D1, 9, 15, 24795.0, 24850.0, 24740.0, 24808.0)  # lower .6 but upper .38
    assert _strat().on_bar(_ctx(), b) is None


def test_bearish_pin_bar_needs_red_close() -> None:
    red = _bar(D1, 9, 15, 24690.0, 24740.0, 24688.0, 24692.0)  # red-ish? close>open: green
    assert _strat().on_bar(_ctx(ema=24760.0), red) is None  # close not below PDL anyway
    green_pin = _bar(D1, 9, 15, 24690.0, 24720.0, 24688.0, 24692.0)
    assert _strat().on_bar(_ctx(ema=24760.0), green_pin) is None  # green close => no short
    red_pin = _bar(D1, 9, 15, 24695.0, 24722.0, 24690.0, 24692.0)  # red, upper wick .84
    sig = _strat().on_bar(_ctx(ema=24760.0), red_pin)
    assert sig is not None and sig.direction == SignalDirection.SELL


def test_doji_cannot_be_both_bullish_and_bearish() -> None:
    b = _bar(D1, 9, 15, 24750.0, 24800.0, 24700.0, 24750.0)
    assert _strat().on_bar(_ctx(pdh=24740.0, pdl=24760.0), b) is None


# ------------------------------------------------------------------ PROVISIONAL plumbing
def test_provisional_yaml_loaded_and_labelled() -> None:
    vals = load_provisional("orb")
    assert vals["min_body_ratio"] == 0.50
    assert vals["max_opposing_wick_ratio"] == 0.35
    assert vals["pin_bar_min_wick_ratio"] == 0.45
    manifest = provisional_manifest("orb")
    assert manifest and all(m["status"] == "PROVISIONAL" and m["why"] for m in manifest)
    keys = {m["key"] for m in manifest}
    assert {"trend_ema_basis", "level_basis", "risk_unit"} <= keys


def test_strategy_reads_provisional_values_and_config_overrides_win() -> None:
    s = _strat()
    assert (s.min_body_ratio, s.max_opposing_wick_ratio, s.pin_bar_min_wick_ratio) == (
        0.50,
        0.35,
        0.45,
    )
    s2 = _strat(min_body_ratio=0.9, max_opposing_wick_ratio=0.05)
    assert s2.min_body_ratio == 0.9
    # body .73 < 0.9 and not a pin bar -> no signal with the stricter provisional value
    assert s2.on_bar(_ctx(), long_bar()) is None


def test_prepare_reresolves_config() -> None:
    s = _strat()
    s.prepare(None, {"target_points": 40.0, "stop_points": 20.0})
    sig = s.on_bar(_ctx(), long_bar())
    assert sig is not None and sig.target == 24830.0 + 40.0 and sig.stop_loss == 24830.0 - 20.0


def test_provisional_rules_declared_and_never_called_approved() -> None:
    rules = ORBStrategy.PROVISIONAL_RULES
    assert isinstance(rules, tuple) and len(rules) >= 5
    text = " ".join(rules).lower() + (ORBStrategy.__doc__ or "").lower()
    assert "author-approved" not in text and "author approved" not in text
    assert any("200" in r and "EMA" in r for r in rules)
    assert any("0.50" in r for r in rules)
    for m in provisional_manifest("orb"):
        assert "author-approved" not in str(m["why"]).lower()


# ------------------------------------------------------------------ evaluation_snapshot
def test_snapshot_is_read_only_and_describes_pending_day() -> None:
    strat, ctx = _strat(), _ctx(levels_date=D1)
    before_state = copy.deepcopy(strat._daily_state)
    before_ctx = copy.deepcopy(ctx.state)
    snap = strat.evaluation_snapshot(D1.isoformat(), ctx)
    assert snap["state"] == "MONITORING" and snap["evaluated"] is False
    assert snap["levels"]["pdh"] == PDH
    assert strat._daily_state == before_state and ctx.state == before_ctx
    assert len(ctx.historical_bars) == 0


def test_snapshot_pending_day_without_levels_is_warming_up() -> None:
    strat = _strat()
    snap = strat.evaluation_snapshot(D1, _ctx(pdh=None))
    assert snap["state"] == "WARMING_UP"
    assert "MISSING_LEVELS" in snap["conditions_failed"]


def test_snapshot_after_signal_and_is_a_copy() -> None:
    strat, ctx = _strat(), _ctx()
    strat.on_bar(ctx, long_bar())
    snap = strat.evaluation_snapshot(D1)
    assert snap["state"] == "DONE_FOR_DAY" and snap["signal_emitted"] is True
    assert snap["direction"] == "BUY" and snap["disposition"] == "SIGNAL"
    assert snap["trigger_candle"]["c"] == 24830.0
    snap["conditions_satisfied"].append("MUTATED")
    assert "MUTATED" not in strat.evaluation_snapshot(D1)["conditions_satisfied"]


def test_no_lookahead_signal_uses_only_current_bar_and_prior_levels() -> None:
    """The decision is a pure function of the 09:15 candle + the supplied prior-day levels."""
    strat, ctx = _strat(), _ctx()
    sig = strat.on_bar(ctx, long_bar())
    assert sig is not None
    assert sig.timestamp == long_bar().exchange_time
    assert datetime.fromisoformat(sig.metadata["decision_time"]) == sig.timestamp + timedelta(
        minutes=5
    )
