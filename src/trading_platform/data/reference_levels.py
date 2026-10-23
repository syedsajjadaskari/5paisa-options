"""Automated Point-in-Time Historical Reference Levels and Technical Indicator Engine.

Key Requirements:
1. Anti-Lookahead: Indicators and levels for bar t are computed strictly using data available up to bar t.
2. Distinct Moving Averages:
   - 200 EMA: 200-period Exponential Moving Average on intraday 5-minute closing prices.
   - 200 DMA: 200-day Simple Moving Average calculated on completed daily closing prices.
3. Market Reference Levels:
   - PDH: Previous Day High (max high of completed previous session).
   - PDL: Previous Day Low (min low of completed previous session).
   - PDC: Previous Day Close (closing price of completed previous session).
   - PDO: Previous Day Open (open of completed previous session).
   - PD Resistance Zone: Upper wick high (PDH) to reversal candle body level.
   - PD Support Zone: Lower wick low (PDL) to support candle body level.
   - Initial Range (IRH, IRL): 09:15-09:20 candle extremes (wicks included).
4. Indicator Warm-up Validation:
   - Flags sessions with insufficient history (< 200 bars for 200 EMA; < 200 days for 200 DMA).
"""

from dataclasses import dataclass
from datetime import date, time
from typing import Any

from trading_platform.core.models import Bar


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
    dma_200: float | None = None
    has_sufficient_dma_warmup: bool = False
    daily_history_count: int = 0


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


class ReferenceLevelCalculator:
    """Calculates point-in-time daily and intraday reference levels with strict anti-lookahead rules."""

    def __init__(
        self,
        ema_warmup_period: int = 200,
        dma_warmup_period: int = 200,
    ) -> None:
        self.ema_warmup_period = ema_warmup_period
        self.dma_warmup_period = dma_warmup_period

    def compute_daily_reference_levels(
        self,
        historical_bars: list[Bar],
        daily_history_bars: list[Bar] | None = None,
    ) -> dict[date, SessionReferenceLevels]:
        """Aggregate completed historical bars by day and compute prior-day reference levels and 200 DMA."""
        if not historical_bars and not daily_history_bars:
            return {}

        # 1. Group intraday bars by date and compute candle-derived zone bodies.
        bars_by_day: dict[date, list[Bar]] = {}
        for b in historical_bars:
            if not time(9, 15) <= b.exchange_time.time() <= time(15, 25):
                continue
            d = b.exchange_time.date()
            if d not in bars_by_day:
                bars_by_day[d] = []
            bars_by_day[d].append(b)

        sorted_days = sorted(bars_by_day.keys())

        intraday_summaries: dict[date, dict[str, Any]] = {}
        for d in sorted_days:
            day_bars = sorted(bars_by_day[d], key=lambda x: x.exchange_time)
            day_high = max(b.high for b in day_bars)
            day_low = min(b.low for b in day_bars)
            day_open = day_bars[0].open
            day_close = day_bars[-1].close

            # Find candle body level at the high candle
            high_candle = next(b for b in day_bars if b.high == day_high)
            res_body = max(high_candle.open, high_candle.close)

            # Find candle body level at the low candle
            low_candle = next(b for b in day_bars if b.low == day_low)
            supp_body = min(low_candle.open, low_candle.close)

            intraday_summaries[d] = {
                "date": d,
                "open": day_open,
                "high": day_high,
                "low": day_low,
                "close": day_close,
                "res_body": res_body,
                "supp_body": supp_body,
            }

        # Official daily bars are the source of PDH/PDL/PDC/PDO and the DMA
        # whenever supplied.  Intraday data remains the only valid source for
        # the body of the candle that formed the high/low zone.
        official_daily: dict[date, dict[str, Any]] = {}
        for db in sorted(daily_history_bars or [], key=lambda x: x.exchange_time):
            d = db.exchange_time.date()
            official_daily[d] = {
                "date": d,
                "open": db.open,
                "high": db.high,
                "low": db.low,
                "close": db.close,
            }

        reference_daily = official_daily or intraday_summaries

        # 2. Calculate prior-day reference levels and 200 DMA.  Every value for
        # day D is selected strictly from dates less than D.
        session_levels: dict[date, SessionReferenceLevels] = {}
        reference_dates = sorted(reference_daily)

        for curr_date in sorted_days:
            prior_dates = [d for d in reference_dates if d < curr_date]
            if not prior_dates:
                session_levels[curr_date] = SessionReferenceLevels(
                    trading_date=curr_date,
                    daily_history_count=0,
                )
                continue

            prev_date = prior_dates[-1]
            prev_day = reference_daily[prev_date]
            prior_closes = [reference_daily[d]["close"] for d in prior_dates]
            has_dma_warmup = len(prior_closes) >= self.dma_warmup_period

            dma_200 = None
            if has_dma_warmup:
                dma_200 = round(
                    sum(prior_closes[-self.dma_warmup_period :]) / self.dma_warmup_period, 2
                )

            intraday_prev = intraday_summaries.get(prev_date)
            session_levels[curr_date] = SessionReferenceLevels(
                trading_date=curr_date,
                pdh=prev_day["high"],
                pdl=prev_day["low"],
                pdc=prev_day["close"],
                pdo=prev_day["open"],
                pd_res_body=intraday_prev["res_body"] if intraday_prev else None,
                pd_supp_body=intraday_prev["supp_body"] if intraday_prev else None,
                dma_200=dma_200,
                has_sufficient_dma_warmup=has_dma_warmup,
                daily_history_count=len(prior_closes),
            )

        return session_levels

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

        enriched_context: list[BarMarketContext] = []

        # Running 200 EMA state
        k = 2.0 / (self.ema_warmup_period + 1.0)
        current_ema: float | None = None
        ema_bars_seen = 0

        # Daily tracking
        current_day: date | None = None
        day_bar_index = 0
        irh: float | None = None
        irl: float | None = None
        day_open_price: float | None = None

        # Swing tracking (lookback without lookahead)
        recent_swing_high: float | None = None
        recent_swing_low: float | None = None

        for _idx, bar in enumerate(sorted_bars):
            bar_date = bar.exchange_time.date()
            bar_time = bar.exchange_time.time()

            # Handle day rollover
            if bar_date != current_day:
                current_day = bar_date
                day_bar_index = 0
                irh = None
                irl = None
                day_open_price = bar.open
                recent_swing_high = None
                recent_swing_low = None

            day_bar_index += 1

            # Update running 200 EMA
            ema_bars_seen += 1
            if current_ema is None:
                current_ema = bar.close
            else:
                current_ema = (bar.close * k) + (current_ema * (1.0 - k))

            has_ema_warmup = ema_bars_seen >= self.ema_warmup_period
            active_ema = round(current_ema, 2) if has_ema_warmup else None

            # Track Initial Range (09:15–09:20 candle)
            if bar_time in (time(9, 15), time(9, 20)):
                irh = bar.high
                irl = bar.low

            # Get session prior levels
            s_levels = session_levels_map.get(
                bar_date, SessionReferenceLevels(trading_date=bar_date)
            )

            # Compute Gap Metrics
            gap_pts = None
            gap_pct = None
            is_gap_up = None
            is_gap_down = None
            is_gap_within = None

            if s_levels.pdc is not None and day_open_price is not None:
                gap_pts = round(day_open_price - s_levels.pdc, 2)
                gap_pct = round((gap_pts / s_levels.pdc) * 100.0, 2)
                is_gap_up = day_open_price > s_levels.pdc
                is_gap_down = day_open_price < s_levels.pdc

                if s_levels.pdh is not None:
                    # Gap within yesterday's range: Open > PDC and Open <= PDH
                    is_gap_within = (day_open_price > s_levels.pdc) and (
                        day_open_price <= s_levels.pdh
                    )

            # Compute backward-looking swing points (3-bar swing using completed prior bars)
            if len(enriched_context) >= 2:
                prev_bar = enriched_context[-1].bar
                prev_prev_bar = enriched_context[-2].bar
                # Confirmed swing high at prev_bar: prev_bar.high > prev_prev_bar.high and prev_bar.high > bar.high
                if prev_bar.high > prev_prev_bar.high and prev_bar.high > bar.high:
                    recent_swing_high = prev_bar.high
                # Confirmed swing low at prev_bar: prev_bar.low < prev_prev_bar.low and prev_bar.low < bar.low
                if prev_bar.low < prev_prev_bar.low and prev_bar.low < bar.low:
                    recent_swing_low = prev_bar.low

            # Trend Classification
            is_uptrend = None
            is_downtrend = None
            if active_ema is not None:
                is_uptrend = bar.close > active_ema
                is_downtrend = bar.close < active_ema

            ctx = BarMarketContext(
                bar=bar,
                session_levels=s_levels,
                irh=irh,
                irl=irl,
                ema_200=active_ema,
                has_sufficient_ema_warmup=has_ema_warmup,
                intraday_bar_index=day_bar_index,
                gap_points=gap_pts,
                gap_pct=gap_pct,
                is_gap_up=is_gap_up,
                is_gap_down=is_gap_down,
                is_gap_within_yesterday_range=is_gap_within,
                recent_swing_high=recent_swing_high,
                recent_swing_low=recent_swing_low,
                is_uptrend=is_uptrend,
                is_downtrend=is_downtrend,
            )
            enriched_context.append(ctx)

        return enriched_context
