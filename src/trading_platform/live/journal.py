"""Durable, tiny persistence for the paper-trading app: trades CSV, decision events, error log.

Only three things are ever persisted here (the live path has NO market-data archive):

* ``TradeCsvJournal``  - one row per CLOSED virtual trade (authoritative P&L ledger).
* ``EventJournal``     - compact decision events (signals, rejections, entries, exits, state
  changes).  Ticks / quotes / candle arrays are refused so this can never become an archive.
* ``get_error_logger`` - a minimal redacting rotating error log.

All roots are parameters so REPLAY output goes to ``runtime/replay`` and never mixes with the
LIVE ledger.  Stdlib only (no DuckDB / SQLite / Parquet).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from trading_platform.core.redaction import (
    SensitiveDataFilter,
    redact_deep,
    redact_sensitive_text,
)
from trading_platform.live.models import IST, TradeRecord

MAX_EVENT_BYTES = 8192
MAX_EVENT_ARRAY_LEN = 40
MAX_EVENT_DICT_KEYS = 40
MAX_EVENT_DEPTH = 5
_MAX_KIND_LEN = 64
# Substrings that mark a field as bulk market data when its value is an ARRAY (or a dict
# with many entries).  Scalars / small dicts with such names are legitimate decision facts
# (``trigger_candle``, ``quote_age_seconds``, ``bid_received_at``, ``entry_quote``).
_FORBIDDEN_ARRAY_NAME_PARTS = ("tick", "quote", "candle", "ohlc", "depth", "bars", "book", "history")
_MAX_NAMED_DICT_KEYS = 12
_MAX_SERIES_RECORDS = 8
_FORBIDDEN_EVENT_KEYS = frozenset(
    {
        "tick",
        "ticks",
        "quotes",
        "candles",
        "bars",
        "ohlc",
        "ohlcv",
        "depth",
        "orderbook",
        "history",
        "tickhistory",
        "quotehistory",
        "candlehistory",
        "ticklist",
        "quotelist",
        "candlelist",
    }
)


class JournalError(RuntimeError):
    """Base class for journal failures."""


class JournalSchemaError(JournalError):
    """An existing file does not have the expected columns (fail closed, never append)."""


class EventRejectedError(JournalError, ValueError):
    """An event was refused (market-data-shaped, oversized, malformed)."""


# --------------------------------------------------------------------------- atomic file helpers


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: str | Path, data: bytes, *, fsync: bool = True, mode: int = 0o600) -> None:
    """Write ``data`` to ``path`` atomically: tmp file + fsync + ``os.replace`` + dir fsync.

    On any failure the temp file is removed and the previous ``path`` content is untouched.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if fsync:
        _fsync_dir(target.parent)


def atomic_write_text(path: str | Path, text: str, *, fsync: bool = True, mode: int = 0o600) -> None:
    """Atomic UTF-8 text write (see :func:`atomic_write_bytes`)."""
    atomic_write_bytes(path, text.encode("utf-8"), fsync=fsync, mode=mode)


def json_default(value: Any) -> Any:
    """``json.dumps`` default hook: datetimes/dates as ISO, enums by value, rest as ``str``."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set | frozenset):
        return sorted(value, key=str)
    return str(value)


def atomic_write_json(
    path: str | Path,
    obj: Any,
    *,
    fsync: bool = True,
    mode: int = 0o600,
    indent: int | None = 2,
) -> None:
    """Atomic JSON write (see :func:`atomic_write_bytes`)."""
    text = json.dumps(obj, indent=indent, default=json_default, allow_nan=False)
    atomic_write_text(path, text + "\n", fsync=fsync, mode=mode)


# --------------------------------------------------------------------------- trades CSV


def _flat(value: Any) -> Any:
    """Keep every trade on exactly one physical line (so a torn tail is always one row)."""
    if isinstance(value, str):
        return value.replace("\r", " ").replace("\n", " ")
    return value


@dataclass
class TradesLoad:
    """Result of reading ``trades.csv`` (never raises for torn/garbled content)."""

    rows: list[dict[str, str]] = field(default_factory=list)
    torn_tail: str | None = None  # unterminated last line (crash mid-write), ignored
    malformed: list[tuple[int, str]] = field(default_factory=list)  # (line_no, reason)
    missing_columns: list[str] = field(default_factory=list)
    unexpected_columns: list[str] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)
    file_exists: bool = False

    @property
    def schema_ok(self) -> bool:
        return not self.missing_columns and not self.unexpected_columns

    @property
    def clean(self) -> bool:
        return self.schema_ok and not self.malformed and self.torn_tail is None

    @property
    def trade_ids(self) -> set[str]:
        return {r["trade_id"] for r in self.rows if r.get("trade_id")}

    def problems(self) -> list[str]:
        out: list[str] = []
        if self.missing_columns:
            out.append(f"missing columns: {self.missing_columns}")
        if self.unexpected_columns:
            out.append(f"unexpected columns: {self.unexpected_columns}")
        if self.torn_tail is not None:
            out.append(f"torn last line ignored ({len(self.torn_tail)} chars)")
        out.extend(f"line {n}: {why}" for n, why in self.malformed)
        if self.duplicate_ids:
            out.append(f"duplicate trade_ids in file: {self.duplicate_ids}")
        return out


class TradeCsvJournal:
    """Append-only ledger of closed virtual trades, one fsynced row per trade.

    * Header is written exactly once.
    * ``append_trade`` is idempotent on ``trade_id`` (second call returns ``False``).
    * A torn (unterminated) last line from a crash is moved to ``<file>.torn`` before the next
      append so it can never corrupt a following row; nothing is silently discarded.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._columns = TradeRecord.csv_columns()
        self._ids: set[str] | None = None
        self._header_ok = False
        self.repairs: list[str] = []
        self.last_load: TradesLoad | None = None

    # ---- reading

    def read_trades(self) -> TradesLoad:
        """Parse the file tolerantly and report every problem found."""
        result = TradesLoad(file_exists=self.path.exists())
        if not result.file_exists:
            self.last_load = result
            return result
        text = self.path.read_bytes().decode("utf-8", errors="replace")
        if text == "":
            self.last_load = result
            return result
        lines = text.split("\n")
        tail = lines.pop()  # '' when the file ends with a newline
        if tail != "":
            result.torn_tail = tail
        header: list[str] | None = None
        for number, line in enumerate(lines, start=1):
            if line == "":
                continue
            try:
                fields = next(csv.reader([line]))
            except (csv.Error, StopIteration) as exc:
                result.malformed.append((number, f"unparseable ({exc})"))
                continue
            if header is None:
                header = fields
                result.missing_columns = [c for c in self._columns if c not in header]
                result.unexpected_columns = [c for c in header if c not in self._columns]
                continue
            if len(fields) != len(header):
                result.malformed.append(
                    (number, f"expected {len(header)} fields, found {len(fields)}")
                )
                continue
            result.rows.append(dict(zip(header, fields, strict=True)))
        if header is None and result.torn_tail is not None:
            # only a partial header exists
            result.missing_columns = list(self._columns)
        seen: set[str] = set()
        for row in result.rows:
            tid = row.get("trade_id", "")
            if tid in seen and tid not in result.duplicate_ids:
                result.duplicate_ids.append(tid)
            seen.add(tid)
        self.last_load = result
        return result

    def load_trades(self) -> list[dict[str, str]]:
        """Rows as dicts of raw strings; problems are available as ``self.last_load``."""
        return self.read_trades().rows

    def load_trades_df(self) -> Any:
        """Rows as a DataFrame with numeric columns coerced (pandas imported lazily)."""
        import pandas as pd

        load = self.read_trades()
        df = pd.DataFrame(load.rows, columns=self._columns)
        text_cols = {
            "trade_id", "session_id", "label", "strategy", "method", "direction", "option_type",
            "symbol", "exit_reason", "entry_reason", "entry_conditions_json", "levels_json",
            "trigger_candle_json", "exit_detail_json", "session_date", "expiry", "signal_time",
            "entry_time", "exit_time",
        }  # fmt: skip
        for col in df.columns:
            if col not in text_cols:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def trade_ids(self) -> set[str]:
        return set(self.read_trades().trade_ids)

    # ---- writing

    def _repair_torn_tail(self) -> None:
        """Quarantine an unterminated last line so the next append starts on a clean line."""
        if not self.path.exists():
            return
        size = self.path.stat().st_size
        if size == 0:
            return
        with open(self.path, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) == b"\n":
                return
            handle.seek(0)
            data = handle.read()
        cut = data.rfind(b"\n") + 1  # 0 when no newline at all
        torn = data[cut:]
        torn_path = self.path.with_name(self.path.name + ".torn")
        fd = os.open(torn_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "ab") as handle:
            handle.write(torn + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        with open(self.path, "r+b") as handle:
            handle.truncate(cut)
            handle.flush()
            os.fsync(handle.fileno())
        self.repairs.append(f"quarantined {len(torn)} torn bytes to {torn_path.name}")

    def _check_header(self) -> bool:
        """Return True when the file is absent/empty (header must be written)."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return True
        if not self._header_ok:
            with open(self.path, encoding="utf-8", errors="replace") as handle:
                first = handle.readline().rstrip("\n")
            header = next(csv.reader([first]), [])
            if header != self._columns:
                raise JournalSchemaError(
                    f"{self.path} header does not match TradeRecord.csv_columns(); refusing to "
                    f"append (missing={[c for c in self._columns if c not in header]}, "
                    f"unexpected={[c for c in header if c not in self._columns]})"
                )
            self._header_ok = True
        return False

    def append_trade(self, trade: TradeRecord) -> bool:
        """Append one closed trade.  Returns ``False`` (no write) if ``trade_id`` is present."""
        if not isinstance(trade, TradeRecord):
            raise TypeError("append_trade expects a TradeRecord")
        if not trade.trade_id:
            raise ValueError("trade_id must be non-empty")
        row = trade.to_row()
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._repair_torn_tail()
            need_header = self._check_header()
            if self._ids is None:
                self._ids = self.read_trades().trade_ids
            if trade.trade_id in self._ids:
                return False
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            if need_header:
                writer.writerow(self._columns)
            writer.writerow([_flat(row[c]) for c in self._columns])
            created = not self.path.exists()
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, buffer.getvalue().encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
            if created:
                _fsync_dir(self.path.parent)
            self._header_ok = True
            self._ids.add(trade.trade_id)
            return True


# --------------------------------------------------------------------------- decision events


def _norm_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _deep_redact(value: Any) -> Any:
    """Key-based payload redaction plus credential scrubbing of every string value."""
    return redact_deep(value)


def _is_record_array(value: list | tuple | set | frozenset) -> bool:
    """A long array of record-like dicts/lists (rows of numbers) is a price series."""
    if len(value) <= _MAX_SERIES_RECORDS:
        return False
    records = 0
    for item in value:
        if isinstance(item, Mapping) and len(item) >= 4:
            records += 1
        elif isinstance(item, list | tuple) and len(item) >= 4:
            records += 1
    return records * 2 >= len(value)


def _check_not_market_data(value: Any, path: str = "payload", depth: int = 1) -> None:
    """Refuse bulk / market-data-shaped payloads (size and shape, not just key names)."""
    is_container = isinstance(value, Mapping | list | tuple | set | frozenset)
    if is_container and depth > MAX_EVENT_DEPTH:
        raise EventRejectedError(
            f"event nesting at {path} exceeds {MAX_EVENT_DEPTH} levels; refusing to journal bulk data"
        )
    if isinstance(value, Mapping):
        if len(value) > MAX_EVENT_DICT_KEYS:
            raise EventRejectedError(
                f"event dict at {path} has {len(value)} keys (> {MAX_EVENT_DICT_KEYS}); "
                "refusing to journal bulk data"
            )
        for key, item in value.items():
            norm = _norm_key(key)
            if norm in _FORBIDDEN_EVENT_KEYS:
                raise EventRejectedError(
                    f"event key {path}.{key!r} looks like market data; events must stay small "
                    "decision records (no ticks/quotes/candle arrays)"
                )
            named = any(part in norm for part in _FORBIDDEN_ARRAY_NAME_PARTS)
            if named and isinstance(item, list | tuple | set | frozenset) and len(item) > 0:
                raise EventRejectedError(
                    f"event key {path}.{key!r} is an array with a market-data name; events must "
                    "stay small decision records (no ticks/quotes/candle arrays)"
                )
            if named and isinstance(item, Mapping) and len(item) > _MAX_NAMED_DICT_KEYS:
                raise EventRejectedError(
                    f"event key {path}.{key!r} is a {len(item)}-entry market-data-named dict; "
                    "refusing to journal bulk data"
                )
            _check_not_market_data(item, f"{path}.{key}", depth + 1)
    elif isinstance(value, list | tuple | set | frozenset):
        if len(value) > MAX_EVENT_ARRAY_LEN:
            raise EventRejectedError(
                f"event array at {path} has {len(value)} items (> {MAX_EVENT_ARRAY_LEN}); "
                "refusing to journal bulk data"
            )
        if _is_record_array(value):
            raise EventRejectedError(
                f"event array at {path} looks like a series of {len(value)} market-data rows"
            )
        for index, item in enumerate(value):
            _check_not_market_data(item, f"{path}[{index}]", depth + 1)


@dataclass
class EventsRead:
    events: list[dict[str, Any]] = field(default_factory=list)
    torn_lines: list[str] = field(default_factory=list)
    file_exists: bool = False


class EventJournal:
    """Compact JSONL decision journal, one file per IST date (``events_YYYYMMDD.jsonl``)."""

    def __init__(self, directory: str | Path, *, fsync_each: bool = True) -> None:
        self.directory = Path(directory)
        self.fsync_each = fsync_each
        self._lock = threading.Lock()
        self._seq: int | None = None

    # ---- paths

    def path_for(self, day: date) -> Path:
        return self.directory / f"events_{day:%Y%m%d}.jsonl"

    @staticmethod
    def _ist_date(t: datetime) -> date:
        if t.tzinfo is None:
            return t.date()  # naive times are interpreted as IST wall clock
        return t.astimezone(IST).date()

    # ---- reading

    def read_events_with_report(self, day: date) -> EventsRead:
        path = self.path_for(day)
        result = EventsRead(file_exists=path.exists())
        if not result.file_exists:
            return result
        text = path.read_bytes().decode("utf-8", errors="replace")
        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                result.torn_lines.append(line[:200])
                continue
            if isinstance(obj, dict):
                result.events.append(obj)
            else:
                result.torn_lines.append(line[:200])
        return result

    def read_events(self, day: date) -> list[dict[str, Any]]:
        """Valid events for ``day``; torn/garbled lines are skipped (see the report variant)."""
        return self.read_events_with_report(day).events

    def _load_seq(self) -> int:
        files = sorted(self.directory.glob("events_*.jsonl")) if self.directory.exists() else []
        for path in reversed(files):
            text = path.read_bytes().decode("utf-8", errors="replace")
            for line in reversed(text.split("\n")):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and isinstance(obj.get("seq"), int):
                    return int(obj["seq"])
        return 0

    @property
    def last_seq(self) -> int:
        with self._lock:
            if self._seq is None:
                self._seq = self._load_seq()
            return self._seq

    # ---- writing

    def append_event(
        self, kind: str, payload: Mapping[str, Any] | None = None, t: datetime | None = None
    ) -> dict[str, Any]:
        """Journal one decision event and return the record written.

        Raises :class:`EventRejectedError` for market-data-shaped or oversized events (the
        caller decides whether that is fatal; nothing is written in that case).
        """
        if not isinstance(kind, str) or not kind.strip() or len(kind) > _MAX_KIND_LEN:
            raise EventRejectedError(f"invalid event kind {kind!r}")
        payload = dict(payload or {})
        _check_not_market_data(payload)
        when = t or datetime.now(IST)
        safe_payload = _deep_redact(json.loads(json.dumps(payload, default=json_default)))
        with self._lock:
            if self._seq is None:
                self._seq = self._load_seq()
            record = {
                "seq": self._seq + 1,
                "t": when.isoformat(),
                "kind": kind.strip(),
                "payload": safe_payload,
            }
            line = json.dumps(record, separators=(",", ":"), default=json_default) + "\n"
            size = len(line.encode("utf-8"))
            if size > MAX_EVENT_BYTES:
                raise EventRejectedError(
                    f"event {kind!r} is {size} bytes (> {MAX_EVENT_BYTES}); events must stay compact"
                )
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.path_for(self._ist_date(when))
            created = not path.exists()
            prefix = ""
            if not created and path.stat().st_size > 0:
                with open(path, "rb") as handle:
                    handle.seek(-1, os.SEEK_END)
                    if handle.read(1) != b"\n":
                        prefix = "\n"  # isolate a torn previous line
            with open(path, "a", encoding="utf-8", buffering=1, newline="\n") as handle:
                handle.write(prefix + line)
                handle.flush()
                if self.fsync_each:
                    os.fsync(handle.fileno())
            if created:
                os.chmod(path, 0o600)
                _fsync_dir(self.directory)
            self._seq += 1
            return record


# --------------------------------------------------------------------------- error log


class _RedactingFormatter(logging.Formatter):
    """Redact the *final* text, including exception tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_text(super().format(record))


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):  # type: ignore[no-untyped-def]
        stream = super()._open()
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass
        return stream


def get_error_logger(
    directory: str | Path,
    *,
    filename: str = "errors.log",
    max_bytes: int = 1_000_000,
    backup_count: int = 3,
) -> logging.Logger:
    """Return the minimal redacting rotating error logger writing ``directory/filename``.

    Idempotent per file.  Messages, arguments and tracebacks are credential-scrubbed; the
    logger does not propagate (so nothing leaks to the root handlers).
    """
    path = (Path(directory) / filename).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    name = f"trading_platform.live.errors.{path}"
    logger = logging.getLogger(name)
    if any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        return logger
    handler = _PrivateRotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    handler.setLevel(logging.WARNING)
    handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
    handler.addFilter(SensitiveDataFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    return logger


def trade_ids_of(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    """Convenience: the set of non-empty ``trade_id`` values in loaded trade rows."""
    return {str(r["trade_id"]) for r in rows if r.get("trade_id")}
