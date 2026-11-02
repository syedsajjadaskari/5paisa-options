"""Opening Range Breakout (ORB) Strategy Implementation.

Source Rulebook: ORB_Strategy_Rulebook-2.pdf (13 pages)
Classification: Intraday Trend & Breakout / Breakdown Playbook
Instrument: NIFTY 50 Index (5-min chart)
Decision Time: 09:20 IST, i.e. when the first 5-minute candle (09:15-09:20) has CLOSED.

Bars are OPEN-stamped (``Bar.exchange_time`` is the bar open label), so the first candle of
the session is the bar labelled 09:15 and its close becomes known at 09:20.  The 09:20-labelled
bar is the SECOND candle and is never a decision candle.

Documented Entry Methods:
- Method 1: Previous-Day Level Breakout (Pages 3-4)  <- the method reviewed against the PDF
  Long: Uptrend (price above 200 EMA), first candle breaks & closes above previous-day
  resistance with a strong green body and small upper wick.
  Short: Downtrend (price below 200 EMA), first candle breaks & closes below previous-day
  support with a strong red body and small lower wick.
  Pin bar (Page 10) is only a CONDITIONAL candle: its shape alone is never sufficient.
- Method 2: Trendline & Inner Resistance / Support Breakout (Pages 4-7)  [not PDF-reviewed here]
- Method 3 / Special Setup: Gap Above / Below 200 DMA (Pages 8-9)         [not PDF-reviewed here]

Risk Parameters (PDF p11):
- Stop-Loss: 30, Profit Target: 60 (RR 1:2), mirrored for shorts.  The PDF leaves the UNIT
  open; this implementation interprets them as NIFTY index points (PROVISIONAL, see
  ``configs/strategy_provisional/orb.yaml``).
- The PDF has no end-of-day rule; the 15:15 square-off is an operational rule of the engines.

Every number or definition the PDF does not give (body ratio, wick ratios, pin-bar ratio,
200-EMA basis, level basis, risk unit) lives in ``configs/strategy_provisional/orb.yaml`` and is
PROVISIONAL.  A strategy depending on them must stay SHADOW_ONLY.
"""

from __future__ import annotations

import copy
import math
from datetime import date, datetime, time, timedelta
from typing import Any

from trading_platform.core.enums import SignalDirection, StrategyStatus, Timeframe
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext
from trading_platform.strategies.price_action import (
    get_body_ratio,
    get_lower_wick_ratio,
    get_upper_wick_ratio,
)
from trading_platform.strategies.provisional import load_provisional

FIRST_CANDLE_OPEN = time(9, 15)
CANDLE_MINUTES = 5

# PDF p11 numbers (unit is PROVISIONAL, see yaml ``risk_unit``).
PDF_TARGET_POINTS = 60.0
PDF_STOP_POINTS = 30.0

_PROVISIONAL_KEYS = (
    "min_body_ratio",
    "max_opposing_wick_ratio",
    "pin_bar_min_wick_ratio",
    "pin_bar_requires_directional_close",
    "trend_ema_basis",
    "level_basis",
    "risk_unit",
)

# Dispositions recorded per day (machine readable, shown by the Strategy Monitor).
DISP_NOT_EVALUATED = "AWAITING_FIRST_CANDLE"
DISP_SIGNAL = "SIGNAL"
DISP_NO_SETUP = "NO_SETUP"
DISP_FIRST_BAR_MISSING = "FIRST_BAR_MISSING_OR_LATE"
DISP_MISSING_LEVELS = "MISSING_LEVELS"
DISP_STALE_LEVELS = "STALE_LEVELS"
DISP_EMA_WARMUP = "EMA_WARMUP_INSUFFICIENT"
DISP_DMA_WARMUP = "REJECTED_INSUFFICIENT_DMA_WARMUP"


def _num(value: Any) -> float | None:
    """Return a finite positive float, or None (bool / NaN / non-numeric are 'missing')."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out) or out <= 0:
        return None
    return out


def _day_key(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class ORBStrategy(BaseStrategy):
    """Opening Range Breakout strategy (Method 1 PDF-reviewed; Methods 2/3 legacy, unreviewed)."""

    PROVISIONAL_RULES: tuple[str, ...] = (
        "Trend filter: 'price above/below 200 EMA' read as the 5-minute close-based 200-period EMA "
        "(PDF gives no timeframe); 'bullish/bearish price structure' is not tested (no definition)",
        "Candle quality: strong body = body/range >= 0.50 (PDF gives no number)",
        "Candle quality: small opposing wick = opposing wick/range <= 0.35 (PDF gives no number)",
        "Pin bar (conditional candle): lower/upper wick >= 0.45 of range and must close in the "
        "trade direction (PDF gives no ratio and no exact close test)",
        "Previous-day resistance/support = highest high / lowest low of the previous session's "
        "regular 5-minute candles (wick extreme, single dataset; the official daily high/low is "
        "only a labelled fallback and can differ by 0.5 pt typically and 37-51 pts on a few "
        "days); the PDF screenshots draw a zone, zone edges are not used",
        "SL 30 / TP 60 read as NIFTY index points (PDF p11 leaves the unit to the trader)",
        "15:15 square-off and 14:30 entry cutoff are operational rules, not in the PDF",
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(name="ORB", config=config, status=StrategyStatus.RESEARCH)
        self.decision_time = time(9, 20)  # first candle close; informational
        self._daily_state: dict[str, dict[str, Any]] = {}
        # Levels used for the last day that was evaluated with complete inputs (stale guard).
        self._last_levels: dict[str, Any] | None = None
        self._resolve_config()

    # ------------------------------------------------------------------ config

    def _resolve_config(self) -> None:
        """Resolve PDF numbers + PROVISIONAL values (yaml defaults, explicit config wins)."""
        overrides = {k: v for k, v in self.config.items() if k in _PROVISIONAL_KEYS}
        prov = load_provisional("orb", overrides)
        self.provisional: dict[str, Any] = {k: prov[k] for k in _PROVISIONAL_KEYS if k in prov}
        self.target_points = float(self.config.get("target_points", PDF_TARGET_POINTS))
        self.stop_points = float(self.config.get("stop_points", PDF_STOP_POINTS))
        self.min_body_ratio = float(prov["min_body_ratio"])
        self.max_opposing_wick_ratio = float(prov["max_opposing_wick_ratio"])
        self.pin_bar_min_wick_ratio = float(prov["pin_bar_min_wick_ratio"])
        self.pin_bar_requires_directional_close = bool(prov["pin_bar_requires_directional_close"])
        self.method = str(self.config.get("method", "METHOD_1"))

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        """Re-resolve parameters (PDF numbers + provisional values + overrides)."""
        self.config.update(config)
        self._resolve_config()

    def reset_state(self) -> None:
        """Clear per-day state and the stale-level memory."""
        super().reset_state()
        self._daily_state.clear()
        self._last_levels = None

    # ------------------------------------------------------------------ driving

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        """Process one COMPLETED 5-minute bar (open-stamped) and evaluate ORB."""
        latest = context.latest_bar
        if (
            latest is None
            or latest.exchange_time != bar.exchange_time
            or latest.interval != bar.interval
        ):
            context.add_bar(bar)  # skip if the driver already appended this bar
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        """Evaluate the first candle of the session (the 09:15-labelled bar) exactly once."""
        bar = context.latest_bar
        if bar is None or bar.interval != Timeframe.M5:
            return None

        day = bar.exchange_time.date().isoformat()
        if day in self._daily_state:
            return None  # one decision per day; also covers 'first bar missing' days

        t = bar.exchange_time.time()
        if (t.hour, t.minute) != (FIRST_CANDLE_OPEN.hour, FIRST_CANDLE_OPEN.minute):
            # First bar the strategy sees today is NOT the 09:15 candle: stay silent all day.
            # Never substitute a later bar for the missing first candle.
            self._daily_state[day] = self._new_state(
                day,
                DISP_FIRST_BAR_MISSING,
                note=(
                    f"first bar seen is {t.strftime('%H:%M')}, not 09:15; ORB decides only on the "
                    "09:15 candle so it stays silent today"
                ),
                failed=["FIRST_CANDLE_0915_AVAILABLE"],
            )
            return None

        state = self._new_state(day, DISP_NO_SETUP)
        self._daily_state[day] = state  # day is consumed by the first-candle decision
        state["last_evaluated_candle"] = bar.exchange_time.isoformat()
        state["trigger_candle"] = self._candle_dict(bar)
        state["satisfied"].append("FIRST_CANDLE_0915")

        levels, problem = self._collect_levels(context.state, day)
        state["levels"] = levels
        if problem is not None:
            state["disposition"], state["note"] = problem
            state["failed"].append(problem[0])
            return None

        direction, reason, satisfied, failed = self._evaluate(bar, levels)
        state["satisfied"].extend(satisfied)
        state["failed"].extend(failed)
        self._last_levels = {
            k: levels.get(k) for k in ("pdh", "pdl", "ema_200", "dma_200", "inner_resistance")
        }
        if direction == SignalDirection.HOLD:
            state["note"] = "first candle did not satisfy every rule"
            return None

        signal = self._build_signal(bar, direction, reason, levels, state)
        if not self.validate_signal(signal, context):
            state["disposition"] = DISP_NO_SETUP
            state["note"] = "signal failed sanity validation"
            return None
        state["disposition"] = DISP_SIGNAL
        state["signal_emitted"] = True
        state["direction"] = direction.value
        state["note"] = reason
        return signal

    # ------------------------------------------------------------------ inputs

    def _required_keys(self) -> list[str]:
        if self.method == "METHOD_1":
            return ["pdh", "pdl", "ema_200"]
        if self.method == "METHOD_2":
            return ["ema_200"]
        if self.method in ("METHOD_3", "GAP_200_DMA"):
            return ["dma_200", "prev_close"]
        return []

    def _collect_levels(
        self, ctx_state: dict[str, Any], day: str
    ) -> tuple[dict[str, Any], tuple[str, str] | None]:
        """Read the point-in-time levels; return (levels, (disposition, note) problem or None)."""
        keys = ["pdh", "pdl", "prev_close", "ema_200", "dma_200"]
        if self.method == "METHOD_2":
            keys += ["inner_resistance", "inner_support"]
        levels: dict[str, Any] = {k: _num(ctx_state.get(k)) for k in keys}
        levels["level_basis"] = self.provisional.get("level_basis")
        levels["trend_ema_basis"] = self.provisional.get("trend_ema_basis")

        required = self._required_keys()
        missing = [k for k in required if levels.get(k) is None]
        if missing:
            if self.method in ("METHOD_3", "GAP_200_DMA") and "dma_200" in missing:
                return levels, (DISP_DMA_WARMUP, "200 DMA unavailable")
            if missing == ["ema_200"] and ctx_state.get("has_sufficient_ema_warmup") is False:
                return levels, (DISP_EMA_WARMUP, "200 EMA warm-up not complete")
            return levels, (DISP_MISSING_LEVELS, "missing levels: " + ",".join(missing))
        if self.method == "METHOD_1" and levels["pdh"] < levels["pdl"]:
            return levels, (DISP_MISSING_LEVELS, "inconsistent levels: PDH below PDL")

        levels_date = ctx_state.get("levels_date")
        if levels_date is not None and _day_key(levels_date) != day:
            return levels, (
                DISP_STALE_LEVELS,
                f"levels are for {_day_key(levels_date)}, not {day}",
            )
        prev = self._last_levels
        if prev is not None and self._looks_stale(levels, prev):
            return levels, (
                DISP_STALE_LEVELS,
                "levels identical to a previous day's (not refreshed for today)",
            )
        return levels, None

    def _looks_stale(self, levels: dict[str, Any], prev: dict[str, Any]) -> bool:
        """A refreshed day cannot have the same PDH+PDL pair, nor the same EMA value, as the
        previous evaluated day; identical values mean the caller left yesterday's state in place."""
        if self.method == "METHOD_1":
            if levels["pdh"] == prev.get("pdh") and levels["pdl"] == prev.get("pdl"):
                return True
        ema = levels.get("ema_200")
        return ema is not None and prev.get("ema_200") is not None and ema == prev["ema_200"]

    # ------------------------------------------------------------------ rules

    def _candle_quality(self, bar: Bar, side: str) -> tuple[bool, list[str], list[str]]:
        """PDF p9-10 candle test for 'long' or 'short' (thresholds PROVISIONAL)."""
        sat: list[str] = []
        fail: list[str] = []
        body = get_body_ratio(bar)
        up = get_upper_wick_ratio(bar)
        low = get_lower_wick_ratio(bar)
        opposing = up if side == "long" else low
        rejection = low if side == "long" else up
        colour_ok = bar.close > bar.open if side == "long" else bar.close < bar.open
        wick_name = "UPPER" if side == "long" else "LOWER"
        wick_ok = opposing <= self.max_opposing_wick_ratio
        body_ok = body >= self.min_body_ratio
        pin_ok = rejection >= self.pin_bar_min_wick_ratio
        if self.pin_bar_requires_directional_close:
            pin_ok = pin_ok and colour_ok

        (sat if colour_ok else fail).append("GREEN_CANDLE" if side == "long" else "RED_CANDLE")
        (sat if wick_ok else fail).append(f"SMALL_{wick_name}_WICK")
        if body_ok:
            sat.append("STRONG_BODY")
        elif pin_ok:
            sat.append("PIN_BAR_CONDITIONAL")
        else:
            fail.append("STRONG_BODY_OR_PIN_BAR")
        ok = wick_ok and (body_ok and colour_ok or pin_ok)
        return ok, sat, fail

    def _evaluate(
        self, bar: Bar, levels: dict[str, Any]
    ) -> tuple[SignalDirection, str, list[str], list[str]]:
        """Return (direction, reason, conditions_satisfied, conditions_failed) for the candle."""
        sat: list[str] = []
        fail: list[str] = []
        close = bar.close
        ema = levels.get("ema_200")

        def trend(side: str) -> bool:
            if ema is None:
                fail.append("EMA_200_AVAILABLE")
                return False
            ok = close > ema if side == "long" else close < ema
            word = "ABOVE" if side == "long" else "BELOW"
            (sat if ok else fail).append(f"TREND_CLOSE_{word}_EMA200(PROVISIONAL)")
            return ok

        def breakout(
            side: str, level: float | None, name: str, reason: str
        ) -> tuple[SignalDirection, str]:
            hold = (SignalDirection.HOLD, "NO_SETUP")
            assert level is not None
            word = "ABOVE" if side == "long" else "BELOW"
            broke = close > level if side == "long" else close < level
            (sat if broke else fail).append(f"CLOSE_{word}_{name}")
            if not broke:
                return hold
            candle_ok, cs, cf = self._candle_quality(bar, side)
            sat.extend(cs)
            fail.extend(cf)
            trend_ok = trend(side)  # always evaluated so the monitor can show it
            if not (candle_ok and trend_ok):
                return hold
            return (SignalDirection.BUY if side == "long" else SignalDirection.SELL), reason

        if self.method == "METHOD_1":
            pdh, pdl = levels["pdh"], levels["pdl"]
            if close > pdh:
                d, r = breakout("long", pdh, "PDH", "ORB_METHOD_1_PDH_BREAKOUT")
            elif close < pdl:
                d, r = breakout("short", pdl, "PDL", "ORB_METHOD_1_PDL_BREAKDOWN")
            else:
                fail.extend(["CLOSE_ABOVE_PDH", "CLOSE_BELOW_PDL"])
                d, r = SignalDirection.HOLD, "NO_SETUP"
            return d, r, sat, fail

        if self.method == "METHOD_2":
            res, sup = levels.get("inner_resistance"), levels.get("inner_support")
            if res is not None and close > res:
                d, r = breakout(
                    "long", res, "INNER_RESISTANCE", "ORB_METHOD_2_INNER_RESISTANCE_BREAKOUT"
                )
            elif sup is not None and close < sup:
                d, r = breakout(
                    "short", sup, "INNER_SUPPORT", "ORB_METHOD_2_INNER_SUPPORT_BREAKDOWN"
                )
            else:
                fail.append("CLOSE_BEYOND_INNER_LEVEL")
                d, r = SignalDirection.HOLD, "NO_SETUP"
            return d, r, sat, fail

        if self.method in ("METHOD_3", "GAP_200_DMA"):
            dma, prev_close = levels["dma_200"], levels["prev_close"]
            if prev_close < dma and bar.open >= dma and close > dma:
                ok, cs, cf = self._candle_quality(bar, "long")
                sat.extend(["GAP_ABOVE_200DMA", *cs])
                fail.extend(cf)
                if ok:
                    return SignalDirection.BUY, "ORB_METHOD_3_GAP_ABOVE_200DMA", sat, fail
            elif prev_close > dma and bar.open <= dma and close < dma:
                ok, cs, cf = self._candle_quality(bar, "short")
                sat.extend(["GAP_BELOW_200DMA", *cs])
                fail.extend(cf)
                if ok:
                    return SignalDirection.SELL, "ORB_METHOD_3_GAP_BELOW_200DMA", sat, fail
            else:
                fail.append("GAP_THROUGH_200DMA_AND_CLOSE_BEYOND")
            return SignalDirection.HOLD, "NO_SETUP", sat, fail

        fail.append(f"UNKNOWN_METHOD_{self.method}")
        return SignalDirection.HOLD, "NO_SETUP", sat, fail

    # ------------------------------------------------------------------ output

    @staticmethod
    def _candle_dict(bar: Bar) -> dict[str, Any]:
        return {
            "time": bar.exchange_time.isoformat(),
            "o": bar.open,
            "h": bar.high,
            "l": bar.low,
            "c": bar.close,
        }

    def _new_state(
        self,
        day: str,
        disposition: str,
        note: str = "",
        failed: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "day": day,
            "disposition": disposition,
            "note": note,
            "satisfied": [],
            "failed": list(failed or []),
            "levels": {},
            "trigger_candle": None,
            "last_evaluated_candle": None,
            "signal_emitted": False,
            "direction": None,
        }

    def _build_signal(
        self,
        bar: Bar,
        direction: SignalDirection,
        reason: str,
        levels: dict[str, Any],
        state: dict[str, Any],
    ) -> Signal:
        ref = bar.close
        sign = 1.0 if direction == SignalDirection.BUY else -1.0
        decision = bar.exchange_time + timedelta(minutes=CANDLE_MINUTES)
        return Signal(
            strategy_name=self.name,
            method_id=reason,
            symbol=bar.symbol,
            direction=direction,
            timestamp=bar.exchange_time,
            reference_price=ref,
            stop_loss=round(ref - sign * self.stop_points, 2),
            target=round(ref + sign * self.target_points, 2),
            reason=reason,
            metadata={
                "method": reason,
                "target_points": self.target_points,
                "stop_points": self.stop_points,
                "risk_unit": self.provisional.get("risk_unit"),
                "conditions_satisfied": list(state["satisfied"]),
                "conditions_failed": list(state["failed"]),
                "levels": {k: v for k, v in levels.items() if v is not None},
                "trigger_candle": dict(state["trigger_candle"]),
                "bar_open_time": bar.exchange_time.isoformat(),
                "decision_time": decision.isoformat(),
                "provisional_rules": list(self.PROVISIONAL_RULES),
            },
        )

    # ------------------------------------------------------------------ monitor

    def evaluation_snapshot(
        self, day_key: str | date | datetime, context: StrategyContext | None = None
    ) -> dict[str, Any]:
        """Read-only view for the Strategy Monitor.  Never mutates strategy or context state.

        ``context`` (optional) lets the snapshot show the levels currently supplied for a day
        that has not been evaluated yet.
        """
        day = _day_key(day_key)
        base: dict[str, Any] = {
            "strategy": self.name,
            "method": self.method,
            "day": day,
            "provisional_rules": list(self.PROVISIONAL_RULES),
        }
        st = self._daily_state.get(day)
        if st is not None:
            blocked = (
                DISP_MISSING_LEVELS,
                DISP_STALE_LEVELS,
                DISP_EMA_WARMUP,
                DISP_FIRST_BAR_MISSING,
            )
            run_state = "BLOCKED" if st["disposition"] in blocked else "DONE_FOR_DAY"
            return base | copy.deepcopy(
                {
                    "state": run_state,
                    "evaluated": st["last_evaluated_candle"] is not None,
                    "disposition": st["disposition"],
                    "note": st["note"],
                    "last_evaluated_candle": st["last_evaluated_candle"],
                    "levels": st["levels"],
                    "conditions_satisfied": st["satisfied"],
                    "conditions_failed": st["failed"],
                    "trigger_candle": st["trigger_candle"],
                    "signal_emitted": st["signal_emitted"],
                    "direction": st["direction"],
                }
            )

        levels: dict[str, Any] = {}
        failed: list[str] = []
        run_state = "MONITORING"
        note = "waiting for the 09:15 candle to close (decision at 09:20)"
        if context is not None:
            levels, problem = self._collect_levels(context.state, day)
            if problem is not None:
                failed.append(problem[0])
                run_state = "WARMING_UP"
                note = problem[1]
        return base | {
            "state": run_state,
            "evaluated": False,
            "disposition": DISP_NOT_EVALUATED,
            "note": note,
            "last_evaluated_candle": None,
            "levels": levels,
            "conditions_satisfied": [],
            "conditions_failed": failed,
            "trigger_candle": None,
            "signal_emitted": False,
            "direction": None,
        }
