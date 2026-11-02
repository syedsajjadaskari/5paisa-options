"""Initial Range Failure (IRF) Strategy Implementation.

Source Rulebook: IRF_Strategy_Guide Updated.pdf (17 pages)
Classification: Intraday Short-Selling Price-Action Framework
Instrument: NIFTY 50 Index (5-min chart)
Trading Direction: Short Trades ONLY

Documented Entry Methods:
- Method 1: Breakout and Immediate Failure (Pages 5-6)
  C1 closes > IRH; immediate next candle C2 closes < IRH.
- Method 2: Extended Upward Move and Reversal (Pages 7-10)
  A candle closes above IRH, price extends upward, a red candle forms, and a relevant recent
  level breaks.  Two equally valid confirmations (pages 8-9):
  * Confirmation A (Support Break): a candle closes below recent support (a pullback low).
  * Confirmation B (Resistance Break): price pushes back above recent resistance (the top of the
    first push) and a red candle then closes back below it.
  If the candle right after the breakout closes back inside the range the setup is Method 1,
  NOT Method 2 (page 9); Method 2 is then disabled for the day.
- Method 3: Advanced IRF / Liquidity Sweep (Pages 11-12)
  Small candles inside IR; red sweep candle spikes with 60-70% upper wick above IRH,
  closing below recent support.

Risk Parameters:
- Profit Target: Fixed 60 points below entry (Target = Entry - 60)
- Stop-Loss: 35 or 40 points based on distance to recent green candle:
  * Distance <= 35 points -> SL = 35 points
  * Distance > 35 points -> SL = 40 points

Not part of the PDF and therefore NOT implemented here: any entry cutoff time (14:30) or forced
end-of-day exit (15:15).  Those are engine-level conventions (backtesting / live session engine).

Bar convention: ``Bar.exchange_time`` is the bar OPEN label (09:15 = the 09:15-09:20 candle).
A bar is only handed to ``on_bar`` once complete, so a signal is known at ``open + 5 minutes``
(published as ``metadata["bar_close_time"]``).  The Signal timestamp keeps the open label for
compatibility with the backtesting engine.

PROVISIONAL interpretations (PDF gives no number / precise definition) live in
``configs/strategy_provisional/irf.yaml`` and are read through ``load_provisional("irf")``.  None of
them is author-approved; see ``IRFStrategy.PROVISIONAL_RULES``.
"""

from datetime import date, datetime, time, timedelta
from typing import Any

from trading_platform.core.enums import SignalDirection, StrategyStatus, Timeframe
from trading_platform.core.models import Bar, Signal
from trading_platform.strategies.base import BaseStrategy, StrategyContext
from trading_platform.strategies.provisional import load_provisional

IR_BAR_OPEN = time(9, 15)
BAR_MINUTES = 5
_KEEP_DAYS = 3
_METHOD_1 = "METHOD_1"
_METHOD_3 = "METHOD_3"
_METHOD_2_VARIANTS = ("METHOD_2", "METHOD_2_SUPPORT_BREAK", "METHOD_2_RESISTANCE_BREAK")
_VALID_METHODS = (_METHOD_1, _METHOD_3, *_METHOD_2_VARIANTS)

# Provisional keys the strategy reads.  Structural keys only support the listed values; an
# unsupported value raises so that a config edit can never be silently ignored.
_PROVISIONAL_KEYS = (
    "min_extension_bars",
    "extension_bar_definition",
    "support_definition",
    "resistance_definition",
    "resistance_push_above_rule",
    "stop_distance_basis",
    "stop_points_when_no_green_candle",
)
_SUPPORTED_STRUCTURE: dict[str, tuple[str, ...]] = {
    "extension_bar_definition": ("CLOSE_ABOVE_IRH",),
    "support_definition": ("LOWEST_LOW_FROM_FIRST_RED_AFTER_BREAKOUT",),
    "resistance_definition": ("HIGHEST_HIGH_OF_FIRST_PUSH",),
    "resistance_push_above_rule": ("PRIOR_BAR_HIGH_ABOVE_RESISTANCE",),
    "stop_distance_basis": ("GREEN_CANDLE_HIGH", "GREEN_CANDLE_CLOSE"),
}


def _candle(bar: Bar) -> dict[str, Any]:
    """Compact OHLC + time (open label and close time) of a bar for signal metadata."""
    return {
        "o": bar.open,
        "h": bar.high,
        "l": bar.low,
        "c": bar.close,
        "time": bar.exchange_time.isoformat(),
        "close_time": (bar.exchange_time + timedelta(minutes=BAR_MINUTES)).isoformat(),
    }


class IRFStrategy(BaseStrategy):
    """Canonical Initial Range Failure (IRF) Strategy implementing all 3 rulebook methods."""

    #: Unresolved rules the Method 2 implementation depends on (human readable).  Any strategy
    #: that depends on a PROVISIONAL rule stays SHADOW_ONLY; nothing here is author-approved.
    PROVISIONAL_RULES: tuple[str, ...] = (
        "Extension length: PDF says only 'extends further upward'; at least 2 closes above IRH "
        "including the breakout candle is used (min_extension_bars)",
        "Extension bar = a bar that CLOSES above IRH (PDF gives no definition)",
        "Recent support = lowest low from the first red candle after the breakout candle "
        "(PDF: 'a low formed by recent candles during the extension, such as a pullback low'); "
        "no lookback length is given and none is applied",
        "Recent resistance = highest high of the first push (breakout candle up to the candle "
        "before the first red candle); fixed once that red candle forms",
        "'Price trades back above resistance' = a bar after the first red candle with high above "
        "resistance, on a bar before the trigger bar (strict sequencing; single-bar wick-through "
        "rejection is not accepted)",
        "Stop-loss basis: distance from entry to the HIGH of the most recent same-session green "
        "candle (PDF does not say which price); 35 when none exists",
        "Only one Method 2 signal is emitted per session (PDF has no one-trade-per-day rule)",
        "A bar after the breakout that does not close above IRH disables Method 2 for the day only "
        "when it is the candle immediately after the breakout (Method 1 per PDF p9); later closes "
        "inside the range do not end the sequence",
        "Method 2 edge case: a candle right after the breakout that closes EXACTLY at IRH counts "
        "as 'not above IRH' and disables Method 2 for the day (close <= IRH), whereas Method 1 "
        "treats only a close strictly below IRH as 'back inside the range'; a close exactly at "
        "IRH therefore leaves neither method live (0 occurrences in 189 historical breakouts)",
        "Data-gap rule: any gap other than 5 minutes between consecutive bars (status DATA_GAP) "
        "or a non-5-minute bar disables IRF for the rest of the day (fail closed; not a PDF rule)",
        "Entry cutoff 14:30 and square-off 15:15 are engine conventions, not in the PDF",
        "Entry fill is next-bar open (backtest) / fresh ask (live), not the PDF's 'at that close'",
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(name="IRF", config=config, status=StrategyStatus.RESEARCH)
        self._daily_state: dict[str, dict[str, Any]] = {}
        self._apply_config()

    # ------------------------------------------------------------------ configuration
    def _apply_config(self) -> None:
        """(Re)read parameters: PDF values from config, PROVISIONAL values via load_provisional."""
        method = str(self.config.get("method", _METHOD_1))
        if method not in _VALID_METHODS:
            raise ValueError(f"Unknown IRF method {method!r}; expected one of {_VALID_METHODS}")
        self.method = method
        self.target_points = float(self.config.get("target_points", 60.0))
        self.default_stop_points = float(self.config.get("default_stop_points", 35.0))
        self.max_stop_points = float(self.config.get("max_stop_points", 40.0))
        # PDF p14: "Rs 35 (distance below Rs 35), Rs 40 (distance above Rs 35)".
        self.stop_distance_threshold = float(self.config.get("stop_distance_threshold", 35.0))

        overrides = {k: self.config[k] for k in _PROVISIONAL_KEYS if k in self.config}
        # Backward compatible aliases for the extension length.
        for legacy in ("extension_bars",):
            if legacy in self.config and "min_extension_bars" not in overrides:
                overrides["min_extension_bars"] = self.config[legacy]
        prov = load_provisional("irf", overrides)
        for key, supported in _SUPPORTED_STRUCTURE.items():
            value = prov.get(key, supported[0])
            if value not in supported:
                raise ValueError(f"IRF provisional {key}={value!r} unsupported; use {supported}")
            prov[key] = value
        self.min_extension_bars = int(prov.get("min_extension_bars", 2))
        if self.min_extension_bars < 1:
            raise ValueError("IRF min_extension_bars must be >= 1")
        self.stop_distance_basis = str(prov["stop_distance_basis"])
        self.no_green_stop_points = float(
            prov.get("stop_points_when_no_green_candle", self.default_stop_points)
        )
        self.provisional_values: dict[str, Any] = {k: prov.get(k) for k in _PROVISIONAL_KEYS}

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        """Initialize parameters."""
        self.config.update(config)
        self._apply_config()

    # ------------------------------------------------------------------ driving
    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        """Process one COMPLETED 5-minute bar and evaluate IRF short setups."""
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        """Evaluate the latest completed bar. Idempotent per bar; one bar at a time."""
        bar = context.latest_bar
        if bar is None:
            return None

        day_key = bar.exchange_time.date().isoformat()
        state = self._daily_state.get(day_key)
        if state is None:
            state = self._open_day(day_key, context)

        last_time = state["last_bar_time"]
        if last_time is not None and bar.exchange_time <= last_time:
            return None  # duplicate / out-of-order bar: never evaluated twice

        signal = self._advance(state, bar, context)
        if signal is not None and not self.validate_signal(signal, context):
            signal = None
        state["last_bar_time"] = bar.exchange_time
        state["last_bar"] = _candle(bar)
        state["bars"].append(bar)
        return signal

    def _open_day(self, day_key: str, context: StrategyContext) -> dict[str, Any]:
        """Create explicit per-day state; stale levels from earlier days are never reused."""
        state: dict[str, Any] = {
            "day": day_key,
            "status": "WAITING_FOR_IR_BAR",
            "reason": "first bar of the day not seen yet",
            "ir_ok": False,
            "irh": None,
            "irl": None,
            "ir_candle": None,
            "last_bar_time": None,
            "last_bar": None,
            "bars": [],
            "has_signalled": False,
            "disabled": False,
            # Method 1 / Method 3 helpers
            "m1_breakout": None,
            # Method 2 sequence
            "breakout": None,  # candle dict of the first close above IRH
            "post_breakout_bars": 0,
            "closes_above_irh": 0,
            "push_high": None,
            "resistance": None,  # final once the first red candle after the breakout forms
            "first_red": None,
            "support": None,
            "pushed_above_resistance": False,
        }
        is_rollover = bool(self._daily_state)
        self._daily_state[day_key] = state
        for old in sorted(self._daily_state)[:-_KEEP_DAYS]:
            del self._daily_state[old]
        if is_rollover:
            # Method 3 reads an externally supplied swing low.  On a day rollover clear the
            # previous day's value explicitly so a missing same-day level means "no signal"
            # instead of silently reusing a stale one.
            context.state["recent_swing_low"] = None
        return state

    def _advance(self, state: dict[str, Any], bar: Bar, context: StrategyContext) -> Signal | None:
        """Run one bar through the day state machine."""
        t = bar.exchange_time
        if state["status"] == "WAITING_FOR_IR_BAR":
            return self._establish_ir(state, bar)
        if state["disabled"] or state["has_signalled"] or not state["ir_ok"]:
            return None
        if bar.interval != Timeframe.M5:
            self._disable(state, "NON_5M_BAR", f"bar {t.time()} is not a 5m bar")
            return None
        gap = t - state["last_bar_time"]
        if gap != timedelta(minutes=BAR_MINUTES):
            self._disable(
                state,
                "DATA_GAP",
                f"missing bar(s) between {state['last_bar_time'].time()} and {t.time()}",
            )
            return None

        if self.method == _METHOD_1:
            return self._step_method1(state, bar)
        if self.method == _METHOD_3:
            return self._step_method3(state, bar, context)
        return self._step_method2(state, bar)

    @staticmethod
    def _disable(state: dict[str, Any], status: str, reason: str) -> None:
        state["disabled"] = True
        state["status"] = status
        state["reason"] = reason

    def _establish_ir(self, state: dict[str, Any], bar: Bar) -> Signal | None:
        """Initial range = the 09:15-09:20 candle only (PDF p3-4); never a later substitute."""
        t = bar.exchange_time
        if t.time() != IR_BAR_OPEN or bar.interval != Timeframe.M5:
            state["disabled"] = True
            state["status"] = "NO_IR_BAR"
            state["reason"] = (
                f"first bar seen is {t.time()} (interval {bar.interval}); the 09:15 initial-range "
                "candle is missing/late, so IRF stays silent for the day"
            )
            return None
        state["ir_ok"] = True
        state["irh"] = bar.high
        state["irl"] = bar.low
        state["ir_candle"] = _candle(bar)
        state["status"] = "WAITING_BREAKOUT"
        state["reason"] = "waiting for a candle to close above IRH"
        return None

    # ------------------------------------------------------------------ Method 1
    def _step_method1(self, state: dict[str, Any], bar: Bar) -> Signal | None:
        irh = state["irh"]
        breakout = state["m1_breakout"]
        if breakout is None:
            if bar.close > irh:
                state["m1_breakout"] = _candle(bar)
                state["status"] = "M1_BREAKOUT_SEEN"
                state["reason"] = "C1 closed above IRH; next candle must close back inside"
            return None
        # C2 must be the immediate next candle closing back inside the range.
        if bar.close < irh:
            sat = ["ir_marked_0915", "c1_close_above_irh", "c2_close_back_inside_range"]
            state["has_signalled"] = True
            state["status"] = "SIGNALLED"
            state["reason"] = "IRF_METHOD_1_IMMEDIATE_FAILURE"
            return self._create_short_signal(
                state, bar, "IRF_METHOD_1_IMMEDIATE_FAILURE", sat, [], {"breakout": breakout}
            )
        state["m1_breakout"] = None
        state["status"] = "WAITING_BREAKOUT"
        state["reason"] = "sequence broken: C2 did not close back inside the range"
        return None

    # ------------------------------------------------------------------ Method 3
    def _step_method3(
        self, state: dict[str, Any], bar: Bar, context: StrategyContext
    ) -> Signal | None:
        irh = state["irh"]
        if bar.high <= irh:
            return None
        upper_wick = bar.high - max(bar.open, bar.close)
        portion_above = bar.high - irh
        if bar.high - bar.low <= 0 or upper_wick <= 0:
            return None
        outside_share = portion_above / upper_wick
        # Wick condition: 60% to 70% of upper wick lies above IRH
        # Compulsory confirmation: red sweep candle closes below recent support
        recent_supp = context.state.get("recent_swing_low")
        if (
            recent_supp is not None
            and 0.55 <= outside_share <= 0.85
            and bar.close < bar.open
            and bar.close <= recent_supp
        ):
            state["has_signalled"] = True
            state["status"] = "SIGNALLED"
            state["reason"] = "IRF_METHOD_3_LIQUIDITY_SWEEP"
            sat = ["ir_marked_0915", "wick_above_irh", "red_sweep_candle", "close_below_support"]
            return self._create_short_signal(
                state,
                bar,
                "IRF_METHOD_3_LIQUIDITY_SWEEP",
                sat,
                [],
                {"support": recent_supp, "outside_wick_share": round(outside_share, 4)},
            )
        return None

    # ------------------------------------------------------------------ Method 2
    def _step_method2(self, state: dict[str, Any], bar: Bar) -> Signal | None:
        """Advance the Method 2 sequence by one completed bar (no look-ahead)."""
        irh = state["irh"]

        if state["breakout"] is None:
            if bar.close > irh:  # step 2: a candle closes above IRH
                state["breakout"] = _candle(bar)
                state["closes_above_irh"] = 1
                state["push_high"] = bar.high
                state["status"] = "EXTENSION_TRACKING"
                state["reason"] = "breakout close above IRH; waiting for extension and a red candle"
            return None

        # PDF p9/p13: if the candle right after the breakout closes back inside the range the
        # setup is Method 1, not Method 2.  Method 2 is switched off for the day.
        if state["post_breakout_bars"] == 0 and bar.close <= irh:
            self._disable(
                state,
                "METHOD1_PATTERN_METHOD2_DISABLED",
                "candle right after the breakout closed back inside the range (Method 1 setup)",
            )
            return None

        # Triggers are evaluated on the state BEFORE this bar is absorbed (levels come only from
        # earlier bars), then the bar updates the state.
        a_ok, b_ok, sat, failed = self._assess_method2(state, bar)
        allow_a = self.method in ("METHOD_2", "METHOD_2_SUPPORT_BREAK")
        allow_b = self.method in ("METHOD_2", "METHOD_2_RESISTANCE_BREAK")
        signal: Signal | None = None
        if (a_ok and allow_a) or (b_ok and allow_b):
            if a_ok and allow_a:
                reason = "IRF_METHOD_2_SUPPORT_BREAK"
            else:
                reason = "IRF_METHOD_2_RESISTANCE_BREAK"
            state["has_signalled"] = True
            state["status"] = "SIGNALLED"
            state["reason"] = reason
            signal = self._create_short_signal(state, bar, reason, sat, failed, {})

        self._absorb_method2(state, bar)
        if not state["has_signalled"]:
            self._refresh_status(state)
        return signal

    def _assess_method2(
        self, state: dict[str, Any], bar: Bar | None
    ) -> tuple[bool, bool, list[str], list[str]]:
        """Evaluate Method 2 conditions against the state; pure (no mutation)."""
        sat: list[str] = []
        failed: list[str] = []
        if state["ir_ok"]:
            sat.append("ir_marked_0915")
        else:
            failed.append("ir_not_marked")
        breakout = state["breakout"]
        if breakout is None:
            failed.append("no_close_above_irh_yet")
            return False, False, sat, failed
        sat.append("breakout_close_above_irh")
        n_ext = state["closes_above_irh"]
        ext_ok = n_ext >= self.min_extension_bars
        (sat if ext_ok else failed).append(
            f"extension_bars_{'met' if ext_ok else 'short'}({n_ext}/{self.min_extension_bars})"
        )
        red_seen = state["first_red"] is not None
        (sat if red_seen else failed).append(
            "red_candle_after_breakout" if red_seen else "no_red_candle_after_breakout"
        )
        support = state["support"]
        resistance = state["resistance"]
        (sat if support is not None else failed).append(
            "support_defined" if support is not None else "support_not_defined"
        )
        (sat if resistance is not None else failed).append(
            "resistance_defined" if resistance is not None else "resistance_not_defined"
        )
        pushed = state["pushed_above_resistance"]
        (sat if pushed else failed).append(
            "traded_back_above_resistance" if pushed else "not_traded_back_above_resistance"
        )
        if bar is None:
            return False, False, sat, failed
        is_red = bar.close < bar.open
        a_ok = ext_ok and support is not None and bar.close < support
        b_ok = ext_ok and pushed and resistance is not None and is_red and bar.close < resistance
        (sat if a_ok else failed).append(
            "support_break_close_below_support" if a_ok else "no_close_below_support"
        )
        (sat if b_ok else failed).append(
            "resistance_break_red_close_below_resistance"
            if b_ok
            else "no_red_close_below_resistance_after_push_above"
        )
        return a_ok, b_ok, sat, failed

    def _absorb_method2(self, state: dict[str, Any], bar: Bar) -> None:
        """Fold a completed bar into the sequence state (after it was evaluated)."""
        irh = state["irh"]
        state["post_breakout_bars"] += 1
        if bar.close > irh:
            state["closes_above_irh"] += 1
        if state["first_red"] is None:
            if bar.close < bar.open:
                # first pullback candle: the first push has topped out -> resistance is final
                state["first_red"] = _candle(bar)
                state["resistance"] = state["push_high"]
                state["support"] = bar.low
            else:
                state["push_high"] = max(state["push_high"], bar.high)
        else:
            state["support"] = min(state["support"], bar.low)
            if bar.high > state["resistance"]:
                state["pushed_above_resistance"] = True

    @staticmethod
    def _refresh_status(state: dict[str, Any]) -> None:
        if state["first_red"] is None:
            state["status"] = "EXTENSION_TRACKING"
            state["reason"] = "extension above IRH; waiting for the first red (pullback) candle"
        else:
            state["status"] = "ARMED"
            state["reason"] = "levels defined; waiting for a support or resistance break close"

    # ------------------------------------------------------------------ read-only snapshot
    def evaluation_snapshot(self, day_key: str | date | datetime) -> dict[str, Any]:
        """Read-only view of what is satisfied/failed for ``day_key``; never mutates state."""
        if isinstance(day_key, datetime):
            key = day_key.date().isoformat()
        elif isinstance(day_key, date):
            key = day_key.isoformat()
        else:
            key = str(day_key)
        state = self._daily_state.get(key)
        base: dict[str, Any] = {
            "strategy": self.name,
            "method": self.method,
            "day": key,
            "provisional_rules": list(self.PROVISIONAL_RULES),
            "provisional_values": dict(self.provisional_values),
        }
        if state is None:
            return {
                **base,
                "status": "NO_DATA",
                "reason": "no completed bar received for this day yet",
                "conditions_satisfied": [],
                "conditions_failed": ["no_bars_received"],
                "levels": {},
                "has_signalled": False,
                "last_bar": None,
            }
        if self.method in _METHOD_2_VARIANTS:
            _, _, sat, failed = self._assess_method2(state, None)
        else:
            sat = ["ir_marked_0915"] if state["ir_ok"] else []
            failed = [] if state["ir_ok"] else ["ir_not_marked"]
        if state["disabled"]:
            failed.append(f"day_disabled:{state['status']}")
        if state["has_signalled"]:
            sat.append("signal_emitted_for_day")
        return {
            **base,
            "status": state["status"],
            "reason": state["reason"],
            "conditions_satisfied": sat,
            "conditions_failed": failed,
            "levels": self._levels(state),
            "has_signalled": state["has_signalled"],
            "last_bar": dict(state["last_bar"]) if state["last_bar"] else None,
            "last_bar_time": state["last_bar_time"].isoformat() if state["last_bar_time"] else None,
        }

    @staticmethod
    def _levels(state: dict[str, Any]) -> dict[str, Any]:
        breakout = state["breakout"] or state["m1_breakout"]
        return {
            "irh": state["irh"],
            "irl": state["irl"],
            "breakout_close": breakout["c"] if breakout else None,
            "support": state["support"],
            "resistance": state["resistance"],
            "first_push_high": state["push_high"],
        }

    # ------------------------------------------------------------------ signal construction
    def _recent_green_reference(self, state: dict[str, Any], entry: float) -> float | None:
        """Distance basis to the most recent same-session green candle before the entry bar."""
        for b in reversed(state["bars"]):
            if b.close > b.open:
                ref = b.high if self.stop_distance_basis == "GREEN_CANDLE_HIGH" else b.close
                return ref - entry
        return None

    def _create_short_signal(
        self,
        state: dict[str, Any],
        bar: Bar,
        reason: str,
        satisfied: list[str],
        failed: list[str],
        extra_levels: dict[str, Any],
    ) -> Signal:
        """Construct the short signal; stop uses the recent same-session green candle (PDF p2/p14)."""
        ref_price = bar.close
        target = round(ref_price - self.target_points, 2)
        distance = self._recent_green_reference(state, ref_price)
        if distance is None:
            stop_points = self.no_green_stop_points
        elif distance > self.stop_distance_threshold:
            stop_points = self.max_stop_points
        else:
            stop_points = self.default_stop_points

        stop_loss = round(ref_price + stop_points, 2)
        levels = {**self._levels(state), **extra_levels}
        sig = Signal(
            strategy_name=self.name,
            method_id=reason,
            symbol=bar.symbol,
            direction=SignalDirection.SELL,
            timestamp=bar.exchange_time,
            reference_price=ref_price,
            stop_loss=stop_loss,
            target=target,
            reason=reason,
            metadata={
                "method": reason,
                "target_points": self.target_points,
                "stop_points": stop_points,
                "stop_distance_to_green": None if distance is None else round(distance, 2),
                "stop_distance_basis": self.stop_distance_basis,
                "bar_close_time": (bar.exchange_time + timedelta(minutes=BAR_MINUTES)).isoformat(),
                "conditions_satisfied": list(satisfied),
                "conditions_failed": list(failed),
                "levels": levels,
                "trigger_candle": _candle(bar),
                "provisional_rules": list(self.PROVISIONAL_RULES)
                if self.method in _METHOD_2_VARIANTS
                else [],
                "tier_note": "provisional interpretation - not author-approved",
            },
        )
        return sig
