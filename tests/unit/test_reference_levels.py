"""Unit tests for ReferenceLevelCalculator and market context enrichment."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from trading_platform.core.models import Bar
from trading_platform.data.reference_levels import ReferenceLevelCalculator


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
    calc = ReferenceLevelCalculator(ema_warmup_period=10, dma_warmup_period=2)

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
    calc = ReferenceLevelCalculator(ema_warmup_period=5, dma_warmup_period=3)

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

    # Test EMA warm-up (threshold = 5 bars)
    contexts = calc.enrich_bars_with_context(bars, daily_levels)
    for idx in range(4):
        # Bars 0, 1, 2, 3 have < 5 bars seen
        assert contexts[idx].has_sufficient_ema_warmup is False
        assert contexts[idx].ema_200 is None

    # Bar 4 is 5th bar -> warmup satisfied
    assert contexts[4].has_sufficient_ema_warmup is True
    assert contexts[4].ema_200 is not None


def test_official_daily_ohlc_overrides_intraday_reference_without_future_leakage() -> None:
    """PDH/PDL/PDC and DMA use completed daily bars, while zone bodies stay intraday."""
    calc = ReferenceLevelCalculator(dma_warmup_period=1)
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

    levels = calc.compute_daily_reference_levels(intraday, official)

    assert levels[d2].pdh == 112.0
    assert levels[d2].pdl == 88.0
    assert levels[d2].pdc == 105.0
    assert levels[d2].pdo == 99.0
    assert levels[d2].dma_200 == 105.0
    assert levels[d2].pd_res_body == 101.0
    assert levels[d2].pd_supp_body == 100.0


def test_post_close_1530_record_cannot_define_prior_day_zone() -> None:
    """The broker's zero-volume 15:30 record is outside continuous 5-minute bar opens."""
    calc = ReferenceLevelCalculator()
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
