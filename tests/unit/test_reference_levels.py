"""Unit tests for ReferenceLevelCalculator and market context enrichment."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from trading_platform.core.models import Bar
from trading_platform.data.reference_levels import ReferenceLevelCalculator

# Hand-made test sessions have a handful of candles, not 75: tolerate every missing slot.
SPARSE = 75


def _make_bar(d: date, hour: int, minute: int, o: float, h: float, low: float, c: float) -> Bar:
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


def test_reference_levels_daily_aggregation_and_gap() -> None:
    """Verify PDH, PDL, PDC, PDO, zones, and day 2 gap metrics."""
    calc = ReferenceLevelCalculator(
        ema_warmup_period=10, dma_warmup_period=2, max_missing_slots=SPARSE
    )

    # Day 1: 3 bars
    d1 = date(2024, 10, 1)
    b1 = _make_bar(d1, 9, 15, 24800.0, 24850.0, 24780.0, 24820.0)
    b2 = _make_bar(d1, 9, 20, 24820.0, 24900.0, 24810.0, 24880.0)
    b3 = _make_bar(d1, 15, 25, 24880.0, 24890.0, 24830.0, 24850.0)

    # Day 2: 2 bars (gap up open at 24920 > Day 1 High 24900)
    d2 = date(2024, 10, 2)
    b4 = _make_bar(d2, 9, 15, 24920.0, 24950.0, 24900.0, 24930.0)
    b5 = _make_bar(d2, 9, 20, 24930.0, 24940.0, 24890.0, 24910.0)

    all_bars = [b1, b2, b3, b4, b5]
    daily_levels = calc.compute_daily_reference_levels(all_bars)

    # Day 1 has no prior day
    assert daily_levels[d1].pdh is None
    assert daily_levels[d1].has_sufficient_dma_warmup is False

    # Day 2 has Day 1 as prior day
    lvl2 = daily_levels[d2]
    assert lvl2.pdh == 24900.0
    assert lvl2.pdl == 24780.0
    assert lvl2.pdo == 24800.0
    assert lvl2.pdc == 24850.0

    # Enrich bars with context
    contexts = calc.enrich_bars_with_context(all_bars, daily_levels)
    assert len(contexts) == 5

    # Check Day 2 bar 1 context (b4)
    ctx_b4 = contexts[3]
    assert ctx_b4.session_levels.pdh == 24900.0
    assert ctx_b4.irh == 24950.0
    assert ctx_b4.irl == 24900.0
    assert ctx_b4.is_gap_up is True
    assert ctx_b4.gap_points == 70.0  # 24920 - 24850 (Day 2 Open - Day 1 Close)
    assert ctx_b4.is_gap_within_yesterday_range is False


def test_reference_levels_ema_and_dma_warmup() -> None:
    """Verify EMA and DMA warm-up gates: return None before threshold is reached."""
    calc = ReferenceLevelCalculator(
        ema_warmup_period=5, dma_warmup_period=3, max_missing_slots=SPARSE
    )

    bars = []
    base_date = date(2024, 1, 1)
    # Generate 4 days with 2 bars each
    for d_idx in range(4):
        curr_d = base_date + timedelta(days=d_idx)
        bars.append(_make_bar(curr_d, 9, 15, 24000.0 + d_idx * 10, 24070.0, 24000.0, 24010.0))
        bars.append(_make_bar(curr_d, 15, 25, 24010.0, 24070.0, 24005.0, 24020.0 + d_idx * 10))

    daily_levels = calc.compute_daily_reference_levels(bars)

    # Day 0: 0 prior days -> DMA None
    assert daily_levels[base_date].has_sufficient_dma_warmup is False
    assert daily_levels[base_date].dma_200 is None

    # Day 1: 1 prior day (< 3 warmup) -> DMA None
    d1 = base_date + timedelta(days=1)
    assert daily_levels[d1].has_sufficient_dma_warmup is False
    assert daily_levels[d1].dma_200 is None

    # Day 2: 2 prior days (< 3 warmup) -> DMA None
    d2 = base_date + timedelta(days=2)
    assert daily_levels[d2].has_sufficient_dma_warmup is False

    # Day 3: 3 prior days (>= 3 warmup) -> DMA calculated!
    d3 = base_date + timedelta(days=3)
    assert daily_levels[d3].has_sufficient_dma_warmup is True
    assert daily_levels[d3].dma_200 is not None

    # EMA warm-up (threshold = 5 PRIOR closes; the value includes the evaluated bar's close)
    contexts = calc.enrich_bars_with_context(bars, daily_levels)
    for idx in range(5):
        # Bars 0..4 have fewer than 5 prior closes (bar 4 has exactly 4)
        assert contexts[idx].has_sufficient_ema_warmup is False
        assert contexts[idx].ema_200 is None

    # Bar 5 is the first bar with 5 prior closes
    assert contexts[5].has_sufficient_ema_warmup is True
    assert contexts[5].ema_200 is not None


def test_lines_and_zone_bodies_are_single_sourced_and_pdc_uses_official_close() -> None:
    """pdh/pdl and the candle bodies come from the SAME 5m dataset; PDC/PDO/DMA are official."""
    calc = ReferenceLevelCalculator(dma_warmup_period=1, max_missing_slots=SPARSE)
    d1 = date(2024, 10, 1)
    d2 = date(2024, 10, 2)
    intraday = [
        _make_bar(d1, 9, 15, 100.0, 110.0, 90.0, 101.0),
        _make_bar(d1, 15, 25, 101.0, 108.0, 95.0, 102.0),
        _make_bar(d2, 9, 15, 106.0, 109.0, 104.0, 108.0),
    ]
    official = [
        _make_bar(d1, 0, 0, 99.0, 112.0, 88.0, 105.0),
        # This current-day bar must not leak into d2's references.
        _make_bar(d2, 0, 0, 106.0, 999.0, 1.0, 500.0),
    ]

    levels = calc.compute_daily_reference_levels(intraday, official)[d2]

    assert (levels.pdh, levels.pdl) == (110.0, 90.0)  # the 5m extremes, not 112 / 88
    assert levels.pd_res_body == 101.0  # max(open, close) of the 110 candle
    assert levels.pd_supp_body == 100.0  # min(open, close) of the 90 candle
    assert levels.pd_supp_body_top == 101.0  # max(open, close) of that same candle
    assert levels.pdl <= levels.pd_supp_body <= levels.pd_supp_body_top
    assert levels.pd_res_body <= levels.pdh
    assert levels.support_zone == (90.0, 101.0) and levels.resistance_zone == (101.0, 110.0)
    assert levels.pdc == 105.0 and levels.pdo == 99.0  # official close/open of that session
    assert levels.dma_200 == 105.0
    assert (levels.official_pdh, levels.official_pdl) == (112.0, 88.0)
    assert levels.lines_source == "PREV_SESSION_5M_REGULAR"
    assert levels.close_source == "OFFICIAL_DAILY" and levels.zone_source == levels.lines_source


def test_incomplete_previous_5m_session_never_mixes_sources() -> None:
    """Strict default: a session missing candles gives official lines and NO zone bodies."""
    calc = ReferenceLevelCalculator()  # max_missing_slots=0
    d1, d2 = date(2024, 10, 1), date(2024, 10, 2)
    intraday = [
        _make_bar(d1, 9, 15, 100.0, 110.0, 90.0, 101.0),
        _make_bar(d1, 15, 25, 101.0, 108.0, 95.0, 102.0),
        _make_bar(d2, 9, 15, 106.0, 109.0, 104.0, 108.0),
    ]
    official = [_make_bar(d1, 0, 0, 99.0, 112.0, 88.0, 105.0)]
    levels = calc.compute_daily_reference_levels(intraday, official)[d2]
    assert (levels.pdh, levels.pdl, levels.pdc) == (112.0, 88.0, 105.0)
    assert levels.lines_source == "OFFICIAL_DAILY_FALLBACK"
    assert levels.pd_res_body is None and levels.pd_supp_body is None
    assert levels.pd_supp_body_top is None
    assert levels.support_zone is None and levels.resistance_zone is None
    # no official data either -> everything explicit None
    levels = calc.compute_daily_reference_levels(intraday)[d2]
    assert levels.pdh is None and levels.pdl is None and levels.pdc is None


def test_post_close_1530_record_cannot_define_prior_day_zone() -> None:
    """The broker's zero-volume 15:30 record is outside continuous 5-minute bar opens."""
    calc = ReferenceLevelCalculator(max_missing_slots=SPARSE)
    d1 = date(2024, 10, 1)
    d2 = date(2024, 10, 2)
    bars = [
        _make_bar(d1, 9, 15, 100.0, 110.0, 90.0, 101.0),
        _make_bar(d1, 15, 25, 101.0, 111.0, 95.0, 102.0),
        _make_bar(d1, 15, 30, 102.0, 999.0, 1.0, 500.0),
        _make_bar(d2, 9, 15, 105.0, 108.0, 100.0, 106.0),
    ]

    levels = calc.compute_daily_reference_levels(bars)

    assert levels[d2].pdh == 111.0
    assert levels[d2].pdl == 90.0
    assert levels[d2].pdc == 102.0


def test_post_close_1530_record_is_excluded_from_extremes_ema_and_closes() -> None:
    d1, d2 = date(2024, 10, 1), date(2024, 10, 2)
    base = [
        _make_bar(d1, 9, 15, 100.0, 110.0, 90.0, 101.0),
        _make_bar(d1, 15, 25, 101.0, 111.0, 95.0, 102.0),
        _make_bar(d2, 9, 15, 105.0, 108.0, 100.0, 106.0),
    ]
    stub = _make_bar(d1, 15, 30, 102.0, 999.0, 1.0, 500.0)
    calc = ReferenceLevelCalculator(ema_warmup_period=2, max_missing_slots=SPARSE)
    with_stub = calc.enrich_bars_with_context([*base, stub])
    without = calc.enrich_bars_with_context(base)
    by_time = {c.bar.exchange_time: c for c in without}
    for c in with_stub:
        if c.bar is stub:
            continue
        ref = by_time[c.bar.exchange_time]
        assert (c.ema_200, c.has_sufficient_ema_warmup) == (
            ref.ema_200,
            ref.has_sufficient_ema_warmup,
        )
        assert c.session_levels == ref.session_levels
    stub_ctx = next(c for c in with_stub if c.bar is stub)
    assert stub_ctx.ema_200 == by_time[base[1].exchange_time].ema_200  # not advanced by the stub


def test_second_candle_never_overwrites_initial_range() -> None:
    d = date(2024, 10, 1)
    calc = ReferenceLevelCalculator()
    bars = [
        _make_bar(d, 9, 15, 100.0, 110.0, 95.0, 105.0),
        _make_bar(d, 9, 20, 105.0, 130.0, 80.0, 90.0),
    ]
    ctx = calc.enrich_bars_with_context(bars)
    assert (ctx[0].irh, ctx[0].irl) == (110.0, 95.0)
    assert (ctx[1].irh, ctx[1].irl) == (110.0, 95.0)
    # No 09:15 candle -> the initial range stays unavailable (a 09:20 candle never substitutes)
    ctx = calc.enrich_bars_with_context(bars[1:])
    assert ctx[0].irh is None and ctx[0].irl is None
    assert ctx[0].is_gap_up is None  # no 09:15 open -> no gap decision either


def test_swing_points_do_not_cross_sessions() -> None:
    d1, d2 = date(2024, 10, 1), date(2024, 10, 2)
    calc = ReferenceLevelCalculator()
    bars = [
        _make_bar(d1, 15, 15, 100.0, 120.0, 100.0, 110.0),
        _make_bar(d1, 15, 20, 110.0, 110.0, 90.0, 100.0),  # swing-low candidate
        _make_bar(d1, 15, 25, 100.0, 125.0, 95.0, 120.0),
        _make_bar(d2, 9, 15, 120.0, 130.0, 119.0, 125.0),
        _make_bar(d2, 9, 20, 125.0, 128.0, 121.0, 126.0),
    ]
    ctx = calc.enrich_bars_with_context(bars)
    assert ctx[2].recent_swing_low == 90.0  # confirmed inside day 1
    assert ctx[3].recent_swing_low is None and ctx[3].recent_swing_high is None
    assert ctx[4].recent_swing_low is None


def test_levels_are_point_in_time_and_prior_sessions_only() -> None:
    d1, d2, d3 = date(2024, 10, 1), date(2024, 10, 2), date(2024, 10, 3)
    calc = ReferenceLevelCalculator(max_missing_slots=SPARSE)
    bars = [
        _make_bar(d1, 9, 15, 100.0, 110.0, 90.0, 101.0),
        _make_bar(d1, 15, 25, 101.0, 108.0, 95.0, 102.0),
        _make_bar(d2, 9, 15, 102.0, 150.0, 20.0, 103.0),
        _make_bar(d2, 15, 25, 103.0, 104.0, 100.0, 103.5),
        _make_bar(d3, 9, 15, 104.0, 105.0, 103.0, 104.5),
    ]
    levels = calc.compute_daily_reference_levels(bars)
    assert levels[d2].pdh == 110.0 and levels[d2].previous_session_date == d1
    assert levels[d3].pdh == 150.0 and levels[d3].pdl == 20.0
    # Truncating the future cannot change what an earlier day saw.
    assert calc.compute_daily_reference_levels(bars[:3])[d2] == levels[d2]


def test_engine_overwrites_every_level_key_each_day_and_never_carries_over() -> None:
    """Day 2 has no usable previous session: day 1's values must be None, not carried over."""
    from trading_platform.backtesting.engine import UnderlyingBacktestEngine
    from trading_platform.strategies.base import BaseStrategy

    seen: dict[datetime, dict] = {}

    class Probe(BaseStrategy):
        def __init__(self) -> None:
            super().__init__("PROBE")

        def prepare(self, data, config) -> None:
            return None

        def on_bar(self, context, bar):
            seen[bar.exchange_time] = dict(context.state)
            return None

        def generate_signal(self, context):
            return None

    d1, d2, d3 = date(2024, 10, 1), date(2024, 10, 2), date(2024, 10, 3)
    bars = [
        _make_bar(d1, 9, 15, 100.0, 110.0, 90.0, 101.0),
        _make_bar(d1, 15, 25, 101.0, 108.0, 95.0, 102.0),
        _make_bar(d2, 9, 15, 106.0, 109.0, 104.0, 108.0),
        _make_bar(d2, 9, 20, 108.0, 109.0, 104.0, 108.0),  # d2 incomplete (strict default)
        _make_bar(d3, 9, 15, 106.0, 109.0, 104.0, 108.0),
    ]
    daily = [_make_bar(d1, 0, 0, 99.0, 112.0, 88.0, 105.0)]
    UnderlyingBacktestEngine().simulate([Probe()], bars, daily_history_bars=daily)
    from trading_platform.data.reference_levels import STRATEGY_STATE_KEYS

    for state in seen.values():
        assert set(STRATEGY_STATE_KEYS) <= set(state)
    day1 = seen[bars[0].exchange_time]
    assert day1["levels_date"] == "2024-10-01" and day1["pdh"] is None
    day2 = seen[bars[2].exchange_time]
    assert day2["levels_date"] == "2024-10-02"
    assert day2["pdh"] == 112.0 and day2["pd_res_body"] is None  # official fallback, no zone
    day3 = seen[bars[4].exchange_time]
    # day 2 (two candles) is incomplete -> official lines are for d2's official bar (none) -> None
    assert day3["levels_date"] == "2024-10-03"
    assert day3["pdh"] is None and day3["prev_close"] is None
    assert day3["pd_res_body"] is None and day3["pd_supp_body"] is None
    assert day3["pd_supp_body_top"] is None and day3["ema_200"] is None
