"""IRF Method 2 conformance tests against IRF_Strategy_Guide Updated.pdf (pp. 2-3, 7-10, 13-14).

ALL BARS IN THIS FILE ARE SYNTHETIC (SIMULATED test data, not market data).  Each test proves one
rule; rules the PDF leaves undefined are marked PROVISIONAL and only prove the documented
interpretation, never author approval.

Convention: Bar.exchange_time is the bar OPEN label; the strategy is driven one completed bar at
a time.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta

import pytest

from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import StrategyContext
from trading_platform.strategies.irf import IRFStrategy
from trading_platform.strategies.provisional import load_provisional, provisional_manifest

D1 = date(2025, 3, 3)
D2 = date(2025, 3, 4)
IRH = 24900.0
IRL = 24800.0


def bar(d: date, hh: int, mm: int, o: float, h: float, low: float, c: float) -> Bar:
    """SIMULATED 5m bar labelled by its open time."""
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


class Driver:
    """Feeds bars one at a time and remembers every emitted signal."""

    def __init__(self, method: str = "METHOD_2", **cfg: object) -> None:
        self.strat = IRFStrategy({"method": method, **cfg})
        self.ctx = StrategyContext()
        self.signals: list[Signal] = []

    def feed(self, b: Bar) -> Signal | None:
        sig = self.strat.on_bar(self.ctx, b)
        if sig is not None:
            self.signals.append(sig)
        return sig

    def feed_many(self, bars: list[Bar]) -> list[Signal | None]:
        return [self.feed(b) for b in bars]


def ir_bar(d: date = D1) -> Bar:
    """09:15 initial-range candle: IRH 24900, IRL 24800 (wicks included)."""
    return bar(d, 9, 15, 24850.0, IRH, IRL, 24860.0)


def minutes(i: int) -> tuple[int, int]:
    """i-th bar after 09:15 -> (hour, minute)."""
    t = datetime(2025, 1, 1, 9, 15) + timedelta(minutes=5 * i)
    return t.hour, t.minute


def seq(d: date, rows: list[tuple[float, float, float, float]], start: int = 1) -> list[Bar]:
    """Build consecutive bars from (o,h,l,c) rows starting `start` bars after 09:15."""
    return [bar(d, *minutes(start + i), *r) for i, r in enumerate(rows)]


# Shared Confirmation-A setup (Figure 3): breakout close, extension, red pullback candle.
FIG3_PREFIX = [
    (24890.0, 24925.0, 24885.0, 24920.0),  # B: close 24920 > IRH (green)
    (24920.0, 24935.0, 24910.0, 24930.0),  # extension, low 24910 (green)
    (24930.0, 24932.0, 24915.0, 24918.0),  # first red pullback candle, low 24915 -> support
]

# Shared Confirmation-B setup (Figure 4): resistance 24940 = top of the first push.
FIG4_PREFIX = [
    (24890.0, 24925.0, 24885.0, 24920.0),  # B (green)
    (24920.0, 24940.0, 24915.0, 24935.0),  # push top: high 24940 -> resistance
    (24935.0, 24936.0, 24905.0, 24910.0),  # first red candle (pullback), low 24905 -> support
    (24910.0, 24938.0, 24908.0, 24936.0),  # green, still below 24940
]


# --------------------------------------------------------------------------- initial range


def test_ir_is_only_the_0915_candle_and_late_start_is_silent() -> None:
    """PDF p3-4: IR = 09:15-09:20 candle only; never substitute a later candle."""
    drv = Driver()
    # 09:15 bar missing: the 09:20 bar must NOT become the IR.
    sigs = drv.feed_many(seq(D1, [(24850.0, 24900.0, 24800.0, 24860.0)] + FIG3_PREFIX, start=1))
    assert not any(sigs)
    snap = drv.strat.evaluation_snapshot(D1)
    assert snap["status"] == "NO_IR_BAR"
    assert snap["levels"]["irh"] is None
    # even a complete valid sequence afterwards stays silent
    drv.feed_many(seq(D1, [(24918.0, 24920.0, 24890.0, 24895.0)], start=5))
    assert drv.signals == []


def test_late_0915_bar_after_other_bars_is_ignored() -> None:
    drv = Driver()
    drv.feed(bar(D1, 9, 20, 24850.0, 24900.0, 24800.0, 24860.0))
    assert drv.feed(ir_bar()) is None  # out-of-order 09:15 arrives late -> ignored
    assert drv.strat.evaluation_snapshot(D1)["status"] == "NO_IR_BAR"


def test_partial_session_starting_at_1345_is_silent() -> None:
    drv = Driver()
    drv.feed(bar(D1, 13, 45, 24850.0, 24900.0, 24800.0, 24860.0))
    drv.feed(bar(D1, 13, 50, 24890.0, 24925.0, 24885.0, 24920.0))
    assert drv.signals == []
    assert drv.strat.evaluation_snapshot(D1)["status"] == "NO_IR_BAR"


def test_per_day_state_resets_and_stale_levels_are_never_reused() -> None:
    drv = Driver()
    drv.feed(ir_bar(D1))
    drv.feed(bar(D1, 9, 20, 24890.0, 24925.0, 24885.0, 24920.0))
    # Day 2 starts without a 09:15 bar: day-1 IRH/breakout must not leak into day 2.
    drv.feed(bar(D2, 9, 20, 24850.0, 24990.0, 24840.0, 24980.0))
    for b in seq(D2, FIG3_PREFIX + [(24918.0, 24920.0, 24890.0, 24895.0)], start=2):
        drv.feed(b)
    assert drv.signals == []
    snap2 = drv.strat.evaluation_snapshot(D2)
    assert snap2["status"] == "NO_IR_BAR"
    assert snap2["levels"]["irh"] is None and snap2["levels"]["support"] is None
    assert drv.strat.evaluation_snapshot(D1)["levels"]["irh"] == IRH


def test_day_rollover_clears_stale_method3_swing_low() -> None:
    """Method 3 reads context.state['recent_swing_low']; yesterday's value must not survive."""
    drv = Driver("METHOD_3")
    drv.ctx.state["recent_swing_low"] = 24870.0
    drv.feed(ir_bar(D1))
    assert drv.ctx.state["recent_swing_low"] == 24870.0  # same-day value untouched
    drv.feed(ir_bar(D2))
    assert drv.ctx.state["recent_swing_low"] is None


# --------------------------------------------------------------------------- Method 1 vs 2


def test_immediate_close_back_inside_is_method1_and_disables_method2() -> None:
    """PDF p9/p13: candle after the breakout closes back inside => Method 1, not Method 2."""
    drv = Driver()
    drv.feed(ir_bar())
    rows = [
        (24890.0, 24925.0, 24885.0, 24920.0),  # B closes above IRH
        (24918.0, 24920.0, 24880.0, 24890.0),  # immediate failure: closes inside the range
        # Later: re-breakout, extension, first red, then close below its low
        (24890.0, 24930.0, 24888.0, 24925.0),
        (24925.0, 24940.0, 24920.0, 24935.0),
        (24935.0, 24936.0, 24915.0, 24918.0),
        (24918.0, 24920.0, 24880.0, 24885.0),
    ]
    assert not any(drv.feed_many(seq(D1, rows)))
    snap = drv.strat.evaluation_snapshot(D1)
    assert snap["status"] == "METHOD1_PATTERN_METHOD2_DISABLED"
    assert any(c.startswith("day_disabled") for c in snap["conditions_failed"])


def test_method1_still_works_unchanged_for_method1_config() -> None:
    drv = Driver("METHOD_1")
    drv.feed(ir_bar())
    drv.feed(bar(D1, 9, 20, 24890.0, 24925.0, 24885.0, 24920.0))
    sig = drv.feed(bar(D1, 9, 25, 24918.0, 24920.0, 24880.0, 24890.0))
    assert sig is not None and sig.method_id == "IRF_METHOD_1_IMMEDIATE_FAILURE"


def test_close_equal_irh_is_not_a_breakout_close() -> None:
    drv = Driver()
    drv.feed(ir_bar())
    drv.feed(bar(D1, 9, 20, 24890.0, 24910.0, 24885.0, IRH))  # close == IRH
    assert drv.strat.evaluation_snapshot(D1)["status"] == "WAITING_BREAKOUT"


def test_wick_above_irh_without_close_is_not_method2_start() -> None:
    drv = Driver()
    drv.feed(ir_bar())
    drv.feed(bar(D1, 9, 20, 24890.0, 24950.0, 24885.0, 24895.0))
    snap = drv.strat.evaluation_snapshot(D1)
    assert "no_close_above_irh_yet" in snap["conditions_failed"]


# --------------------------------------------------------------------------- Confirmation A


def test_support_break_above_irh_fires_without_closing_inside_range() -> None:
    """PDF Fig 3 / p8: any candle closing below the pullback low is the trigger; the close does
    not have to be back at/inside the range (baseline wrongly required close <= IRH)."""
    drv = Driver()
    drv.feed(ir_bar())
    sigs = drv.feed_many(seq(D1, FIG3_PREFIX + [(24918.0, 24919.0, 24905.0, 24910.0)]))
    assert sigs[:3] == [None, None, None]
    sig = sigs[3]
    assert sig is not None
    assert sig.method_id == "IRF_METHOD_2_SUPPORT_BREAK"
    assert sig.direction == SignalDirection.SELL
    assert sig.reference_price == 24910.0 and 24910.0 > IRH
    assert sig.target == 24910.0 - 60.0


def test_support_trigger_candle_need_not_be_red() -> None:
    """PDF p8: 'A candle closes below that support' (any colour); red is required on B only."""
    drv = Driver()
    drv.feed(ir_bar())
    green_below = (24890.0, 24914.0, 24885.0, 24912.0)  # green, closes below support 24915
    sigs = drv.feed_many(seq(D1, FIG3_PREFIX + [green_below]))
    assert sigs[3] is not None and sigs[3].method_id == "IRF_METHOD_2_SUPPORT_BREAK"


def test_red_candle_with_no_level_break_is_not_a_signal() -> None:
    """PDF p9 'What does not qualify': a red candle by itself is not confirmation."""
    drv = Driver()
    drv.feed(ir_bar())
    sigs = drv.feed_many(seq(D1, FIG3_PREFIX + [(24925.0, 24926.0, 24917.0, 24920.0)]))
    assert not any(sigs)


def test_first_red_candle_cannot_trigger_itself_provisional() -> None:
    """PROVISIONAL (support definition): the pullback low is defined by the first red candle,
    so that candle cannot also be the trigger."""
    drv = Driver()
    drv.feed(ir_bar())
    red_below_prior_lows = (24930.0, 24931.0, 24890.0, 24895.0)  # closes under every earlier low
    sigs = drv.feed_many(seq(D1, FIG3_PREFIX[:2] + [red_below_prior_lows]))
    assert not any(sigs)
    snap = drv.strat.evaluation_snapshot(D1)
    assert snap["levels"]["support"] == 24890.0  # the red candle's own low


def test_support_is_the_pullback_low_not_the_lowest_low_of_all_extension_bars() -> None:
    """PDF Fig 3: the support line sits at the pullback (red) candle's low, ABOVE an earlier
    extension bar's low.  A close between the two levels breaks support."""
    drv = Driver()
    drv.feed(ir_bar())
    # extension bar low 24910 < pullback low 24915; trigger close 24912 is between them
    sigs = drv.feed_many(seq(D1, FIG3_PREFIX + [(24918.0, 24919.0, 24905.0, 24912.0)]))
    assert sigs[3] is not None
    assert sigs[3].metadata["levels"]["support"] == 24915.0


def test_support_ignores_bars_before_the_first_red_candle() -> None:
    drv = Driver()
    drv.feed(ir_bar())
    drv.feed_many(seq(D1, FIG3_PREFIX))
    assert drv.strat.evaluation_snapshot(D1)["levels"]["support"] == 24915.0


# --------------------------------------------------------------------------- Confirmation B


def test_resistance_break_worked_example_from_pdf_p10() -> None:
    """PDF p10 worked example: IRH 24081.75 / IRL 24039.95; first push tops at 24128.75; price
    pulls back, trades back above it, a red candle closes back below at 24123.50 -> short with
    stop 24158.50 and target 24063.50."""
    irh, irl = 24081.75, 24039.95
    drv = Driver()
    drv.feed(bar(D1, 9, 15, 24050.0, irh, irl, 24060.0))
    rows = [
        (24070.0, 24100.0, 24068.0, 24095.0),  # B: closes above IRH 24081.75 (green)
        (24095.0, 24128.75, 24090.0, 24125.0),  # first push tops at 24128.75 (green)
        (24125.0, 24126.0, 24096.0, 24100.0),  # red pullback, low 24096 (support far below entry)
        (24100.0, 24120.0, 24098.0, 24118.0),  # green back up (most recent green: high 24120)
        (24118.0, 24131.0, 24117.0, 24129.0),  # trades back above 24128.75, closes above it
        (24129.0, 24130.0, 24122.0, 24123.5),  # red candle closes back below resistance
    ]
    sigs = drv.feed_many(seq(D1, rows))
    sig = sigs[5]
    assert sig is not None
    assert not any(sigs[:5])
    assert sig.method_id == "IRF_METHOD_2_RESISTANCE_BREAK"
    assert sig.reference_price == 24123.5
    assert sig.stop_loss == pytest.approx(24158.5)  # 35 pts: green candle within 35 of entry
    assert sig.target == pytest.approx(24063.5)
    assert sig.metadata["levels"]["resistance"] == 24128.75
    assert sig.metadata["levels"]["irh"] == irh
    # entry is ~42 points above IRH, impossible under the old close<=IRH gate
    assert sig.reference_price - irh > 40


def test_resistance_is_first_push_top_and_does_not_rise_with_later_highs() -> None:
    drv = Driver()
    drv.feed(ir_bar())
    drv.feed_many(seq(D1, FIG4_PREFIX))
    assert drv.strat.evaluation_snapshot(D1)["levels"]["resistance"] == 24940.0
    drv.feed(bar(D1, *minutes(5), 24936.0, 24990.0, 24935.0, 24985.0))  # much higher high
    snap = drv.strat.evaluation_snapshot(D1)
    assert snap["levels"]["resistance"] == 24940.0
    assert "traded_back_above_resistance" in snap["conditions_satisfied"]


def test_price_still_holding_above_resistance_is_not_a_signal() -> None:
    """PDF p9: until a red candle closes back below resistance the push has not failed."""
    drv = Driver()
    drv.feed(ir_bar())
    rows = FIG4_PREFIX + [
        (24936.0, 24948.0, 24935.0, 24945.0),  # pushes above resistance and holds
        (24945.0, 24950.0, 24940.5, 24941.0),  # red but still closes above resistance
    ]
    assert not any(drv.feed_many(seq(D1, rows)))


def test_red_close_below_resistance_after_push_above_fires() -> None:
    drv = Driver()
    drv.feed(ir_bar())
    rows = FIG4_PREFIX + [
        (24936.0, 24948.0, 24935.0, 24945.0),  # traded back above resistance (green)
        (24945.0, 24946.0, 24930.0, 24935.0),  # red close back below 24940
    ]
    sigs = drv.feed_many(seq(D1, rows))
    assert sigs[5] is not None and sigs[5].method_id == "IRF_METHOD_2_RESISTANCE_BREAK"
    assert "resistance_break_red_close_below_resistance" in sigs[5].metadata["conditions_satisfied"]


def test_green_close_below_resistance_is_not_a_resistance_break() -> None:
    """PDF p8-9: resistance break needs a RED candle."""
    drv = Driver("METHOD_2_RESISTANCE_BREAK")
    drv.feed(ir_bar())
    rows = FIG4_PREFIX + [
        (24936.0, 24948.0, 24935.0, 24945.0),
        (24920.0, 24938.0, 24918.0, 24936.0),  # green, closes below 24940
    ]
    assert not any(drv.feed_many(seq(D1, rows)))


def test_single_bar_wick_through_and_reject_is_not_accepted_provisional() -> None:
    """PROVISIONAL (strict sequencing): a bar that wicks above resistance AND closes red below it
    does not count as 'price traded back above' before the trigger bar."""
    drv = Driver("METHOD_2_RESISTANCE_BREAK")
    drv.feed(ir_bar())
    rows = FIG4_PREFIX + [(24936.0, 24945.0, 24930.0, 24932.0)]  # red, wick above, close below
    assert not any(drv.feed_many(seq(D1, rows)))


def test_variants_only_fire_their_own_confirmation() -> None:
    support_rows = FIG3_PREFIX + [(24918.0, 24919.0, 24905.0, 24910.0)]
    d_res = Driver("METHOD_2_RESISTANCE_BREAK")
    d_res.feed(ir_bar())
    assert not any(d_res.feed_many(seq(D1, support_rows)))
    d_sup = Driver("METHOD_2_SUPPORT_BREAK")
    d_sup.feed(ir_bar())
    rows = FIG4_PREFIX + [
        (24936.0, 24948.0, 24935.0, 24945.0),
        (24945.0, 24946.0, 24930.0, 24935.0),
    ]
    # last bar closes 24935 which is above support 24905 -> no support break either
    assert not any(d_sup.feed_many(seq(D1, rows)))


# --------------------------------------------------------------------------- extension / dupes


def test_min_extension_bars_is_provisional_config_not_hard_coded() -> None:
    """PROVISIONAL: extension length read through load_provisional('irf')."""
    assert load_provisional("irf")["min_extension_bars"] == 2
    rows = [
        (24890.0, 24925.0, 24885.0, 24920.0),  # B
        (24920.0, 24922.0, 24915.0, 24917.0),  # first red, still above IRH (extension bar #2)
        (24917.0, 24918.0, 24890.0, 24895.0),  # closes below its low
    ]
    ok = Driver()
    ok.feed(ir_bar())
    assert ok.feed_many(seq(D1, rows))[2] is not None
    strict = Driver(min_extension_bars=3)
    strict.feed(ir_bar())
    assert not any(strict.feed_many(seq(D1, rows)))


def test_only_one_signal_per_setup_and_no_double_evaluation() -> None:
    drv = Driver()
    drv.feed(ir_bar())
    sigs = drv.feed_many(
        seq(
            D1,
            FIG3_PREFIX
            + [
                (24918.0, 24919.0, 24905.0, 24910.0),  # trigger
                (24910.0, 24911.0, 24890.0, 24895.0),  # further lower close: no 2nd signal
            ],
        )
    )
    assert sigs[3] is not None and sigs[4] is None
    # re-driving the same completed bar (duplicate delivery) is ignored
    assert drv.strat.generate_signal(drv.ctx) is None
    assert len(drv.signals) == 1
    assert drv.strat.evaluation_snapshot(D1)["status"] == "SIGNALLED"


def test_missing_bar_in_the_session_fails_closed() -> None:
    drv = Driver()
    drv.feed(ir_bar())
    drv.feed_many(seq(D1, FIG3_PREFIX))
    # skip one 5m bar, then a would-be trigger
    sig = drv.feed(bar(D1, *minutes(5), 24918.0, 24919.0, 24905.0, 24910.0))
    assert sig is None
    assert drv.strat.evaluation_snapshot(D1)["status"] == "DATA_GAP"


# --------------------------------------------------------------------------- stop / metadata


def test_stop_is_35_when_recent_green_within_35_and_40_when_farther() -> None:
    near = Driver()
    near.feed(ir_bar())
    s_near = near.feed_many(seq(D1, FIG3_PREFIX + [(24918.0, 24919.0, 24905.0, 24910.0)]))[3]
    assert s_near is not None
    # most recent green candle high 24935 - entry 24910 = 25 <= 35 -> 35
    assert s_near.stop_loss == pytest.approx(24910.0 + 35.0)

    far = Driver()
    far.feed(ir_bar())
    rows = [
        (24890.0, 24925.0, 24885.0, 24920.0),
        (24920.0, 24990.0, 24910.0, 24985.0),  # green, high 24990
        (24985.0, 24986.0, 24960.0, 24965.0),  # first red, low 24960
        (24965.0, 24966.0, 24930.0, 24935.0),  # closes below support; entry 24935
    ]
    s_far = far.feed_many(seq(D1, rows))[3]
    assert s_far is not None
    assert s_far.stop_loss == pytest.approx(24935.0 + 40.0)  # 24990 - 24935 = 55 > 35
    assert s_far.metadata["stop_points"] == 40.0


def test_stop_green_candle_search_is_same_session_only() -> None:
    """Yesterday's green candle must never select the stop (baseline scanned all history)."""
    drv = Driver()
    drv.feed(ir_bar(D1))
    # yesterday: a green candle with a very high high
    drv.feed(bar(D1, 9, 20, 24890.0, 25200.0, 24885.0, 24895.0))
    # today: every bar is red/doji-red so no same-day green exists before the trigger
    drv.feed(bar(D2, 9, 15, 24860.0, IRH, IRL, 24850.0))  # red IR bar
    rows = [
        (24960.0, 24962.0, 24940.0, 24950.0),  # B: red but closes above IRH
        (24950.0, 24952.0, 24930.0, 24935.0),  # first red, low 24930
        (24935.0, 24936.0, 24910.0, 24915.0),  # closes below support
    ]
    sig = drv.feed_many(seq(D2, rows))[2]
    assert sig is not None
    assert sig.metadata["stop_distance_to_green"] is None
    assert sig.stop_loss == pytest.approx(24915.0 + 35.0)  # no-green fallback (provisional)


def test_signal_metadata_is_rich_and_machine_readable() -> None:
    drv = Driver()
    drv.feed(ir_bar())
    sig = drv.feed_many(seq(D1, FIG3_PREFIX + [(24918.0, 24919.0, 24905.0, 24910.0)]))[3]
    assert sig is not None
    md = sig.metadata
    assert "support_break_close_below_support" in md["conditions_satisfied"]
    assert "no_red_close_below_resistance_after_push_above" in md["conditions_failed"]
    assert md["levels"]["irh"] == IRH and md["levels"]["irl"] == IRL
    assert md["levels"]["support"] == 24915.0
    tc = md["trigger_candle"]
    assert (tc["o"], tc["h"], tc["l"], tc["c"]) == (24918.0, 24919.0, 24905.0, 24910.0)
    assert tc["time"].endswith("09:35:00")
    assert md["bar_close_time"].endswith("09:40:00")  # signal known at bar open + 5 minutes
    assert md["provisional_rules"] and "not author-approved" in md["tier_note"]


# --------------------------------------------------------------------------- snapshot / config


def test_evaluation_snapshot_is_read_only_and_explains_state() -> None:
    drv = Driver()
    empty = drv.strat.evaluation_snapshot(D1)
    assert empty["status"] == "NO_DATA" and drv.strat._daily_state == {}

    drv.feed(ir_bar())
    drv.feed_many(seq(D1, FIG3_PREFIX))
    before = copy.deepcopy(drv.strat._daily_state)
    snap = drv.strat.evaluation_snapshot(D1)
    snap_again = drv.strat.evaluation_snapshot(D1.isoformat())
    assert drv.strat._daily_state == before
    assert snap == snap_again
    assert snap["status"] == "ARMED"
    assert "red_candle_after_breakout" in snap["conditions_satisfied"]
    assert "not_traded_back_above_resistance" in snap["conditions_failed"]
    assert snap["levels"]["support"] == 24915.0
    assert snap["levels"]["resistance"] == 24935.0
    assert snap["provisional_rules"]
    # mutating the snapshot cannot change strategy state
    snap["levels"]["support"] = 1.0
    assert drv.strat._daily_state == before


def test_provisional_rules_and_yaml_are_labelled_and_never_author_approved() -> None:
    assert IRFStrategy.PROVISIONAL_RULES
    manifest = provisional_manifest("irf")
    keys = {m["key"] for m in manifest}
    assert {"min_extension_bars", "support_definition", "resistance_definition"} <= keys
    for m in manifest:
        assert m["status"] == "PROVISIONAL"
        assert "author-approved" not in m["why"].lower().replace("not author-approved", "")


def test_every_provisional_key_is_actually_read_by_the_strategy() -> None:
    strat = IRFStrategy({"method": "METHOD_2"})
    yaml_keys = {m["key"] for m in provisional_manifest("irf")}
    assert yaml_keys == set(strat.provisional_values)


def test_unsupported_config_raises_instead_of_being_ignored() -> None:
    with pytest.raises(ValueError):
        IRFStrategy({"method": "METHOD_9"})
    with pytest.raises(ValueError):
        IRFStrategy({"method": "METHOD_2", "support_definition": "SWING_PIVOT"})
    with pytest.raises(ValueError):
        IRFStrategy({"method": "METHOD_2", "min_extension_bars": 0})


def test_no_look_ahead_signal_uses_only_bars_up_to_the_trigger() -> None:
    """Signals must be identical whether or not later bars exist."""
    rows = FIG3_PREFIX + [(24918.0, 24919.0, 24905.0, 24910.0)]
    later = [(24910.0, 25100.0, 24400.0, 24500.0)]
    a, b = Driver(), Driver()
    a.feed(ir_bar())
    b.feed(ir_bar())
    sa = a.feed_many(seq(D1, rows))[3]
    sb = b.feed_many(seq(D1, rows + later))[3]
    assert sa is not None and sb is not None
    assert sa.model_dump() == sb.model_dump()
