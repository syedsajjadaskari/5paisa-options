"""Tolerant, read-only loaders for the dashboard.

Every loader returns an "empty but valid" result (and a human-readable problem string) when a
file is missing, partial or corrupt - the dashboard must never crash because the trading
process is not running or was killed mid-write.

Nothing here writes to disk, starts a process or imports broker code.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.dashboard import text as dash_text

IST = ZoneInfo("Asia/Kolkata")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ENV_VAR = "PAPER_RUNTIME_DIR"
DEFAULT_STALE_AFTER_SECONDS = 30.0

SNAPSHOT_OK = "OK"
SNAPSHOT_MISSING = "MISSING"
SNAPSHOT_CORRUPT = "CORRUPT"
SNAPSHOT_STALE = "STALE"
SNAPSHOT_STOPPED = "STOPPED"
SNAPSHOT_WRONG_MODE = "WRONG_MODE"

# trades.csv columns (== live.models.TradeRecord.csv_columns(); duplicated so that the reader
# keeps working even while the engine modules are being edited).
TRADE_COLUMNS: list[str] = [
    "trade_id", "session_date", "session_id", "label", "strategy", "method", "direction",
    "option_type", "symbol", "expiry", "strike", "scrip_code", "lot_size", "lots", "quantity",
    "signal_time", "entry_time", "exit_time", "entry_spot", "exit_spot", "stop_spot",
    "target_spot", "entry_bid", "entry_ask", "entry_premium", "exit_bid", "exit_premium",
    "entry_slippage", "exit_slippage", "gross_pnl", "entry_charges", "exit_charges",
    "total_charges", "net_pnl", "exit_reason", "entry_reason", "entry_conditions_json",
    "levels_json", "trigger_candle_json", "exit_detail_json", "balance_after",
]  # fmt: skip
_NUMERIC_TRADE_COLUMNS = [
    "strike", "scrip_code", "lot_size", "lots", "quantity", "entry_spot", "exit_spot",
    "stop_spot", "target_spot", "entry_bid", "entry_ask", "entry_premium", "exit_bid",
    "exit_premium", "entry_slippage", "exit_slippage", "gross_pnl", "entry_charges",
    "exit_charges", "total_charges", "net_pnl", "balance_after",
]  # fmt: skip
_TIME_TRADE_COLUMNS = ["signal_time", "entry_time", "exit_time"]


# --------------------------------------------------------------------------- paths


def resolve_runtime_dir(env: dict[str, str] | None = None) -> Path:
    """``$PAPER_RUNTIME_DIR`` if set, otherwise ``<repo>/runtime``.  Relative values are
    resolved against the repo root so the result does not depend on the launch directory."""
    source = os.environ if env is None else env
    raw = (source.get(RUNTIME_ENV_VAR) or "").strip()
    path = Path(raw).expanduser() if raw else Path("runtime")
    return path if path.is_absolute() else PROJECT_ROOT / path


def parse_ts(value: Any) -> datetime | None:
    """Parse ISO / ``'... IST'`` strings into a *naive IST* datetime (None if unparseable)."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp | datetime):
        ts = pd.Timestamp(value)
    else:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "nat", "none", "null"}:
            return None
        if text.endswith(" IST"):
            text = text[:-4]
        try:
            ts = pd.Timestamp(text)
        except (ValueError, TypeError):
            return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert(IST).tz_localize(None)
    return ts.to_pydatetime()


def to_ist_naive(values: Any) -> pd.Series:
    """Vector form of :func:`parse_ts` (unparseable -> NaT)."""
    series = values if isinstance(values, pd.Series) else pd.Series(list(values))
    parsed = [parse_ts(v) for v in series]
    return pd.Series(pd.to_datetime(parsed), index=series.index)


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # NaN -> None


# --------------------------------------------------------------------------- snapshot


@dataclass
class SnapshotInfo:
    """Result of reading ``live_state.json``."""

    data: dict[str, Any] | None
    status: str  # SNAPSHOT_*
    path: Path
    written_at: datetime | None = None  # naive IST
    age_seconds: float | None = None
    message: str = ""

    @property
    def active(self) -> bool:
        """True only when a fresh snapshot of a running session was read."""
        return self.status == SNAPSHOT_OK and self.data is not None

    @property
    def mode(self) -> str:
        return str(((self.data or {}).get("session") or {}).get("mode") or "")

    @property
    def finished_replay(self) -> bool:
        """True for a REPLAY snapshot whose simulated session ended (not 'stale')."""
        return self.status == SNAPSHOT_STOPPED and self.mode.upper() == "REPLAY"

    @property
    def sim_time(self) -> datetime | None:
        """Simulated clock of a replay snapshot (``sim_time``, else its simulated ``written_at``)."""
        raw = (self.data or {}).get("sim_time")
        return parse_ts(raw) if raw else self.written_at

    def age_text(self) -> str:
        return format_age(self.age_seconds)


def format_age(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def load_snapshot(
    path: Path | str,
    *,
    now: datetime | None = None,
    stale_after: float = DEFAULT_STALE_AFTER_SECONDS,
    expect_mode: str | None = None,
) -> SnapshotInfo:
    """Read the runtime snapshot.  Never raises.

    ``expect_mode`` ('LIVE' / 'REPLAY'): a snapshot of the other mode is *not* treated as active
    (status ``WRONG_MODE``) so simulated state can never be presented as live state.
    ``now`` is a naive-IST datetime (injectable for tests).
    """
    path = Path(path)
    if not path.exists():
        return SnapshotInfo(None, SNAPSHOT_MISSING, path, message="no snapshot file")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("snapshot root is not an object")
    except (OSError, ValueError) as exc:
        return SnapshotInfo(None, SNAPSHOT_CORRUPT, path, message=f"unreadable snapshot: {exc}")

    written = parse_ts(raw.get("written_at"))
    clock = now or datetime.now(IST).replace(tzinfo=None)
    session = raw.get("session") if isinstance(raw.get("session"), dict) else {}
    mode = str(session.get("mode") or "")
    engine_state = str(session.get("engine_state") or "")
    is_replay = mode.upper() == "REPLAY"

    if is_replay:
        # A replay stamps written_at with the SIMULATED clock, which says nothing about whether
        # the process is still running.  Freshness comes from the file's modification time.
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, IST).replace(tzinfo=None)
        except OSError:
            mtime = None
        age = (clock - mtime).total_seconds() if mtime else None
    else:
        age = (clock - written).total_seconds() if written else None

    info = SnapshotInfo(raw, SNAPSHOT_OK, path, written_at=written, age_seconds=age)
    if expect_mode and mode and mode != expect_mode:
        info.status = SNAPSHOT_WRONG_MODE
        info.message = f"snapshot belongs to a {mode} session, not {expect_mode}"
    elif is_replay and engine_state in {"STOPPED", "SHUTTING_DOWN"}:
        info.status = SNAPSHOT_STOPPED
        info.message = "replay finished"
    elif age is None:
        info.status = SNAPSHOT_STALE
        info.message = "snapshot has no valid written_at timestamp"
    elif age > stale_after:
        info.status = SNAPSHOT_STALE
        info.message = f"last snapshot {format_age(age)} ago"
    elif engine_state in {"STOPPED", "SHUTTING_DOWN"}:
        info.status = SNAPSHOT_STOPPED
        info.message = f"session {engine_state}"
    return info


def snapshot_candles(snapshot: dict[str, Any] | None) -> pd.DataFrame:
    """Candles present in the snapshot (and nothing else).  Invalid rows are dropped, never
    repaired.  Columns: time (naive IST open), open, high, low, close, source."""
    rows = []
    for c in (snapshot or {}).get("candles") or []:
        if not isinstance(c, dict):
            continue
        t = parse_ts(c.get("t"))
        o, h, lo, cl = (_num(c.get(k)) for k in ("o", "h", "l", "c"))
        if t is None or None in (o, h, lo, cl):
            continue
        rows.append(
            {"time": t, "open": o, "high": h, "low": lo, "close": cl,
             "source": str(c.get("source") or "UNKNOWN")}
        )  # fmt: skip
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "source"])
    if not df.empty:
        df = df.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
    return df


def snapshot_markers(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalised ``markers`` list from the snapshot (bad rows dropped).

    EXIT markers carry ``exit_reason`` (the recorded field, else the leading token of the engine
    text such as ``'TARGET @ 107.45'``); ENTRY markers carry ``direction`` (recorded, else the
    CE/PE token of the text).  Nothing is invented: unparseable stays absent / UNKNOWN."""
    out = []
    for m in (snapshot or {}).get("markers") or []:
        if not isinstance(m, dict):
            continue
        t, price = parse_ts(m.get("t")), _num(m.get("price"))
        kind = str(m.get("kind") or "").upper()
        if t is None or price is None or kind not in {"SIGNAL", "ENTRY", "EXIT"}:
            continue
        text = str(m.get("text") or "")
        item: dict[str, Any] = {
            "kind": kind, "t": t, "price": price, "strategy": str(m.get("strategy") or ""),
            "trade_id": m.get("trade_id"), "text": text,
        }  # fmt: skip
        if kind == "EXIT":
            reason = str(m.get("exit_reason") or "").strip().upper() or dash_text.exit_reason_from_text(text)
            if reason:
                item["exit_reason"] = reason
        elif kind == "ENTRY":
            direction = str(m.get("direction") or "").strip() or dash_text.option_side_from_text(text)
            if direction:
                item["direction"] = direction
        premium = _num(m.get("premium"))
        if premium is not None:
            item["premium"] = premium
        out.append(item)
    return out


# --------------------------------------------------------------------------- trades


@dataclass
class TradesData:
    df: pd.DataFrame
    path: Path
    exists: bool
    problems: list[str] = field(default_factory=list)


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


def load_trades(path: Path | str) -> TradesData:
    """Load a trades.csv ledger into typed columns (naive-IST times, ``session_date`` as date)."""
    path = Path(path)
    if not path.exists():
        return TradesData(_empty_trades(), path, False)
    problems: list[str] = []
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False, on_bad_lines="skip")
    except (OSError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        if isinstance(exc, pd.errors.EmptyDataError):
            return TradesData(_empty_trades(), path, True)
        return TradesData(_empty_trades(), path, True, [f"trades file unreadable: {exc}"])
    missing = [c for c in TRADE_COLUMNS if c not in raw.columns]
    if missing:
        problems.append(f"trades file lacks columns: {', '.join(missing[:8])}")
    df = raw.reindex(columns=TRADE_COLUMNS + [c for c in raw.columns if c not in TRADE_COLUMNS])
    df = df[df["trade_id"].fillna("").astype(str).str.strip() != ""].copy()
    n_before = len(df)
    df = df.drop_duplicates("trade_id", keep="first")
    if len(df) != n_before:
        problems.append(f"{n_before - len(df)} duplicate trade_id row(s) ignored")
    for col in _NUMERIC_TRADE_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in _TIME_TRADE_COLUMNS:
        df[col] = to_ist_naive(df[col])
    sd = pd.to_datetime(df["session_date"], errors="coerce")
    sd = sd.fillna(df["exit_time"].dt.normalize()).fillna(df["entry_time"].dt.normalize())
    df["session_date"] = [None if pd.isna(v) else v.date() for v in sd]
    df["expiry"] = [
        None if pd.isna(v) else v.date() for v in pd.to_datetime(df["expiry"], errors="coerce")
    ]
    df = df.sort_values(["exit_time", "trade_id"], kind="stable").reset_index(drop=True)
    return TradesData(df, path, True, problems)


def split_replay_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(non_replay, replay) - rows labelled REPLAY/SIMULATED never count as LIVE ledger."""
    if df.empty:
        return df, df
    label = df["label"].fillna("").astype(str).str.upper()
    is_replay = label.str.contains("REPLAY") | label.str.contains("SIMULATED")
    return df[~is_replay].reset_index(drop=True), df[is_replay].reset_index(drop=True)


# --------------------------------------------------------------------------- events


def load_events(directory: Path | str, day: date, *, limit: int = 2000) -> list[dict[str, Any]]:
    """Decision events for ``day`` from ``events_YYYYMMDD.jsonl`` (torn/corrupt lines skipped)."""
    path = Path(directory) / f"events_{day:%Y%m%d}.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    events.append(rec)
    except OSError:
        return []
    return events[-limit:]


def signal_markers_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """SIGNAL markers from journaled signal events.  Only events that recorded both a time and a
    reference price become markers (nothing is guessed)."""
    out = []
    for ev in events:
        kind = str(ev.get("kind") or "")
        if "SIGNAL" not in kind.upper():
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        price = _num(payload.get("spot_reference"))
        t = parse_ts(payload.get("decision_time") or payload.get("candle_open_time") or ev.get("t"))
        if price is None or t is None:
            continue
        out.append(
            {"kind": "SIGNAL", "t": t, "price": price,
             "strategy": str(payload.get("strategy") or ""), "trade_id": None,
             "text": str(payload.get("reason") or payload.get("signal_id") or kind)}
        )  # fmt: skip
    return out


# --------------------------------------------------------------------------- historical data


def historical_5m_path(root: Path | None = None) -> Path:
    return (root or PROJECT_ROOT) / "data" / "processed" / "nifty" / "5m" / "nifty50_5m.parquet"


def historical_1d_path(root: Path | None = None) -> Path:
    return (root or PROJECT_ROOT) / "data" / "processed" / "nifty" / "1d" / "nifty50_1d.parquet"


def load_historical(path: Path | str) -> pd.DataFrame:
    """Verified historical OHLC (read-only).  Columns: timestamp (naive IST), open..close, date."""
    path = Path(path)
    cols = ["timestamp", "open", "high", "low", "close", "volume", "date"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - any parquet problem means "no historical data"
        return pd.DataFrame(columns=cols)
    if "timestamp" not in df.columns:
        return pd.DataFrame(columns=cols)
    df = df.copy()
    df["timestamp"] = to_ist_naive(df["timestamp"])
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    df["date"] = df["timestamp"].dt.date
    return df


def historical_dates(df5: pd.DataFrame) -> list[date]:
    return sorted(set(df5["date"])) if not df5.empty else []


def candles_for_date(df5: pd.DataFrame, day: date) -> pd.DataFrame:
    """Verified 5m candles for one session in the same shape as :func:`snapshot_candles`."""
    if df5.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "source"])
    sub = df5[df5["date"] == day]
    out = sub.rename(columns={"timestamp": "time"})[["time", "open", "high", "low", "close"]].copy()
    out["source"] = "HISTORICAL_FILE"
    return out.reset_index(drop=True)


def previous_day_levels(df1d: pd.DataFrame, day: date) -> dict[str, Any]:
    """PDH / PDL / PDC from the verified daily dataset (last session strictly before ``day``)."""
    if df1d.empty:
        return {}
    prior = df1d[df1d["date"] < day]
    if prior.empty:
        return {}
    last = prior.iloc[-1]
    return {
        "PDH": float(last["high"]), "PDL": float(last["low"]), "PDC": float(last["close"]),
        "prev_date": last["date"],
    }  # fmt: skip


# --------------------------------------------------------------------------- backtest outputs


@dataclass
class BacktestRun:
    run_id: str
    path: Path
    summary: dict[str, Any]
    trades: pd.DataFrame  # normalised, index points only
    problems: list[str] = field(default_factory=list)


_POINTS_ALIASES = {
    "trade_id": ["portfolio_trade_id", "trade_id", "candidate_trade_id"],
    "strategy": ["strategy_name", "strategy"],
    "method": ["method_id", "method"],
    "direction": ["direction"],
    "entry_time": ["executed_entry_time", "entry_time"],
    "exit_time": ["exit_time"],
    "entry_price": ["entry_price"],
    "exit_price": ["exit_price"],
    "stop": ["stop_loss", "stop"],
    "target": ["target"],
    "exit_reason": ["exit_reason"],
    "points_pnl": ["points_pnl", "pnl_points"],
}
POINTS_COLUMNS = list(_POINTS_ALIASES) + ["date"]


def normalise_points_trades(raw: pd.DataFrame) -> pd.DataFrame:
    """Map a backtest ``portfolio_trades`` frame to the dashboard's index-POINTS schema.

    Rows the portfolio rejected (``portfolio_decision`` != ACCEPTED) are dropped.
    """
    if raw.empty:
        return pd.DataFrame(columns=POINTS_COLUMNS)
    df = raw
    if "portfolio_decision" in df.columns:
        df = df[df["portfolio_decision"].astype(str).str.upper() == "ACCEPTED"]
    out = pd.DataFrame(index=df.index)
    for name, aliases in _POINTS_ALIASES.items():
        src = next((a for a in aliases if a in df.columns), None)
        out[name] = df[src] if src else None
    for col in ("entry_time", "exit_time"):
        out[col] = to_ist_naive(out[col])
    for col in ("entry_price", "exit_price", "stop", "target", "points_pnl"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "date" in df.columns:
        dates = pd.to_datetime(df["date"], errors="coerce")
    else:
        dates = out["entry_time"].dt.normalize()
    out["date"] = [None if pd.isna(v) else v.date() for v in dates]
    out = out.dropna(subset=["points_pnl"])
    return out.sort_values(["exit_time", "trade_id"], kind="stable").reset_index(drop=True)


def load_points_trades_csv(path: Path | str) -> tuple[pd.DataFrame, list[str]]:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=POINTS_COLUMNS), [f"{path.name} not found"]
    try:
        raw = pd.read_csv(path, keep_default_na=False, on_bad_lines="skip")
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        return pd.DataFrame(columns=POINTS_COLUMNS), [f"{path.name} unreadable: {exc}"]
    return normalise_points_trades(raw), []


def list_backtest_runs(root: Path | str) -> list[str]:
    """Run ids (newest name first) under ``reports/backtest`` that hold a portfolio_trades.csv."""
    root = Path(root)
    if not root.is_dir():
        return []
    runs = [p.name for p in root.iterdir() if p.is_dir() and (p / "portfolio_trades.csv").exists()]
    return sorted(runs, reverse=True)


def load_backtest_run(root: Path | str, run_id: str) -> BacktestRun:
    path = Path(root) / run_id
    problems: list[str] = []
    summary: dict[str, Any] = {}
    sp = path / "summary.json"
    if sp.exists():
        try:
            loaded = json.loads(sp.read_text(encoding="utf-8"))
            summary = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError) as exc:
            problems.append(f"summary.json unreadable: {exc}")
    else:
        problems.append("summary.json not found")
    trades, probs = load_points_trades_csv(path / "portfolio_trades.csv")
    return BacktestRun(run_id, path, summary, trades, problems + probs)


def legacy_portfolio_csv_path(root: Path | None = None) -> Path:
    return (root or PROJECT_ROOT) / "COMBINED_PORTFOLIO_TRADES.csv"


# --------------------------------------------------------------------------- config / disclosure


def configured_starting_capital(root: Path | None = None) -> float | None:
    """``account.starting_capital_inr`` from configs/live_paper.yaml (None if unreadable)."""
    path = (root or PROJECT_ROOT) / "configs" / "live_paper.yaml"
    try:
        import yaml

        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return _num((cfg.get("account") or {}).get("starting_capital_inr"))
    except Exception:  # noqa: BLE001 - config is optional for the reader
        return None


_DISCLOSURE_CACHE: dict[str, dict[str, Any]] = {}


def strategy_disclosure(name: str) -> dict[str, Any]:
    """Provisional-assumption disclosure for a strategy.

    ``{"manifest": [{key,value,status,why}], "rules": [str]}`` - both empty if the strategy
    modules are unavailable (the dashboard then says so rather than guessing).
    """
    key = name.upper()
    if key in _DISCLOSURE_CACHE:
        return _DISCLOSURE_CACHE[key]
    manifest: list[dict[str, Any]] = []
    rules: list[str] = []
    try:
        from trading_platform.strategies.provisional import provisional_manifest

        manifest = list(provisional_manifest(key))
    except Exception:  # noqa: BLE001
        manifest = []
    try:
        from trading_platform.live.authorization import provisional_rules

        rules = list(provisional_rules(key))
    except Exception:  # noqa: BLE001
        rules = []
    result = {"manifest": manifest, "rules": rules}
    _DISCLOSURE_CACHE[key] = result
    return result


# --------------------------------------------------------------------------- source bundle

SOURCE_LIVE = "Live paper"
SOURCE_REPLAY = "Replay (simulated)"
SOURCES = (SOURCE_LIVE, SOURCE_REPLAY)


@dataclass
class SourceBundle:
    """Everything the dashboard reads for one selected source (LIVE or REPLAY, never both)."""

    source: str
    root: Path
    snapshot: SnapshotInfo
    trades: TradesData
    events_dir: Path
    excluded_rows: int = 0  # trades.csv rows dropped because their label says REPLAY/SIMULATED

    @property
    def is_replay(self) -> bool:
        return self.source == SOURCE_REPLAY


def load_source(
    runtime_dir: Path | str,
    source: str,
    *,
    now: datetime | None = None,
    stale_after: float = DEFAULT_STALE_AFTER_SECONDS,
) -> SourceBundle:
    """Load snapshot + ledger for LIVE (``runtime/``) or REPLAY (``runtime/replay/``).

    The two are strictly separated: a REPLAY-mode snapshot is never accepted as LIVE, and rows
    labelled REPLAY/SIMULATED never enter the LIVE ledger view.
    """
    runtime = Path(runtime_dir)
    if source == SOURCE_REPLAY:
        root = runtime / "replay"
        candidates = [root / "live_state.json", runtime / "live_state.json"]
        snap = load_snapshot(candidates[0], now=now, stale_after=stale_after, expect_mode="REPLAY")
        if snap.status in {SNAPSHOT_MISSING, SNAPSHOT_WRONG_MODE}:
            alt = load_snapshot(candidates[1], now=now, stale_after=stale_after,
                                expect_mode="REPLAY")  # fmt: skip
            if alt.status not in {SNAPSHOT_MISSING, SNAPSHOT_WRONG_MODE}:
                snap = alt
        trades = load_trades(root / "journal" / "trades.csv")
        return SourceBundle(source, root, snap, trades, root / "journal")
    snap = load_snapshot(runtime / "live_state.json", now=now, stale_after=stale_after,
                         expect_mode="LIVE")  # fmt: skip
    trades = load_trades(runtime / "journal" / "trades.csv")
    live_rows, replay_rows = split_replay_rows(trades.df)
    trades = TradesData(live_rows, trades.path, trades.exists, trades.problems)
    return SourceBundle(source, runtime, snap, trades, runtime / "journal",
                        excluded_rows=len(replay_rows))  # fmt: skip


def effective_snapshot(info: SnapshotInfo) -> dict[str, Any] | None:
    """Snapshot content usable for display (fresh, stale or stopped), else None."""
    if info.status in {SNAPSHOT_OK, SNAPSHOT_STALE, SNAPSHOT_STOPPED}:
        return info.data
    return None
