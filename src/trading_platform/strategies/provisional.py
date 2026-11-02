"""Loader for clearly-labelled PROVISIONAL strategy interpretations.

The author PDFs leave several numeric/structural rules undefined.  Where code needs a
value anyway, it lives in ``configs/strategy_provisional/<strategy>.yaml`` as::

    min_body_ratio:
      value: 0.45
      status: PROVISIONAL        # never "author-approved"
      why: PDF says "good body" without a number

Defaults equal the values the frozen baseline engine used, so loading this file is NOT
a parameter optimization.  A strategy that depends on any PROVISIONAL value stays
SHADOW_ONLY unless a separately approved research-paper configuration says otherwise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[3]
PROVISIONAL_DIR = _ROOT / "configs" / "strategy_provisional"


def load_provisional(name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return ``{key: value}``; explicit ``overrides`` (tests/config) win over the file."""
    path = PROVISIONAL_DIR / f"{name.lower()}.yaml"
    values: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
        for key, entry in raw.items():
            values[key] = entry["value"] if isinstance(entry, dict) and "value" in entry else entry
    if overrides:
        values.update(overrides)
    return values


def provisional_manifest(name: str) -> list[dict[str, Any]]:
    """List of {key,value,status,why} for display in the dashboard / reports."""
    path = PROVISIONAL_DIR / f"{name.lower()}.yaml"
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or {}
    out = []
    for key, entry in raw.items():
        if isinstance(entry, dict):
            out.append({"key": key, "value": entry.get("value"), "status": entry.get("status", "PROVISIONAL"), "why": entry.get("why", "")})
        else:
            out.append({"key": key, "value": entry, "status": "PROVISIONAL", "why": ""})
    return out
