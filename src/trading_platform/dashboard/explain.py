"""Trade explanation built ONLY from fields the engine recorded.

``explain_trade`` never infers or guesses.  Anything absent from the row (or from its JSON
blobs) is reported as :data:`NOT_RECORDED` and listed in ``result["missing"]``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from trading_platform.dashboard import text as dash_text
from trading_platform.live import labels

NOT_RECORDED = "not recorded"

_PREV_DAY_KEYS = dash_text.PREVIOUS_DAY_KEYS
_CONFIRMATION_KEYS = ("confirmation_rule", "confirmation", "confirm_rule", "confirmation_candle")
_EXIT_CONDITION_KEYS = ("condition", "exit_condition", "description", "detail", "trigger", "text")


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    if isinstance(value, str) and value.strip().lower() in {"", "nan", "nat", "none", "null"}:
        return False
    return True


def _val(row: Mapping[str, Any], key: str) -> Any:
    v = row.get(key)
    return v if _present(v) else NOT_RECORDED


def parse_json_field(value: Any) -> Any | None:
    """Parse a recorded JSON blob; None if absent, empty or invalid."""
    if not _present(value):
        return None
    if isinstance(value, dict | list):
        return value or None
    try:
        parsed = json.loads(str(value))
    except ValueError:
        return None
    return parsed if parsed not in ({}, [], "") else None


def _split_conditions(raw: Any) -> dict[str, Any] | None:
    """Normalise recorded entry conditions to satisfied / failed / details (None if empty)."""
    if raw is None:
        return None
    satisfied: list[str] = []
    failed: list[str] = []
    details: dict[str, Any] = {}
    if isinstance(raw, list):
        satisfied = [str(x) for x in raw]
    elif isinstance(raw, dict):
        for key in ("satisfied", "conditions_satisfied", "passed"):
            if isinstance(raw.get(key), list):
                satisfied += [str(x) for x in raw[key]]
        for key in ("failed", "conditions_failed"):
            if isinstance(raw.get(key), list):
                failed += [str(x) for x in raw[key]]
        used = {"satisfied", "conditions_satisfied", "passed", "failed", "conditions_failed"}
        rest = {k: v for k, v in raw.items() if k not in used}
        for k, v in rest.items():
            if isinstance(v, bool):
                (satisfied if v else failed).append(str(k))
            else:
                details[str(k)] = v
    else:
        details["value"] = raw
    return {"satisfied": satisfied, "failed": failed, "details": details}


def _levels(raw: Any, strategy: str | None = None) -> dict[str, Any] | None:
    """Recorded levels split into previous-day ones and the rest.  Strategy-internal names
    (``pdh``, ``prev_close``, ``ema_200`` ...) are mapped onto the standard names first."""
    if not isinstance(raw, dict):
        return None
    norm = dash_text.normalize_levels(raw, strategy)
    prev = {k: norm[k] for k in norm if str(k).upper() in _PREV_DAY_KEYS and _present(norm[k])}
    other = {k: v for k, v in norm.items() if k not in prev}
    return {"previous_day": prev, "other": other}


def _confirmation(conditions_raw: Any, levels_raw: Any, trigger_raw: Any) -> Any:
    for blob in (conditions_raw, levels_raw, trigger_raw):
        if isinstance(blob, dict):
            for key in _CONFIRMATION_KEYS:
                if _present(blob.get(key)):
                    return blob[key]
    return NOT_RECORDED


def _exit_condition(exit_detail: Any) -> Any:
    if isinstance(exit_detail, dict):
        for key in _EXIT_CONDITION_KEYS:
            if _present(exit_detail.get(key)) and isinstance(exit_detail[key], str | int | float):
                return exit_detail[key]
    return NOT_RECORDED


def explain_trade(row: Mapping[str, Any]) -> dict[str, Any]:
    """Structured explanation of one closed trade (a trades.csv row as a mapping).

    Returns ``{"trade_id", "opened": {...}, "closed": {...}, "missing": [...]}``.  ``opened``
    and ``closed`` hold only recorded values; unrecorded ones equal :data:`NOT_RECORDED`.
    """
    conditions_raw = parse_json_field(row.get("entry_conditions_json"))
    levels_raw = parse_json_field(row.get("levels_json"))
    trigger_raw = parse_json_field(row.get("trigger_candle_json"))
    exit_raw = parse_json_field(row.get("exit_detail_json"))

    conditions = _split_conditions(conditions_raw)
    levels = _levels(levels_raw, str(row.get("strategy") or ""))
    opened: dict[str, Any] = {
        "title": "WHY THE TRADE OPENED",
        "strategy": _val(row, "strategy"),
        "method": _val(row, "method"),
        "direction": _val(row, "direction"),
        "entry_reason": _val(row, "entry_reason"),
        "signal_time": _val(row, "signal_time"),
        "trigger_candle": trigger_raw if trigger_raw is not None else NOT_RECORDED,
        "entry_conditions": conditions if conditions is not None else NOT_RECORDED,
        "levels": levels if levels is not None else NOT_RECORDED,
        "previous_day_levels": (levels["previous_day"] or NOT_RECORDED) if levels else NOT_RECORDED,
        "confirmation_rule": _confirmation(conditions_raw, levels_raw, trigger_raw),
        "contract": {
            "symbol": _val(row, "symbol"),
            "option_type": _val(row, "option_type"),
            "strike": _val(row, "strike"),
            "expiry": _val(row, "expiry"),
            "lots": _val(row, "lots"),
            "quantity": _val(row, "quantity"),
        },
        "fill": {
            "entry_time": _val(row, "entry_time"),
            "entry_spot": _val(row, "entry_spot"),
            "entry_bid": _val(row, "entry_bid"),
            "entry_ask": _val(row, "entry_ask"),
            "entry_slippage": _val(row, "entry_slippage"),
            "entry_premium": _val(row, "entry_premium"),
        },
        "label": _val(row, "label"),
    }
    closed: dict[str, Any] = {
        "title": "WHY THE TRADE CLOSED",
        "exit_reason": _val(row, "exit_reason"),
        "exit_condition": _exit_condition(exit_raw),
        "exit_detail": exit_raw if exit_raw is not None else NOT_RECORDED,
        "exit_time": _val(row, "exit_time"),
        "underlying_at_trigger": _val(row, "exit_spot"),
        "original_stop": _val(row, "stop_spot"),
        "original_target": _val(row, "target_spot"),
        "option_exit_bid": _val(row, "exit_bid"),
        "exit_slippage": _val(row, "exit_slippage"),
        "exit_premium": _val(row, "exit_premium"),
        "entry_charges": _val(row, "entry_charges"),
        "exit_charges": _val(row, "exit_charges"),
        "total_charges": _val(row, "total_charges"),
        "gross_pnl": _val(row, "gross_pnl"),
        "net_pnl": _val(row, "net_pnl"),
    }
    missing = [f"opened.{k}" for k, v in opened.items() if v == NOT_RECORDED]
    missing += [
        f"opened.contract.{k}" for k, v in opened["contract"].items() if v == NOT_RECORDED
    ]
    missing += [f"opened.fill.{k}" for k, v in opened["fill"].items() if v == NOT_RECORDED]
    missing += [f"closed.{k}" for k, v in closed.items() if v == NOT_RECORDED]
    # derived wording (added after `missing` so it never counts as an unrecorded field)
    opened["strategy_label"] = dash_text.strategy_label_of(row)
    opened["reason_text"] = dash_text.reason_sentence(row)
    opened["rule_id"] = dash_text.rule_id_of(row)
    opened["direction_text"] = dash_text.direction_text(row)
    closed["exit_sentence"] = (
        None if closed["exit_reason"] == NOT_RECORDED
        else labels.exit_sentence(str(closed["exit_reason"]))
    )  # fmt: skip
    return {
        "trade_id": _val(row, "trade_id"),
        "opened": opened,
        "closed": closed,
        "missing": missing,
    }
