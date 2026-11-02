"""Why did the V3 backtest NOT produce a signal where the manual journal has a trade?

Replays a single trading day through the REAL strategy classes with exactly the context the
backtest engine injects (``ReferenceLevelCalculator.enrich_bars_with_context``), then reads the
strategies' own read-only ``evaluation_snapshot``.  Nothing here changes a strategy, tunes a
threshold or enables a fill; alternate methods and alternate level definitions are evaluated
as DIAGNOSTICS ONLY, and are never part of the V3 portfolio.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from trading_platform.core.models import Bar
from trading_platform.data.reference_levels import ReferenceLevelCalculator
from trading_platform.strategies.base import BaseStrategy, StrategyContext
from trading_platform.strategies.irf import IRFStrategy
from trading_platform.strategies.orb import ORBStrategy
from trading_platform.strategies.pdr import PDRStrategy
from trading_platform.strategies.pds import PDSStrategy
from trading_platform.strategies.provisional import load_provisional

ROOT_CAUSES = (
    "Incorrect strategy implementation",
    "Rulebook ambiguity",
    "Different candle timing",
    "Missing or invalid market data",
    "Strategy-method mismatch",
    "Manual discretionary entry",
    "Portfolio position conflict",
    "Unspecified or missing manual information",
    "Unsupported strategy (RF disabled)",
)

# Diagnostic alternates (NOT part of the V3 portfolio).  The journal does not record the method.
ALTERNATE_METHODS: dict[str, list[tuple[str, type[BaseStrategy], dict[str, Any]]]] = {
    "ORB": [
        ("ORB_M2_INNER_LEVEL", ORBStrategy, {"method": "METHOD_2"}),
        ("ORB_M3_GAP_200DMA", ORBStrategy, {"method": "METHOD_3"}),
    ],
    "IRF": [
        ("IRF_M1_IMMEDIATE_FAILURE", IRFStrategy, {"method": "METHOD_1"}),
        ("IRF_M3_LIQUIDITY_SWEEP", IRFStrategy, {"method": "METHOD_3"}),
    ],
    "PDS": [
        ("PDS_ALL_METHODS", PDSStrategy, {"method": "ALL"}),
    ],
    "PDR": [],
}

V3_CLASSES: dict[str, tuple[type[BaseStrategy], dict[str, Any]]] = {
    "PDS": (PDSStrategy, {"method": "METHOD_1"}),
    "ORB": (ORBStrategy, {"method": "METHOD_1"}),
    "IRF": (IRFStrategy, {"method": "METHOD_2"}),
    "PDR": (PDRStrategy, {}),
}


@dataclass
class ReplayOutcome:
    signals: list[dict[str, Any]] = field(default_factory=list)
    snapshot_at_manual: dict[str, Any] = field(default_factory=dict)
    snapshot_eod: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RootCause:
    category: str
    sub_reason: str
    basis: str  # PDF_RULE | PROVISIONAL_RULE | UNVERIFIABLE_MANUAL | DATA | TIMING | PORTFOLIO | EXCLUDED
    implementation_defect: str  # NO | SUSPECTED | YES
    evidence: str


class DayReplayer:
    """Replays one session at a time with the backtest's own point-in-time contexts."""

    def __init__(self, bars_1d: list[Bar], bars_5m: list[Bar]) -> None:
        self.bars_1d = bars_1d
        self.bars_5m = sorted(bars_5m, key=lambda b: b.exchange_time)
        calc = ReferenceLevelCalculator()
        enriched = calc.enrich_bars_with_context(self.bars_5m, daily_history_bars=bars_1d)
        self.ctx = {c.bar.exchange_time: c for c in enriched}
        self.by_day: dict[date, list[Bar]] = {}
        for b in self.bars_5m:
            self.by_day.setdefault(b.exchange_time.date(), []).append(b)
        self.days = sorted(self.by_day)
        self.daily = {b.exchange_time.date(): b for b in bars_1d}

    def previous_day(self, day: date) -> date | None:
        earlier = [d for d in self.days if d < day]
        return earlier[-1] if earlier else None

    def replay(
        self,
        cls: type[BaseStrategy],
        config: dict[str, Any],
        day: date,
        manual_bar_open: time | None,
    ) -> ReplayOutcome:
        out = ReplayOutcome()
        bars = self.by_day.get(day)
        if not bars:
            return out
        strat = cls(dict(config))
        strat.reset_state()
        ctx = StrategyContext()
        for bar in bars:
            c = self.ctx.get(bar.exchange_time)
            ctx.state.clear()
            ctx.state.update(c.strategy_state() if c else {})
            sig = strat.on_bar(ctx, bar)
            if sig is not None:
                out.signals.append(
                    {
                        "candle_open": bar.exchange_time.strftime("%H:%M"),
                        "direction": str(sig.direction),
                        "rule": sig.method_id,
                    }
                )
            if manual_bar_open is not None and bar.exchange_time.time() == manual_bar_open:
                out.snapshot_at_manual = _snapshot(strat, day, ctx)
        out.snapshot_eod = _snapshot(strat, day, ctx)
        return out

    # ------------------------------------------------------------------ ORB facts

    def orb_first_candle_facts(self, day: date) -> dict[str, Any]:
        """INDEPENDENT recomputation (raw bars, not the strategy) of the Method-1 conditions."""
        bars = self.by_day.get(day, [])
        first = next((b for b in bars if b.exchange_time.time() == time(9, 15)), None)
        prev_day = self.previous_day(day)
        if first is None or prev_day is None:
            return {"available": False}
        prev_regular = [
            b
            for b in self.by_day[prev_day]
            if time(9, 15) <= b.exchange_time.time() <= time(15, 25)
        ]
        ind_pdh = max(b.high for b in prev_regular)
        ind_pdl = min(b.low for b in prev_regular)
        state = self.ctx[first.exchange_time].strategy_state()
        official = self.daily.get(prev_day)
        rng = (first.high - first.low) or 1e-9
        body = abs(first.close - first.open) / rng
        up_wick = (first.high - max(first.open, first.close)) / rng
        lo_wick = (min(first.open, first.close) - first.low) / rng
        prov = load_provisional("orb")
        min_body = float(prov.get("min_body_ratio", 0.5))
        max_wick = float(prov.get("max_opposing_wick_ratio", 0.35))
        ema = state.get("ema_200")
        facts: dict[str, Any] = {
            "available": True,
            "o": first.open,
            "h": first.high,
            "l": first.low,
            "c": first.close,
            "prev_date": prev_day.isoformat(),
            "pdh": state.get("pdh"),
            "pdl": state.get("pdl"),
            "independent_pdh": ind_pdh,
            "independent_pdl": ind_pdl,
            "levels_match_independent": (
                state.get("pdh") == ind_pdh and state.get("pdl") == ind_pdl
            ),
            "official_pdh": official.high if official else None,
            "official_pdl": official.low if official else None,
            "zone_res_body": state.get("pd_res_body"),
            "zone_supp_body_top": state.get("pd_supp_body_top"),
            "prev_close": state.get("prev_close"),
            "ema_200": ema,
            "body_ratio": round(body, 3),
            "upper_wick_ratio": round(up_wick, 3),
            "lower_wick_ratio": round(lo_wick, 3),
            "gap_points": (first.open - state["prev_close"]) if state.get("prev_close") else None,
        }
        facts["close_vs_pdh"] = round(first.close - state["pdh"], 2) if state.get("pdh") else None
        facts["close_vs_pdl"] = round(first.close - state["pdl"], 2) if state.get("pdl") else None
        facts["beyond_v3_level"] = bool(
            (state.get("pdh") and first.close > state["pdh"])
            or (state.get("pdl") and first.close < state["pdl"])
        )

        def passes_filters(side: str) -> bool:
            if ema is None:
                return False
            if side == "long":
                return (
                    first.close > first.open
                    and first.close > ema
                    and body >= min_body
                    and up_wick <= max_wick
                )
            return (
                first.close < first.open
                and first.close < ema
                and body >= min_body
                and lo_wick <= max_wick
            )

        alt: dict[str, bool] = {}
        for side, defs in (
            (
                "long",
                {
                    "zone_body_edge": state.get("pd_res_body"),
                    "official_daily_high": facts["official_pdh"],
                    "wick_extreme": state.get("pdh"),
                },
            ),
            (
                "short",
                {
                    "zone_body_edge": state.get("pd_supp_body_top"),
                    "official_daily_high": facts["official_pdl"],
                    "wick_extreme": state.get("pdl"),
                },
            ),
        ):
            for name, lvl in defs.items():
                if lvl is None:
                    continue
                broke = first.close > lvl if side == "long" else first.close < lvl
                key = f"{side}_{name}"
                alt[key + "_broken"] = bool(broke)
                alt[key + "_would_pass_filters"] = bool(broke and passes_filters(side))
        facts["alt_level_flags"] = alt
        facts["passes_filters_long"] = passes_filters("long")
        facts["passes_filters_short"] = passes_filters("short")
        return facts

    def bar_at(self, day: date, hhmm: str) -> dict[str, float] | None:
        for b in self.by_day.get(day, []):
            if b.exchange_time.strftime("%H:%M") == hhmm:
                return {"o": b.open, "h": b.high, "l": b.low, "c": b.close}
        return None


def _snapshot(strat: BaseStrategy, day: date, ctx: StrategyContext) -> dict[str, Any]:
    fn = strat.evaluation_snapshot  # type: ignore[attr-defined]
    params = inspect.signature(fn).parameters
    snap = fn(day.isoformat(), ctx) if len(params) >= 2 else fn(day.isoformat())
    snap = dict(snap)
    snap.pop("provisional_rules", None)
    return snap


# --------------------------------------------------------------------------- classification


def _within(alt_signals: list[dict[str, Any]], manual_hhmm: str, minutes: int = 10) -> list[str]:
    mt = datetime.strptime(manual_hhmm, "%H:%M")
    hits = []
    for s in alt_signals:
        st = datetime.strptime(s["candle_open"], "%H:%M")
        if abs((st - mt).total_seconds()) / 60 <= minutes:
            hits.append(s["candle_open"])
    return hits


def classify_unmatched(
    strategy: str,
    manual_hhmm: str,
    replay: ReplayOutcome,
    v3_signals_same_day: list[str],
    alt_signals: dict[str, list[dict[str, Any]]],
    orb_facts: dict[str, Any] | None,
) -> RootCause:
    """Assign ONE primary root cause from the evidence.  Never decides the manual trade was wrong."""
    snap = replay.snapshot_at_manual or replay.snapshot_eod
    failed = [str(x) for x in snap.get("conditions_failed", [])]
    state_text = " ".join(
        str(snap.get(k, ""))
        for k in ("state", "status", "phase", "disposition", "reason", "state_reason")
    )

    if any(
        k in state_text.upper()
        for k in (
            "NO_FIRST_BAR",
            "LEVELS_STALE",
            "WARMING_UP",
            "DATA_GAP",
            "LEVELS_MISSING",
            "BLOCKED",
        )
    ):
        return RootCause(
            "Missing or invalid market data",
            state_text.strip(),
            "DATA",
            "NO",
            f"strategy state: {state_text.strip()}",
        )

    alt_hits_all = {
        k: _within(v, manual_hhmm) for k, v in alt_signals.items() if _within(v, manual_hhmm)
    }
    if alt_hits_all and strategy in ("IRF", "PDS"):
        return RootCause(
            "Strategy-method mismatch",
            "a different method of the same strategy signals within 10 minutes of the manual time: "
            + ", ".join(f"{k}@{','.join(v)}" for k, v in sorted(alt_hits_all.items()))
            + " (the journal does not record the method; the V3 portfolio uses "
            + ("IRF Method 2" if strategy == "IRF" else "PDS Method 1")
            + ")",
            "PDF_RULE",
            "NO",
            f"V3 {strategy} state at manual time: {state_text.strip()}",
        )

    if v3_signals_same_day:
        return RootCause(
            "Different candle timing",
            f"V3 {strategy} signalled the same day at {', '.join(v3_signals_same_day)} but not within 10 min of {manual_hhmm}",
            "TIMING",
            "NO",
            "same-date same-strategy V3 signal exists; manual and automated candles differ by more than 10 minutes",
        )

    if strategy == "ORB":
        f = orb_facts or {}
        alt_hits = {
            k: _within(v, manual_hhmm) for k, v in alt_signals.items() if _within(v, manual_hhmm)
        }
        if not f.get("available"):
            return RootCause(
                "Missing or invalid market data",
                "no 09:15 candle or no prior session",
                "DATA",
                "NO",
                "ORB facts unavailable",
            )
        defect = "NO" if f.get("levels_match_independent") else "SUSPECTED"
        if alt_hits:
            return RootCause(
                "Strategy-method mismatch",
                "the ORB trigger matches an ORB setup other than Method 1: "
                + ", ".join(sorted(alt_hits)),
                "PDF_RULE",
                defect,
                f"09:15 close {f['c']} is {'beyond' if f['beyond_v3_level'] else 'inside'} the previous-day range "
                f"[{f['pdl']}, {f['pdh']}]; alternate-method signal at {alt_hits}",
            )
        if not f["beyond_v3_level"]:
            alt = f.get("alt_level_flags", {})
            would = sorted(k for k, v in alt.items() if k.endswith("_would_pass_filters") and v)
            if would:
                return RootCause(
                    "Rulebook ambiguity",
                    "the 09:15 candle would qualify if the previous-day 'level' were read differently: "
                    + ", ".join(would),
                    "PROVISIONAL_RULE",
                    defect,
                    f"close {f['c']} vs wick-extreme PDH {f['pdh']} / PDL {f['pdl']}; zone/official level variants flagged (sensitivity only, not applied)",
                )
            if f.get("passes_filters_long") or f.get("passes_filters_short"):
                return RootCause(
                    "Strategy-method mismatch",
                    "the 09:15 candle passes the trend + candle-quality tests in its direction but closed "
                    "short of every previous-day level definition tested: CONSISTENT WITH (not proof of) an "
                    "ORB Method 2 inner-level breakout, which the journal does not label and which needs a "
                    "hand-drawn inner level + trendline that cannot be machine-verified",
                    "UNVERIFIABLE_MANUAL",
                    defect,
                    f"close {f['c']} vs PDH {f['pdh']} (diff {f['close_vs_pdh']}) / PDL {f['pdl']} "
                    f"(diff {f['close_vs_pdl']}); body {f['body_ratio']}, EMA200 {f['ema_200']}",
                )
            return RootCause(
                "Manual discretionary entry",
                "the 09:15 candle closed short of every previous-day level definition tested AND fails the "
                "trend / candle-quality tests; no ORB setup can be reconstructed from the available evidence",
                "UNVERIFIABLE_MANUAL",
                defect,
                f"close {f['c']} vs PDH {f['pdh']} (diff {f['close_vs_pdh']}) / PDL {f['pdl']} (diff {f['close_vs_pdl']}); "
                f"body {f['body_ratio']}, EMA200 {f['ema_200']}",
            )
        return RootCause(
            "Rulebook ambiguity",
            "level broken but candle-quality / trend thresholds (PROVISIONAL: body>=0.50, opposing wick<=0.35, 5m EMA200) rejected the candle",
            "PROVISIONAL_RULE",
            defect,
            f"close {f['c']} beyond level; body {f['body_ratio']}, up-wick {f['upper_wick_ratio']}, "
            f"lo-wick {f['lower_wick_ratio']}, EMA200 {f['ema_200']}; failed {failed}",
        )

    if strategy == "IRF":
        alt_hits = {
            k: _within(v, manual_hhmm) for k, v in alt_signals.items() if _within(v, manual_hhmm)
        }
        if "METHOD1_PATTERN" in state_text.upper() or alt_hits:
            return RootCause(
                "Strategy-method mismatch",
                "the day's pattern is IRF Method 1 / 3 (immediate failure or sweep), not Method 2"
                + (f"; alternate signal at {alt_hits}" if alt_hits else ""),
                "PDF_RULE",
                "NO",
                f"IRF status: {state_text.strip()}; failed {failed[:4]}",
            )
        if (
            "WAITING_BREAKOUT" in state_text.upper()
            or any("breakout_close_above_irh" in f_ for f_ in failed)
            or "no_breakout" in state_text.lower()
        ):
            return RootCause(
                "Manual discretionary entry",
                "no candle had closed above the initial-range high by the manual entry time and no Method 1/3 pattern exists within 10 minutes, so there was no initial-range failure to trade (IRF is short-only per the PDF)",
                "PDF_RULE",
                "NO",
                f"IRF status: {state_text.strip()}; failed {failed[:4]}",
            )
        return RootCause(
            "Rulebook ambiguity",
            "IRF Method 2 sequence incomplete under the provisional extension / support / resistance definitions",
            "PROVISIONAL_RULE",
            "NO",
            f"IRF status: {state_text.strip()}; failed {failed[:5]}",
        )

    if strategy == "PDR":
        if "OPEN_NOT_ABOVE_ZONE" in state_text.upper():
            return RootCause(
                "Rulebook ambiguity",
                "provisional gate 'open must be above the resistance-zone top' rejected a gap-up that opened inside / at the zone",
                "PROVISIONAL_RULE",
                "NO",
                f"{state_text.strip()}; failed {failed[:3]}; levels {snap.get('levels')}",
            )
        return RootCause(
            "Manual discretionary entry",
            "PDR precondition not met: " + (", ".join(failed[:3]) or state_text.strip()),
            "PDF_RULE",
            "NO",
            f"{state_text.strip()}; levels {snap.get('levels')}",
        )

    if strategy == "PDS":
        satisfied = [str(x) for x in snap.get("conditions_satisfied", [])]
        gap_down = any("gap down" in s for s in satisfied)
        if not gap_down:
            return RootCause(
                "Manual discretionary entry",
                "PDS requires a gap-down open (PDF); the session did not gap down",
                "PDF_RULE",
                "NO",
                f"{state_text.strip()}; satisfied {satisfied[:3]}",
            )
        return RootCause(
            "Rulebook ambiguity",
            "gap-down day, but the provisional single-source support zone was never retested (zone construction / proximity unspecified in the PDF)",
            "PROVISIONAL_RULE",
            "NO",
            f"{state_text.strip()}; levels {snap.get('levels')}",
        )

    return RootCause(
        "Manual discretionary entry",
        "no setup evidence",
        "UNVERIFIABLE_MANUAL",
        "NO",
        state_text.strip(),
    )
