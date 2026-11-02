"""Human-readable wording for the dashboard (pure functions, no Streamlit, no I/O).

Wraps the shared ``trading_platform.live.labels`` so the dashboard, the terminal and the
journals describe a trade the same way: ``ORB M1`` instead of the engine rule id, a sentence
instead of ``ORB_METHOD_1_PDH_BREAKOUT``.  The raw rule id is only ever a *secondary* detail.

Nothing here invents a rule: an unknown id degrades to a readable form of itself.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from trading_platform.live import labels

NOT_RECORDED = "not recorded"
STRATEGY_PREFIXES = {"ORB", "PDS", "PDR", "IRF", "RF"}
KNOWN_EXIT_REASONS = {"TARGET", "STOP", "EOD", "RISK", "STRATEGY_EXIT", "SHUTDOWN_UNRESOLVED"}

# recorded (strategy-internal, lower-case) level names -> the names the chart / tables use
_LEVEL_ALIASES = {
    "pdh": "PDH",
    "pdl": "PDL",
    "pdc": "PDC",
    "prev_close": "PDC",
    "ema_200": "EMA200",
    "ema200": "EMA200",
    "irh": "IRH",
    "irl": "IRL",
}
PREVIOUS_DAY_KEYS = ("PDH", "PDL", "PDC")


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return not (isinstance(value, str) and value.strip().lower() in {"", "nan", "nat", "none", "null"})


def looks_like_rule_id(value: Any) -> bool:
    """``ORB_METHOD_1_PDH_BREAKOUT``-shaped machine id (not a sentence, not ``METHOD_1``)."""
    text = str(value or "").strip()
    return bool(
        re.fullmatch(r"[A-Z0-9]+(?:_[A-Z0-9]+)+", text) and text.split("_", 1)[0] in STRATEGY_PREFIXES
    )


def rule_id_of(record: Mapping[str, Any] | None) -> str | None:
    """The raw engine rule id recorded on a trade / position / signal, if any."""
    for key in ("rule_id", "entry_reason", "reason", "method"):
        value = (record or {}).get(key)
        if _present(value) and looks_like_rule_id(value):
            return str(value).strip()
    return None


def reason_sentence(record: Mapping[str, Any] | None) -> str:
    """Why the trade opened, as a sentence.  ``reason_text`` (engine) wins, then the rule id."""
    rec = record or {}
    given = rec.get("reason_text")
    if _present(given):
        return str(given).strip()
    rid = rule_id_of(rec)
    if rid:
        return labels.rule_sentence(rid)
    for key in ("entry_reason", "reason"):
        if _present(rec.get(key)):
            return str(rec[key]).strip()
    return NOT_RECORDED


def strategy_label_of(record: Mapping[str, Any] | None) -> str:
    """Short label such as ``ORB M1`` (engine ``strategy_label`` wins)."""
    rec = record or {}
    if _present(rec.get("strategy_label")):
        return str(rec["strategy_label"]).strip()
    name = str(rec.get("strategy") or "").strip()
    if not name:
        return "n/a"
    method = rule_id_of(rec) or (str(rec["method"]) if _present(rec.get("method")) else None)
    return labels.strategy_label(name, method)


def direction_text(record: Mapping[str, Any] | None) -> str:
    rec = record or {}
    if _present(rec.get("direction_label")):
        return str(rec["direction_label"]).strip()
    option_type = rec.get("option_type") if _present(rec.get("option_type")) else None
    direction = rec.get("direction") if _present(rec.get("direction")) else None
    if option_type is None and direction is None:
        return "n/a"
    return labels.direction_label(direction, option_type)


def exit_reason_from_text(text: Any) -> str | None:
    """Leading token of an engine marker text (``'TARGET @ 107.45'`` -> ``TARGET``)."""
    parts = str(text or "").strip().split()
    if not parts:
        return None
    token = parts[0].strip(":,").upper()
    return token if token in KNOWN_EXIT_REASONS else None


def option_side_from_text(text: Any) -> str | None:
    """``CE`` / ``PE`` from an engine ENTRY marker text (``'ORB CE @ 74.45'``)."""
    for token in str(text or "").upper().replace("@", " ").split():
        if token in {"CE", "PE"}:
            return token
    return None


def normalize_levels(levels: Mapping[str, Any] | None, strategy: str | None = None) -> dict[str, Any]:
    """Map recorded level names onto the chart's names (``pdh`` -> ``PDH``, ``ema_200`` ->
    ``EMA200``).  Strategy zones (PDS ``zone_low/zone_top``, PDR ``zone_bottom/zone_top``) become
    ``support_zone`` / ``resistance_zone``.  Unknown keys pass through unchanged; an already
    canonical key is never overwritten by an alias."""
    out: dict[str, Any] = {}
    raw = dict(levels or {})
    for key, value in raw.items():
        name = _LEVEL_ALIASES.get(str(key).lower(), str(key))
        if name in out and name != str(key):
            continue
        out[name] = value
    name = (strategy or "").upper()
    if name == "PDS" and "support_zone" not in out:
        lo, hi = raw.get("zone_low"), raw.get("zone_top")
        if _present(lo) and _present(hi):
            out["support_zone"] = [lo, hi]
    elif name == "PDR" and "resistance_zone" not in out:
        lo, hi = raw.get("zone_bottom"), raw.get("zone_top")
        if _present(lo) and _present(hi):
            out["resistance_zone"] = [lo, hi]
    return out


def fmt_level(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return f"{float(value):,.2f}"
    if isinstance(value, list | tuple) and len(value) == 2:
        return " - ".join(fmt_level(v) for v in value)
    return str(value)


def format_levels(levels: Mapping[str, Any]) -> str:
    return ", ".join(f"{k} {fmt_level(v)}" for k, v in levels.items())


def format_candle(candle: Any) -> str:
    """Readable ``O / H / L / C`` line for a recorded trigger candle (never raw JSON)."""
    if not isinstance(candle, Mapping):
        return NOT_RECORDED
    parts = []
    for key, name in (("o", "O"), ("h", "H"), ("l", "L"), ("c", "C")):
        value = candle.get(key)
        parts.append(f"{name} {fmt_level(value)}" if isinstance(value, int | float) else f"{name} n/a")
    when = str(candle.get("time") or candle.get("t") or "").strip()
    close = str(candle.get("close_time") or "").strip()
    head = ""
    if when:
        head = f"5m candle opened {_hhmm(when)}" + (f", closed {_hhmm(close)}" if close else "")
        head += ": "
    return head + "  ".join(parts)


def _hhmm(value: str) -> str:
    match = re.search(r"(\d{2}:\d{2})(?::\d{2})?", value)
    return match.group(1) if match else value


def format_details(details: Mapping[str, Any]) -> str:
    def one(value: Any) -> str:
        return fmt_level(value) if isinstance(value, int | float | list | tuple) else str(value)

    return "; ".join(f"{k}: {one(v)}" for k, v in details.items())
