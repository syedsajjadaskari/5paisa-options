"""Joint chronological portfolio backtest over the four authorized strategies (V3_PDF_CORRECTED).

What this is
------------
A clean historical backtest of the ORIGINAL (unoptimized) strategy configuration in NIFTY
INDEX POINTS.  There are no verified historical option prices, so this module never computes
option-rupee P&L; a points result does not validate option profitability.

Strategies: PDS Method 1 (bearish), ORB Method 1, IRF Method 2, PDR Retest Bounce (bullish).
RF is permanently excluded and never imported here.

How the portfolio is simulated
------------------------------
ONE chronological loop (``UnderlyingBacktestEngine.simulate``) feeds every bar to every
strategy, so no state machine ever misses a bar.  Signals are processed FIFO by signal time
with an explicit tie-break priority (``constants.PORTFOLIO_PRIORITY``: PDS, ORB, IRF, PDR).
The accepted signal is filled at the NEXT bar open plus slippage.  At most ONE position is
open at any time; a signal arriving while a position is open (or whose candle closes at/after
14:30, or that loses a same-candle tie-break) is REJECTED and recorded with a reason.  A
rejected signal is lost - strategies are not re-armed (they own their once-per-day flags).

Four distinct datasets are produced:

1. ``signals``            every raw engine signal with its standalone and portfolio disposition
2. ``standalone_trades``  each strategy run alone with the same engine
3. ``portfolio_trades``   accepted combined-portfolio trades (sequenced, non-overlapping)
4. ``rejected_signals``   signals the portfolio rejected, with reason and blocking id

``signal_id`` (= strategy:method:signal-candle-open-time) is carried onto every trade so the
datasets join.

``build_authorized_strategies()`` is the ONE place strategies are constructed; the paper app
imports it so paper and backtest cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_platform.backtesting.constants import (
    BAR_MINUTES,
    DEFAULT_SLIPPAGE_POINTS,
    ENGINE_VERSION_LABEL,
    ENTRY_CUTOFF_TIME,
    MAX_CONCURRENT_POSITIONS,
    PNL_UNIT_LABEL,
    PORTFOLIO_PRIORITY,
    SQUARE_OFF_TIME,
)
from trading_platform.backtesting.engine import (
    EngineSignalRecord,
    SignalStatus,
    UnderlyingBacktestEngine,
    UnderlyingSimulationResult,
    UnderlyingTradeRecord,
)
from trading_platform.backtesting.metrics import PointsMetrics, compute_points_metrics
from trading_platform.core.enums import (
    DataSource,
    Exchange,
    InstrumentType,
    Timeframe,
)
from trading_platform.core.models import Bar
from trading_platform.strategies.base import BaseStrategy
from trading_platform.strategies.irf import IRFStrategy
from trading_platform.strategies.orb import ORBStrategy
from trading_platform.strategies.pdr import PDRStrategy
from trading_platform.strategies.pds import PDSStrategy

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")

DEFAULT_WINDOW_START = date(2025, 9, 1)
DEFAULT_WINDOW_END = date(2026, 8, 31)

SCOPE_NOTE = (
    "NIFTY INDEX-POINT backtest of the ORIGINAL strategy configuration, run WITHOUT any "
    "optimization or parameter tuning.  Points are index points only: no historical option "
    "prices exist, so no option-rupee P&L is computed and nothing here validates option "
    "profitability.  The 2025-09..2026-08 window overlaps the data used when strategies and "
    "methods were selected (e.g. IRF Method 2 over Method 1), so treat results as a research "
    "candidate, not an out-of-sample estimate."
)

#: Frozen/verified locations a backtest run must never write into.
FROZEN_PREFIXES: tuple[str, ...] = (
    "reports/phase2d",
    "reports/phase3a",
    "reports/independent_codex_audit",
    "reports/manual_journal",
    "docs",
    "data",
)


# --------------------------------------------------------------------------- strategies

#: (name, class, constructor config).  Order == PORTFOLIO_PRIORITY.  RF is deliberately absent.
AUTHORIZED_STRATEGY_SPECS: tuple[tuple[str, type[BaseStrategy], dict[str, Any]], ...] = (
    ("PDS", PDSStrategy, {"method": "METHOD_1"}),
    ("ORB", ORBStrategy, {"method": "METHOD_1"}),
    ("IRF", IRFStrategy, {"method": "METHOD_2"}),
    ("PDR", PDRStrategy, {}),
)


def build_authorized_strategies() -> dict[str, BaseStrategy]:
    """Fresh instances of the four authorized strategies, ordered by tie-break priority.

    The single construction point shared by the backtest and the paper app.  Every call
    returns NEW objects (strategies carry per-day state).  RF is never constructed.
    """
    strategies = {name: cls(dict(cfg)) for name, cls, cfg in AUTHORIZED_STRATEGY_SPECS}
    if tuple(strategies) != tuple(PORTFOLIO_PRIORITY):  # defensive: lists must agree
        raise RuntimeError("AUTHORIZED_STRATEGY_SPECS order differs from PORTFOLIO_PRIORITY")
    return strategies


def effective_strategy_parameters(strategies: Mapping[str, BaseStrategy]) -> dict[str, Any]:
    """Public scalar attributes + ctor config of each strategy (for params hash / manifest)."""
    out: dict[str, Any] = {}
    for name, strat in strategies.items():
        attrs: dict[str, Any] = {}
        for key, value in sorted(vars(strat).items()):
            if key.startswith("_") or key in ("config", "original_parameters"):
                continue
            if isinstance(value, bool | int | float | str):
                attrs[key] = value
            elif isinstance(value, time):
                attrs[key] = value.isoformat()
        out[name] = {
            "class": type(strat).__name__,
            "config": {k: strat.config[k] for k in sorted(strat.config)},
            "attributes": attrs,
        }
    return out


# --------------------------------------------------------------------------- params & result


@dataclass(frozen=True)
class BacktestParams:
    """Execution parameters of one backtest run."""

    slippage_points: float = DEFAULT_SLIPPAGE_POINTS
    entry_cutoff: time = ENTRY_CUTOFF_TIME
    square_off: time = SQUARE_OFF_TIME
    bar_minutes: int = BAR_MINUTES
    priority: tuple[str, ...] = tuple(PORTFOLIO_PRIORITY)
    max_concurrent_positions: int = MAX_CONCURRENT_POSITIONS
    window_start: date | None = DEFAULT_WINDOW_START
    window_end: date | None = DEFAULT_WINDOW_END

    def to_dict(self) -> dict[str, Any]:
        return {
            "slippage_points_per_side": self.slippage_points,
            "entry_cutoff": self.entry_cutoff.strftime("%H:%M"),
            "square_off": self.square_off.strftime("%H:%M"),
            "bar_minutes": self.bar_minutes,
            "tie_break_priority": list(self.priority),
            "max_concurrent_positions": self.max_concurrent_positions,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
        }


@dataclass
class PortfolioBacktestResult:
    """The four datasets plus metrics of one run."""

    params: BacktestParams
    strategy_parameters: dict[str, Any]
    signals: list[dict[str, Any]]
    standalone_trades: list[UnderlyingTradeRecord]
    portfolio_trades: list[UnderlyingTradeRecord]
    rejected_signals: list[dict[str, Any]]
    summary: dict[str, Any]
    raw_signals_consistent: bool = True
    invariant_violations: list[str] = field(default_factory=list)

    def in_window(self, d: date) -> bool:
        return _in_window(d, self.params.window_start, self.params.window_end)


def _in_window(d: date, start: date | None, end: date | None) -> bool:
    return (start is None or d >= start) and (end is None or d <= end)


# --------------------------------------------------------------------------- data loading


def load_nifty_bars(base_dir: Path) -> tuple[list[Bar], list[Bar]]:
    """Load the verified daily and 5-minute NIFTY parquet files as ``(bars_1d, bars_5m)``.

    Same files and conversion as ``scripts/generate_final_paper_decision_artifacts.py``:
    tz-naive IST bar-open stamps, RAW bars (no session normalisation).
    """
    import pandas as pd  # local import: keeps the paper-app import of this module light

    path_1d = base_dir / "data" / "processed" / "nifty" / "1d" / "nifty50_1d.parquet"
    path_5m = base_dir / "data" / "processed" / "nifty" / "5m" / "nifty50_5m.parquet"

    def to_bars(df: Any, interval: Timeframe) -> list[Bar]:
        bars: list[Bar] = []
        for row in df.itertuples(index=False):
            local = row.timestamp.to_pydatetime().replace(tzinfo=TZ_IST)
            bars.append(
                Bar(
                    timestamp=local.astimezone(TZ_UTC),
                    exchange_time=local,
                    symbol="NIFTY",
                    instrument_id="999920000",
                    exchange=Exchange.NSE,
                    instrument_type=InstrumentType.INDEX,
                    interval=interval,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=int(row.volume),
                    source=DataSource.FIVEPAISA_HISTORICAL,
                )
            )
        return bars

    return to_bars(pd.read_parquet(path_1d), Timeframe.D1), to_bars(
        pd.read_parquet(path_5m), Timeframe.M5
    )


# --------------------------------------------------------------------------- rows


def trade_to_row(trade: UnderlyingTradeRecord, window: tuple[date | None, date | None]) -> dict:
    """Flat CSV/JSON-safe dict of one trade."""
    direction = trade.direction.value if hasattr(trade.direction, "value") else trade.direction
    return {
        "trade_id": trade.trade_id,
        "signal_id": trade.signal_id,
        "strategy": trade.strategy_name,
        "method": trade.method,
        "direction": direction,
        "date": trade.date.isoformat(),
        "signal_candle_open": trade.signal_time.isoformat() if trade.signal_time else "",
        "entry_time": trade.entry_time.isoformat(),
        "entry_price": trade.entry_price,
        "exit_time": trade.exit_time.isoformat(),
        "exit_price": trade.exit_price,
        "exit_reason": trade.exit_reason,
        "points_pnl": trade.points_pnl,
        "is_win": trade.is_win,
        "duration_minutes": round((trade.exit_time - trade.entry_time).total_seconds() / 60.0, 1),
        "target": trade.target,
        "stop_loss": trade.stop_loss,
        "uncertain_fill": trade.is_uncertain_fill,
        "slippage_points_per_side": trade.slippage_points,
        "in_window": _in_window(trade.date, *window),
    }


def _signal_row(
    rec: EngineSignalRecord,
    standalone: EngineSignalRecord | None,
    window: tuple[date | None, date | None],
) -> dict[str, Any]:
    return {
        "signal_id": rec.signal_id,
        "strategy": rec.strategy_name,
        "method": rec.method,
        "direction": rec.direction,
        "date": rec.date.isoformat(),
        "signal_candle_open": rec.signal_candle_open.isoformat(),
        "signal_candle_close": rec.signal_candle_close.isoformat(),
        "reference_price": rec.reference_price,
        "stop_points": rec.stop_points,
        "target_points": rec.target_points,
        "reason": rec.reason,
        "standalone_status": standalone.status.value if standalone else "",
        "standalone_reason": standalone.rejection_reason if standalone else "",
        "standalone_trade_id": (standalone.trade_id or "") if standalone else "",
        "portfolio_status": rec.status.value,
        "portfolio_reason": rec.rejection_reason,
        "portfolio_trade_id": rec.trade_id or "",
        "blocking_trade_id": rec.blocking_trade_id or "",
        "blocking_signal_id": rec.blocking_signal_id or "",
        "in_window": _in_window(rec.date, *window),
    }


# --------------------------------------------------------------------------- invariants


def validate_portfolio_invariants(trades: Sequence[UnderlyingTradeRecord]) -> list[str]:
    """Return human-readable violations (empty list == clean).

    Checks: unique trade ids, unique signal ids, no overlapping positions (a new entry must
    be strictly AFTER the previous exit stamp), no entry at/after the cutoff is checked by
    the engine itself.
    """
    problems: list[str] = []
    ordered = sorted(trades, key=lambda t: (t.entry_time, t.exit_time, t.trade_id))
    trade_ids = Counter(t.trade_id for t in ordered)
    signal_ids = Counter(t.signal_id for t in ordered if t.signal_id)
    problems += [f"duplicate trade_id {k} x{v}" for k, v in trade_ids.items() if v > 1]
    problems += [f"duplicate signal_id {k} x{v}" for k, v in signal_ids.items() if v > 1]
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        if cur.entry_time <= prev.exit_time:
            problems.append(
                f"overlap: {cur.trade_id} enters {cur.entry_time.isoformat()} "
                f"<= {prev.trade_id} exit {prev.exit_time.isoformat()}"
            )
    return problems


# --------------------------------------------------------------------------- the run


def run_portfolio_backtest(
    bars_5m: list[Bar],
    bars_1d: list[Bar] | None,
    params: BacktestParams | None = None,
    strategy_factory: Callable[[], Mapping[str, BaseStrategy]] = build_authorized_strategies,
) -> PortfolioBacktestResult:
    """Run standalone (each strategy alone) and the joint portfolio simulation.

    ``bars_5m`` should include the warm-up history before ``params.window_start``: the whole
    dataset is simulated and the window only selects what is REPORTED.  ``strategy_factory``
    must return fresh strategies on every call (tests inject synthetic ones).
    """
    params = params or BacktestParams()
    window = (params.window_start, params.window_end)
    engine = UnderlyingBacktestEngine(
        slippage_points=params.slippage_points,
        eod_exit_time=params.square_off,
        entry_cutoff_time=params.entry_cutoff,
        bar_minutes=params.bar_minutes,
    )

    # Point-in-time reference levels are strategy independent: compute once.
    from trading_platform.data.reference_levels import ReferenceLevelCalculator

    sorted_bars = sorted(bars_5m, key=lambda b: b.exchange_time)
    enriched = ReferenceLevelCalculator().enrich_bars_with_context(
        sorted_bars, daily_history_bars=bars_1d
    )
    enriched_map = {ctx.bar.exchange_time: ctx for ctx in enriched}

    # 1+2. Standalone: each strategy alone, same engine.
    standalone_sims: dict[str, UnderlyingSimulationResult] = {}
    for name in params.priority:
        strategy = strategy_factory()[name]
        standalone_sims[name] = engine.simulate(
            [strategy], sorted_bars, enriched_contexts=enriched_map
        )

    # 3. Joint portfolio: all strategies, one position slot, explicit tie-break priority.
    strategies = dict(strategy_factory())
    portfolio_sim = engine.simulate(
        [strategies[n] for n in params.priority if n in strategies],
        sorted_bars,
        enriched_contexts=enriched_map,
        priority=params.priority,
    )
    strategy_parameters = effective_strategy_parameters(strategies)

    standalone_trades = sorted(
        (t for sim in standalone_sims.values() for t in sim.trades),
        key=lambda t: (t.entry_time, params.priority.index(t.strategy_name), t.trade_id),
    )
    standalone_by_id = {
        rec.signal_id: rec for sim in standalone_sims.values() for rec in sim.signals
    }
    signal_rows = [
        _signal_row(rec, standalone_by_id.get(rec.signal_id), window)
        for rec in portfolio_sim.signals
    ]
    raw_consistent = {r.signal_id for r in portfolio_sim.signals} == set(standalone_by_id)
    rejected_rows = [r for r in signal_rows if r["portfolio_status"] == SignalStatus.REJECTED]
    violations = validate_portfolio_invariants(portfolio_sim.trades)

    result = PortfolioBacktestResult(
        params=params,
        strategy_parameters=strategy_parameters,
        signals=signal_rows,
        standalone_trades=standalone_trades,
        portfolio_trades=list(portfolio_sim.trades),
        rejected_signals=rejected_rows,
        summary={},
        raw_signals_consistent=raw_consistent,
        invariant_violations=violations,
    )
    result.summary = build_summary(result)
    if violations:
        raise AssertionError("Portfolio invariants violated: " + "; ".join(violations[:5]))
    return result


# --------------------------------------------------------------------------- summary


def _scope_metrics(
    result: PortfolioBacktestResult, in_window_only: bool
) -> dict[str, dict[str, Any]]:
    def pick(trades: Iterable[UnderlyingTradeRecord]) -> list[UnderlyingTradeRecord]:
        return [t for t in trades if (not in_window_only) or result.in_window(t.date)]

    out: dict[str, dict[str, Any]] = {"standalone": {}}
    for name in result.params.priority:
        mine = pick(t for t in result.standalone_trades if t.strategy_name == name)
        out["standalone"][name] = compute_points_metrics(mine).to_dict()
    portfolio = pick(result.portfolio_trades)
    out["portfolio_combined"] = compute_points_metrics(portfolio).to_dict()
    # Informational only: all standalone trades pooled.  They OVERLAP in time, so the drawdown
    # of this pooled sequence is order-dependent and NOT an achievable single-account curve.
    out["standalone_pooled_overlapping"] = compute_points_metrics(
        pick(result.standalone_trades)
    ).to_dict()
    out["portfolio_by_strategy"] = {
        name: compute_points_metrics([t for t in portfolio if t.strategy_name == name]).to_dict()
        for name in result.params.priority
    }
    out["portfolio_exit_mix"] = dict(Counter(t.exit_reason for t in portfolio))
    rows = [r for r in result.rejected_signals if (not in_window_only) or r["in_window"]]
    out["portfolio_rejections_by_reason"] = dict(Counter(r["portfolio_reason"] for r in rows))
    out["raw_signals"] = len(
        [r for r in result.signals if (not in_window_only) or r["in_window"]]
    )
    return out


def build_summary(result: PortfolioBacktestResult) -> dict[str, Any]:
    """Machine-readable summary (window headline + whole-dataset numbers)."""
    return {
        "engine_version": ENGINE_VERSION_LABEL,
        "unit": PNL_UNIT_LABEL,
        "scope_note": SCOPE_NOTE,
        "params": result.params.to_dict(),
        "raw_signals_consistent_standalone_vs_joint": result.raw_signals_consistent,
        "window": _scope_metrics(result, in_window_only=True),
        "full_dataset": _scope_metrics(result, in_window_only=False),
    }


def metric_rows(
    scope: Mapping[str, Any], priority: Sequence[str]
) -> list[tuple[str, PointsMetrics]]:
    """``[(label, PointsMetrics)]`` for the printed table: each standalone then the portfolio."""
    rows: list[tuple[str, PointsMetrics]] = []
    for name in priority:
        rows.append((f"{name} (alone)", _metrics_from_dict(scope["standalone"][name])))
    rows.append(("PORTFOLIO", _metrics_from_dict(scope["portfolio_combined"])))
    return rows


def _metrics_from_dict(d: Mapping[str, Any]) -> PointsMetrics:
    data = {k: v for k, v in d.items() if k != "profit_factor_note"}
    if data.get("profit_factor") is None:
        data["profit_factor"] = float("inf")
    return PointsMetrics(**data)


# --------------------------------------------------------------------------- integrity / manifest

_HASH_LINE = re.compile(r"`(?P<path>[\w./\- ]+\.\w+)`.*?`(?P<sha>[0-9a-f]{64})`")


def parse_manifest_hashes(manifest_path: Path) -> dict[str, str]:
    """``{relative_path: sha256}`` for every file listed with a hash in the baseline manifest."""
    hashes: dict[str, str] = {}
    if not manifest_path.exists():
        return hashes
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        match = _HASH_LINE.search(line)
        if match:
            hashes[match.group("path").strip()] = match.group("sha")
    return hashes


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_dataset_hashes(
    base_dir: Path,
    manifest_path: Path | None = None,
    dataset_paths: Sequence[str] = (
        "data/processed/nifty/5m/nifty50_5m.parquet",
        "data/processed/nifty/1d/nifty50_1d.parquet",
        "data/processed/nifty/1m/nifty50_1m.parquet",
    ),
) -> list[dict[str, Any]]:
    """Hash the input datasets and compare against the baseline manifest where listed.

    Never raises on a mismatch (callers warn).  ``status`` is one of MATCH, MISMATCH,
    NOT_LISTED_IN_MANIFEST, MISSING_FILE.
    """
    manifest_path = manifest_path or (base_dir / "docs" / "baseline_v1_manifest.md")
    expected = parse_manifest_hashes(manifest_path)
    out: list[dict[str, Any]] = []
    for rel in dataset_paths:
        path = base_dir / rel
        entry: dict[str, Any] = {"path": rel, "expected_sha256": expected.get(rel)}
        if not path.exists():
            entry.update(actual_sha256=None, status="MISSING_FILE")
        else:
            actual = sha256_file(path)
            entry["actual_sha256"] = actual
            if rel not in expected:
                entry["status"] = "NOT_LISTED_IN_MANIFEST"
            else:
                entry["status"] = "MATCH" if actual == expected[rel] else "MISMATCH"
        out.append(entry)
    return out


def canonical_params_hash(params: BacktestParams, strategy_parameters: Mapping[str, Any]) -> str:
    payload = {
        "engine_version": ENGINE_VERSION_LABEL,
        "params": params.to_dict(),
        "strategies": strategy_parameters,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def git_state(base_dir: Path) -> dict[str, Any]:
    """Local git HEAD and whether the tree is dirty (no network; failures are reported)."""
    def _git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *args], cwd=base_dir, capture_output=True, text=True, timeout=30, check=True
            )
            return done.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    status = _git("status", "--porcelain")
    return {
        "git_sha": _git("rev-parse", "HEAD"),
        "git_dirty": None if status is None else bool(status),
    }


def code_hashes(base_dir: Path) -> dict[str, str]:
    """sha256 of the source files whose behaviour defines the result (dirty-tree safe)."""
    rels = [
        "src/trading_platform/backtesting/engine.py",
        "src/trading_platform/backtesting/portfolio_runner.py",
        "src/trading_platform/backtesting/arbitration.py",
        "src/trading_platform/backtesting/metrics.py",
        "src/trading_platform/backtesting/constants.py",
        "src/trading_platform/data/reference_levels.py",
        "src/trading_platform/strategies/pds.py",
        "src/trading_platform/strategies/orb.py",
        "src/trading_platform/strategies/irf.py",
        "src/trading_platform/strategies/pdr.py",
        "src/trading_platform/strategies/price_action.py",
        "src/trading_platform/strategies/provisional.py",
    ]
    prov_dir = base_dir / "configs" / "strategy_provisional"
    if prov_dir.is_dir():
        rels += sorted(str(p.relative_to(base_dir)) for p in prov_dir.glob("*.yaml"))
    return {rel: sha256_file(base_dir / rel) for rel in rels if (base_dir / rel).exists()}


def assert_safe_output_dir(base_dir: Path, out_dir: Path) -> None:
    """Refuse to write inside frozen/verified locations or over an existing run folder."""
    resolved = out_dir.resolve()
    for prefix in FROZEN_PREFIXES:
        frozen = (base_dir / prefix).resolve()
        if resolved == frozen or frozen in resolved.parents:
            raise ValueError(f"Refusing to write into frozen location: {prefix}")
    if resolved.exists() and any(resolved.iterdir()):
        raise FileExistsError(f"Run directory already exists and is not empty: {resolved}")


# --------------------------------------------------------------------------- writers


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: Sequence[str]) -> None:
    import csv

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


TRADE_COLUMNS = [
    "trade_id", "signal_id", "strategy", "method", "direction", "date", "signal_candle_open",
    "entry_time", "entry_price", "exit_time", "exit_price", "exit_reason", "points_pnl",
    "is_win", "duration_minutes", "target", "stop_loss", "uncertain_fill",
    "slippage_points_per_side", "in_window",
]  # fmt: skip
SIGNAL_COLUMNS = [
    "signal_id", "strategy", "method", "direction", "date", "signal_candle_open",
    "signal_candle_close", "reference_price", "stop_points", "target_points", "reason",
    "standalone_status", "standalone_reason", "standalone_trade_id", "portfolio_status",
    "portfolio_reason", "portfolio_trade_id", "blocking_trade_id", "blocking_signal_id",
    "in_window",
]  # fmt: skip


def _md_table(rows: list[tuple[str, PointsMetrics]]) -> str:
    lines = [
        "| strategy | trades | win rate | net pts | PF | max DD | avg duration (min) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, m in rows:
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        lines.append(
            f"| {label} | {m.trades} | {m.win_rate_pct:.2f}% | {m.net_points:.2f} | {pf} | "
            f"{m.max_drawdown_points:.2f} | {m.avg_duration_minutes:.1f} |"
        )
    return "\n".join(lines)


def render_summary_markdown(result: PortfolioBacktestResult, run_id: str) -> str:
    s = result.summary
    p = result.params
    win = f"{p.window_start} to {p.window_end}"
    out = [
        f"# NIFTY index-point backtest - {ENGINE_VERSION_LABEL}",
        "",
        f"Run: `{run_id}`",
        "",
        f"> {SCOPE_NOTE}",
        "",
        "## Execution model",
        "",
        f"- Entry at the NEXT bar open after the signal candle close, plus {p.slippage_points} "
        "pt adverse slippage; exit slippage of the same size on EVERY exit type.",
        f"- No entry at/after {p.entry_cutoff:%H:%M} (signal candles closing at/after the "
        f"cutoff are dropped); square-off at {p.square_off:%H:%M}; forced close if no "
        "square-off bar arrives.",
        "- Same-bar stop/target ambiguity resolves to the stop (conservative).",
        f"- One joint chronological simulation, max {p.max_concurrent_positions} concurrent "
        f"position, tie-break priority {' > '.join(p.priority)}.",
        "",
        f"## Evaluation window {win} (in-sample, see note above)",
        "",
        _md_table(metric_rows(s["window"], p.priority)),
        "",
        "## Whole simulated dataset (includes warm-up months)",
        "",
        _md_table(metric_rows(s["full_dataset"], p.priority)),
        "",
        "## Portfolio detail (window)",
        "",
        f"- Raw engine signals: {s['window']['raw_signals']}",
        f"- Exit mix (accepted): {json.dumps(s['window']['portfolio_exit_mix'])}",
        "- Rejected signals by reason: "
        f"{json.dumps(s['window']['portfolio_rejections_by_reason'])}",
        "- Pooled standalone trades overlap in time; their drawdown is order-dependent and not "
        "an achievable single-account curve: "
        f"{json.dumps(s['window']['standalone_pooled_overlapping'])}",
        "",
        "Max drawdown for the PORTFOLIO row uses only the accepted, sequenced trades.",
        "",
    ]
    return "\n".join(out)


def write_run_outputs(
    result: PortfolioBacktestResult,
    out_dir: Path,
    base_dir: Path,
    run_id: str,
    dataset_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    """Write the seven run files into ``out_dir`` (created; must be new/empty and not frozen)."""
    assert_safe_output_dir(base_dir, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    window = (result.params.window_start, result.params.window_end)
    files: dict[str, Path] = {
        "signals": out_dir / "signals.csv",
        "standalone_trades": out_dir / "standalone_trades.csv",
        "portfolio_trades": out_dir / "portfolio_trades.csv",
        "rejected_signals": out_dir / "rejected_signals.csv",
        "summary_json": out_dir / "summary.json",
        "summary_md": out_dir / "summary.md",
        "manifest": out_dir / "manifest.json",
    }
    _write_csv(files["signals"], result.signals, SIGNAL_COLUMNS)
    _write_csv(
        files["standalone_trades"],
        [trade_to_row(t, window) for t in result.standalone_trades],
        TRADE_COLUMNS,
    )
    _write_csv(
        files["portfolio_trades"],
        [trade_to_row(t, window) for t in result.portfolio_trades],
        TRADE_COLUMNS,
    )
    _write_csv(files["rejected_signals"], result.rejected_signals, SIGNAL_COLUMNS)
    files["summary_json"].write_text(
        json.dumps(result.summary, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    files["summary_md"].write_text(render_summary_markdown(result, run_id), encoding="utf-8")

    manifest = {
        "run_id": run_id,
        "engine_version": ENGINE_VERSION_LABEL,
        "unit": PNL_UNIT_LABEL,
        "scope_note": SCOPE_NOTE,
        "created_at_utc": datetime.now(TZ_UTC).isoformat(),
        "python": sys.version.split()[0],
        "params": result.params.to_dict(),
        "params_hash_sha256": canonical_params_hash(result.params, result.strategy_parameters),
        "strategy_parameters": result.strategy_parameters,
        "strategies_included": list(result.params.priority),
        "strategies_excluded": ["RF (permanently disabled, never loaded)"],
        "dataset_hashes": dataset_checks or [],
        "code_hashes": code_hashes(base_dir),
        "raw_signals_consistent_standalone_vs_joint": result.raw_signals_consistent,
        "row_counts": {
            "signals": len(result.signals),
            "standalone_trades": len(result.standalone_trades),
            "portfolio_trades": len(result.portfolio_trades),
            "rejected_signals": len(result.rejected_signals),
        },
        "output_file_sha256": {
            k: sha256_file(p) for k, p in files.items() if k != "manifest"
        },
        **git_state(base_dir),
    }
    files["manifest"].write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return files

