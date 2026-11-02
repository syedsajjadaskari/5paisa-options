"""LiveReferenceBuilder behaviour on synthetic sessions (no data files, no network)."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading_platform.core.enums import DataSource, Timeframe
from trading_platform.core.models import Bar
from trading_platform.data.reference_levels import STRATEGY_STATE_KEYS
from trading_platform.live.levels import LiveReferenceBuilder
from trading_platform.live.models import DailyOhlc, WarmupData

IST = ZoneInfo("Asia/Kolkata")
D0 = date(2026, 10, 26)  # Monday: previous session
D1 = date(2026, 10, 27)  # Tuesday: trading date under test
EMA_PROBLEM = "fewer than 200 prior 5m closes for EMA200"


def _bar(day: date, hh: int, mm: int, o: float, h: float, low: float, c: float, vol: int = 1000):
    t = datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)
    return Bar(
        timestamp=t.astimezone(ZoneInfo("UTC")),
        exchange_time=t,
        symbol="NIFTY",
        instrument_id="999920000",
        interval=Timeframe.M5,
        open=o,
        high=h,
        low=low,
        close=c,
        volume=vol,
        source=DataSource.SYNTHETIC,
    )


def _slots() -> list[tuple[int, int]]:
    start = datetime(2026, 1, 1, 9, 15)
    return [((start + timedelta(minutes=5 * i)).hour, (start + timedelta(minutes=5 * i)).minute)
            for i in range(75)]  # fmt: skip


def make_session(day: date, base: float, *, skip: set[int] = frozenset(), stub: bool = False):
    """75 deterministic candles.  Candle 20 is the high (body 5 below the high), candle 50 is the
    low (body 4 above the low), so the zone bodies are known exactly."""
    bars: list[Bar] = []
    price = base
    for i, (hh, mm) in enumerate(_slots()):
        o = price
        c = price + (1.5 if i % 3 else -1.0)
        h = max(o, c) + 2.0
        low = min(o, c) - 2.0
        if i == 20:
            o, c, h, low = price, price + 8.0, price + 16.0, price - 1.0
        if i == 50:
            o, c, h, low = price, price - 6.0, price + 1.0, price - 12.0
        price = c
        if i not in skip:
            bars.append(_bar(day, hh, mm, o, h, low, c))
    if stub:
        bars.append(_bar(day, 15, 30, price, price + 500.0, max(price - 500.0, 1.0), price, 0))
    return bars


def sessions(n_prior: int, *, stub: bool = True) -> list[Bar]:
    """``n_prior`` consecutive prior sessions ending on D0 (weekends ignored in this fake)."""
    out: list[Bar] = []
    for k in range(n_prior):
        day = D0 - timedelta(days=n_prior - 1 - k)
        out += make_session(day, 24000.0 + 7.0 * k, stub=stub)
    return out


def warm(prior_bars: list[Bar], prev: DailyOhlc | None = None, daily=None) -> WarmupData:
    if prev is None:
        last = [b for b in prior_bars if b.exchange_time.date() == D0 and b.exchange_time.hour < 15]
        prev = DailyOhlc(
            D0,
            last[0].open,
            max(b.high for b in last) + 0.3,
            min(b.low for b in last) - 0.4,
            last[-1].close + 0.15,
        )
    return WarmupData(previous_session=prev, daily_bars=daily or [], intraday_bars=prior_bars)


def day_bars(base: float = 24500.0, **kw) -> list[Bar]:
    return make_session(D1, base, **kw)


def _approx_equal(a: dict, b: dict) -> None:
    assert a.keys() == b.keys()
    for k in a:
        if isinstance(a[k], float) and isinstance(b[k], float):
            assert math.isclose(a[k], b[k], abs_tol=1e-9), k
        else:
            assert a[k] == b[k], k


# ----------------------------------------------------------------------------- sources


def test_zone_is_single_sourced_from_previous_session_5m_candles() -> None:
    prior = sessions(4)
    w = warm(prior)
    b = LiveReferenceBuilder(w, D1)
    assert b.problems() == []  # 4 sessions = 300 prior closes

    p = [
        x for x in prior if x.exchange_time.date() == D0 and x.exchange_time.time() <= time(15, 25)
    ]
    high = max(x.high for x in p)
    low = min(x.low for x in p)
    hi_c = next(x for x in p if x.high == high)
    lo_c = next(x for x in p if x.low == low)
    s = b.state_for_bar(day_bars()[0])
    # Plain lines come from the SAME 5m dataset as the bodies, not the (different) official bar.
    assert s["pdh"] == high != w.previous_session.high
    assert s["pdl"] == low != w.previous_session.low
    assert s["pd_res_body"] == max(hi_c.open, hi_c.close)
    assert s["pd_supp_body"] == min(lo_c.open, lo_c.close)
    assert s["pd_supp_body_top"] == max(lo_c.open, lo_c.close)
    assert s["pdl"] <= s["pd_supp_body"] <= s["pd_supp_body_top"]
    assert s["pd_res_body"] <= s["pdh"]
    # PDC is the official close of that same previous session (gap reference only).
    assert s["prev_close"] == w.previous_session.close
    assert s["levels_date"] == D1.isoformat()

    d = b.display_levels()
    assert d["PDH"] == s["pdh"] and d["PDL"] == s["pdl"] and d["PDC"] == s["prev_close"]
    assert d["support_zone"] == [s["pdl"], s["pd_supp_body_top"]]
    assert d["resistance_zone"] == [s["pd_res_body"], s["pdh"]]
    assert d["support_zone"][0] <= d["support_zone"][1]
    assert d["resistance_zone"][0] <= d["resistance_zone"][1]
    assert d["sources"]["PDH_PDL"] == "PREV_SESSION_5M_REGULAR"
    assert d["sources"]["PDC"] == "OFFICIAL_DAILY"
    assert d["sources"]["status"] == "PROVISIONAL"
    assert d["sources"]["official_PDH"] == w.previous_session.high
    json.dumps(d)  # snapshot-safe


def test_pdc_falls_back_to_last_5m_close_without_official_daily() -> None:
    prior = sessions(4)
    w = warm(prior, prev=DailyOhlc(D0 - timedelta(days=1), 1.0, 2.0, 0.5, 1.5))  # older day only
    b = LiveReferenceBuilder(w, D1)
    s = b.state_for_bar(day_bars()[0])
    last_close = [x for x in prior if x.exchange_time.date() == D0][74].close
    assert s["prev_close"] == last_close
    assert b.display_levels()["sources"]["PDC"] == "PREV_SESSION_LAST_5M_CLOSE"


def test_incomplete_previous_5m_session_falls_back_to_official_without_zone() -> None:
    prior = [
        x
        for x in sessions(4)
        if not (x.exchange_time.date() == D0 and x.exchange_time.time() == time(11, 0))
    ]
    w = warm(prior)
    b = LiveReferenceBuilder(w, D1)
    s = b.state_for_bar(day_bars()[0])
    assert s["pdh"] == w.previous_session.high and s["pdl"] == w.previous_session.low
    assert s["pd_res_body"] is None and s["pd_supp_body"] is None
    assert s["pd_supp_body_top"] is None
    d = b.display_levels()
    assert d["support_zone"] is None and d["resistance_zone"] is None
    assert d["sources"]["PDH_PDL"] == "OFFICIAL_DAILY_FALLBACK"
    assert d["sources"]["zones"] == "UNAVAILABLE"
    assert "incomplete" in d["notes"] and "zones unavailable" in d["notes"]
    assert b.problems() == []  # ORB/IRF can still run; zone strategies fail closed on None


def test_stale_5m_history_is_not_used_for_zones() -> None:
    """5m history ends the day before the previous session -> never reused as 'previous day'."""
    full = sessions(4)
    prior = [x for x in full if x.exchange_time.date() < D0]
    b = LiveReferenceBuilder(warm(prior, warm(full).previous_session), D1)
    s = b.state_for_bar(day_bars()[0])
    assert s["pd_res_body"] is None and s["pd_supp_body"] is None
    assert b.display_levels()["sources"]["PDH_PDL"] == "OFFICIAL_DAILY_FALLBACK"


def test_missing_previous_session_everything_explicit_none() -> None:
    w = WarmupData(
        previous_session=DailyOhlc(D1, 1.0, 2.0, 0.5, 1.5),  # not before the trading date
        daily_bars=[],
        intraday_bars=[],
    )
    b = LiveReferenceBuilder(w, D1)
    probs = b.problems()
    assert any(p.startswith("previous session reference levels unavailable") for p in probs)
    assert EMA_PROBLEM in probs
    s = b.state_for_bar(day_bars()[0])
    assert set(s) == set(STRATEGY_STATE_KEYS)
    for k in STRATEGY_STATE_KEYS:
        if k not in {"levels_date", "has_sufficient_dma_warmup", "has_sufficient_ema_warmup"}:
            assert s[k] is None, k
    assert s["levels_date"] == D1.isoformat()
    d = b.display_levels()
    assert d["PDH"] is None and d["support_zone"] is None and d["EMA200"] is None


# ----------------------------------------------------------------------------- EMA


def test_insufficient_history_blocks_with_exact_reason_and_no_ema() -> None:
    b = LiveReferenceBuilder(warm(sessions(2)), D1)  # 150 prior closes
    assert b.problems() == [EMA_PROBLEM]
    s = b.state_for_bar(day_bars()[0])
    assert s["ema_200"] is None and s["has_sufficient_ema_warmup"] is False
    assert s["pdh"] is not None  # levels are still delivered
    assert b.display_levels()["EMA200"] is None


def test_ema_needs_200_prior_closes_boundary_and_is_recursive() -> None:
    full = sessions(4, stub=False)  # 300 regular closes
    prev = DailyOhlc(D0, 24000.0, 24100.0, 23900.0, 24050.0)
    for n_prior, available in ((199, False), (200, True)):
        bld = LiveReferenceBuilder(warm(full[-n_prior:], prev=prev), D1)
        st = bld.state_for_bar(day_bars()[0])
        assert (st["ema_200"] is not None) is available, n_prior
        assert st["has_sufficient_ema_warmup"] is available
        assert (bld.problems() == []) is available

    # recursion: ema_after = prev + alpha * (close - prev), alpha = 2/201, includes this bar
    bld = LiveReferenceBuilder(warm(full[-250:]), D1)
    bars = day_bars()
    first = bld.state_for_bar(bars[0])["ema_200"]
    bld.on_bar_completed(bars[0])
    second = bld.state_for_bar(bars[1])["ema_200"]
    alpha = 2.0 / 201.0
    assert second == pytest.approx(first + alpha * (bars[1].close - first), abs=0.011)


def test_state_is_pure_and_idempotent() -> None:
    b = LiveReferenceBuilder(warm(sessions(4)), D1)
    bars = day_bars()
    a1 = b.state_for_bar(bars[0])
    a2 = b.state_for_bar(bars[0])
    assert a1 == a2
    b.on_bar_completed(bars[0])
    b.on_bar_completed(bars[0])  # repeated commit is a no-op
    _approx_equal(b.state_for_bar(bars[0]), a1)  # same bar after commit: unchanged
    ref = LiveReferenceBuilder(warm(sessions(4)), D1)
    ref.on_bar_completed(bars[0])
    # a second bar peeked many times must not move the EMA
    peeks = [b.state_for_bar(bars[1])["ema_200"] for _ in range(5)]
    assert len(set(peeks)) == 1
    b.on_bar_completed(bars[1])
    ref.on_bar_completed(bars[1])
    assert b.display_levels()["EMA200"] == ref.display_levels()["EMA200"]


def test_1530_stub_never_counts() -> None:
    with_stub = LiveReferenceBuilder(warm(sessions(4, stub=True)), D1)
    without = LiveReferenceBuilder(warm(sessions(4, stub=False)), D1)
    bars = day_bars(stub=True)
    for bar in bars:
        _approx_equal(with_stub.state_for_bar(bar), without.state_for_bar(bar))
        with_stub.on_bar_completed(bar)
        without.on_bar_completed(bar)
    stub = bars[-1]
    assert stub.exchange_time.time() == time(15, 30)
    # the stub neither set the extremes nor the EMA of the evaluated stub bar itself
    last_regular = bars[-2]
    s_stub = with_stub.state_for_bar(stub)
    s_last = with_stub.state_for_bar(last_regular)
    assert s_stub["ema_200"] == s_last["ema_200"]
    s = with_stub.state_for_bar(day_bars(stub=True)[0])
    assert s["pdh"] < 24100 + 400  # +500 stub high ignored
    assert with_stub.display_levels()["PDH"] == without.display_levels()["PDH"]


# ----------------------------------------------------------------------------- no leakage


def test_no_future_leakage_when_later_bars_are_truncated() -> None:
    prior = sessions(4)
    bars = day_bars(stub=True)
    full = LiveReferenceBuilder(warm(prior), D1)
    states_full = []
    for bar in bars:
        states_full.append(full.state_for_bar(bar))
        full.on_bar_completed(bar)
    for cut in (1, 2, 10, 40, 74):
        short = LiveReferenceBuilder(warm(prior), D1)
        for i, bar in enumerate(bars[:cut]):
            _approx_equal(short.state_for_bar(bar), states_full[i])
            short.on_bar_completed(bar)


def test_warmup_bars_dated_today_or_later_are_ignored() -> None:
    prior = sessions(4)
    leak = prior + make_session(D1, 99999.0)  # today's bars smuggled into the warm-up
    clean = LiveReferenceBuilder(warm(prior), D1)
    dirty = LiveReferenceBuilder(warm(leak, warm(prior).previous_session), D1)
    bar = day_bars()[0]
    _approx_equal(clean.state_for_bar(bar), dirty.state_for_bar(bar))
    assert "ignored" in dirty.display_levels()["notes"]


def test_foreign_date_bar_yields_all_none_and_is_not_recorded() -> None:
    b = LiveReferenceBuilder(warm(sessions(4)), D1)
    other = make_session(D1 + timedelta(days=1), 24500.0)[0]
    s = b.state_for_bar(other)
    assert s["levels_date"] == D1.isoformat() != other.exchange_time.date().isoformat()
    assert s["pdh"] is None and s["ema_200"] is None
    before = b.display_levels()["EMA200"]
    b.on_bar_completed(other)
    assert b.display_levels()["EMA200"] == before


# ----------------------------------------------------------------------------- restart, display


def test_restore_today_equals_sequential_feed() -> None:
    prior = sessions(4)
    bars = day_bars(stub=True)
    seq = LiveReferenceBuilder(warm(prior), D1)
    for bar in bars[:30]:
        seq.on_bar_completed(bar)
    restored = LiveReferenceBuilder(warm(prior), D1)
    restored.restore_today(bars[:30])
    assert restored.display_levels() == seq.display_levels()
    for bar in bars[30:]:
        _approx_equal(restored.state_for_bar(bar), seq.state_for_bar(bar))
        restored.on_bar_completed(bar)
        seq.on_bar_completed(bar)
    # restoring again (even with fewer bars) replaces, never double counts
    restored.restore_today(bars[:30])
    fresh = LiveReferenceBuilder(warm(prior), D1)
    for bar in bars[:30]:
        fresh.on_bar_completed(bar)
    assert restored.display_levels() == fresh.display_levels()
    _approx_equal(restored.state_for_bar(bars[30]), fresh.state_for_bar(bars[30]))


def test_irh_irl_are_the_0915_candle_only() -> None:
    b = LiveReferenceBuilder(warm(sessions(4)), D1)
    assert b.display_levels()["IRH"] is None and b.display_levels()["IRL"] is None
    bars = day_bars()
    b.on_bar_completed(bars[0])
    d = b.display_levels()
    assert (d["IRH"], d["IRL"]) == (bars[0].high, bars[0].low)
    b.on_bar_completed(bars[1])  # the 09:20 candle must not overwrite the initial range
    d = b.display_levels()
    assert (d["IRH"], d["IRL"]) == (bars[0].high, bars[0].low)


def test_irh_irl_stay_none_when_0915_candle_is_missing() -> None:
    b = LiveReferenceBuilder(warm(sessions(4)), D1)
    bars = day_bars(skip={0})
    b.restore_today(bars)
    d = b.display_levels()
    assert d["IRH"] is None and d["IRL"] is None


def _zigzag(day: date, lows: list[float]) -> list[Bar]:
    out = []
    for (hh, mm), low in zip(_slots(), lows, strict=False):
        out.append(_bar(day, hh, mm, low + 5.0, low + 9.0, low, low + 6.0))
    return out


def test_swing_low_is_per_session_and_never_carried_over() -> None:
    zig = [50.0, 40.0, 45.0, 30.0, 35.0, 36.0]
    prior = [x for x in sessions(4, stub=False) if x.exchange_time.date() < D0]
    prior += _zigzag(D0, [60.0, 50.0, 55.0] * 25)
    full_prev = DailyOhlc(D0, 60.0, 70.0, 50.0, 62.0)
    b = LiveReferenceBuilder(warm(prior, prev=full_prev), D1)
    bars = _zigzag(D1, zig)
    states = [b.state_for_bar(bars[0])]
    b.on_bar_completed(bars[0])
    for bar in bars[1:]:
        states.append(b.state_for_bar(bar))
        b.on_bar_completed(bar)
    got = [s["recent_swing_low"] for s in states]
    # yesterday's zig-zag lows never leak in; a low confirmed at bar i-1 is known on bar i
    assert got == [None, None, 40.0, 40.0, 30.0, 30.0]


def test_display_levels_shape_and_json() -> None:
    b = LiveReferenceBuilder(warm(sessions(4)), D1)
    d = b.display_levels()
    for key in (
        "PDH PDL PDC support_zone resistance_zone IRH IRL EMA200 sources notes levels_date"
    ).split():
        assert key in d
    assert d["notes"] == ""
    assert isinstance(d["EMA200"], float)  # warm-up EMA is shown pre-session
    json.dumps(d)
    # incomplete-history notes carry the exact blocking reason
    short = LiveReferenceBuilder(warm(sessions(2)), D1).display_levels()
    assert EMA_PROBLEM in short["notes"]
