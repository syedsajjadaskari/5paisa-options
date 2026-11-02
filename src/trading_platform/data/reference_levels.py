"""Point-in-time reference levels and technical indicators (ONE implementation, backtest + live).

``ReferenceLevelCalculator`` (batch, used by the backtest) and
``trading_platform.live.levels.LiveReferenceBuilder`` (incremental, used by the live paper app)
both call the functions and classes defined here, so the two can never drift apart:

* ``summarize_regular_session``  - one previous session -> extremes + candle bodies (single dataset)
* ``ReferenceHistory.levels_for``- the previous-session levels for a trading date
* ``IntradayContextTracker``     - recursive 5m EMA, initial range, swing points (incremental)
* ``build_strategy_state``       - the COMPLETE ``StrategyContext.state`` dict handed to strategies

Rules
-----
1. Anti-lookahead: levels for trading date ``D`` come from sessions STRICTLY BEFORE ``D``;
   the indicator for bar ``t`` uses only bars up to and including ``t`` (it is evaluated on the
   completed bar).  Nothing is ever carried over from another day: a value that cannot be
   computed is ``None`` (explicit), never a previous day's value and never fabricated.
2. Regular-session bars only: bar-open stamps 09:15..15:25 (75 five-minute bars).  The vendor's
   flat zero-volume 15:30 stub bars (and anything else outside that range) are excluded from
   session extremes, bodies, the EMA, the initial range and swing detection.
3. 200 EMA: recursive EMA (``alpha = 2/(N+1)``, seeded with the first regular close in the
   history) over 5-minute closes, INCLUDING the evaluated bar's close, reported (rounded to
   0.01) only when at least ``N`` PRIOR regular closes existed.  200 DMA: simple average of the
   last 200 completed daily closes before the trading date.
4. Initial Range (IRH/IRL): the 09:15 candle only (wicks included).  The 09:20 candle never
   overwrites it, and when the 09:15 candle is missing IRH/IRL stay ``None``.

Previous-session sources (SINGLE-SOURCED zones)  --  PROVISIONAL source choice
------------------------------------------------------------------------------
The strategy rules need a support/resistance "zone" = the wick of the previous session's
extreme candle plus that candle's body.  Taking the wick extreme (``pdl``/``pdh``) from the
official daily file and the body from the intraday candle mixes two datasets (the official
daily low differs from the 5m low by more than 0.5 pt on ~45% of sessions in the verified
history), which can invert or distort the zone.  Therefore:

* ``pdh``, ``pdl``, ``pd_res_body``, ``pd_supp_body``, ``pd_supp_body_top`` ALL come from ONE
  dataset: the previous session's regular-session 5m candles.  ``pdh`` = highest 5m high;
  ``pd_res_body`` = ``max(open, close)`` of the (first) candle that made it; ``pdl`` = lowest
  5m low; ``pd_supp_body`` = ``min(open, close)`` and ``pd_supp_body_top`` = ``max(open, close)``
  of the (first) candle that made it.  By construction ``pdl <= pd_supp_body <= pd_supp_body_top``
  and ``pd_res_body <= pdh``, so a zone can never invert.
  Support zone = ``[pdl, pd_supp_body_top]`` (lower wick + body, as the strategy PDF draws it);
  resistance zone = ``[pd_res_body, pdh]`` (upper wick above the body, as PDR consumes it).
* ``prev_close`` (PDC) and ``pdo`` come from the OFFICIAL daily bar when it exists for that same
  previous session (it is the reference the exchange/broker publishes for gap calculation);
  otherwise from the last / first regular 5m candle of the previous session.  This is a
  single scalar used only for the gap test and is never part of a zone.
* If the previous session's 5m data is unusable (missing, not the latest session, missing its
  first/last candle or more than ``max_missing_slots`` candles) the plain lines fall back to
  the official daily OHLC (labelled ``OFFICIAL_DAILY_FALLBACK``) and the zone bodies are
  ``None`` - a fallback line is never combined with a body from another dataset.
* The official daily high/low stay available as diagnostics (``official_pdh/pdl/pdc``).

The source choice above is PROVISIONAL (the PDF does not fix the data source) and is reported
through ``SessionReferenceLevels.lines_source/close_source/zone_source``.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from trading_platform.core.models import Bar

REGULAR_SESSION_FIRST_OPEN = time(9, 15)
REGULAR_SESSION_LAST_OPEN = time(15, 25)
EXPECTED_SESSION_BARS = 75  # 09:15 .. 15:25 inclusive, 5-minute bar opens

SOURCE_PREV_SESSION_5M = "PREV_SESSION_5M_REGULAR"
SOURCE_OFFICIAL_DAILY = "OFFICIAL_DAILY"
SOURCE_OFFICIAL_FALLBACK = "OFFICIAL_DAILY_FALLBACK"
SOURCE_PREV_SESSION_LAST_5M = "PREV_SESSION_LAST_5M_CLOSE"

STRATEGY_STATE_KEYS: tuple[str, ...] = (
    "pdh",
    "pdl",
    "prev_close",
    "pd_res_body",
    "pd_supp_body",
    "pd_supp_body_top",
    "dma_200",
    "has_sufficient_dma_warmup",
    "ema_200",
    "has_sufficient_ema_warmup",
    "recent_swing_high",
    "recent_swing_low",
    "levels_date",
)


def is_regular_session_bar(bar: Bar) -> bool:
    """True for a regular-session 5m bar (open stamp 09:15..15:25); excludes 15:30 stubs."""
    return REGULAR_SESSION_FIRST_OPEN <= bar.exchange_time.time() <= REGULAR_SESSION_LAST_OPEN


# --------------------------------------------------------------------------- data classes


@dataclass(frozen=True)
class SessionReferenceLevels:
    """Pre-computed previous session reference levels for a specific trading day."""

    trading_date: date
    pdh: float | None = None
    pdl: float | None = None
    pdc: float | None = None
    pdo: float | None = None
    pd_res_body: float | None = None
    pd_supp_body: float | None = None
    pd_supp_body_top: float | None = None
    dma_200: float | None = None
    has_sufficient_dma_warmup: bool = False
    daily_history_count: int = 0
    previous_session_date: date | None = None
    lines_source: str | None = None  # source of pdh / pdl
    close_source: str | None = None  # source of pdc / pdo
    zone_source: str | None = None  # source of the three body values (None => no zone)
    official_pdh: float | None = None  # diagnostics only
    official_pdl: float | None = None
    official_pdc: float | None = None
    notes: tuple[str, ...] = ()

    @property
    def support_zone(self) -> tuple[float, float] | None:
        """[pdl, pd_supp_body_top] (lower wick + body) or None when not single-sourced."""
        if self.pdl is None or self.pd_supp_body_top is None or self.zone_source is None:
            return None
        return (self.pdl, self.pd_supp_body_top)

    @property
    def resistance_zone(self) -> tuple[float, float] | None:
        """[pd_res_body, pdh] or None when not single-sourced."""
        if self.pdh is None or self.pd_res_body is None or self.zone_source is None:
            return None
        return (self.pd_res_body, self.pdh)


@dataclass(frozen=True)
class IntradayContext:
    """Intraday values for ONE evaluated 5m bar (all inclusive of that bar's own data)."""

    bar_open_time: datetime
    ema_200: float | None = None
    has_ema_warmup: bool = False
    prior_closes: int = 0  # regular closes that existed BEFORE this bar
    irh: float | None = None
    irl: float | None = None
    day_open: float | None = None  # open of the 09:15 candle (None if it is missing)
    day_high: float | None = None
    day_low: float | None = None
    bar_index: int = 0  # 1-based regular-bar count within the session
    recent_swing_high: float | None = None
    recent_swing_low: float | None = None
    regular: bool = True


@dataclass(frozen=True)
class BarMarketContext:
    """Complete point-in-time market context for a single 5-minute bar."""

    bar: Bar
    session_levels: SessionReferenceLevels
    irh: float | None = None
    irl: float | None = None
    ema_200: float | None = None
    has_sufficient_ema_warmup: bool = False
    intraday_bar_index: int = 0
    gap_points: float | None = None
    gap_pct: float | None = None
    is_gap_up: bool | None = None
    is_gap_down: bool | None = None
    is_gap_within_yesterday_range: bool | None = None
    recent_swing_high: float | None = None
    recent_swing_low: float | None = None
    is_uptrend: bool | None = None
    is_downtrend: bool | None = None

    def strategy_state(self) -> dict[str, Any]:
        """COMPLETE ``StrategyContext.state`` dict for this bar (None for unavailable keys)."""
        return build_strategy_state(
            self.session_levels,
            ema_200=self.ema_200,
            has_ema_warmup=self.has_sufficient_ema_warmup,
            recent_swing_high=self.recent_swing_high,
            recent_swing_low=self.recent_swing_low,
        )


def build_strategy_state(
    levels: SessionReferenceLevels | None,
    *,
    levels_date: date | None = None,
    ema_200: float | None = None,
    has_ema_warmup: bool = False,
    recent_swing_high: float | None = None,
    recent_swing_low: float | None = None,
) -> dict[str, Any]:
    """The one place that assembles the strategy context state.

    Every key in ``STRATEGY_STATE_KEYS`` is always present; unavailable values are ``None``.
    Callers must ASSIGN/overwrite this dict into ``context.state`` (never merge only the
    non-None values) so nothing can survive from the previous day.
    """
    day = levels_date or (levels.trading_date if levels is not None else None)
    lv = levels
    return {
        "pdh": lv.pdh if lv else None,
        "pdl": lv.pdl if lv else None,
        "prev_close": lv.pdc if lv else None,
        "pd_res_body": lv.pd_res_body if lv else None,
        "pd_supp_body": lv.pd_supp_body if lv else None,
        "pd_supp_body_top": lv.pd_supp_body_top if lv else None,
        "dma_200": lv.dma_200 if lv else None,
        "has_sufficient_dma_warmup": bool(lv.has_sufficient_dma_warmup) if lv else False,
        "ema_200": ema_200,
        "has_sufficient_ema_warmup": bool(has_ema_warmup),
        "recent_swing_high": recent_swing_high,
        "recent_swing_low": recent_swing_low,
        "levels_date": day.isoformat() if day is not None else None,
    }


# --------------------------------------------------------------------------- sessions


@dataclass(frozen=True)
class PriorSessionSummary:
    """Extremes and candle bodies of ONE session's regular 5m candles (single dataset)."""

    session_date: date
    n_bars: int
    missing_slots: int
    complete: bool
    incomplete_reason: str | None
    open: float
    high: float
    low: float
    close: float
    res_body: float  # max(open, close) of the (first) candle with the session high
    supp_body: float  # min(open, close) of the (first) candle with the session low
    supp_body_top: float  # max(open, close) of that same low candle
    high_time: datetime
    low_time: datetime
    first_open_time: time
    last_open_time: time


def regular_bars_by_date(bars: list[Bar] | tuple[Bar, ...]) -> dict[date, list[Bar]]:
    """Group regular-session bars by date, ascending, one bar per open time (last one wins)."""
    grouped: dict[date, dict[datetime, Bar]] = {}
    for b in bars:
        if not is_regular_session_bar(b):
            continue
        grouped.setdefault(b.exchange_time.date(), {})[b.exchange_time] = b
    return {d: [grouped[d][t] for t in sorted(grouped[d])] for d in sorted(grouped)}


def summarize_regular_session(
    session_date: date, day_bars: list[Bar], max_missing_slots: int = 0
) -> PriorSessionSummary | None:
    """Summarize one session from its regular 5m bars (ascending, one per open time).

    ``complete`` requires the 09:15 and 15:25 candles and at most ``max_missing_slots``
    absent candles, so a hidden gap cannot silently move the extremes.
    """
    regular = [b for b in day_bars if is_regular_session_bar(b)]
    if not regular:
        return None
    regular.sort(key=lambda b: b.exchange_time)
    high = max(b.high for b in regular)
    low = min(b.low for b in regular)
    high_candle = next(b for b in regular if b.high == high)
    low_candle = next(b for b in regular if b.low == low)
    distinct = len({b.exchange_time for b in regular})
    missing = max(0, EXPECTED_SESSION_BARS - distinct)
    first_t, last_t = regular[0].exchange_time.time(), regular[-1].exchange_time.time()
    reason: str | None = None
    if first_t != REGULAR_SESSION_FIRST_OPEN:
        reason = f"09:15 candle missing (first candle {first_t:%H:%M})"
    elif last_t != REGULAR_SESSION_LAST_OPEN:
        reason = f"15:25 candle missing (last candle {last_t:%H:%M})"
    elif missing > max_missing_slots:
        reason = f"{missing} of {EXPECTED_SESSION_BARS} candles missing"
    return PriorSessionSummary(
        session_date=session_date,
        n_bars=len(regular),
        missing_slots=missing,
        complete=reason is None,
        incomplete_reason=reason,
        open=regular[0].open,
        high=high,
        low=low,
        close=regular[-1].close,
        res_body=max(high_candle.open, high_candle.close),
        supp_body=min(low_candle.open, low_candle.close),
        supp_body_top=max(low_candle.open, low_candle.close),
        high_time=high_candle.exchange_time,
        low_time=low_candle.exchange_time,
        first_open_time=first_t,
        last_open_time=last_t,
    )


@dataclass(frozen=True)
class OfficialDay:
    """One official daily OHLC record."""

    session_date: date
    open: float
    high: float
    low: float
    close: float


def official_day_from(item: Any) -> OfficialDay:
    """Build an ``OfficialDay`` from a daily ``Bar`` or a ``live.models.DailyOhlc``."""
    d = getattr(item, "session_date", None)
    if d is None:
        d = item.exchange_time.date()
    return OfficialDay(d, float(item.open), float(item.high), float(item.low), float(item.close))


class ReferenceHistory:
    """Previous-session levels for any trading date, from strictly earlier sessions only."""

    def __init__(
        self,
        intraday: dict[date, PriorSessionSummary],
        official: dict[date, OfficialDay],
        dma_period: int = 200,
    ) -> None:
        self._intraday = intraday
        self._official = official
        self._dma_period = dma_period
        self._intraday_dates = sorted(intraday)
        self._official_dates = sorted(official)
        # DMA reference series: official daily closes whenever supplied, else session closes.
        if official:
            self._dma_dates = self._official_dates
            self._dma_closes = [official[d].close for d in self._dma_dates]
        else:
            self._dma_dates = self._intraday_dates
            self._dma_closes = [intraday[d].close for d in self._dma_dates]

    def levels_for(self, trading_date: date) -> SessionReferenceLevels:
        n_dma = bisect_left(self._dma_dates, trading_date)
        has_dma = n_dma >= self._dma_period
        dma = (
            round(sum(self._dma_closes[n_dma - self._dma_period : n_dma]) / self._dma_period, 2)
            if has_dma
            else None
        )
        i = bisect_left(self._intraday_dates, trading_date)
        j = bisect_left(self._official_dates, trading_date)
        intra = self._intraday[self._intraday_dates[i - 1]] if i else None
        off = self._official[self._official_dates[j - 1]] if j else None
        if intra is None and off is None:
            return SessionReferenceLevels(
                trading_date=trading_date,
                dma_200=dma,
                has_sufficient_dma_warmup=has_dma,
                daily_history_count=n_dma,
            )

        prev_date = max(x.session_date for x in (intra, off) if x is not None)
        intra_ok = intra is not None and intra.session_date == prev_date and intra.complete
        off_ok = off is not None and off.session_date == prev_date
        notes: list[str] = []
        if intra is not None and not intra_ok:
            if intra.session_date != prev_date:
                notes.append(
                    f"5m history ends {intra.session_date} but the previous session is {prev_date}"
                )
            else:
                notes.append(f"previous-session 5m data incomplete: {intra.incomplete_reason}")

        common: dict[str, Any] = {
            "trading_date": trading_date,
            "dma_200": dma,
            "has_sufficient_dma_warmup": has_dma,
            "daily_history_count": n_dma,
            "previous_session_date": prev_date,
            "official_pdh": off.high if off_ok and off else None,
            "official_pdl": off.low if off_ok and off else None,
            "official_pdc": off.close if off_ok and off else None,
        }
        if intra_ok and intra is not None:
            return SessionReferenceLevels(
                pdh=intra.high,
                pdl=intra.low,
                pdc=off.close if off_ok and off else intra.close,
                pdo=off.open if off_ok and off else intra.open,
                pd_res_body=intra.res_body,
                pd_supp_body=intra.supp_body,
                pd_supp_body_top=intra.supp_body_top,
                lines_source=SOURCE_PREV_SESSION_5M,
                close_source=SOURCE_OFFICIAL_DAILY if off_ok else SOURCE_PREV_SESSION_LAST_5M,
                zone_source=SOURCE_PREV_SESSION_5M,
                notes=tuple(notes),
                **common,
            )
        if off_ok and off is not None:
            notes.append("zone bodies unavailable: plain lines use the official daily OHLC")
            return SessionReferenceLevels(
                pdh=off.high,
                pdl=off.low,
                pdc=off.close,
                pdo=off.open,
                lines_source=SOURCE_OFFICIAL_FALLBACK,
                close_source=SOURCE_OFFICIAL_DAILY,
                notes=tuple(notes),
                **common,
            )
        notes.append("no usable previous-session data: all levels unavailable")
        return SessionReferenceLevels(notes=tuple(notes), **common)


# --------------------------------------------------------------------------- intraday state


@dataclass(frozen=True)
class _TrackerState:
    session_date: date | None = None
    ema: float | None = None  # unrounded recursive EMA over all regular closes so far
    closes_seen: int = 0
    bar_index: int = 0
    day_open: float | None = None
    irh: float | None = None
    irl: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    prev1: tuple[float, float] | None = None  # (high, low) of the previous regular bar today
    prev2: tuple[float, float] | None = None
    swing_high: float | None = None
    swing_low: float | None = None
    last_open_time: datetime | None = None


class IntradayContextTracker:
    """Incremental, deterministic intraday state shared by the backtest and the live builder.

    ``peek(bar)`` is pure.  ``advance(bar)`` commits a bar exactly once per distinct bar open
    time (re-advancing the same bar is a no-op returning the same context), so callers may call
    it repeatedly.  Non-regular bars (15:30 stubs, pre-open) never change any state.
    """

    def __init__(self, ema_period: int = 200) -> None:
        if ema_period < 1:
            raise ValueError("ema_period must be >= 1")
        self.ema_period = ema_period
        self._alpha = 2.0 / (ema_period + 1.0)
        self._state = _TrackerState()
        self._snapshots: dict[datetime, IntradayContext] = {}

    # ------------------------------------------------------------------ introspection

    @property
    def closes_seen(self) -> int:
        """Regular 5m closes committed so far (all sessions)."""
        return self._state.closes_seen

    @property
    def session_date(self) -> date | None:
        return self._state.session_date

    def latest(self, session_date: date | None = None) -> IntradayContext:
        """Context after the last committed bar (for display); empty session fields if the
        requested ``session_date`` has no committed bar yet."""
        st = self._state
        when = st.last_open_time or datetime.min
        return self._context(st, when, regular=True, session_date=session_date)

    def checkpoint(self) -> tuple[_TrackerState, dict[datetime, IntradayContext]]:
        """Opaque immutable-ish snapshot to restore with ``restore`` (used for replays)."""
        return self._state, dict(self._snapshots)

    def restore(self, token: tuple[_TrackerState, dict[datetime, IntradayContext]]) -> None:
        self._state = token[0]
        self._snapshots = dict(token[1])

    # ------------------------------------------------------------------ core

    def _step(self, st: _TrackerState, bar: Bar) -> _TrackerState:
        d = bar.exchange_time.date()
        if st.session_date != d:
            st = _TrackerState(
                session_date=d,
                ema=st.ema,
                closes_seen=st.closes_seen,
                last_open_time=st.last_open_time,
            )
        ema = (
            bar.close if st.ema is None else bar.close * self._alpha + st.ema * (1.0 - self._alpha)
        )
        swing_high, swing_low = st.swing_high, st.swing_low
        if st.prev1 is not None and st.prev2 is not None:
            # A swing is confirmed at the previous bar once the current (completed) bar is known.
            if st.prev1[0] > st.prev2[0] and st.prev1[0] > bar.high:
                swing_high = st.prev1[0]
            if st.prev1[1] < st.prev2[1] and st.prev1[1] < bar.low:
                swing_low = st.prev1[1]
        is_first = bar.exchange_time.time() == REGULAR_SESSION_FIRST_OPEN
        return _TrackerState(
            session_date=d,
            ema=ema,
            closes_seen=st.closes_seen + 1,
            bar_index=st.bar_index + 1,
            day_open=bar.open if is_first else st.day_open,
            irh=bar.high if is_first else st.irh,
            irl=bar.low if is_first else st.irl,
            day_high=bar.high if st.day_high is None else max(st.day_high, bar.high),
            day_low=bar.low if st.day_low is None else min(st.day_low, bar.low),
            prev1=(bar.high, bar.low),
            prev2=st.prev1,
            swing_high=swing_high,
            swing_low=swing_low,
            last_open_time=bar.exchange_time,
        )

    def _context(
        self,
        st: _TrackerState,
        when: datetime,
        *,
        regular: bool,
        session_date: date | None = None,
    ) -> IntradayContext:
        """Context of the LAST bar folded into ``st`` (its prior-close count is closes - 1)."""
        if session_date is not None and st.session_date != session_date:
            st = _TrackerState(ema=st.ema, closes_seen=st.closes_seen)
        prior = max(st.closes_seen - 1, 0)
        warm = st.ema is not None and prior >= self.ema_period
        return IntradayContext(
            bar_open_time=when,
            ema_200=round(st.ema, 2) if warm and st.ema is not None else None,
            has_ema_warmup=warm,
            prior_closes=prior,
            irh=st.irh,
            irl=st.irl,
            day_open=st.day_open,
            day_high=st.day_high,
            day_low=st.day_low,
            bar_index=st.bar_index,
            recent_swing_high=st.swing_high,
            recent_swing_low=st.swing_low,
            regular=regular,
        )

    def peek(self, bar: Bar) -> IntradayContext | None:
        """Context the evaluation of ``bar`` sees (pure; never mutates).

        Returns ``None`` only for a stale regular bar that is older than the committed state
        and was never committed (it cannot be reconstructed).
        """
        when = bar.exchange_time
        if not is_regular_session_bar(bar):
            st = self._state
            return self._context(st, when, regular=False, session_date=when.date())
        known = self._snapshots.get(when)
        if known is not None:
            return known
        st = self._state
        if st.last_open_time is not None and when <= st.last_open_time:
            return None
        return self._context(self._step(st, bar), when, regular=True)

    def advance(self, bar: Bar) -> IntradayContext | None:
        """Commit a completed bar (idempotent per bar open time) and return its context."""
        ctx = self.peek(bar)
        if ctx is None or not ctx.regular or bar.exchange_time in self._snapshots:
            return ctx
        new_state = self._step(self._state, bar)
        if new_state.session_date != self._state.session_date:
            self._snapshots = {}
        self._state = new_state
        self._snapshots[bar.exchange_time] = ctx
        return ctx


# --------------------------------------------------------------------------- batch calculator


class ReferenceLevelCalculator:
    """Calculates point-in-time daily and intraday reference levels (strict anti-lookahead)."""

    def __init__(
        self,
        ema_warmup_period: int = 200,
        dma_warmup_period: int = 200,
        max_missing_slots: int = 0,
    ) -> None:
        self.ema_warmup_period = ema_warmup_period
        self.dma_warmup_period = dma_warmup_period
        self.max_missing_slots = max_missing_slots

    def build_history(
        self,
        historical_bars: list[Bar],
        daily_history_bars: list[Bar] | None = None,
        extra_official: Iterable[OfficialDay] = (),
    ) -> ReferenceHistory:
        """Per-session summaries (regular 5m bars only) + official daily records.

        ``extra_official`` (e.g. the live app's explicitly fetched previous-session OHLC)
        is merged over ``daily_history_bars`` for the same date.
        """
        intraday: dict[date, PriorSessionSummary] = {}
        for d, day_bars in regular_bars_by_date(historical_bars).items():
            summary = summarize_regular_session(d, day_bars, self.max_missing_slots)
            if summary is not None:
                intraday[d] = summary
        official: dict[date, OfficialDay] = {}
        for db in sorted(daily_history_bars or [], key=lambda x: x.exchange_time):
            od = official_day_from(db)
            official[od.session_date] = od
        for od in extra_official:
            official[od.session_date] = od
        return ReferenceHistory(intraday, official, self.dma_warmup_period)

    def compute_daily_reference_levels(
        self,
        historical_bars: list[Bar],
        daily_history_bars: list[Bar] | None = None,
    ) -> dict[date, SessionReferenceLevels]:
        """Prior-session reference levels for every date that has regular intraday bars."""
        if not historical_bars and not daily_history_bars:
            return {}
        history = self.build_history(historical_bars, daily_history_bars)
        days = sorted(
            {b.exchange_time.date() for b in historical_bars if is_regular_session_bar(b)}
        )
        return {d: history.levels_for(d) for d in days}

    def enrich_bars_with_context(
        self,
        historical_bars: list[Bar],
        daily_levels: dict[date, SessionReferenceLevels] | None = None,
        daily_history_bars: list[Bar] | None = None,
    ) -> list[BarMarketContext]:
        """Iterate bar-by-bar in chronological order and compute point-in-time context."""
        if not historical_bars:
            return []

        sorted_bars = sorted(historical_bars, key=lambda x: x.exchange_time)
        session_levels_map = daily_levels or self.compute_daily_reference_levels(
            sorted_bars, daily_history_bars=daily_history_bars
        )
        tracker = IntradayContextTracker(self.ema_warmup_period)
        enriched: list[BarMarketContext] = []

        for bar in sorted_bars:
            bar_date = bar.exchange_time.date()
            ic = tracker.advance(bar)
            if ic is None:  # duplicate/out-of-order regular bar: reuse the committed context
                ic = tracker.latest(bar_date)
            s_levels = session_levels_map.get(
                bar_date, SessionReferenceLevels(trading_date=bar_date)
            )

            gap_pts = gap_pct = None
            is_gap_up = is_gap_down = is_gap_within = None
            if s_levels.pdc is not None and ic.day_open is not None:
                gap_pts = round(ic.day_open - s_levels.pdc, 2)
                gap_pct = round((gap_pts / s_levels.pdc) * 100.0, 2)
                is_gap_up = ic.day_open > s_levels.pdc
                is_gap_down = ic.day_open < s_levels.pdc
                if s_levels.pdh is not None:
                    is_gap_within = ic.day_open > s_levels.pdc and ic.day_open <= s_levels.pdh

            is_up = is_down = None
            if ic.ema_200 is not None:
                is_up = bar.close > ic.ema_200
                is_down = bar.close < ic.ema_200

            enriched.append(
                BarMarketContext(
                    bar=bar,
                    session_levels=s_levels,
                    irh=ic.irh,
                    irl=ic.irl,
                    ema_200=ic.ema_200,
                    has_sufficient_ema_warmup=ic.has_ema_warmup,
                    intraday_bar_index=ic.bar_index,
                    gap_points=gap_pts,
                    gap_pct=gap_pct,
                    is_gap_up=is_gap_up,
                    is_gap_down=is_gap_down,
                    is_gap_within_yesterday_range=is_gap_within,
                    recent_swing_high=ic.recent_swing_high,
                    recent_swing_low=ic.recent_swing_low,
                    is_uptrend=is_up,
                    is_downtrend=is_down,
                )
            )
        return enriched


__all__ = [
    "EXPECTED_SESSION_BARS",
    "REGULAR_SESSION_FIRST_OPEN",
    "REGULAR_SESSION_LAST_OPEN",
    "STRATEGY_STATE_KEYS",
    "BarMarketContext",
    "IntradayContext",
    "IntradayContextTracker",
    "OfficialDay",
    "PriorSessionSummary",
    "ReferenceHistory",
    "ReferenceLevelCalculator",
    "SessionReferenceLevels",
    "build_strategy_state",
    "is_regular_session_bar",
    "official_day_from",
    "regular_bars_by_date",
    "summarize_regular_session",
]
