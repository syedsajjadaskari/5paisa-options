"""PDR conformance tests against PDR_Strategy_Guide.pdf.

All bars are SYNTHETIC (hand-built OHLC, not market data).  Each test names the PDF rule it
proves or the PROVISIONAL rule (configs/strategy_provisional/pdr.yaml) it exercises.

Fixture zone: previous-day high (zone top) 24800, zone bottom (candle body level) 24790,
previous close 24750.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta

import pytest

from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import StrategyContext
from trading_platform.strategies.pdr import PDRStrategy
from trading_platform.strategies.provisional import load_provisional, provisional_manifest

D1 = date(2025, 3, 10)
D2 = date(2025, 3, 11)
LEVELS = {"pdh": 24800.0, "prev_close": 24750.0, "pd_res_body": 24790.0}


def bar(d: date, hhmm: str, o: float, h: float, low: float, c: float) -> Bar:
    """SYNTHETIC completed 5m bar; hhmm is the bar OPEN label."""
    hour, minute = int(hhmm[:2]), int(hhmm[3:])
    t = datetime(d.year, d.month, d.day, hour, minute)
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


def make(config: dict | None = None, levels: dict | None = LEVELS):
    strat = PDRStrategy(config=config)
    ctx = StrategyContext()
    if levels:
        ctx.state.update(levels)
    return strat, ctx


def feed(strat: PDRStrategy, ctx: StrategyContext, bars: list[Bar]) -> list[Signal]:
    return [s for b in bars if (s := strat.on_bar(ctx, b)) is not None]


def gap_bar(d: date = D1) -> Bar:
    """09:15 gap-up bar: open 24830 > prev_close 24750 and > zone top 24800; no zone touch."""
    return bar(d, "09:15", 24830, 24850, 24820, 24840)


def touch_bar(d: date = D1, hhmm: str = "09:20") -> Bar:
    """Red candle whose low (24803) is within 5 pts above zone top: a zone test."""
    return bar(d, hhmm, 24815, 24818, 24803, 24810)


def green_bar(d: date = D1, hhmm: str = "09:25", close: float = 24825) -> Bar:
    return bar(d, hhmm, 24810, close + 5, 24808, close)


# ----------------------------------------------------------------------------- happy path


def test_gap_up_zone_test_then_green_candle_emits_buy_with_rich_metadata() -> None:
    strat, ctx = make()
    sigs = feed(strat, ctx, [gap_bar(), touch_bar(), green_bar()])
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.direction == SignalDirection.BUY
    assert sig.method_id == "PDR_RETEST_BOUNCE"
    assert sig.reference_price == 24825
    assert sig.target == 24825 + 70.0  # PDF: fixed 70 points
    assert sig.stop_loss == 24825 - 35.0  # PDF: 35 (selector is PROVISIONAL)
    assert sig.timestamp == datetime(2025, 3, 10, 9, 25)  # bar OPEN label
    md = sig.metadata
    assert md["decision_time"] == datetime(2025, 3, 10, 9, 30).isoformat()
    assert md["target_points"] == 70.0 and md["stop_points"] == 35.0
    assert md["stop_rule"] == "PROVISIONAL_35_OR_40_SELECTOR"
    assert md["conditions_failed"] == []
    assert any(c.startswith("gap_up") for c in md["conditions_satisfied"])
    assert "green_candle_confirmation" in md["conditions_satisfied"]
    assert md["levels"]["zone_top"] == 24800 and md["levels"]["zone_bottom"] == 24790
    assert md["levels"]["pdh"] == 24800 and md["levels"]["day_open"] == 24830
    assert md["trigger_candle"] == {
        "o": 24810,
        "h": 24830,
        "l": 24808,
        "c": 24825,
        "time": datetime(2025, 3, 10, 9, 25).isoformat(),
    }
    assert md["zone_touch_candle_time"] == datetime(2025, 3, 10, 9, 20).isoformat()
    assert PDRStrategy.PROVISIONAL_RULES and md["provisional_rules"]


def test_no_signal_when_price_never_returns_to_zone() -> None:
    """PDF 4.1: if price keeps rising without returning to the zone there is no setup."""
    strat, ctx = make()
    bars = [gap_bar()] + [
        bar(D1, f"09:{m:02d}", 24850 + m, 24860 + m, 24845 + m, 24858 + m) for m in range(20, 55, 5)
    ]
    assert feed(strat, ctx, bars) == []
    assert strat.evaluation_snapshot(D1)["phase"] == "AWAITING_ZONE_TEST"


def test_far_from_zone_does_not_qualify_as_test() -> None:
    """PDF 4.2: a move that stays far from the zone does not qualify; 5 pt proximity is
    PROVISIONAL (24805 qualifies, 24806 does not)."""
    strat, ctx = make()
    near = bar(D1, "09:20", 24815, 24818, 24805, 24810)
    far = bar(D1, "09:20", 24815, 24818, 24806, 24810)
    assert feed(strat, ctx, [gap_bar(), near, green_bar()])
    strat2, ctx2 = make()
    assert feed(strat2, ctx2, [gap_bar(), far, green_bar()]) == []


@pytest.mark.parametrize(
    "touch",
    [
        bar(D1, "09:20", 24815, 24820, 24795, 24812),  # wick/inside zone
        bar(D1, "09:20", 24815, 24818, 24791, 24792),  # body inside zone
        bar(D1, "09:20", 24815, 24818, 24780, 24795),  # wick through zone, close inside
    ],
)
def test_wick_body_and_inside_zone_interactions_count(touch: Bar) -> None:
    strat, ctx = make()
    assert len(feed(strat, ctx, [gap_bar(), touch, green_bar()])) == 1


# ------------------------------------------------------------------------------ gap gate


def test_requires_gap_up_open_above_previous_close() -> None:
    """PDF 4.1: market must open gap up (open > previous close)."""
    strat, ctx = make(levels={"pdh": 24900.0, "prev_close": 24850.0, "pd_res_body": 24890.0})
    no_gap = bar(D1, "09:15", 24845, 24920, 24840, 24910)  # open 24845 <= prev_close 24850
    bars = [no_gap, bar(D1, "09:20", 24915, 24918, 24903, 24910), green_bar(close=24925)]
    assert feed(strat, ctx, bars) == []
    snap = strat.evaluation_snapshot(D1)
    assert snap["phase"] == "NO_GAP_UP" and snap["state"] == "DONE_FOR_DAY"
    assert any(c.startswith("gap_up") for c in snap["conditions_failed"])


def test_open_must_be_above_zone_top_provisional_chart_reading() -> None:
    """PROVISIONAL: gap-up that opens inside/below the zone is price approaching from below."""
    low_gap = bar(D1, "09:15", 24795, 24805, 24790, 24802)  # > prev_close 24750, < zone top
    strat, ctx = make()
    assert feed(strat, ctx, [low_gap, touch_bar(), green_bar()]) == []
    assert strat.evaluation_snapshot(D1)["phase"] == "OPEN_NOT_ABOVE_ZONE"
    strat2, ctx2 = make(config={"require_open_above_zone_top": False})
    assert len(feed(strat2, ctx2, [low_gap, touch_bar(), green_bar()])) == 1


def test_gap_uses_0915_bar_open_not_a_later_bar() -> None:
    """09:15 gap-down bar followed by a bar that opens above the previous close: still silent."""
    strat, ctx = make(levels={"pdh": 24900.0, "prev_close": 24850.0, "pd_res_body": 24890.0})
    no_gap = bar(D1, "09:15", 24845, 24850, 24840, 24848)  # open <= prev_close
    later_gap = bar(D1, "09:20", 24960, 24965, 24903, 24910)  # opens above, touches zone
    confirm = bar(D1, "09:25", 24910, 24935, 24908, 24925)
    assert feed(strat, ctx, [no_gap, later_gap, confirm]) == []
    assert strat.evaluation_snapshot(D1)["phase"] == "NO_GAP_UP"


# ----------------------------------------------------------------------- first bar handling


def test_missing_0915_bar_keeps_strategy_silent_for_the_day() -> None:
    """A late start must not substitute the 09:20 bar for the day open."""
    strat, ctx = make()
    bars = [
        bar(D1, "09:20", 24830, 24835, 24803, 24810),
        touch_bar(D1, "09:25"),
        green_bar(D1, "09:30"),
    ]
    assert feed(strat, ctx, bars) == []
    snap = strat.evaluation_snapshot(D1)
    assert snap["phase"] == "FIRST_BAR_MISSING" and snap["state"] == "BLOCKED"
    assert any("first_bar_0915" in c for c in snap["conditions_failed"])


def test_entry_never_on_first_bar_of_day() -> None:
    """PDF 4.1: do not enter at the open.  A green 09:15 bar that dips into the zone is not
    a confirmation, even when same-bar touch+confirm is allowed."""
    first = bar(D1, "09:15", 24830, 24840, 24803, 24838)  # touches zone, green, gap-up
    strat, ctx = make(config={"allow_same_bar_touch_and_confirm": True})
    assert feed(strat, ctx, [first]) == []
    # ...but that 09:15 touch is a valid test for the next green candle.
    assert len(feed(strat, ctx, [green_bar(D1, "09:20")])) == 1


# ------------------------------------------------------------------------------ the latch


def test_confirmation_must_come_after_the_test_candle() -> None:
    """PDF 4.3/6: green candle formed AFTER the interaction (literal reading, PROVISIONAL)."""
    same = bar(D1, "09:20", 24805, 24830, 24803, 24825)  # touches and is green, same candle
    strat, ctx = make()
    assert feed(strat, ctx, [gap_bar(), same]) == []
    # the touching candle itself is a valid test for the next green candle
    assert len(feed(strat, ctx, [green_bar()])) == 1


def test_same_bar_touch_and_confirm_is_provisional_opt_in() -> None:
    same = bar(D1, "09:20", 24805, 24830, 24803, 24825)
    strat, ctx = make(config={"allow_same_bar_touch_and_confirm": True})
    assert len(feed(strat, ctx, [gap_bar(), same])) == 1


def test_red_candle_after_test_is_not_a_confirmation() -> None:
    strat, ctx = make()
    red = bar(D1, "09:25", 24820, 24822, 24806, 24808)
    assert feed(strat, ctx, [gap_bar(), touch_bar(), red]) == []
    snap = strat.evaluation_snapshot(D1)
    assert snap["phase"] == "ZONE_TESTED_AWAITING_GREEN"
    assert "zone_tested" in snap["conditions_satisfied"]
    assert any(c.startswith("green_candle_confirmation") for c in snap["conditions_failed"])


def test_close_below_zone_discards_earlier_test_provisional() -> None:
    """PROVISIONAL: support failed (close below zone bottom) -> a fresh test is required."""
    strat, ctx = make()
    breakdown = bar(D1, "09:25", 24805, 24806, 24770, 24780)  # closes below 24790
    recovery = bar(D1, "09:30", 24812, 24830, 24810, 24826)  # green, close back above bottom
    assert feed(strat, ctx, [gap_bar(), touch_bar(), breakdown, recovery]) == []
    assert strat.evaluation_snapshot(D1)["phase"] == "AWAITING_ZONE_TEST"
    # without the reset the old latch would have fired on the recovery candle
    strat2, ctx2 = make(config={"reset_touch_on_close_below_zone_bottom": False})
    assert len(feed(strat2, ctx2, [gap_bar(), touch_bar(), breakdown, recovery])) == 1


def test_green_confirmation_closing_below_zone_bottom_rejected_provisional() -> None:
    strat, ctx = make()
    weak = bar(D1, "09:25", 24770, 24788, 24765, 24780)  # green but below 24790
    assert feed(strat, ctx, [gap_bar(), touch_bar(), weak]) == []
    strat2, ctx2 = make(
        config={
            "require_confirm_close_at_or_above_zone_bottom": False,
            "reset_touch_on_close_below_zone_bottom": False,
        }
    )
    assert len(feed(strat2, ctx2, [gap_bar(), touch_bar(), weak])) == 1


def test_latch_limits_are_optional_provisional_knobs() -> None:
    late = [gap_bar(), touch_bar()] + [
        bar(D1, f"09:{m:02d}", 24850, 24855, 24845, 24850) for m in range(25, 45, 5)
    ]
    far_green = bar(D1, "09:45", 24850, 24900, 24848, 24895)
    strat, ctx = make()  # no limit by default
    assert len(feed(strat, ctx, late + [far_green])) == 1
    strat2, ctx2 = make(config={"max_bars_between_touch_and_confirm": 3})
    assert feed(strat2, ctx2, late + [far_green]) == []
    strat3, ctx3 = make(config={"max_confirm_distance_above_zone_top_points": 40})
    assert feed(strat3, ctx3, late + [far_green]) == []


# --------------------------------------------------------------------- one signal / dedupe


def test_one_signal_per_setup_no_duplicate_emission() -> None:
    strat, ctx = make()
    sigs = feed(
        strat,
        ctx,
        [gap_bar(), touch_bar(), green_bar(), touch_bar(D1, "09:30"), green_bar(D1, "09:35")],
    )
    assert len(sigs) == 1
    assert strat.evaluation_snapshot(D1)["phase"] == "SIGNALLED"


def test_max_signals_per_day_requires_a_fresh_zone_test() -> None:
    strat, ctx = make(config={"max_signals_per_day": 2})
    sigs = feed(
        strat,
        ctx,
        [
            gap_bar(),
            touch_bar(),
            green_bar(),
            green_bar(D1, "09:30", 24830),  # no new test since the first signal
            touch_bar(D1, "09:35"),
            green_bar(D1, "09:40"),
        ],
    )
    assert [s.timestamp.strftime("%H:%M") for s in sigs] == ["09:25", "09:40"]


# ----------------------------------------------------------------------------- stop / target


def test_stop_selector_is_provisional_35_or_40() -> None:
    near = make()
    sig35 = feed(*near, [gap_bar(), touch_bar(), green_bar(close=24825)])[0]  # 35 above bottom
    assert sig35.metadata["stop_points"] == 35.0
    far = make()
    sig40 = feed(*far, [gap_bar(), touch_bar(), green_bar(close=24826)])[0]  # 36 above bottom
    assert sig40.metadata["stop_points"] == 40.0
    assert sig40.stop_loss == 24826 - 40.0 and sig40.target == 24826 + 70.0


# --------------------------------------------------------------------------- per-day state


def test_state_resets_by_date_and_requires_fresh_levels() -> None:
    strat, ctx = make()
    assert len(feed(strat, ctx, [gap_bar(), touch_bar(), green_bar()])) == 1
    # Day 2: new explicit levels, no leakage of day-1 latch or signal count.
    ctx.state.update({"pdh": 24900.0, "prev_close": 24850.0, "pd_res_body": 24890.0})
    d2 = [
        bar(D2, "09:15", 24930, 24950, 24920, 24940),
        bar(D2, "09:20", 24915, 24918, 24903, 24910),
        bar(D2, "09:25", 24910, 24935, 24908, 24925),
    ]
    sigs = feed(strat, ctx, d2)
    assert len(sigs) == 1 and sigs[0].metadata["levels"]["pdh"] == 24900


def test_touch_latch_does_not_leak_into_next_day() -> None:
    strat, ctx = make()
    feed(strat, ctx, [gap_bar(), touch_bar()])  # day 1 ends zone-tested, no confirmation
    ctx.state.update({"pdh": 24900.0, "prev_close": 24850.0, "pd_res_body": 24890.0})
    d2 = [
        bar(D2, "09:15", 24930, 24950, 24920, 24940),
        bar(D2, "09:20", 24940, 24960, 24935, 24955),
    ]
    assert feed(strat, ctx, d2) == []  # green but no day-2 zone test


def test_stale_or_missing_levels_mean_no_signal() -> None:
    strat, ctx = make()
    assert len(feed(strat, ctx, [gap_bar(), touch_bar(), green_bar()])) == 1
    # Day 2 caller forgot to refresh the levels: identical to day 1 -> stale -> silent.
    d2 = [gap_bar(D2), touch_bar(D2), green_bar(D2)]
    assert feed(strat, ctx, d2) == []
    assert strat.evaluation_snapshot(D2)["phase"] == "LEVELS_STALE"
    # Explicit levels_date mismatch is stale as well.
    strat2, ctx2 = make(levels={**LEVELS, "levels_date": "2025-03-09"})
    assert feed(strat2, ctx2, [gap_bar(), touch_bar(), green_bar()]) == []
    assert strat2.evaluation_snapshot(D1)["phase"] == "LEVELS_STALE"
    # ...and a matching levels_date is accepted.
    strat3, ctx3 = make(levels={**LEVELS, "levels_date": D1.isoformat()})
    assert len(feed(strat3, ctx3, [gap_bar(), touch_bar(), green_bar()])) == 1
    # Missing level -> silent, WARMING_UP.
    strat4, ctx4 = make(levels={"pdh": 24800.0, "prev_close": 24750.0})
    assert feed(strat4, ctx4, [gap_bar(), touch_bar(), green_bar()]) == []
    snap = strat4.evaluation_snapshot(D1)
    assert snap["phase"] == "LEVELS_MISSING" and snap["state"] == "WARMING_UP"


def test_inconsistent_zone_levels_are_rejected() -> None:
    strat, ctx = make(levels={"pdh": 24800.0, "prev_close": 24750.0, "pd_res_body": 24810.0})
    assert feed(strat, ctx, [gap_bar(), touch_bar(), green_bar()]) == []
    assert strat.evaluation_snapshot(D1)["phase"] == "LEVELS_INCONSISTENT"


def test_zone_height_cap_is_optional_provisional() -> None:
    strat, ctx = make(config={"max_zone_height_points": 5})  # zone is 10 tall
    assert feed(strat, ctx, [gap_bar(), touch_bar(), green_bar()]) == []
    assert strat.evaluation_snapshot(D1)["phase"] == "ZONE_TOO_TALL"


# ----------------------------------------------------------------------- driving one bar at a time


def test_late_start_replays_context_bars_but_never_emits_a_stale_signal() -> None:
    """Bars in the context that were never evaluated update state; a setup that completed on
    one of them is consumed, not emitted late."""
    strat, ctx = make()
    for b in (gap_bar(), touch_bar(), green_bar()):  # added without evaluation
        ctx.add_bar(b)
    assert strat.on_bar(ctx, bar(D1, "09:30", 24825, 24840, 24820, 24835)) is None
    snap = strat.evaluation_snapshot(D1)
    assert snap["missed_setups"] == 1 and snap["phase"] == "SIGNALLED"


def test_state_catches_up_so_a_skipped_test_candle_still_arms_the_latch() -> None:
    strat, ctx = make()
    for b in (gap_bar(), touch_bar()):  # not evaluated (e.g. position was open)
        ctx.add_bar(b)
    sig = strat.on_bar(ctx, green_bar())
    assert sig is not None and sig.timestamp == datetime(2025, 3, 10, 9, 25)


def test_levels_arriving_late_do_not_lose_the_gate() -> None:
    strat, ctx = make(levels=None)
    assert strat.on_bar(ctx, gap_bar()) is None
    assert strat.evaluation_snapshot(D1)["phase"] == "LEVELS_MISSING"
    ctx.state.update(LEVELS)
    assert strat.on_bar(ctx, touch_bar()) is None
    assert strat.on_bar(ctx, green_bar()) is not None


def test_reset_state_clears_every_day() -> None:
    strat, ctx = make()
    feed(strat, ctx, [gap_bar(), touch_bar(), green_bar()])
    strat.reset_state()
    assert strat.evaluation_snapshot()["phase"] == "NOT_EVALUATED"
    assert strat._daily_state == {}


# ------------------------------------------------------------------------- evaluation snapshot


def test_evaluation_snapshot_is_read_only_and_explains_state() -> None:
    strat, ctx = make()
    feed(strat, ctx, [gap_bar(), touch_bar()])
    before = copy.deepcopy(strat._daily_state)
    snap = strat.evaluation_snapshot(D1.isoformat())
    snap["conditions_satisfied"].append("tamper")
    snap["levels"]["pdh"] = -1
    strat.evaluation_snapshot()
    assert strat._daily_state == before
    snap2 = strat.evaluation_snapshot(D1)
    assert snap2["state"] == "MONITORING" and snap2["phase"] == "ZONE_TESTED_AWAITING_GREEN"
    assert snap2["levels"]["pdh"] == 24800 and snap2["levels"]["zone_bottom"] == 24790
    assert snap2["last_evaluated_candle"] == datetime(2025, 3, 10, 9, 20).isoformat()
    assert snap2["provisional_rules"]
    unknown = strat.evaluation_snapshot("2025-01-01")
    assert unknown["phase"] == "NOT_EVALUATED"


# ------------------------------------------------------------------------- provisional config


def test_provisional_values_come_from_yaml_and_are_labelled() -> None:
    manifest = {m["key"]: m for m in provisional_manifest("pdr")}
    assert manifest, "configs/strategy_provisional/pdr.yaml must exist"
    assert all(m["status"] == "PROVISIONAL" for m in manifest.values())
    assert all(m["why"] for m in manifest.values())
    assert "approved" not in " ".join(m["status"].lower() for m in manifest.values())
    values = load_provisional("pdr")
    strat = PDRStrategy()
    assert strat.rules == {k: values[k] for k in strat.rules}
    assert set(strat.rules) <= set(values)
    # PDF-stated numbers are not provisional and not in the file.
    assert "target_points" not in values and strat.target_points == 70.0


def test_config_overrides_and_legacy_aliases_are_honoured() -> None:
    strat = PDRStrategy(
        config={"zone_buffer_points": 2.0, "default_stop_points": 36.0, "max_stop_points": 41.0}
    )
    assert strat.rules["zone_proximity_points"] == 2.0
    assert strat.rules["stop_points_default"] == 36.0 and strat.rules["stop_points_wide"] == 41.0
    strat.prepare(None, {"zone_proximity_points": 3.0, "target_points": 71.0})
    assert strat.rules["zone_proximity_points"] == 3.0 and strat.target_points == 71.0


def test_missing_yaml_key_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import trading_platform.strategies.provisional as prov

    (tmp_path / "pdr.yaml").write_text("zone_proximity_points:\n  value: 5.0\n")
    monkeypatch.setattr(prov, "PROVISIONAL_DIR", tmp_path)
    with pytest.raises(ValueError, match="missing"):
        PDRStrategy()


def test_bars_outside_regular_session_are_ignored() -> None:
    strat, ctx = make()
    pre = bar(D1, "09:10", 24700, 24705, 24695, 24702)
    assert strat.on_bar(ctx, pre) is None
    assert feed(strat, ctx, [gap_bar(), touch_bar(), green_bar()])
    assert strat.evaluation_snapshot(D1)["levels"]["day_open"] == 24830


def test_signal_times_are_five_minutes_apart_label_convention() -> None:
    strat, ctx = make()
    sig = feed(strat, ctx, [gap_bar(), touch_bar(), green_bar()])[0]
    open_t = sig.timestamp
    assert datetime.fromisoformat(sig.metadata["decision_time"]) == open_t + timedelta(minutes=5)
