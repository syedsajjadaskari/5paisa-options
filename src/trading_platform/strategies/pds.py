"""Previous Day Support (PDS) strategy - bearish role-reversal short (buy PE on the options layer).

Source rulebook: ``docs/strategy_rulebooks/PDS strategy.pdf`` (NIFTY 50, 5-minute chart).

PDF flow implemented here (one COMPLETED 5m bar at a time)::

    previous-day support zone (candle body + lower wick of the day's low)
      -> next session opens GAP DOWN, trading BELOW the zone
      -> price RISES from below and tests the zone (wick / body / inside / "very close")
      -> the FIRST RED candle at the zone decides:
           Method 1: red candle with a good body              -> short
           Method 2: red pin bar with higher-price-rejection  -> short
           weak first red candle                              -> Method 3: arm a small support,
                                                                 short when it breaks
      -> target 70 points, stop 35 or 40 points (distance of the recent green candle)

Bar convention: ``Bar.exchange_time`` is the bar-OPEN label (09:15 = the 09:15-09:20 candle) and
the signal is only known at bar close (``open + 5min``; see ``Signal.metadata["decision_time"]``).

Levels are injected by the caller into ``context.state`` (``pdl``, ``prev_close``,
``pd_supp_body``; optional ``pd_supp_body_top`` = upper body edge of the support candle and
``levels_date`` = ISO date the levels belong to).  Levels are frozen per trading day when first
read; a missing / non-numeric / stale level means NO signal for that day.

Everything the PDF leaves undefined is a PROVISIONAL value read from
``configs/strategy_provisional/pds.yaml`` (see :data:`PDSStrategy.PROVISIONAL_RULES`).  While
any of them is in play the strategy is not author-approved and must stay SHADOW_ONLY.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from trading_platform.core.enums import SignalDirection, StrategyStatus
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext
from trading_platform.strategies.price_action import (
    get_body_ratio,
    get_upper_wick_ratio,
    is_hpr_pin_bar,
    is_strong_body_candle,
)
from trading_platform.strategies.provisional import load_provisional

SESSION_FIRST_BAR = time(9, 15)
SESSION_LAST_BAR = time(15, 25)  # last genuine 5m bar-open label; later labels are ignored
_BAR_MINUTES = 5
_KEEP_DAYS = 5

M1_ID = "PDS_METHOD_1_STRONG_RED_CANDLE"
M2_ID = "PDS_METHOD_2_HPR_PIN_BAR"
M3_ID = "PDS_METHOD_3_SMALL_SUPPORT_BREAK"

# Check names (kept short: they are shown verbatim on the dashboard Strategy Monitor).
C_LEVELS = "levels valid for today"
C_FIRST_BAR = "09:15 bar present"
C_GAP = "gap down (open < prev close)"
C_BELOW = "open below zone"
C_TESTED = "zone tested from below"
C_FIRST_RED = "first red candle at zone"
C_M1 = "M1 good red body"
C_M2 = "M2 red HPR pin bar"
C_M3_ARMED = "M3 small support armed"
C_M3_BREAK = "M3 small support broken"
C_ZONE_OK = "zone not failed (no close above)"


@dataclass
class _DayState:
    """Mutable per-trading-day state machine for one PDS instance."""

    day: date
    levels: dict[str, float] | None = None
    levels_reason: str = "LEVELS_NOT_READ"
    first_bar_time: time | None = None
    gate_passed: bool = False
    terminal: str | None = None  # NO_FIRST_BAR | NO_GAP_DOWN | OPEN_NOT_BELOW_ZONE | ZONE_FAILED
    terminal_reason: str = ""
    day_open: float | None = None
    bars: list[Bar] = field(default_factory=list)
    last_bar_time: datetime | None = None
    zone_reached: bool = False
    first_red: dict[str, Any] | None = None
    small_support: float | None = None
    signals_emitted: int = 0
    missed_signals: list[dict[str, Any]] = field(default_factory=list)
    missing_bars: int = 0
    last_signal: dict[str, Any] | None = None
    checks: dict[str, bool | None] = field(default_factory=dict)


class PDSStrategy(BaseStrategy):
    """Previous Day Support (PDS) short strategy, drivable one completed 5m bar at a time."""

    PROVISIONAL_RULES: tuple[str, ...] = (
        "Good red candle = body/range >= min_body_ratio (PDF gives no numeric definition)",
        "HPR pin bar = red close with upper wick/range >= pin_bar_wick_ratio (PDF gives no "
        "wick:body ratio)",
        "'Very close' zone tolerance zone_proximity_pts (PDF gives no number)",
        "Zone = [pdl, top of the body of the first 5m candle that made the previous session's "
        "low]: lower wick plus candle body, all taken from the previous session's regular 5m "
        "candles (single dataset; the official daily low is only a labelled fallback that "
        "carries no body). Strategy-level fallback when no body top is supplied: "
        "[pdl, pd_supp_body]",
        "Open must be below the zone low (reading of 'after the open price is trading below the "
        "zone'); a gap down that opens inside/above the zone is skipped (68 of the 116 gap-down "
        "days in the history are skipped by this gate; see docs/AUTHOR_CONFIRMATION_REQUEST.md)",
        "The first red candle at the zone is the only one judged for Methods 1 and 2: if it is "
        "weak (fails the body / pin-bar test) it consumes the approach and a later strong red "
        "candle at the same approach is not taken unless Method 3 is enabled (this alone costs "
        "the 2025-12-26 manual-journal day under METHOD_1)",
        "Opening 09:15 candle can never be the test/decision candle",
        "Method 3 small support = low of the weak first red candle (one candle); break = candle "
        "close below it (support_break_mode); armed only after a weak first red candle",
        "A close above the zone (+ proximity) invalidates the setup for the day",
        "After price leaves the zone downward a new rise to the zone starts a new approach "
        "(first red candle is judged again)",
        "Stop distance measure = recent green candle HIGH minus signal-candle close, searched in "
        "today's earlier bars; no green candle => 35 points; exactly 35 => 35",
        "At most max_signals_per_day (1) signal per day",
        "Entry on the completed signal candle (PDF entry timing open); 14:30 entry cutoff and "
        "15:15 square-off are platform conventions, not PDF rules",
    )

    METHODS = {
        "METHOD_1": frozenset({1}),
        "METHOD_2": frozenset({2}),
        "METHOD_3": frozenset({3}),
        "ALL": frozenset({1, 2, 3}),
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(name="PDS", config=config, status=StrategyStatus.RESEARCH)
        self._daily_state: dict[str, _DayState] = {}
        self._configure()

    # ------------------------------------------------------------------ configuration

    def _configure(self) -> None:
        """Resolve PDF constants and PROVISIONAL values (yaml, overridden by ``self.config``)."""
        params = load_provisional("pds", overrides=dict(self.config))
        # PDF numbers (fixed by the author).
        self.target_points = float(params.get("target_points", 70.0))
        self.default_stop_points = float(params.get("default_stop_points", 35.0))
        self.max_stop_points = float(params.get("max_stop_points", 40.0))
        # PROVISIONAL values (configs/strategy_provisional/pds.yaml).
        self.min_body_ratio = float(params["min_body_ratio"])
        self.pin_bar_wick_ratio = float(params["pin_bar_wick_ratio"])
        self.zone_proximity_pts = float(params["zone_proximity_pts"])
        self.support_break_mode = str(params["support_break_mode"]).lower()
        if self.support_break_mode not in ("close", "touch"):
            raise ValueError("support_break_mode must be 'close' or 'touch'")
        self.green_distance_threshold = float(params["green_candle_distance_threshold_pts"])
        self.max_signals_per_day = int(params["max_signals_per_day"])
        self.allow_opening_bar_entry = bool(params["allow_opening_bar_entry"])
        self.require_open_below_zone = bool(params["require_open_below_zone"])
        self.invalidate_on_close_above_zone = bool(params["invalidate_on_close_above_zone"])
        method = str(params.get("method", "METHOD_1")).upper()
        if method not in self.METHODS:
            raise ValueError(f"unknown PDS method {method!r}; expected one of {sorted(self.METHODS)}")
        self.method = method
        self.enabled_methods = self.METHODS[method]

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        """Re-resolve parameters (PDF constants + PROVISIONAL values) after a config change."""
        self.config.update(config)
        self._configure()

    def reset_state(self) -> None:
        """Forget every per-day state (new run / new backtest)."""
        super().reset_state()
        self._daily_state.clear()

    # ------------------------------------------------------------------ driving

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        """Add the completed 5m bar to the context and evaluate PDS."""
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        """Advance today's state machine up to ``context.latest_bar`` and return a new signal.

        Earlier same-day bars that reached the context without an ``on_bar`` call (for example
        while a position was open, or after a restart that replays history) are folded into the
        state first and can never emit.  Calling this twice for the same bar emits nothing.
        """
        latest = context.latest_bar
        if latest is None or not self._is_session_bar(latest):
            return None
        day = latest.exchange_time.date()
        st = self._day_state(day)

        pending = self._pending_bars(context, st, day)
        if not pending:
            return None
        if st.levels is None:
            levels, reason = self._read_levels(context.state, day)
            if levels is None:
                st.levels_reason = reason
                st.checks[C_LEVELS] = False
                return None
            st.levels = levels
            st.levels_reason = "OK"
            st.checks[C_LEVELS] = True
        elif st.terminal is None and self._level_drift(st.levels, context.state, day):
            # Levels changed under us mid-day: refuse to trade on an inconsistent picture.
            self._stop_day(
                st, "LEVELS_CHANGED", "context levels changed after the day's levels were frozen"
            )
            st.checks[C_LEVELS] = False

        signal: Signal | None = None
        for bar in pending:
            is_latest = bar.exchange_time == latest.exchange_time
            produced = self._step(st, bar, emit=is_latest, context=context)
            if is_latest:
                signal = produced
        return signal

    # ------------------------------------------------------------------ read-only snapshot

    def evaluation_snapshot(
        self, day_key: str | date, context: StrategyContext | None = None
    ) -> dict[str, Any]:
        """Read-only view of today's evaluation for the live Strategy Monitor (never mutates).

        ``context`` is optional; when given and the day has not been evaluated yet it is only
        used to preview the injected reference levels.
        """
        day = day_key if isinstance(day_key, date) else date.fromisoformat(str(day_key))
        st = self._daily_state.get(day.isoformat())
        snap: dict[str, Any] = {
            "strategy": self.name,
            "day": day.isoformat(),
            "method": self.method,
            "provisional_rules": list(self.PROVISIONAL_RULES),
            "shadow_only_reason": "PDS depends on PROVISIONAL rules (see provisional_rules)",
        }
        if st is None:
            levels, reason = (None, "NO_CONTEXT")
            if context is not None:
                levels, reason = self._read_levels(context.state, day)
            snap.update(
                state="WARMING_UP" if levels is None else "MONITORING",
                state_reason=reason if levels is None else "levels ready, no bar evaluated yet",
                conditions_satisfied=[],
                conditions_failed=[],
                levels=self._levels_view(levels),
                signals_emitted=0,
                last_bar_time=None,
                trigger_candle=None,
            )
            return snap
        state, why = self._state_name(st)
        satisfied = [k for k, v in st.checks.items() if v is True]
        failed = [k for k, v in st.checks.items() if v is False]
        snap.update(
            state=state,
            state_reason=why,
            conditions_satisfied=satisfied,
            conditions_failed=failed,
            conditions_pending=[k for k, v in st.checks.items() if v is None],
            levels=self._levels_view(st.levels),
            day_open=st.day_open,
            signals_emitted=st.signals_emitted,
            last_bar_time=st.last_bar_time.isoformat() if st.last_bar_time else None,
            first_bar_time=st.first_bar_time.strftime("%H:%M") if st.first_bar_time else None,
            zone_reached=st.zone_reached,
            small_support=st.small_support,
            first_red_candle=dict(st.first_red) if st.first_red else None,
            trigger_candle=dict(st.last_signal["trigger_candle"]) if st.last_signal else None,
            last_signal=dict(st.last_signal) if st.last_signal else None,
            missed_signals=[dict(m) for m in st.missed_signals],
            missing_bars=st.missing_bars,
        )
        return snap

    # ------------------------------------------------------------------ levels

    @staticmethod
    def _num(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        v = float(value)
        return v if math.isfinite(v) and v > 0 else None

    def _read_levels(
        self, state: dict[str, Any], day: date
    ) -> tuple[dict[str, float] | None, str]:
        """Validate the injected levels for ``day``.  Never raises, never mutates anything."""
        pdl = self._num(state.get("pdl"))
        prev_close = self._num(state.get("prev_close"))
        body = self._num(state.get("pd_supp_body"))
        missing = [
            name
            for name, v in (("pdl", pdl), ("prev_close", prev_close), ("pd_supp_body", body))
            if v is None
        ]
        if missing:
            return None, f"LEVELS_MISSING: {', '.join(missing)}"
        assert pdl is not None and prev_close is not None and body is not None
        levels_date = state.get("levels_date")
        if levels_date is not None:
            tag = levels_date.isoformat() if hasattr(levels_date, "isoformat") else str(levels_date)
            if tag[:10] != day.isoformat():
                return None, f"LEVELS_STALE: levels_date {tag[:10]} != {day.isoformat()}"
        top_raw = state.get("pd_supp_body_top")
        top = self._num(top_raw) if top_raw is not None else None
        zone_low = min(pdl, body)
        zone_top = max(pdl, body, top if top is not None else 0.0)
        levels = {
            "pdl": pdl,
            "prev_close": prev_close,
            "pd_supp_body": body,
            "zone_low": zone_low,
            "zone_top": zone_top,
        }
        if top is not None:
            levels["pd_supp_body_top"] = top
        signature = (pdl, prev_close, body, top)
        for key, other in self._daily_state.items():
            if key < day.isoformat() and other.levels is not None:
                prior = other.levels
                prior_sig = (
                    prior["pdl"],
                    prior["prev_close"],
                    prior["pd_supp_body"],
                    prior.get("pd_supp_body_top"),
                )
                if prior_sig == signature and levels_date is None:
                    return None, "LEVELS_STALE: identical to the previous day's levels"
        return levels, "OK"

    def _level_drift(self, frozen: dict[str, float], state: dict[str, Any], day: date) -> bool:
        """True when the context now carries different levels than the ones frozen for ``day``."""
        now, _ = self._read_levels(state, day)
        if now is None:
            return True
        return any(abs(now[k] - frozen[k]) > 1e-9 for k in ("pdl", "prev_close", "pd_supp_body"))

    @staticmethod
    def _levels_view(levels: dict[str, float] | None) -> dict[str, Any]:
        if levels is None:
            return {}
        view: dict[str, Any] = dict(levels)
        view["zone_width"] = round(levels["zone_top"] - levels["zone_low"], 2)
        return view

    # ------------------------------------------------------------------ state helpers

    def _day_state(self, day: date) -> _DayState:
        key = day.isoformat()
        if key not in self._daily_state:
            self._daily_state[key] = _DayState(day=day)
            for old in sorted(self._daily_state)[:-_KEEP_DAYS]:
                del self._daily_state[old]
        return self._daily_state[key]

    @staticmethod
    def _is_session_bar(bar: Bar) -> bool:
        return SESSION_FIRST_BAR <= bar.exchange_time.time() <= SESSION_LAST_BAR

    def _pending_bars(self, context: StrategyContext, st: _DayState, day: date) -> list[Bar]:
        """Today's session bars not yet folded into the state, oldest first."""
        out: list[Bar] = []
        for bar in reversed(context.historical_bars):
            if bar.exchange_time.date() != day:
                break
            if st.last_bar_time is not None and bar.exchange_time <= st.last_bar_time:
                break
            if self._is_session_bar(bar):
                out.append(bar)
        out.reverse()
        return out

    def _state_name(self, st: _DayState) -> tuple[str, str]:
        """(StrategyRunState-compatible state, human reason)."""
        if st.levels is None:
            return "WARMING_UP", st.levels_reason
        if st.terminal == "LEVELS_CHANGED":
            return "BLOCKED", f"{st.terminal}: {st.terminal_reason}"
        if st.terminal is not None:
            return "DONE_FOR_DAY", f"{st.terminal}: {st.terminal_reason}"
        if st.signals_emitted >= self.max_signals_per_day:
            return "DONE_FOR_DAY", "signal limit for the day reached"
        if not st.gate_passed:
            return "MONITORING", "waiting for the 09:15 candle to close"
        if st.small_support is not None:
            return "MONITORING", f"M3 small support armed at {st.small_support}"
        if st.first_red is not None:
            return "MONITORING", "first red candle at the zone did not qualify; waiting for a new approach"
        if st.zone_reached:
            return "MONITORING", "zone tested from below; waiting for the first red candle"
        return "MONITORING", "below the zone; waiting for a rise into the zone"

    # ------------------------------------------------------------------ state machine

    def _step(
        self, st: _DayState, bar: Bar, *, emit: bool, context: StrategyContext
    ) -> Signal | None:
        """Fold one completed bar into the state; return a Signal only when ``emit``."""
        if st.last_bar_time is not None:
            gap = (bar.exchange_time - st.last_bar_time) / timedelta(minutes=_BAR_MINUTES)
            if gap > 1.5:
                st.missing_bars += int(round(gap)) - 1
        st.last_bar_time = bar.exchange_time
        levels = st.levels
        assert levels is not None
        t = bar.exchange_time.time()

        if st.terminal is not None:
            return None

        first_bar = not st.bars
        st.bars.append(bar)

        # 1. Gate on the first bar of the day: it must be the 09:15 candle (no substitution).
        if first_bar:
            st.first_bar_time = t
            st.day_open = bar.open
            if t != SESSION_FIRST_BAR:
                st.checks[C_FIRST_BAR] = False
                self._stop_day(st, "NO_FIRST_BAR", f"first bar seen is {t:%H:%M}, not 09:15")
                return None
            st.checks[C_FIRST_BAR] = True
            gap_down = bar.open < levels["prev_close"]
            st.checks[C_GAP] = gap_down
            if not gap_down:
                self._stop_day(
                    st, "NO_GAP_DOWN", f"open {bar.open} >= prev close {levels['prev_close']}"
                )
                return None
            below = bar.open < levels["zone_low"]
            st.checks[C_BELOW] = below
            if self.require_open_below_zone and not below:
                self._stop_day(
                    st,
                    "OPEN_NOT_BELOW_ZONE",
                    f"open {bar.open} not below zone low {levels['zone_low']}",
                )
                return None
            st.gate_passed = True
        if not st.gate_passed:
            return None
        if st.signals_emitted >= self.max_signals_per_day:
            return None

        zone_low, zone_top, prox = levels["zone_low"], levels["zone_top"], self.zone_proximity_pts
        if first_bar and not self.allow_opening_bar_entry:
            self._zone_failure_check(st, bar, zone_top, prox)
            return None

        overlaps = bar.high >= zone_low - prox and bar.low <= zone_top + prox
        is_red = bar.close < bar.open
        found: tuple[str, dict[str, Any]] | None = None

        # 2. Method 3: armed small support breaks.
        if st.small_support is not None:
            broken = (
                bar.close < st.small_support
                if self.support_break_mode == "close"
                else bar.low < st.small_support
            )
            st.checks[C_M3_BREAK] = broken
            if broken:
                found = (M3_ID, {"small_support": st.small_support})

        # 3. First red candle at the zone: Methods 1 / 2 or arm Method 3.
        if found is None and overlaps:
            st.zone_reached = True
            st.checks[C_TESTED] = True
            if st.first_red is None and is_red:
                found = self._judge_first_red(st, bar, zone_low, prox)

        if found is not None:
            return self._emit(st, bar, found, emit=emit, context=context)

        # 4. No signal on this bar: zone failure / new approach bookkeeping.
        if self._zone_failure_check(st, bar, zone_top, prox):
            return None
        if bar.high < zone_low - prox and (
            st.zone_reached or st.first_red is not None or st.small_support is not None
        ):
            # Price left the zone downward: a later rise to the zone is a NEW approach.
            st.zone_reached = False
            st.first_red = None
            st.small_support = None
            for name in (C_TESTED, C_FIRST_RED, C_M1, C_M2, C_M3_ARMED, C_M3_BREAK):
                st.checks[name] = None
        return None

    def _judge_first_red(
        self, st: _DayState, bar: Bar, zone_low: float, prox: float
    ) -> tuple[str, dict[str, Any]] | None:
        """Evaluate the first red candle at the zone (Methods 1/2, or arm Method 3)."""
        body_ok = is_strong_body_candle(bar, self.min_body_ratio)
        pin_ok = is_hpr_pin_bar(bar, self.pin_bar_wick_ratio) and bar.high >= zone_low - prox
        st.first_red = self._candle(bar)
        st.checks[C_FIRST_RED] = True
        st.checks[C_M1] = body_ok
        st.checks[C_M2] = pin_ok
        extra = {
            "body_ratio": round(get_body_ratio(bar), 4),
            "upper_wick_ratio": round(get_upper_wick_ratio(bar), 4),
        }
        if 1 in self.enabled_methods and body_ok:
            return M1_ID, extra
        if 2 in self.enabled_methods and pin_ok:
            return M2_ID, extra
        if not body_ok and not pin_ok and 3 in self.enabled_methods:
            st.small_support = bar.low
            st.checks[C_M3_ARMED] = True
            st.checks[C_M3_BREAK] = None
        return None

    def _zone_failure_check(self, st: _DayState, bar: Bar, zone_top: float, prox: float) -> bool:
        """Close above the zone => it failed as resistance for the day.  True if triggered."""
        if not self.invalidate_on_close_above_zone:
            st.checks[C_ZONE_OK] = True
            return False
        if bar.close > zone_top + prox:
            st.checks[C_ZONE_OK] = False
            self._stop_day(
                st, "ZONE_FAILED", f"close {bar.close} above zone top {zone_top} + {prox}"
            )
            return True
        st.checks[C_ZONE_OK] = True
        return False

    @staticmethod
    def _stop_day(st: _DayState, code: str, reason: str) -> None:
        st.terminal = code
        st.terminal_reason = reason

    # ------------------------------------------------------------------ signal construction

    @staticmethod
    def _candle(bar: Bar) -> dict[str, Any]:
        return {
            "o": bar.open,
            "h": bar.high,
            "l": bar.low,
            "c": bar.close,
            "time": bar.exchange_time.isoformat(),
            "close_time": (bar.exchange_time + timedelta(minutes=_BAR_MINUTES)).isoformat(),
        }

    def _stop_points(self, st: _DayState, ref_price: float) -> tuple[float, float | None]:
        """35/40 point stop from the recent green candle's distance (today's earlier bars)."""
        recent_green_high = None
        for earlier in reversed(st.bars[:-1]):
            if earlier.close > earlier.open:
                recent_green_high = earlier.high
                break
        if (
            recent_green_high is not None
            and (recent_green_high - ref_price) > self.green_distance_threshold
        ):
            return self.max_stop_points, recent_green_high
        return self.default_stop_points, recent_green_high

    def _emit(
        self,
        st: _DayState,
        bar: Bar,
        found: tuple[str, dict[str, Any]],
        *,
        emit: bool,
        context: StrategyContext,
    ) -> Signal | None:
        entry_reason, extra = found
        levels = st.levels
        assert levels is not None
        ref_price = bar.close
        stop_points, green_high = self._stop_points(st, ref_price)
        trigger = self._candle(bar)

        checks = dict(st.checks)
        if entry_reason == M3_ID:
            checks[C_M3_BREAK] = True
        # The other first-candle method is an alternative, not a failed requirement.
        alternative = {M1_ID: C_M2, M2_ID: C_M1}.get(entry_reason)
        satisfied = [k for k, v in checks.items() if v is True]
        failed = [k for k, v in checks.items() if v is False and k != alternative]

        metadata: dict[str, Any] = {
            "method": entry_reason,
            "target_points": self.target_points,
            "stop_points": stop_points,
            "conditions_satisfied": satisfied,
            "conditions_failed": failed,
            "levels": self._levels_view(levels),
            "trigger_candle": trigger,
            "decision_time": trigger["close_time"],
            "first_red_candle": dict(st.first_red) if st.first_red else None,
            "recent_green_high": green_high,
            "day_open": st.day_open,
            "provisional_rules": list(self.PROVISIONAL_RULES),
            "shadow_only_reason": "depends on PROVISIONAL rules; not author-approved",
            **extra,
        }
        signal = Signal(
            strategy_name=self.name,
            method_id=entry_reason,
            symbol=bar.symbol,
            direction=SignalDirection.SELL,
            timestamp=bar.exchange_time,
            reference_price=ref_price,
            stop_loss=round(ref_price + stop_points, 2),
            target=round(ref_price - self.target_points, 2),
            reason=entry_reason,
            metadata=metadata,
        )
        if not self.validate_signal(signal, context):
            self._stop_day(st, "INVALID_SIGNAL", "signal failed validate_signal")
            return None
        st.signals_emitted += 1
        st.last_signal = {
            "method_id": entry_reason,
            "trigger_candle": trigger,
            "reference_price": ref_price,
            "stop_points": stop_points,
            "target_points": self.target_points,
            "emitted": emit,
        }
        if not emit:
            st.missed_signals.append(
                {"method_id": entry_reason, "trigger_candle": trigger, "reason": "CATCH_UP_NOT_EMITTED"}
            )
            return None
        return signal
