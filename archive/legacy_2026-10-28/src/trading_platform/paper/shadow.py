"""Shadow-signal-only evaluation with an explicit no-execution boundary."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_platform.core.models import Bar
from trading_platform.data.point_in_time_context import PointInTimeReferenceContext

TZ_IST = ZoneInfo("Asia/Kolkata")

REJECTION_CATEGORIES = (
    "INCOMPLETE_CANDLE",
    "MISSING_REFERENCE_DATA",
    "STALE_UNDERLYING",
    "INSUFFICIENT_HISTORY",
    "RULEBOOK_AMBIGUITY",
    "INDICATOR_NOT_READY",
    "MARKET_SESSION_RESTRICTION",
    "STRATEGY_CONDITION_FALSE",
    "INTERNAL_ERROR",
)


def classify_rejection_reason(reason_code: str) -> str:
    """Map an explicit shadow reason to the audit's stable category vocabulary."""
    if "INCOMPLETE" in reason_code or "FUTURE" in reason_code:
        return "INCOMPLETE_CANDLE"
    if "POINT_IN_TIME" in reason_code or "REFERENCE" in reason_code:
        return "MISSING_REFERENCE_DATA"
    if "STALE" in reason_code:
        return "STALE_UNDERLYING"
    if "HISTORY" in reason_code or "WARMUP" in reason_code:
        return "INSUFFICIENT_HISTORY"
    if "INDICATOR" in reason_code or "EMA" in reason_code or "DMA" in reason_code:
        return "INDICATOR_NOT_READY"
    if "SESSION" in reason_code or "MARKET_CLOSED" in reason_code:
        return "MARKET_SESSION_RESTRICTION"
    if "CONDITION_FALSE" in reason_code or "NO_SETUP" in reason_code or "DATA_READY_CONDITIONS_FALSE" in reason_code:
        return "STRATEGY_CONDITION_FALSE"
    if "INTERNAL" in reason_code or "ERROR" in reason_code:
        return "INTERNAL_ERROR"
    if "RULEBOOK" in reason_code or "UNSPECIFIED" in reason_code or "AMBIGUITY" in reason_code:
        return "RULEBOOK_AMBIGUITY"
    return "INTERNAL_ERROR"


@dataclass
class ShadowSignalCollector:
    """Record rule evaluations without creating orders, positions or P&L."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    diagnostic_state: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    METHODS = (
        ("PDS", "METHOD_1", "INTERPRETATION_DEPENDENT"),
        ("ORB", "METHOD_1", "INTERPRETATION_DEPENDENT"),
        ("IRF", "METHOD_2", "INTERPRETATION_DEPENDENT"),
        ("PDR", "RETEST_BOUNCE", "INTERPRETATION_DEPENDENT"),
        ("RF", "CANONICAL", "UNEXECUTABLE_RULEBOOK"),
    )

    ENABLED_METHODS = (
        ("PDS", "METHOD_1"),
        ("ORB", "METHOD_1"),
        ("IRF", "METHOD_2"),
        ("PDR", "RETEST_BOUNCE"),
    )

    def _append_diagnostic_row(
        self,
        *,
        strategy: str,
        method: str,
        bar: Bar,
        context: PointInTimeReferenceContext,
        outcome: str,
        rule_evaluations: dict[str, Any],
        missing_inputs: list[str] | None = None,
        signal_direction: str = "NONE",
    ) -> dict[str, Any]:
        values = context.as_dict()
        reason_code = outcome
        category = classify_rejection_reason(outcome)
        row = {
            "strategy": strategy,
            "method": method,
            "signal_date": bar.exchange_time.date().isoformat(),
            "signal_time": bar.exchange_time.isoformat(),
            "decision_available_time": context.candle_available_timestamp.isoformat(),
            "direction": signal_direction,
            "input_candle_open": bar.open,
            "input_candle_high": bar.high,
            "input_candle_low": bar.low,
            "input_candle_close": bar.close,
            "input_candle_timestamp": bar.exchange_time.isoformat(),
            "candle_source": bar.source,
            "candle_validation_status": context.validation_status,
            "context_inputs": json.dumps(
                {
                    key: values.get(key)
                    for key in (
                        "pdh",
                        "pdl",
                        "pdc",
                        "pdo",
                        "pd_res_body",
                        "pd_supp_body",
                        "ema_200",
                        "dma_200",
                        "session_open",
                        "irh",
                        "irl",
                        "gap_points",
                        "is_gap_up",
                        "is_gap_down",
                    )
                },
                sort_keys=True,
                default=str,
            ),
            "reference_context_validation": context.validation_status,
            "previous_day_levels": json.dumps(
                {key: values.get(key) for key in ("pdh", "pdl", "pdc", "pdo", "pd_res_body", "pd_supp_body")},
                sort_keys=True,
                default=str,
            ),
            "missing_inputs": json.dumps(sorted(missing_inputs or [])),
            "rule_evaluations": json.dumps(rule_evaluations, sort_keys=True, default=str),
            "status": "SIGNAL_DETECTED" if outcome == "DATA_READY_SIGNAL_DETECTED" else "REJECTED",
            "reason_code": reason_code,
            "rejection_category": category,
            "signal_eligible": outcome == "DATA_READY_SIGNAL_DETECTED",
            "execution_created": False,
        }
        self.rows.append(row)
        return row

    def evaluate_strategy_bar(
        self,
        strategy: str,
        method: str,
        bar: Bar,
        context: PointInTimeReferenceContext,
    ) -> dict[str, Any]:
        """Evaluate one enabled strategy with frozen context and no execution."""
        values = context.as_dict()
        rule_evaluations: dict[str, Any] = {
            "timestamp_aligned": bar.exchange_time.minute % 5 == 0 and bar.exchange_time.second == 0,
            "candle_available_at_decision": context.candle_available_timestamp <= context.decision_timestamp,
            "session_supported": values.get("session_supported", False),
            "reference_context_status": context.validation_status,
        }
        required = {
            "PDS": ("pdl", "pdc", "pd_supp_body"),
            "PDR": ("pdh", "pdc", "pd_res_body"),
            "ORB": ("pdh", "pdl", "ema_200"),
            "IRF": ("irh",),
        }.get(strategy, tuple())
        missing = [field for field in required if values.get(field) is None]
        if not rule_evaluations["timestamp_aligned"]:
            return self._append_diagnostic_row(
                strategy=strategy,
                method=method,
                bar=bar,
                context=context,
                outcome="INVALID_TIMESTAMP",
                rule_evaluations=rule_evaluations,
                missing_inputs=["aligned_5m_timestamp"],
            )
        if not rule_evaluations["candle_available_at_decision"]:
            return self._append_diagnostic_row(
                strategy=strategy,
                method=method,
                bar=bar,
                context=context,
                outcome="INCOMPLETE_CANDLE",
                rule_evaluations=rule_evaluations,
                missing_inputs=["candle_close_availability"],
            )
        if not rule_evaluations["session_supported"]:
            return self._append_diagnostic_row(
                strategy=strategy,
                method=method,
                bar=bar,
                context=context,
                outcome="MISSING_REFERENCE_DATA",
                rule_evaluations=rule_evaluations,
                missing_inputs=["supported_session"],
            )
        if missing:
            rule_evaluations["required_inputs_available"] = False
            return self._append_diagnostic_row(
                strategy=strategy,
                method=method,
                bar=bar,
                context=context,
                outcome="MISSING_REFERENCE_DATA",
                rule_evaluations=rule_evaluations,
                missing_inputs=missing,
            )
        rule_evaluations["required_inputs_available"] = True
        key = (strategy, bar.exchange_time.date().isoformat())
        state = self.diagnostic_state.setdefault(key, {})
        bar_time = bar.exchange_time.time()
        if strategy == "ORB":
            is_decision_candle = bar_time == time(9, 15)
            rule_evaluations["decision_candle"] = is_decision_candle
            if not is_decision_candle:
                outcome = "DATA_READY_CONDITIONS_FALSE"
            else:
                long_break = bar.close > values["pdh"] and bar.close >= values["ema_200"]
                short_break = bar.close < values["pdl"] and bar.close <= values["ema_200"]
                rule_evaluations.update(
                    {
                        "long_level_and_trend": long_break,
                        "short_level_and_trend": short_break,
                        "candle_quality_rulebook_defined": False,
                    }
                )
                outcome = (
                    "RULEBOOK_AMBIGUITY"
                    if long_break or short_break
                    else "DATA_READY_CONDITIONS_FALSE"
                )
        elif strategy == "IRF":
            if bar_time == time(9, 15):
                state["irh"] = values.get("irh")
                state["breakout_seen"] = False
                state["extension_count"] = 0
            irh = state.get("irh", values.get("irh"))
            close_above = irh is not None and bar.close > irh
            if close_above:
                state["breakout_seen"] = True
                state["extension_count"] = int(state.get("extension_count", 0)) + 1
            red_after_extension = bool(state.get("breakout_seen")) and state.get("extension_count", 0) > 1 and bar.close < bar.open
            rule_evaluations.update(
                {
                    "irh": irh,
                    "close_above_irh": close_above,
                    "extension_count": state.get("extension_count", 0),
                    "red_after_extension": red_after_extension,
                    "recent_support_definition_available": False,
                }
            )
            if red_after_extension:
                outcome = "RULEBOOK_AMBIGUITY"
            else:
                outcome = "DATA_READY_CONDITIONS_FALSE"
        elif strategy == "RF":
            # RF remains intentionally non-evaluable until the author confirms
            # the canonical exit/target.  Do not let it fall through to the
            # PDS/PDR zone-test diagnostics and look like a false condition.
            rule_evaluations["canonical_exit_defined"] = False
            outcome = "RULEBOOK_AMBIGUITY"
        else:
            if bar_time == time(9, 15):
                state["gap_gate"] = (
                    bar.open < values["pdc"] if strategy == "PDS" else bar.open > values["pdc"]
                )
                state["zone_tested"] = False
            gap_gate = bool(state.get("gap_gate", False))
            zone_low = values["pdl"] if strategy == "PDS" else values["pd_res_body"]
            zone_high = values["pd_supp_body"] if strategy == "PDS" else values["pdh"]
            zone_touched = bar.high >= zone_low and bar.low <= zone_high
            if zone_touched:
                state["zone_tested"] = True
            confirmation = (
                bar.close < bar.open if strategy == "PDS" else bar.close > bar.open
            )
            rule_evaluations.update(
                {
                    "gap_gate": gap_gate,
                    "zone_touched_exactly": zone_touched,
                    "zone_tested_before_or_now": bool(state.get("zone_tested", False)),
                    "confirmation_candle_direction": confirmation,
                    "proximity_rule_defined": False,
                    "quality_threshold_rule_defined": False if strategy == "PDS" else True,
                }
            )
            if gap_gate and state.get("zone_tested", False) and confirmation:
                outcome = "RULEBOOK_AMBIGUITY"
            else:
                outcome = "DATA_READY_CONDITIONS_FALSE"

        return self._append_diagnostic_row(
            strategy=strategy,
            method=method,
            bar=bar,
            context=context,
            outcome=outcome,
            rule_evaluations=rule_evaluations,
            missing_inputs=[],
        )

    def evaluate_closed_candle(
        self,
        bar: Bar,
        *,
        previous_day_levels: dict[str, float] | None = None,
        replay: bool = False,
        candle_complete: bool = True,
        stale_underlying: bool = False,
        history_sufficient: bool = True,
        indicator_ready: bool = True,
        session_supported: bool = True,
        strategy_condition: bool | None = None,
        internal_error: str | None = None,
    ) -> None:
        """Evaluate one completed candle and record a conservative rejection.

        The optional gates are diagnostic inputs only.  They make each safety
        rejection explicit in tests and reports; they never create an order or
        paper fill.
        """
        is_aligned = bar.exchange_time.minute % 5 == 0 and bar.exchange_time.second == 0
        future = bar.exchange_time > datetime.now(TZ_IST) and not replay
        base_evaluation = {
            "completed_candle": candle_complete,
            "aligned_to_5m": is_aligned,
            "future_observation": future,
            "previous_day_levels_available": bool(previous_day_levels),
            "stale_underlying": stale_underlying,
            "history_sufficient": history_sufficient,
            "indicator_ready": indicator_ready,
            "session_supported": session_supported,
            "strategy_condition": strategy_condition,
        }
        for strategy, method, confidence in self.METHODS:
            if internal_error:
                reason = f"INTERNAL_ERROR:{internal_error}"
                status = "REJECTED"
            elif not candle_complete or not is_aligned or future:
                reason = "REJECTED_INCOMPLETE_OR_FUTURE_CANDLE"
                status = "REJECTED"
            elif not session_supported:
                reason = "REJECTED_MARKET_SESSION_RESTRICTION"
                status = "REJECTED"
            elif stale_underlying:
                reason = "REJECTED_STALE_UNDERLYING"
                status = "REJECTED"
            elif not history_sufficient:
                reason = "REJECTED_INSUFFICIENT_HISTORY"
                status = "REJECTED"
            elif not indicator_ready:
                reason = "REJECTED_INDICATOR_NOT_READY"
                status = "REJECTED"
            elif confidence == "UNEXECUTABLE_RULEBOOK":
                reason = "REJECTED_RF_EXIT_UNSPECIFIED"
                status = "REJECTED"
            elif not previous_day_levels:
                reason = "REJECTED_MISSING_POINT_IN_TIME_LEVELS"
                status = "REJECTED"
            elif strategy_condition is False:
                reason = "REJECTED_STRATEGY_CONDITION_FALSE"
                status = "REJECTED"
            else:
                reason = "REJECTED_RULEBOOK_AMBIGUITY_PENDING_DEVELOPER_CONFIRMATION"
                status = "REJECTED"
            evaluation = dict(base_evaluation)
            evaluation["rulebook_confidence"] = confidence
            evaluation["rejection_category"] = classify_rejection_reason(reason)
            self.rows.append(
                {
                    "strategy": strategy,
                    "method": method,
                    "signal_date": bar.exchange_time.date().isoformat(),
                    "signal_time": bar.exchange_time.isoformat(),
                    "direction": "NONE",
                    "input_candle_open": bar.open,
                    "input_candle_high": bar.high,
                    "input_candle_low": bar.low,
                    "input_candle_close": bar.close,
                    "input_candle_timestamp": bar.exchange_time.isoformat(),
                    "previous_day_levels": json.dumps(previous_day_levels or {}, sort_keys=True),
                    "rule_evaluations": json.dumps(evaluation, sort_keys=True),
                    "status": status,
                    "reason_code": reason,
                    "rejection_category": classify_rejection_reason(reason),
                    "rulebook_confidence": confidence,
                    "execution_created": False,
                }
            )

    def export_csv(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fields = list(self.rows[0]) if self.rows else ["status"]
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.rows)
        return target
