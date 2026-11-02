"""PDS (Previous Day Support) conformance tests against the author PDF.

Every bar in this module is SYNTHETIC (hand-built to isolate one rule).  Nothing here is
market data and nothing here proves an edge.  Rules the PDF leaves undefined are tested as
PROVISIONAL (read from configs/strategy_provisional/pds.yaml), never as author-approved.

Zone used unless stated: pdl 24800 .. pd_supp_body 24815, prev_close 24850, proximity 5.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import StrategyContext
from trading_platform.strategies.pds import M1_ID, M2_ID, M3_ID, PDSStrategy
from trading_platform.strategies.provisional import load_provisional, provisional_manifest

D1 = date(2026, 3, 2)
D2 = date(2026, 3, 3)
LEVELS = {"pdl": 24800.0, "pd_supp_body": 24815.0, "prev_close": 24850.0}


def bar(d: date, hhmm: str, o: float, h: float, low: float, c: float) -> Bar:
    """SYNTHETIC 5m bar; ``hhmm`` is the bar-OPEN label."""
    hh, mm = (int(x) for x in hhmm.split(":"))
    t = datetime(d.year, d.month, d.day, hh, mm)
    return Bar(
        symbol="NIFTY",
        instrument_id="NIFTY50",
        timestamp=t,
        exchange_time=t,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1000,
    )


def ctx_with(levels: dict[str, Any] | None = None) -> StrategyContext:
    ctx = StrategyContext()
    ctx.state.update(LEVELS if levels is None else levels)
    return ctx


def drive(
    strat: PDSStrategy, ctx: StrategyContext, bars: list[Bar]
) -> list[tuple[str, Signal | None]]:
    return [(b.exchange_time.strftime("%H:%M"), strat.on_bar(ctx, b)) for b in bars]


def signals(results: list[tuple[str, Signal | None]]) -> list[tuple[str, Signal]]:
    return [(t, s) for t, s in results if s is not None]


# Reusable synthetic bars -------------------------------------------------------------

def open_bar(d: date = D1, o: float = 24780.0) -> Bar:
    """09:15 gap-down candle that opens below the zone (red, stays below the zone)."""
    return bar(d, "09:15", o, o + 5, o - 25, o - 15)


def green_in_zone(d: date, hhmm: str = "09:20") -> Bar:
    """Green candle rallying into the zone (touches 24806), closes inside it."""
    return bar(d, hhmm, 24790.0, 24806.0, 24788.0, 24804.0)


def strong_red_in_zone(d: date, hhmm: str, close: float = 24771.0) -> Bar:
    """Red candle at the zone with a decisive body (body ratio ~ 0.78)."""
    return bar(d, hhmm, 24805.0, 24808.0, 24768.0, close)


def weak_red_in_zone(d: date, hhmm: str) -> Bar:
    """Small red candle at the zone: body 3 / range 12, upper wick 4 / range 12."""
    return bar(d, hhmm, 24802.0, 24806.0, 24794.0, 24799.0)


# --------------------------------------------------------------------------- gate


def test_no_gap_down_means_no_signal_all_day() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    res = drive(
        strat,
        ctx,
        [
            bar(D1, "09:15", 24855.0, 24860.0, 24790.0, 24800.0),  # open >= prev_close
            bar(D1, "09:20", 24800.0, 24806.0, 24760.0, 24765.0),
            strong_red_in_zone(D1, "09:25"),
        ],
    )
    assert signals(res) == []
    snap = strat.evaluation_snapshot(D1.isoformat())
    assert snap["state"] == "DONE_FOR_DAY"
    assert "gap down (open < prev close)" in snap["conditions_failed"]


def test_gap_down_that_opens_inside_the_zone_is_skipped_provisional() -> None:
    """PROVISIONAL reading: 'after the open price is trading below the zone'."""
    strat = PDSStrategy()
    ctx = ctx_with()
    res = drive(
        strat,
        ctx,
        [
            bar(D1, "09:15", 24810.0, 24812.0, 24795.0, 24800.0),  # gap down but inside zone
            strong_red_in_zone(D1, "09:20"),
        ],
    )
    assert signals(res) == []
    snap = strat.evaluation_snapshot(D1)
    assert "open below zone" in snap["conditions_failed"]


def test_missing_first_bar_keeps_strategy_silent_for_the_day() -> None:
    """No substitution of a later bar for the 09:15 gate candle (old code accepted 09:20)."""
    strat = PDSStrategy()
    ctx = ctx_with()
    res = drive(
        strat,
        ctx,
        [
            bar(D1, "09:20", 24780.0, 24790.0, 24760.0, 24770.0),  # late first bar
            green_in_zone(D1, "09:25"),
            strong_red_in_zone(D1, "09:30"),
        ],
    )
    assert signals(res) == []
    snap = strat.evaluation_snapshot(D1)
    assert snap["state"] == "DONE_FOR_DAY"
    assert "NO_FIRST_BAR" in snap["state_reason"]
    assert "09:15 bar present" in snap["conditions_failed"]


def test_opening_bar_is_never_the_decision_candle_provisional() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    # Opens below the zone, rallies into it, closes red with a big body.
    opener = bar(D1, "09:15", 24798.0, 24812.0, 24770.0, 24775.0)
    assert strat.on_bar(ctx, opener) is None
    snap = strat.evaluation_snapshot(D1)
    assert snap["state"] == "MONITORING"
    assert snap["first_red_candle"] is None


def test_opening_bar_entry_can_be_enabled_by_provisional_override() -> None:
    strat = PDSStrategy({"allow_opening_bar_entry": True})
    ctx = ctx_with()
    opener = bar(D1, "09:15", 24798.0, 24812.0, 24770.0, 24775.0)
    sig = strat.on_bar(ctx, opener)
    assert sig is not None and sig.method_id == M1_ID


# --------------------------------------------------------------------------- setup


def test_method1_first_red_candle_at_zone_after_rise_from_below() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    res = drive(strat, ctx, [open_bar(), green_in_zone(D1), strong_red_in_zone(D1, "09:25")])
    sigs = signals(res)
    assert [t for t, _ in sigs] == ["09:25"]
    sig = sigs[0][1]
    assert sig.method_id == M1_ID
    assert sig.direction == SignalDirection.SELL
    assert sig.reference_price == 24771.0
    assert sig.target == 24771.0 - 70.0
    assert sig.metadata["target_points"] == 70.0


def test_strong_red_candle_far_from_zone_is_not_a_trigger() -> None:
    """Old code latched 'zone_tested' for the day and fired on any later strong red candle."""
    strat = PDSStrategy()
    ctx = ctx_with()
    far_green = bar(D1, "09:25", 24770.0, 24785.0, 24765.0, 24780.0)  # fully below the zone
    far_red = bar(D1, "09:30", 24780.0, 24782.0, 24758.0, 24760.0)  # strong red, not at zone
    res = drive(strat, ctx, [open_bar(), green_in_zone(D1), far_green, far_red])
    assert signals(res) == []


def test_strong_red_below_zone_without_any_test_is_not_a_trigger() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    res = drive(
        strat, ctx, [open_bar(), bar(D1, "09:20", 24770.0, 24772.0, 24740.0, 24742.0)]
    )
    assert signals(res) == []


def test_only_the_first_red_candle_at_the_zone_counts_for_method1() -> None:
    """A weak first red candle consumes Methods 1/2; a later strong red at the zone is not 'first'."""
    strat = PDSStrategy({"method": "METHOD_1"})
    ctx = ctx_with()
    later_strong = bar(D1, "09:25", 24805.0, 24808.0, 24780.0, 24782.0)
    res = drive(strat, ctx, [open_bar(), weak_red_in_zone(D1, "09:20"), later_strong])
    assert signals(res) == []
    snap = strat.evaluation_snapshot(D1)
    assert snap["first_red_candle"]["time"].endswith("09:20:00")
    assert "M1 good red body" in snap["conditions_failed"]


def test_green_candles_at_the_zone_do_not_use_up_the_first_red() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    res = drive(
        strat,
        ctx,
        [
            open_bar(),
            green_in_zone(D1, "09:20"),
            bar(D1, "09:25", 24804.0, 24812.0, 24802.0, 24810.0),  # green, inside zone
            strong_red_in_zone(D1, "09:30"),
        ],
    )
    assert [t for t, _ in signals(res)] == ["09:30"]


def test_zone_that_price_closes_above_has_failed_as_resistance_provisional() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    breakout = bar(D1, "09:20", 24795.0, 24835.0, 24793.0, 24830.0)  # closes above 24815 + 5
    pullback_red = bar(D1, "09:25", 24830.0, 24832.0, 24800.0, 24803.0)
    res = drive(strat, ctx, [open_bar(), breakout, pullback_red])
    assert signals(res) == []
    snap = strat.evaluation_snapshot(D1)
    assert snap["state"] == "DONE_FOR_DAY" and "ZONE_FAILED" in snap["state_reason"]


def test_zone_failure_rule_can_be_switched_off() -> None:
    strat = PDSStrategy({"invalidate_on_close_above_zone": False})
    ctx = ctx_with()
    breakout = bar(D1, "09:20", 24795.0, 24835.0, 24793.0, 24830.0)
    pullback_red = bar(D1, "09:25", 24830.0, 24832.0, 24790.0, 24803.0)
    res = drive(strat, ctx, [open_bar(), breakout, pullback_red])
    assert [t for t, _ in signals(res)] == ["09:25"]


def test_new_approach_after_leaving_the_zone_downward_is_judged_again_provisional() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    res = drive(
        strat,
        ctx,
        [
            open_bar(),
            weak_red_in_zone(D1, "09:20"),  # first approach: weak first red (M1 mode: consumed)
            bar(D1, "09:25", 24790.0, 24793.0, 24770.0, 24775.0),  # leaves the zone downward
            green_in_zone(D1, "09:30"),  # new rise
            strong_red_in_zone(D1, "09:35"),  # first red of the NEW approach
        ],
    )
    assert [t for t, _ in signals(res)] == ["09:35"]


def test_zone_proximity_boundary_is_provisional_config() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    near = bar(D1, "09:20", 24793.0, 24795.0, 24765.0, 24768.0)  # high == zone_low - 5 -> tests
    assert strat.on_bar(ctx, open_bar()) is None
    sig = strat.on_bar(ctx, near)
    assert sig is not None and sig.method_id == M1_ID

    strat2 = PDSStrategy()
    ctx2 = ctx_with()
    too_far = bar(D1, "09:20", 24793.0, 24794.9, 24765.0, 24768.0)  # 0.1 short of proximity
    assert strat2.on_bar(ctx2, open_bar()) is None
    assert strat2.on_bar(ctx2, too_far) is None


def test_zone_covers_body_when_upper_body_edge_is_supplied() -> None:
    """PDF: the zone covers body AND lower wick; pd_supp_body_top (optional) is the body top."""
    in_body_band = bar(D1, "09:20", 24838.0, 24840.0, 24828.0, 24829.0)  # strong red, in body band
    strat = PDSStrategy()
    ctx = ctx_with({**LEVELS, "pd_supp_body_top": 24840.0})
    assert strat.on_bar(ctx, open_bar()) is None
    sig = strat.on_bar(ctx, in_body_band)
    assert sig is not None and sig.metadata["levels"]["zone_top"] == 24840.0

    # Without the body top the same candle sits entirely above the wick-only proxy zone.
    proxy = PDSStrategy()
    ctx2 = ctx_with()
    assert proxy.on_bar(ctx2, open_bar()) is None
    assert proxy.on_bar(ctx2, in_body_band) is None
    assert proxy.evaluation_snapshot(D1)["first_red_candle"] is None


# --------------------------------------------------------------------------- methods


def test_method2_red_pin_bar_with_hpr_wick_reaching_the_zone() -> None:
    strat = PDSStrategy({"method": "METHOD_2"})
    ctx = ctx_with()
    pin = bar(D1, "09:20", 24780.0, 24815.0, 24765.0, 24770.0)  # wick 35/50, red, tiny body
    res = drive(strat, ctx, [open_bar(), pin])
    sig = signals(res)[0][1]
    assert sig.method_id == M2_ID


def test_method2_green_pin_bar_is_not_a_trigger() -> None:
    strat = PDSStrategy({"method": "METHOD_2"})
    ctx = ctx_with()
    green_pin = bar(D1, "09:20", 24770.0, 24815.0, 24765.0, 24780.0)  # long upper wick, green
    assert signals(drive(strat, ctx, [open_bar(), green_pin])) == []


def test_method3_arms_only_after_weak_first_red_then_close_below_support() -> None:
    strat = PDSStrategy({"method": "METHOD_3"})
    ctx = ctx_with()
    res = drive(
        strat,
        ctx,
        [
            open_bar(),
            weak_red_in_zone(D1, "09:20"),  # support = 24794
            bar(D1, "09:25", 24799.0, 24800.0, 24788.0, 24790.0),  # closes below
        ],
    )
    sig = signals(res)[0][1]
    assert sig.method_id == M3_ID
    assert sig.metadata["small_support"] == 24794.0


def test_method3_support_break_needs_a_close_by_default_provisional() -> None:
    touch_only = bar(D1, "09:25", 24799.0, 24800.0, 24790.0, 24796.0)  # low < 24794? no: 24790 < 24794
    strat = PDSStrategy({"method": "METHOD_3"})
    ctx = ctx_with()
    res = drive(strat, ctx, [open_bar(), weak_red_in_zone(D1, "09:20"), touch_only])
    assert signals(res) == []  # touched below support but closed above it

    strat_touch = PDSStrategy({"method": "METHOD_3", "support_break_mode": "touch"})
    ctx_touch = ctx_with()
    res = drive(strat_touch, ctx_touch, [open_bar(), weak_red_in_zone(D1, "09:20"), touch_only])
    assert [t for t, _ in signals(res)] == ["09:25"]


def test_method3_does_not_arm_after_a_good_first_red_candle() -> None:
    strat = PDSStrategy({"method": "METHOD_3"})
    ctx = ctx_with()
    breakdown = bar(D1, "09:25", 24771.0, 24772.0, 24740.0, 24745.0)
    res = drive(strat, ctx, [open_bar(), strong_red_in_zone(D1, "09:20"), breakdown])
    assert signals(res) == []


def test_all_methods_flow_good_candle_is_method1_weak_candle_falls_back_to_method3() -> None:
    strat = PDSStrategy({"method": "ALL"})
    ctx = ctx_with()
    sigs = signals(drive(strat, ctx, [open_bar(), strong_red_in_zone(D1, "09:20")]))
    assert sigs[0][1].method_id == M1_ID

    strat2 = PDSStrategy({"method": "ALL"})
    ctx2 = ctx_with()
    breakdown = bar(D1, "09:25", 24799.0, 24800.0, 24788.0, 24790.0)
    sigs2 = signals(drive(strat2, ctx2, [open_bar(), weak_red_in_zone(D1, "09:20"), breakdown]))
    assert sigs2[0][1].method_id == M3_ID


# --------------------------------------------------------------------------- stop / target


def _sig_with_green_high(green_high: float, red_close: float) -> Signal:
    strat = PDSStrategy()
    ctx = ctx_with()
    green = bar(D1, "09:20", 24790.0, green_high, 24788.0, 24804.0)
    red = bar(D1, "09:25", 24805.0, 24808.0, red_close - 3.0, red_close)
    res = drive(strat, ctx, [open_bar(), green, red])
    return signals(res)[0][1]


def test_stop_is_40_when_recent_green_candle_distance_exceeds_35() -> None:
    sig = _sig_with_green_high(24840.0, 24768.0)  # 72 > 35
    assert sig.metadata["stop_points"] == 40.0
    assert sig.stop_loss == 24768.0 + 40.0


def test_stop_is_35_when_distance_below_35() -> None:
    sig = _sig_with_green_high(24806.0, 24785.0)  # 21
    assert sig.metadata["stop_points"] == 35.0
    assert sig.stop_loss == 24785.0 + 35.0


def test_stop_is_35_when_distance_is_exactly_35_provisional() -> None:
    sig = _sig_with_green_high(24806.0, 24771.0)  # exactly 35
    assert sig.metadata["stop_points"] == 35.0


def test_stop_ignores_green_candles_of_previous_sessions() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    ctx.add_bar(bar(D1, "15:20", 25000.0, 25200.0, 24990.0, 25100.0))  # old huge green candle
    # Day D2 needs its own levels (explicitly set, different from D1's).
    ctx.state.update({"pdl": 24800.0, "pd_supp_body": 24815.0, "prev_close": 24851.0})
    res = drive(
        strat, ctx, [open_bar(D2), green_in_zone(D2), strong_red_in_zone(D2, "09:25", 24785.0)]
    )
    sig = signals(res)[0][1]
    assert sig.metadata["stop_points"] == 35.0  # today's green high 24806 - 24785 = 21


def test_stop_is_35_when_no_green_candle_exists_today() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    red_test = bar(D1, "09:20", 24805.0, 24808.0, 24768.0, 24771.0)
    sig = signals(drive(strat, ctx, [open_bar(), red_test]))[0][1]
    assert sig.metadata["stop_points"] == 35.0


# --------------------------------------------------------------------------- levels & days


def test_missing_level_means_no_signal() -> None:
    for missing in ("pdl", "prev_close", "pd_supp_body"):
        levels = {k: v for k, v in LEVELS.items() if k != missing}
        strat = PDSStrategy()
        ctx = ctx_with(levels)
        res = drive(strat, ctx, [open_bar(), green_in_zone(D1), strong_red_in_zone(D1, "09:25")])
        assert signals(res) == [], missing
        snap = strat.evaluation_snapshot(D1)
        assert snap["state"] == "WARMING_UP" and missing in snap["state_reason"]


def test_non_numeric_level_means_no_signal() -> None:
    strat = PDSStrategy()
    ctx = ctx_with({**LEVELS, "pdl": float("nan")})
    assert signals(drive(strat, ctx, [open_bar(), strong_red_in_zone(D1, "09:20")])) == []


def test_stale_levels_date_means_no_signal() -> None:
    strat = PDSStrategy()
    ctx = ctx_with({**LEVELS, "levels_date": D1.isoformat()})
    res = drive(
        strat, ctx, [open_bar(D2), green_in_zone(D2), strong_red_in_zone(D2, "09:25")]
    )
    assert signals(res) == []
    assert "LEVELS_STALE" in strat.evaluation_snapshot(D2)["state_reason"]


def test_matching_levels_date_is_accepted() -> None:
    strat = PDSStrategy()
    ctx = ctx_with({**LEVELS, "levels_date": D1.isoformat()})
    res = drive(strat, ctx, [open_bar(), strong_red_in_zone(D1, "09:20")])
    assert len(signals(res)) == 1


def test_levels_identical_to_previous_day_are_treated_as_stale() -> None:
    """The caller forgot to refresh context.state for the new day: never reuse yesterday's."""
    strat = PDSStrategy()
    ctx = ctx_with()
    assert len(signals(drive(strat, ctx, [open_bar(), strong_red_in_zone(D1, "09:20")]))) == 1
    res = drive(strat, ctx, [open_bar(D2), strong_red_in_zone(D2, "09:20")])
    assert signals(res) == []
    assert "LEVELS_STALE" in strat.evaluation_snapshot(D2)["state_reason"]


def test_levels_changing_mid_day_blocks_the_day() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    assert strat.on_bar(ctx, open_bar()) is None
    ctx.state["pdl"] = 24700.0
    assert strat.on_bar(ctx, strong_red_in_zone(D1, "09:20")) is None
    assert strat.evaluation_snapshot(D1)["state"] == "BLOCKED"


def test_per_day_state_resets_and_one_day_does_not_block_the_next() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    day1 = drive(strat, ctx, [open_bar(D1), strong_red_in_zone(D1, "09:20")])
    assert len(signals(day1)) == 1
    ctx.state.update({"pdl": 24900.0, "pd_supp_body": 24915.0, "prev_close": 24950.0})
    d2_open = bar(D2, "09:15", 24880.0, 24885.0, 24855.0, 24865.0)
    d2_red = bar(D2, "09:20", 24905.0, 24908.0, 24868.0, 24871.0)
    day2 = drive(strat, ctx, [d2_open, d2_red])
    assert len(signals(day2)) == 1
    assert strat.evaluation_snapshot(D1)["signals_emitted"] == 1
    assert strat.evaluation_snapshot(D2)["levels"]["pdl"] == 24900.0


def test_gap_down_gate_of_one_day_does_not_leak_into_the_next() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    drive(strat, ctx, [bar(D1, "09:15", 24860.0, 24865.0, 24850.0, 24855.0)])  # no gap-down
    ctx.state.update({"prev_close": 24851.0})
    res = drive(strat, ctx, [open_bar(D2), strong_red_in_zone(D2, "09:20")])
    assert len(signals(res)) == 1


# --------------------------------------------------------------------------- duplicates


def test_one_signal_per_setup_and_per_day() -> None:
    strat = PDSStrategy({"method": "ALL"})
    ctx = ctx_with()
    bars = [
        open_bar(),
        strong_red_in_zone(D1, "09:20"),
        strong_red_in_zone(D1, "09:25"),
        strong_red_in_zone(D1, "09:30"),
    ]
    assert [t for t, _ in signals(drive(strat, ctx, bars))] == ["09:20"]


def test_same_bar_evaluated_twice_emits_once() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    strat.on_bar(ctx, open_bar())
    trigger = strong_red_in_zone(D1, "09:20")
    assert strat.on_bar(ctx, trigger) is not None
    assert strat.generate_signal(ctx) is None
    assert strat.on_bar(ctx, trigger) is None


# --------------------------------------------------------------------------- catch-up


def test_bars_added_without_on_bar_are_folded_in_and_never_emit_late() -> None:
    """Backtest feeds bars via add_bar while a position is open; restart replays history."""
    strat = PDSStrategy()
    ctx = ctx_with()
    strat.on_bar(ctx, open_bar())
    ctx.add_bar(green_in_zone(D1, "09:20"))
    ctx.add_bar(strong_red_in_zone(D1, "09:25"))  # setup happened while nobody was looking
    late = bar(D1, "09:30", 24771.0, 24772.0, 24740.0, 24745.0)
    assert strat.on_bar(ctx, late) is None
    snap = strat.evaluation_snapshot(D1)
    assert snap["signals_emitted"] == 1
    assert snap["missed_signals"][0]["reason"] == "CATCH_UP_NOT_EMITTED"


def test_replaying_the_days_history_then_a_new_bar_matches_bar_by_bar_driving() -> None:
    bars = [open_bar(), green_in_zone(D1), strong_red_in_zone(D1, "09:25")]
    live = PDSStrategy()
    ctx_live = ctx_with()
    expected = signals(drive(live, ctx_live, bars))

    restarted = PDSStrategy()
    ctx_r = ctx_with()
    ctx_r.add_bar(bars[0])
    ctx_r.add_bar(bars[1])
    got = restarted.on_bar(ctx_r, bars[2])
    assert got is not None and got.method_id == expected[0][1].method_id


def test_late_start_without_the_opening_bar_is_silent() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    res = drive(strat, ctx, [green_in_zone(D1, "10:00"), strong_red_in_zone(D1, "10:05")])
    assert signals(res) == []


def test_spurious_1530_bar_is_ignored() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    assert strat.on_bar(ctx, bar(D1, "15:30", 24800.0, 24810.0, 24790.0, 24795.0)) is None
    assert strat.evaluation_snapshot(D1)["state"] == "WARMING_UP"  # nothing was evaluated
    res = drive(strat, ctx, [open_bar(), strong_red_in_zone(D1, "09:20")])
    assert len(signals(res)) == 1


# --------------------------------------------------------------------------- metadata


def test_signal_metadata_explains_the_trade() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    sig = signals(drive(strat, ctx, [open_bar(), green_in_zone(D1), strong_red_in_zone(D1, "09:25")]))[0][1]
    md = sig.metadata
    assert "first red candle at zone" in md["conditions_satisfied"]
    assert "zone tested from below" in md["conditions_satisfied"]
    assert "M1 good red body" in md["conditions_satisfied"]
    assert "M2 red HPR pin bar" not in md["conditions_failed"]  # alternative, not a requirement
    assert isinstance(md["conditions_failed"], list)
    assert md["levels"]["pdl"] == 24800.0
    assert md["levels"]["zone_low"] == 24800.0 and md["levels"]["zone_top"] == 24815.0
    assert md["levels"]["prev_close"] == 24850.0
    tc = md["trigger_candle"]
    assert (tc["o"], tc["h"], tc["l"], tc["c"]) == (24805.0, 24808.0, 24768.0, 24771.0)
    assert tc["time"].startswith("2026-03-02T09:25")
    assert md["decision_time"].startswith("2026-03-02T09:30")
    assert md["method"] == M1_ID and md["stop_points"] in (35.0, 40.0)
    assert md["provisional_rules"] == list(PDSStrategy.PROVISIONAL_RULES)
    assert sig.timestamp == datetime(2026, 3, 2, 9, 25)  # bar-OPEN label


def test_m3_signal_lists_why_first_candle_methods_failed() -> None:
    strat = PDSStrategy({"method": "ALL"})
    ctx = ctx_with()
    breakdown = bar(D1, "09:25", 24799.0, 24800.0, 24788.0, 24790.0)
    sig = signals(drive(strat, ctx, [open_bar(), weak_red_in_zone(D1, "09:20"), breakdown]))[0][1]
    assert "M3 small support broken" in sig.metadata["conditions_satisfied"]
    assert "M1 good red body" in sig.metadata["conditions_failed"]
    assert "M2 red HPR pin bar" in sig.metadata["conditions_failed"]


# --------------------------------------------------------------------------- snapshot


def test_evaluation_snapshot_is_read_only_and_json_friendly() -> None:
    import json

    strat = PDSStrategy()
    ctx = ctx_with()
    drive(strat, ctx, [open_bar(), green_in_zone(D1)])
    before = copy.deepcopy(strat._daily_state)
    snap = strat.evaluation_snapshot(D1.isoformat())
    snap["conditions_satisfied"].append("tamper")
    snap["provisional_rules"].append("tamper")
    again = strat.evaluation_snapshot(D1.isoformat(), ctx)
    assert strat._daily_state == before
    assert "tamper" not in again["conditions_satisfied"]
    assert "tamper" not in strat.PROVISIONAL_RULES
    json.dumps(again)
    assert again["state"] == "MONITORING"
    assert "zone tested from below" in again["conditions_satisfied"]
    assert again["levels"]["zone_width"] == 15.0
    assert again["zone_reached"] is True


def test_evaluation_snapshot_before_any_bar_previews_levels_without_state() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    snap = strat.evaluation_snapshot(D1, ctx)
    assert snap["state"] == "MONITORING" and snap["levels"]["pdl"] == 24800.0
    assert strat._daily_state == {}
    empty = strat.evaluation_snapshot(D1)
    assert empty["state"] == "WARMING_UP"


# --------------------------------------------------------------------------- provisional plumbing


def test_provisional_values_come_from_yaml_and_are_labelled() -> None:
    values = load_provisional("pds")
    strat = PDSStrategy()
    assert strat.min_body_ratio == values["min_body_ratio"] == 0.45
    assert strat.pin_bar_wick_ratio == values["pin_bar_wick_ratio"]
    assert strat.zone_proximity_pts == values["zone_proximity_pts"]
    manifest = provisional_manifest("pds")
    assert manifest and all(m["status"] == "PROVISIONAL" and m["why"] for m in manifest)
    assert all("author-approved" not in m["why"].lower().replace("not author-approved", "") for m in manifest)
    assert isinstance(PDSStrategy.PROVISIONAL_RULES, tuple) and PDSStrategy.PROVISIONAL_RULES


def test_provisional_value_override_changes_behaviour() -> None:
    strat = PDSStrategy({"min_body_ratio": 0.95})
    ctx = ctx_with()
    assert signals(drive(strat, ctx, [open_bar(), strong_red_in_zone(D1, "09:20")])) == []


def test_pdf_constants_are_fixed_numbers() -> None:
    strat = PDSStrategy()
    assert (strat.target_points, strat.default_stop_points, strat.max_stop_points) == (
        70.0,
        35.0,
        40.0,
    )


def test_unknown_method_and_break_mode_are_rejected() -> None:
    with pytest.raises(ValueError):
        PDSStrategy({"method": "METHOD_9"})
    with pytest.raises(ValueError):
        PDSStrategy({"support_break_mode": "wick"})


def test_reset_state_clears_every_day() -> None:
    strat = PDSStrategy()
    ctx = ctx_with()
    drive(strat, ctx, [open_bar(), strong_red_in_zone(D1, "09:20")])
    strat.reset_state()
    assert strat._daily_state == {}


def test_old_days_are_pruned() -> None:
    strat = PDSStrategy()
    for i in range(10):
        d = D1 + timedelta(days=i)
        strat._day_state(d)
    assert len(strat._daily_state) == 5
