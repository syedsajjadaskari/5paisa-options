"""Parity: LiveReferenceBuilder == batch calculator == what the backtest engine injects.

Uses the verified historical NIFTY parquet datasets read-only, through the same loader as
scripts/generate_final_paper_decision_artifacts.py::load_data.  For every checked date the
builder is warmed up with data STRICTLY BEFORE that date and then fed the day's bars one at a
time; ``state_for_bar`` must equal the state the backtest engine handed to the strategies.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from trading_platform.backtesting.engine import UnderlyingBacktestEngine
from trading_platform.core.models import Bar, Signal
from trading_platform.data.reference_levels import STRATEGY_STATE_KEYS, ReferenceLevelCalculator
from trading_platform.live.levels import LiveReferenceBuilder
from trading_platform.live.models import DailyOhlc, WarmupData
from trading_platform.strategies.base import BaseStrategy, StrategyContext

ROOT = Path(__file__).resolve().parents[2]
DATA_5M = ROOT / "data" / "processed" / "nifty" / "5m" / "nifty50_5m.parquet"
DATA_1D = ROOT / "data" / "processed" / "nifty" / "1d" / "nifty50_1d.parquet"


class _ProbeStrategy(BaseStrategy):
    """Records the exact context.state the engine hands to a strategy for every bar."""

    def __init__(self) -> None:
        super().__init__("PROBE")
        self.seen: dict[Any, dict[str, Any]] = {}

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        return None

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        self.seen[bar.exchange_time] = dict(context.state)
        return None

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        return None


@pytest.fixture(scope="module")
def history() -> dict[str, Any]:
    if not (DATA_5M.exists() and DATA_1D.exists()):
        pytest.skip("verified historical parquet datasets not available")
    from trading_platform.backtesting.portfolio_runner import load_nifty_bars

    bars_1d, bars_5m = load_nifty_bars(ROOT)
    probe = _ProbeStrategy()
    UnderlyingBacktestEngine(slippage_points=0.5).run(probe, bars_5m, daily_history_bars=bars_1d)
    calc = ReferenceLevelCalculator()
    contexts = calc.enrich_bars_with_context(bars_5m, daily_history_bars=bars_1d)
    by_day: dict[date, list[Bar]] = {}
    for b in bars_5m:
        by_day.setdefault(b.exchange_time.date(), []).append(b)
    return {
        "bars_1d": bars_1d,
        "bars_5m": bars_5m,
        "probe": probe.seen,
        "batch": {c.bar.exchange_time: c.strategy_state() for c in contexts},
        "by_day": by_day,
        "days": sorted(by_day),
    }


def _warmup(history: dict[str, Any], day: date) -> WarmupData:
    prior_1d = [b for b in history["bars_1d"] if b.exchange_time.date() < day]
    prior_5m = [b for b in history["bars_5m"] if b.exchange_time.date() < day]
    prev = prior_1d[-1] if prior_1d else None
    assert prev is not None
    return WarmupData(
        previous_session=DailyOhlc(
            prev.exchange_time.date(), prev.open, prev.high, prev.low, prev.close
        ),
        daily_bars=prior_1d,
        intraday_bars=prior_5m,
    )


def _same(a: dict[str, Any], b: dict[str, Any], where: str) -> None:
    assert set(a) == set(b) == set(STRATEGY_STATE_KEYS), where
    for key in STRATEGY_STATE_KEYS:
        x, y = a[key], b[key]
        if isinstance(x, float) or isinstance(y, float):
            assert x is not None and y is not None, f"{where} {key}: {x!r} vs {y!r}"
            assert math.isclose(x, y, rel_tol=0, abs_tol=1e-9), f"{where} {key}: {x} vs {y}"
        else:
            assert x == y, f"{where} {key}: {x!r} vs {y!r}"


def _check_day(history: dict[str, Any], day: date) -> int:
    builder = LiveReferenceBuilder(_warmup(history, day), day)
    n = 0
    for bar in history["by_day"][day]:
        live = builder.state_for_bar(bar)
        key = bar.exchange_time
        _same(live, history["probe"][key], f"{day} {key:%H:%M} vs engine")
        _same(live, history["batch"][key], f"{day} {key:%H:%M} vs batch")
        assert live["levels_date"] == day.isoformat()
        builder.on_bar_completed(bar)
        n += 1
    return n


def test_engine_injects_complete_state_with_levels_date(history: dict[str, Any]) -> None:
    for key, state in list(history["probe"].items())[:: max(1, len(history["probe"]) // 50)]:
        assert set(state) >= set(STRATEGY_STATE_KEYS)
        assert state["levels_date"] == key.date().isoformat()


def test_live_builder_matches_backtest_for_many_dates(history: dict[str, Any]) -> None:
    days = history["days"]
    assert len(days) > 250
    # first sessions (EMA not yet warm), early, middle, late, the last session and the
    # sessions around the 2025-10-21 Muhurat session (incomplete 5m data, 13 bars).
    muhurat = date(2025, 10, 21)
    around = [d for d in days if muhurat <= d <= date(2025, 10, 24)]
    picks = sorted({days[0], days[1], days[3], days[20], days[60], days[130], days[200], days[-1]})
    picks += around
    assert len(picks) >= 8
    total = sum(_check_day(history, d) for d in dict.fromkeys(picks))
    assert total > 500


def test_ema_becomes_available_exactly_after_200_prior_closes(history: dict[str, Any]) -> None:
    day = history["days"][3]  # 3 * 75 = 225 prior regular closes -> available from bar 1
    first = history["by_day"][day][0]
    builder = LiveReferenceBuilder(_warmup(history, day), day)
    assert builder.problems() == []
    state = builder.state_for_bar(first)
    assert state["ema_200"] is not None and state["has_sufficient_ema_warmup"] is True

    day2 = history["days"][2]  # 150 prior closes -> unavailable all day (150 + 75 > 200 later)
    builder2 = LiveReferenceBuilder(_warmup(history, day2), day2)
    assert builder2.problems() == ["fewer than 200 prior 5m closes for EMA200"]
    bars = history["by_day"][day2]
    assert builder2.state_for_bar(bars[0])["ema_200"] is None
    for bar in bars[:50]:
        builder2.on_bar_completed(bar)
    # 150 + 50 = 200 prior closes -> the 51st bar of the day already has an EMA and no problems
    assert builder2.problems() == []
    assert builder2.state_for_bar(bars[50])["ema_200"] is not None


def test_parity_survives_restart_mid_day(history: dict[str, Any]) -> None:
    day = history["days"][100]
    bars = history["by_day"][day]
    builder = LiveReferenceBuilder(_warmup(history, day), day)
    builder.restore_today(bars[:30])
    for bar in bars[30:]:
        _same(builder.state_for_bar(bar), history["probe"][bar.exchange_time], f"{day} restart")
        builder.on_bar_completed(bar)
