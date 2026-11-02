"""Live reference-level builder: the strategy context for the paper-trading engine.

``LiveReferenceBuilder`` gives the live engine exactly what the backtest injects into
``StrategyContext.state`` for every bar.  It does not re-implement any level maths: it calls
the shared implementation in ``trading_platform.data.reference_levels`` (the same
``ReferenceHistory.levels_for`` / ``IntradayContextTracker`` / ``build_strategy_state`` that
``ReferenceLevelCalculator`` and ``UnderlyingBacktestEngine`` use), so live and backtest cannot
drift apart (a parity test replays historical dates and compares every key to 1e-9).

Source decisions (PROVISIONAL - the strategy PDF does not fix a data source)
---------------------------------------------------------------------------
* ``pdh``, ``pdl``, ``pd_res_body``, ``pd_supp_body``, ``pd_supp_body_top`` all come from ONE
  dataset: the previous session's regular-session 5m candles (09:15..15:25 open stamps) in
  ``WarmupData.intraday_bars``.  Zone edges and candle bodies therefore always belong to the
  same candle and a zone can never invert (``pdl <= pd_supp_body <= pd_supp_body_top``,
  ``pd_res_body <= pdh``).
  Support zone = ``[pdl, pd_supp_body_top]`` (lower wick + body); resistance zone =
  ``[pd_res_body, pdh]``.
* ``prev_close`` (PDC) uses the OFFICIAL daily close of the same previous session
  (``WarmupData.previous_session`` / ``daily_bars``) when available - it is used only for the
  gap test - else the last regular 5m close of that session.
* If the previous session's 5m data is not usable (not the latest session, missing its 09:15 or
  15:25 candle, or any candle missing) the plain lines fall back to the official daily OHLC
  (``OFFICIAL_DAILY_FALLBACK``) and the three zone bodies are ``None``: strategies that need a
  zone then fail closed, and a fallback line is never mixed with a body from another dataset.
* The 200 EMA is a recursive 5m-close EMA over every regular warm-up close followed by today's
  completed bars, seeded with the first close of the warm-up history (so a short warm-up
  differs slightly from a multi-year backtest seed; this is inherent and visible in
  ``display_levels()['sources']['ema200']``).  It is reported only when at least 200 PRIOR
  regular closes existed, otherwise ``ema_200`` is ``None`` and ``problems()`` says so.
* 15:30 vendor stub bars and any other non-regular bar never touch the EMA, the initial range,
  the day high/low or swing detection.

Contract used by the engine
---------------------------
``problems()``            empty list == ready.  Non-empty: strategies must stay WARMING_UP /
                          BLOCKED and show these exact reasons.  ``warnings()`` lists degraded
                          but non-blocking facts (e.g. zone bodies unavailable).
``state_for_bar(bar)``    COMPLETE ``context.state`` dict for evaluating the completed ``bar``
                          (ASSIGN it, do not merge).  Pure and idempotent; the EMA includes
                          ``bar``'s own close but is committed only by ``on_bar_completed``.
``on_bar_completed(bar)`` commit a completed bar (once per distinct open time; repeats no-op).
``restore_today(bars)``   after a restart: reset to the post-warm-up state and replay today's
                          completed bars.
``display_levels()``      dict for the terminal / dashboard snapshot ``levels`` section.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from trading_platform.core.models import Bar
from trading_platform.data.reference_levels import (
    IntradayContext,
    IntradayContextTracker,
    OfficialDay,
    ReferenceLevelCalculator,
    SessionReferenceLevels,
    build_strategy_state,
    official_day_from,
)
from trading_platform.live.models import WarmupData

DEFAULT_EMA_PERIOD = 200
_OFFICIAL_MISMATCH_TOLERANCE = 0.011  # points; the two official sources should agree to a paisa


class LiveReferenceBuilder:
    """Incremental reference-context builder for one trading date (see module docstring)."""

    def __init__(
        self,
        warmup: WarmupData,
        trading_date: date,
        *,
        ema_period: int = DEFAULT_EMA_PERIOD,
        dma_period: int = 200,
        max_missing_slots: int = 0,
    ) -> None:
        self.trading_date = trading_date
        self.ema_period = ema_period
        self._warnings: list[str] = []
        self._warned_foreign_bar = False

        # Strictly-earlier sessions only: nothing dated on/after the trading date may leak in.
        prior_5m = [b for b in warmup.intraday_bars if b.exchange_time.date() < trading_date]
        dropped = len(warmup.intraday_bars) - len(prior_5m)
        if dropped:
            self._warnings.append(
                f"{dropped} warm-up 5m bars dated on/after {trading_date} were ignored"
            )
        prior_daily = [b for b in warmup.daily_bars if b.exchange_time.date() < trading_date]

        extra: list[OfficialDay] = []
        prev = warmup.previous_session
        if prev is not None and prev.session_date < trading_date:
            prev_day = official_day_from(prev)
            extra.append(prev_day)
            same = next(
                (b for b in prior_daily if b.exchange_time.date() == prev.session_date), None
            )
            if same is not None and any(
                abs(a - b) > _OFFICIAL_MISMATCH_TOLERANCE
                for a, b in (
                    (same.high, prev.high),
                    (same.low, prev.low),
                    (same.close, prev.close),
                )
            ):
                self._warnings.append(
                    f"previous_session OHLC for {prev.session_date} differs from the daily "
                    "history bar; previous_session is used"
                )
        elif prev is not None:
            self._warnings.append(
                f"previous_session dated {prev.session_date} is not before {trading_date}; ignored"
            )

        calculator = ReferenceLevelCalculator(
            ema_warmup_period=ema_period,
            dma_warmup_period=dma_period,
            max_missing_slots=max_missing_slots,
        )
        history = calculator.build_history(prior_5m, prior_daily, extra_official=extra)
        self._levels: SessionReferenceLevels = history.levels_for(trading_date)

        self._tracker = IntradayContextTracker(ema_period)
        for bar in sorted(prior_5m, key=lambda b: b.exchange_time):
            self._tracker.advance(bar)
        self._warm_token = self._tracker.checkpoint()
        self._warm_closes = self._tracker.closes_seen
        self._source_notes = list(warmup.source_notes)

    # ------------------------------------------------------------------ readiness

    @property
    def levels(self) -> SessionReferenceLevels:
        return self._levels

    def problems(self) -> list[str]:
        """Blocking reasons; empty means every strategy context is ready."""
        out: list[str] = []
        lv = self._levels
        if lv.pdh is None or lv.pdl is None or lv.pdc is None:
            detail = "; ".join(lv.notes) or "no prior session data"
            out.append(f"previous session reference levels unavailable ({detail})")
        if self._tracker.closes_seen < self.ema_period:
            out.append(f"fewer than {self.ema_period} prior 5m closes for EMA{self.ema_period}")
        return out

    def warnings(self) -> list[str]:
        """Degraded but non-blocking facts (shown in ``display_levels()['notes']``)."""
        out = list(self._warnings)
        out.extend(self._levels.notes)
        if self._levels.pdh is not None and self._levels.zone_source is None:
            out.append("support/resistance zones unavailable (PDS / PDR cannot evaluate)")
        return out

    # ------------------------------------------------------------------ per-bar API

    def state_for_bar(self, bar: Bar) -> dict[str, Any]:
        """Complete ``context.state`` for evaluating the completed ``bar`` (pure, idempotent)."""
        if bar.exchange_time.date() != self.trading_date:
            # Not today's bar: nothing may be computed; levels_date stays the builder's session
            # so a strategy rejects the mismatch as stale.
            return build_strategy_state(None, levels_date=self.trading_date)
        ic = self._tracker.peek(bar)
        if ic is None:  # an old, never-committed bar: its indicators cannot be reconstructed
            return build_strategy_state(self._levels, levels_date=self.trading_date)
        return build_strategy_state(
            self._levels,
            levels_date=self.trading_date,
            ema_200=ic.ema_200,
            has_ema_warmup=ic.has_ema_warmup,
            recent_swing_high=ic.recent_swing_high,
            recent_swing_low=ic.recent_swing_low,
        )

    def on_bar_completed(self, bar: Bar) -> None:
        """Record a completed bar (EMA advance, day high/low, IRH/IRL); idempotent per bar."""
        if bar.exchange_time.date() != self.trading_date:
            if not self._warned_foreign_bar:
                self._warned_foreign_bar = True
                self._warnings.append(f"bar dated {bar.exchange_time.date()} ignored (not today)")
            return
        self._tracker.advance(bar)

    def restore_today(self, bars: list[Bar]) -> None:
        """Replay today's already-completed bars after a restart (replaces any recorded bars)."""
        self._tracker.restore(self._warm_token)
        for bar in sorted(bars, key=lambda b: b.exchange_time):
            if bar.exchange_time.date() == self.trading_date:
                self._tracker.advance(bar)

    # ------------------------------------------------------------------ display

    def today_context(self) -> IntradayContext:
        """Intraday context after the last recorded bar (empty session fields before 09:15)."""
        return self._tracker.latest(self.trading_date)

    def display_levels(self) -> dict[str, Any]:
        """Levels for the terminal / dashboard snapshot (``None`` where unavailable)."""
        lv = self._levels
        ic = self.today_context()
        support = lv.support_zone
        resistance = lv.resistance_zone
        sources = {
            "status": "PROVISIONAL",
            "previous_session": lv.previous_session_date.isoformat()
            if lv.previous_session_date
            else None,
            "PDH_PDL": lv.lines_source or "UNAVAILABLE",
            "PDC": lv.close_source or "UNAVAILABLE",
            "zones": lv.zone_source or "UNAVAILABLE",
            "IR": "09:15 candle only (provider-completed 5m)",
            "ema200": (
                f"recursive 5m close EMA({self.ema_period}), seeded from the first warm-up "
                f"close, {self._warm_closes} warm-up closes"
            ),
            "official_PDH": lv.official_pdh,
            "official_PDL": lv.official_pdl,
            "official_PDC": lv.official_pdc,
        }
        notes = "; ".join(self.problems() + self.warnings() + self._source_notes)
        return {
            "PDH": lv.pdh,
            "PDL": lv.pdl,
            "PDC": lv.pdc,
            "support_zone": [support[0], support[1]] if support else None,
            "resistance_zone": [resistance[0], resistance[1]] if resistance else None,
            "IRH": ic.irh,
            "IRL": ic.irl,
            "EMA200": ic.ema_200,
            "levels_date": self.trading_date.isoformat(),
            "sources": sources,
            "notes": notes,
        }


__all__ = ["LiveReferenceBuilder"]
