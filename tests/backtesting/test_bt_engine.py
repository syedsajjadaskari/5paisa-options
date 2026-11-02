"""Engine execution-model tests (SYNTHETIC bars, scripted strategies)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_bt_support import (
    DAY,
    BreakoutStrategy,
    ScriptedStrategy,
    at,
    flat_bars,
    make_bar,
    replace_bar,
)

from trading_platform.backtesting.constants import (
    DEFAULT_SLIPPAGE_POINTS,
    ENTRY_CUTOFF_TIME,
    SQUARE_OFF_TIME,
)
from trading_platform.backtesting.engine import (
    RejectionReason,
    SignalStatus,
    UnderlyingBacktestEngine,
    build_signal_id,
)
from trading_platform.core.enums import SignalDirection

BUY = SignalDirection.BUY
SELL = SignalDirection.SELL


def _run(strategy, bars, slip=0.5):
    return UnderlyingBacktestEngine(slippage_points=slip).run(strategy, bars)


def test_constants_are_the_documented_values():
    assert DEFAULT_SLIPPAGE_POINTS == 0.5
    assert ENTRY_CUTOFF_TIME.strftime("%H:%M") == "14:30"
    assert SQUARE_OFF_TIME.strftime("%H:%M") == "15:15"
    engine = UnderlyingBacktestEngine()
    assert engine.slippage_points == 0.5
    assert engine.entry_cutoff_time == ENTRY_CUTOFF_TIME
    assert engine.eod_exit_time == SQUARE_OFF_TIME


# ------------------------------------------------------------------ next-bar entry


def test_entry_is_next_bar_open_plus_slippage_not_signal_close():
    bars = flat_bars(at(9, 15), 6, 100.0)
    replace_bar(bars, at(9, 15), 100, 111, 99, 110)  # signal candle closes at 110
    replace_bar(bars, at(9, 20), 105, 106, 104, 105)  # entry bar opens at 105
    strat = ScriptedStrategy("T", {at(9, 15): BUY})
    rep = _run(strat, bars)
    trade = rep.trades[0]
    assert trade.entry_time == at(9, 20)  # the bar AFTER the signal candle
    assert trade.entry_price == 105.5  # open + slippage, not the 110 close
    assert trade.signal_time == at(9, 15)
    assert trade.signal_id == build_signal_id("T", "T_SCRIPTED", at(9, 15))
    assert rep.signals[0].trade_id == trade.trade_id
    assert rep.signals[0].status == SignalStatus.FILLED


def test_sell_entry_has_adverse_slippage():
    bars = flat_bars(at(9, 15), 6, 100.0)
    rep = _run(ScriptedStrategy("T", {at(9, 15): SELL}), bars)
    assert rep.trades[0].entry_price == 99.5


# ------------------------------------------------------------------ slippage on every exit type


@pytest.mark.parametrize("slip", [0.5, 1.0])
def test_target_exit_pays_exit_slippage_buy_and_sell(slip):
    for direction, entry_move in ((BUY, 1), (SELL, -1)):
        bars = flat_bars(at(9, 15), 8, 100.0)
        entry = 100.0 + entry_move * slip
        target = entry + entry_move * 60.0
        # bar 09:25 (index 2) reaches the target without touching the 30 pt stop
        replace_bar(
            bars, at(9, 25), 100, max(100, target) + 1, min(100, target) - 1, target
        )
        rep = _run(ScriptedStrategy("T", {at(9, 15): direction}), bars, slip)
        trade = rep.trades[0]
        assert trade.exit_reason == "TARGET"
        assert trade.exit_price == pytest.approx(target - entry_move * slip)
        # +60 distance, minus entry slip (already in the fill) and exit slip
        assert trade.points_pnl == pytest.approx(60.0 - slip)
        assert trade.slippage_points == slip


@pytest.mark.parametrize("slip", [0.5, 1.0])
def test_stop_exit_pays_exit_slippage_buy_and_sell(slip):
    for direction, sgn in ((BUY, 1), (SELL, -1)):
        bars = flat_bars(at(9, 15), 8, 100.0)
        entry = 100.0 + sgn * slip
        stop = entry - sgn * 30.0
        # touches the stop intrabar, opens inside the stop (no gap)
        replace_bar(
            bars, at(9, 25), 100, max(100, stop) + 0.5, min(100, stop) - 0.5, 100
        )
        rep = _run(ScriptedStrategy("T", {at(9, 15): direction}), bars, slip)
        trade = rep.trades[0]
        assert trade.exit_reason == "STOP_LOSS"
        assert trade.exit_price == pytest.approx(stop - sgn * slip)
        assert trade.points_pnl == pytest.approx(-30.0 - slip)


def test_gap_through_stop_exits_at_open_with_slippage():
    bars = flat_bars(at(9, 15), 8, 100.0)
    # BUY entry 100.5, stop 70.5; bar 09:25 gaps open at 60 (far below the stop)
    replace_bar(bars, at(9, 25), 60, 62, 58, 61)
    rep = _run(ScriptedStrategy("T", {at(9, 15): BUY}), bars)
    t = rep.trades[0]
    assert t.exit_reason == "STOP_LOSS"
    assert t.exit_price == 59.5  # gap open 60 minus slippage, NOT the 70.5 stop
    assert t.points_pnl == pytest.approx(59.5 - 100.5)

    bars = flat_bars(at(9, 15), 8, 100.0)
    # SELL entry 99.5, stop 129.5; gap up open at 140
    replace_bar(bars, at(9, 25), 140, 142, 138, 141)
    t = _run(ScriptedStrategy("T", {at(9, 15): SELL}), bars).trades[0]
    assert t.exit_price == 140.5
    assert t.points_pnl == pytest.approx(99.5 - 140.5)


def test_gap_through_target_still_fills_at_target_minus_slippage():
    """Conservative: a favourable gap beyond the target does not earn the better open."""
    bars = flat_bars(at(9, 15), 8, 100.0)
    replace_bar(bars, at(9, 25), 200, 205, 199, 203)  # BUY target = 160.5
    t = _run(ScriptedStrategy("T", {at(9, 15): BUY}), bars).trades[0]
    assert t.exit_reason == "TARGET"
    assert t.exit_price == pytest.approx(160.0)


# ------------------------------------------------------------------ same-bar conflict


def test_same_bar_stop_target_conflict_resolves_to_stop_with_slippage():
    bars = flat_bars(at(9, 15), 8, 100.0)
    # BUY entry 100.5 (stop 70.5, target 160.5). Bar 09:20 (entry bar) touches both.
    replace_bar(bars, at(9, 20), 100, 200, 50, 100)
    t = _run(ScriptedStrategy("T", {at(9, 15): BUY}), bars).trades[0]
    assert t.exit_reason == "INTRABAR_SL_CONFLICT"
    assert t.is_uncertain_fill is True
    assert t.exit_time == t.entry_time  # stopped on its own entry bar
    assert t.exit_price == pytest.approx(70.0)  # min(open, stop) - slip
    assert t.points_pnl == pytest.approx(-30.5)

    bars = flat_bars(at(9, 15), 8, 100.0)
    replace_bar(bars, at(9, 20), 100, 150, 20, 100)  # SELL entry 99.5, stop 129.5, tgt 39.5
    t = _run(ScriptedStrategy("T", {at(9, 15): SELL}), bars).trades[0]
    assert t.exit_reason == "INTRABAR_SL_CONFLICT"
    assert t.exit_price == pytest.approx(130.0)  # max(open, stop) + slip
    assert t.points_pnl == pytest.approx(-30.5)


# ------------------------------------------------------------------ EOD / forced close


def test_eod_square_off_at_1515_bar_open_with_slippage():
    bars = flat_bars(at(14, 0), 20, 100.0)  # 14:00 .. 15:35
    replace_bar(bars, at(15, 15), 90, 91, 89, 90)  # open 90, nowhere near stop(70.5)
    t = _run(ScriptedStrategy("T", {at(14, 0): BUY}), bars).trades[0]
    assert t.exit_reason == "EOD_SQUAREOFF"
    assert t.exit_time == at(15, 15)
    assert t.exit_price == pytest.approx(89.5)  # open - slip (BUY)
    assert t.points_pnl == pytest.approx(89.5 - 100.5)


def test_force_close_at_date_change_when_no_square_off_bar():
    day2 = DAY + timedelta(days=1)
    bars = flat_bars(at(13, 0), 12, 100.0)  # day 1: 13:00 .. 13:55, no 15:15 bar
    replace_bar(bars, at(13, 55), 101, 104, 100, 103)  # last bar close 103
    day2_bars = flat_bars(at(9, 15, day2), 6, 100.0)
    rep = _run(ScriptedStrategy("T", {at(13, 0): BUY}), bars + day2_bars)
    assert rep.total_trades == 1
    t = rep.trades[0]
    assert t.exit_reason == "FORCED_SESSION_END"
    assert t.is_uncertain_fill is True
    assert t.exit_time == at(13, 55)  # last bar of day 1 - never carried to day 2
    assert t.exit_price == pytest.approx(102.5)  # last close minus slip
    assert t.date == DAY


def test_force_close_at_end_of_data():
    bars = flat_bars(at(13, 0), 6, 100.0)
    replace_bar(bars, at(13, 25), 100, 100, 97, 98)
    t = _run(ScriptedStrategy("T", {at(13, 0): BUY}), bars).trades[0]
    assert t.exit_reason == "FORCED_SESSION_END"
    assert t.exit_price == pytest.approx(97.5)


def test_pending_signal_is_not_carried_into_next_session():
    day2 = DAY + timedelta(days=1)
    bars = flat_bars(at(14, 5), 4, 100.0)  # 14:05, 14:10, 14:15, 14:20 (last bar of day 1)
    bars += flat_bars(at(9, 15, day2), 4, 100.0)
    rep = _run(ScriptedStrategy("T", {at(14, 20): BUY}), bars)
    assert rep.total_trades == 0
    assert rep.signals[0].rejection_reason == RejectionReason.NO_ENTRY_BAR


# ------------------------------------------------------------------ 14:30 cutoff convention


def test_cutoff_is_applied_to_signal_candle_close():
    # 14:20 candle closes 14:25 -> fills at the 14:25 open (< 14:30): allowed.
    bars = flat_bars(at(14, 10), 6, 100.0)  # 14:10 .. 14:35
    rep = _run(ScriptedStrategy("T", {at(14, 20): BUY}), bars)
    assert rep.total_trades == 1
    assert rep.trades[0].entry_time == at(14, 25)

    # 14:25 candle closes 14:30 -> its fill would be AT 14:30: dropped.
    rep = _run(ScriptedStrategy("T", {at(14, 25): BUY}), bars)
    assert rep.total_trades == 0
    sig = rep.signals[0]
    assert sig.rejection_reason == RejectionReason.AFTER_ENTRY_CUTOFF
    assert sig.signal_candle_close == at(14, 30)

    # a late signal (15:00) is still RECORDED, rejected for the cutoff
    late = flat_bars(at(14, 50), 6, 100.0)
    rep = _run(ScriptedStrategy("T", {at(15, 0): BUY}), late)
    assert rep.total_trades == 0
    assert [s.rejection_reason for s in rep.signals] == ["AFTER_ENTRY_CUTOFF"]


# ------------------------------------------------------------------ state machines never miss bars


def test_strategy_is_fed_every_bar_even_while_in_position():
    bars = flat_bars(at(9, 15), 30, 100.0)
    strat = ScriptedStrategy("T", {at(9, 15): BUY, at(9, 35): BUY})
    rep = _run(strat, bars)
    assert strat.bars_seen == len(bars)
    # second signal arrived while the first position (flat bars, never exits until EOD) is open
    assert [s.status for s in rep.signals] == [SignalStatus.FILLED, SignalStatus.REJECTED]
    assert rep.signals[1].rejection_reason == RejectionReason.OVERLAP_POSITION_OPEN
    assert rep.signals[1].blocking_trade_id == rep.trades[0].trade_id
    assert rep.total_trades == 1


def test_signal_on_the_bar_where_position_exits_intrabar_is_allowed_next_bar():
    bars = flat_bars(at(9, 15), 12, 100.0)
    replace_bar(bars, at(9, 25), 100, 101, 60, 100)  # stops the BUY (entry 100.5, stop 70.5)
    strat = ScriptedStrategy("T", {at(9, 15): BUY, at(9, 25): BUY})
    rep = _run(strat, bars)
    assert rep.total_trades == 2
    first, second = rep.trades
    assert first.exit_time == at(9, 25)
    assert second.entry_time == at(9, 30)  # strictly after the first exit stamp


# ------------------------------------------------------------------ determinism / no look-ahead


def _breakout_bars(seed_shift: float = 0.0):
    bars = []
    price = 100.0
    for day_offset in range(3):
        day = DAY + timedelta(days=day_offset)
        for i in range(60):  # 09:15 .. 14:10
            drift = ((i * 7 + day_offset * 3) % 11 - 5) * 4.0 + seed_shift
            o = price
            c = round(price + drift, 2)
            bars.append(
                make_bar(
                    at(9, 15, day) + timedelta(minutes=5 * i), o, max(o, c) + 6.0, min(o, c) - 6.0, c
                )
            )
            price = c
    return bars


def test_run_is_deterministic():
    bars = _breakout_bars()
    a = _run(BreakoutStrategy(), bars)
    b = _run(BreakoutStrategy(), bars)
    assert a.trades == b.trades
    assert a.signals == b.signals
    assert a.total_trades > 0


def test_truncation_invariance_no_look_ahead():
    """Results for a prefix of the data must equal the same trades computed on the full data."""
    bars = _breakout_bars()
    full = _run(BreakoutStrategy(), bars)
    assert full.total_trades >= 2
    assert {t.exit_reason for t in full.trades} - {"FORCED_SESSION_END"}, "need real exits"
    for cut in range(30, len(bars), 11):
        prefix = bars[:cut]
        last_open = prefix[-1].exchange_time
        part = _run(BreakoutStrategy(), prefix)
        # trades that fully completed before the cut must be identical (forced closes excluded)
        done_full = [t for t in full.trades if t.exit_time < last_open]
        done_part = [
            t for t in part.trades if t.exit_time < last_open and t.exit_reason != "FORCED_SESSION_END"
        ]
        assert done_part == done_full
        # every signal visible in the prefix exists in the full run with the same id
        ids_full = {s.signal_id for s in full.signals if s.signal_candle_open <= last_open}
        ids_part = {s.signal_id for s in part.signals}
        assert ids_part == ids_full


def test_future_bars_cannot_change_past_trades():
    bars = _breakout_bars()
    base = _run(BreakoutStrategy(), bars)
    cut = 100
    mutated = list(bars[:cut]) + [
        make_bar(b.exchange_time, 9999.0, 10010.0, 9990.0, 10000.0) for b in bars[cut:]
    ]
    changed = _run(BreakoutStrategy(), mutated)
    horizon = bars[cut - 1].exchange_time
    assert [t for t in base.trades if t.exit_time < horizon] == [
        t for t in changed.trades if t.exit_time < horizon
    ]


# ------------------------------------------------------------------ input validation / dedupe


def test_duplicate_bar_timestamps_are_rejected():
    bars = flat_bars(at(9, 15), 3)
    with pytest.raises(ValueError, match="Duplicate bar timestamp"):
        _run(ScriptedStrategy("T", {}), bars + [bars[1]])


def test_duplicate_strategy_names_are_rejected():
    bars = flat_bars(at(9, 15), 3)
    with pytest.raises(ValueError, match="Duplicate strategy names"):
        UnderlyingBacktestEngine().simulate(
            [ScriptedStrategy("T", {}), ScriptedStrategy("T", {})], bars
        )


def test_duplicate_signal_id_never_produces_a_second_trade():
    class _ConstantIdEngine(UnderlyingBacktestEngine):
        def _signal_record(self, strategy_name, sig, bar):
            rec = super()._signal_record(strategy_name, sig, bar)
            rec.signal_id = "FIXED_ID"
            return rec

    bars = flat_bars(at(9, 15), 30, 100.0)
    replace_bar(bars, at(9, 25), 100, 101, 60, 100)  # first trade stops out at 09:25
    strat = ScriptedStrategy("T", {at(9, 15): BUY, at(9, 40): BUY})
    result = _ConstantIdEngine().simulate([strat], bars)
    assert len(result.trades) == 1
    assert [s.rejection_reason for s in result.signals] == ["", "DUPLICATE_SIGNAL"]
