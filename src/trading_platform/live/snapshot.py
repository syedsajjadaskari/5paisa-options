"""Runtime snapshot ``runtime/live_state.json`` - the single writer/reader of the file.

Written atomically (tmp + rename) by the engine process at ~1 Hz and read by the terminal
``status`` command and the Streamlit dashboard.  Readers must tolerate a missing, partial,
corrupt or stale file, so :func:`read_snapshot` never raises.  Every key and every free-text
string value (event text, connection notes, strategy notes) is credential-scrubbed before the
write (same deep scrub as the event journal).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from trading_platform.core.redaction import redact_deep
from trading_platform.live.journal import atomic_write_json
from trading_platform.live.models import IST, SCHEMA_VERSION, ist_now

log = logging.getLogger(__name__)

MAX_CANDLES = 80
MAX_RECENT_EVENTS = 50
MAX_MARKERS = 200
DEFAULT_STALE_AFTER_SECONDS = 10.0

REQUIRED_KEYS = (
    "schema_version",
    "written_at",
    "session",
    "connection",
    "spot",
    "candles",
    "markers",
    "account",
    "risk",
    "strategies",
    "position",
    "recent_events",
)
OPTIONAL_KEYS = (
    "heartbeat_age_note",
    "current_candle",
    "quotes",
    "levels",
    "last_event",
    "last_closed_trade",
)


class SnapshotStatus(StrEnum):
    OK = "OK"
    STALE = "STALE"
    MISSING = "MISSING"
    CORRUPT = "CORRUPT"


@dataclass(frozen=True)
class SnapshotView:
    """What a reader got.  ``data`` is present for OK and STALE, ``None`` otherwise."""

    data: dict[str, Any] | None
    status: SnapshotStatus
    age_seconds: float | None
    reason: str = ""

    @property
    def is_fresh(self) -> bool:
        return self.status is SnapshotStatus.OK


@dataclass
class SnapshotWriteResult:
    ok: bool
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    skipped: bool = False  # throttled, nothing written


def build_empty_snapshot(
    *,
    session_id: str = "",
    session_date: str = "",
    mode: str = "LIVE",
    label: str = "",
    pid: int | None = None,
    started_at: str = "",
    engine_state: str = "STARTING",
    state_reason: str = "",
    market_status: str = "CLOSED",
    starting_capital: float = 0.0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A valid, data-free snapshot used before the first real data arrives."""
    stamp = now or ist_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "written_at": stamp.isoformat(),
        "heartbeat_age_note": "reader computes age from written_at",
        "session": {
            "session_id": session_id,
            "date": session_date or stamp.astimezone(IST).date().isoformat(),
            "mode": mode,
            "label": label,
            "pid": pid,
            "started_at": started_at,
            "engine_state": engine_state,
            "state_reason": state_reason,
            "market_status": market_status,
        },
        "connection": {
            "broker": "5paisa",
            "status": "DISCONNECTED",
            "last_valid_quote_at": None,
            "data_age_seconds": None,
            "stale": True,
            "candle_source": None,
            "candle_trusted": False,
            "notes": [],
        },
        "spot": {
            "price": None,
            "exchange_time": None,
            "day_high": None,
            "day_low": None,
            "prev_close": None,
        },
        "current_candle": None,
        "quotes": {"CE": None, "PE": None},
        "levels": {},
        "candles": [],
        "markers": [],
        "account": {
            "starting_capital": starting_capital,
            "cash": starting_capital,
            "equity": starting_capital,
            "realized_pnl": 0.0,
            "session_realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "session_open_equity": starting_capital,
            "trades_today": 0,
            "wins_today": 0,
            "losses_today": 0,
            "daily_loss_limit": 0.0,
            "daily_loss_used": 0.0,
        },
        "risk": {
            "state": "OK",
            "max_daily_loss_inr": 0.0,
            "entry_cutoff": "14:30",
            "square_off": "15:15",
            "cutoff_passed": False,
        },
        "strategies": [],
        "position": None,
        "last_event": None,
        "recent_events": [],
        "last_closed_trade": None,
    }


def _bounded(value: Any, limit: int, name: str, warnings: list[str]) -> Any:
    if isinstance(value, list) and len(value) > limit:
        warnings.append(f"{name} trimmed from {len(value)} to {limit} (oldest dropped)")
        return value[-limit:]
    return value


class SnapshotWriter:
    """Atomic, bounded, redacted snapshot writer.  Safe to call at ~1 Hz from one thread."""

    def __init__(
        self,
        path: str | Path,
        *,
        session_id: str = "",
        session_date: str = "",
        mode: str = "LIVE",
        label: str = "",
        pid: int | None = None,
        started_at: str = "",
        min_interval_seconds: float = 0.0,
    ) -> None:
        self.path = Path(path)
        self.session_fields: dict[str, Any] = {
            "session_id": session_id,
            "date": session_date,
            "mode": mode,
            "label": label,
            "pid": pid,
            "started_at": started_at,
        }
        self.min_interval_seconds = min_interval_seconds
        self._last_write = float("-inf")
        self._lock = threading.Lock()

    def write(self, data: Mapping[str, Any], *, now: datetime | None = None) -> SnapshotWriteResult:
        """Validate, bound and atomically write ``data`` (the caller's dict is not modified).

        Never raises for content or I/O problems; inspect the returned result.  Missing
        required keys are filled from the empty skeleton and reported as warnings.
        """
        if not isinstance(data, Mapping):
            raise TypeError("snapshot data must be a mapping")
        with self._lock:
            if (
                self.min_interval_seconds > 0
                and time.monotonic() - self._last_write < self.min_interval_seconds
            ):
                return SnapshotWriteResult(ok=True, skipped=True)
            stamp = now or ist_now()
            warnings: list[str] = []
            doc = dict(data)
            skeleton = build_empty_snapshot(now=stamp)
            for key in REQUIRED_KEYS:
                if key not in doc:
                    warnings.append(f"required key {key!r} missing; filled with empty default")
                    doc[key] = skeleton[key]
            for key in OPTIONAL_KEYS:
                if key not in doc:
                    log.debug("snapshot optional key %s missing", key)
            session = dict(doc["session"]) if isinstance(doc["session"], Mapping) else {}
            for key, value in self.session_fields.items():
                if value not in ("", None) and session.get(key) in (None, ""):
                    session[key] = value
            doc["session"] = session
            doc["schema_version"] = SCHEMA_VERSION
            doc["written_at"] = stamp.isoformat()
            doc["candles"] = _bounded(doc["candles"], MAX_CANDLES, "candles", warnings)
            doc["recent_events"] = _bounded(
                doc["recent_events"], MAX_RECENT_EVENTS, "recent_events", warnings
            )
            doc["markers"] = _bounded(doc["markers"], MAX_MARKERS, "markers", warnings)
            for key in ("candles", "recent_events", "markers", "strategies"):
                if not isinstance(doc[key], list):
                    warnings.append(f"{key} is not a list; replaced with empty list")
                    doc[key] = []
            for message in warnings:
                log.warning("snapshot: %s", message)
            try:
                atomic_write_json(self.path, redact_deep(doc), fsync=False, indent=None)
            except (OSError, ValueError, TypeError) as exc:
                return SnapshotWriteResult(
                    ok=False, warnings=warnings, error=f"{type(exc).__name__}: {exc}"
                )
            self._last_write = time.monotonic()
            return SnapshotWriteResult(ok=True, warnings=warnings)


def read_snapshot(
    path: str | Path,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> SnapshotView:
    """Read the snapshot; never raises.  Age is computed from ``written_at``."""
    target = Path(path)
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return SnapshotView(None, SnapshotStatus.MISSING, None, f"{target} does not exist")
    except OSError as exc:
        return SnapshotView(None, SnapshotStatus.CORRUPT, None, f"cannot read snapshot: {exc}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return SnapshotView(None, SnapshotStatus.CORRUPT, None, f"invalid JSON (partial?): {exc}")
    if not isinstance(data, dict):
        return SnapshotView(None, SnapshotStatus.CORRUPT, None, "snapshot is not a JSON object")
    if data.get("schema_version") != SCHEMA_VERSION:
        return SnapshotView(
            None,
            SnapshotStatus.CORRUPT,
            None,
            f"unsupported schema_version {data.get('schema_version')!r}",
        )
    try:
        written = datetime.fromisoformat(str(data["written_at"]))
        if written.tzinfo is None:
            written = written.replace(tzinfo=IST)
    except (KeyError, TypeError, ValueError):
        return SnapshotView(None, SnapshotStatus.CORRUPT, None, "written_at missing or invalid")
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        return SnapshotView(None, SnapshotStatus.CORRUPT, None, f"missing required keys: {missing}")
    current = now or ist_now()
    age = (current - written).total_seconds()
    if age < 0:
        age = 0.0  # clock skew between writer and reader; treat as fresh
    if age > stale_after_seconds:
        return SnapshotView(
            data,
            SnapshotStatus.STALE,
            age,
            f"last update {age:.1f}s ago (> {stale_after_seconds:g}s): engine not running?",
        )
    return SnapshotView(data, SnapshotStatus.OK, age, "")
