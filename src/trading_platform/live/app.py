"""Application layer of the NIFTY options VIRTUAL paper-trading app.

One manually launched process: ``./.venv/bin/python scripts/run_paper_trading.py``.

* LIVE (default)   read-only 5paisa market data, in memory, virtual fills only.
* ``--replay``     clearly labelled SIMULATED historical replay (separate runtime root).
* ``--doctor``     the startup checks only (no trading loop).
* ``--acknowledge-recovery``  explicit operator action for an uncertain restart.

There is no order, modify or cancel capability anywhere in this module or in the providers it
drives: the app can only create VIRTUAL fills inside :class:`~trading_platform.live.engine.
SessionEngine`.  Unresolved virtual positions are never closed or invented on shutdown; they stay
in ``runtime/checkpoint.json``.

Exit codes
----------
0  clean: session finished, holiday / nothing to do, operator stop (a preserved virtual position is
   reported, not an error), doctor all-green, recovery acknowledged
1  startup / doctor check failed (fail closed, no trading), or an acknowledgement was refused
2  usage or configuration error
3  another instance holds the single-instance lock
5  unexpected error after startup (state was checkpointed, see ``runtime/logs/errors.log``)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import queue
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Any

from rich.console import Console

from trading_platform.core import redaction
from trading_platform.data.calendar import NSECalendar
from trading_platform.live import labels
from trading_platform.live.authorization import (
    LABEL_RESEARCH,
    LABEL_SHADOW,
    AuthDecision,
    authorize,
    authorize_all,
    provisional_rules,
)
from trading_platform.live.checkpoint import (
    Checkpoint,
    LoadFailure,
    RecoveryAction,
    RecoveryDecision,
    build_recovery_ack,
    ledger_reconciles,
    load_checkpoint,
    load_recovery_ack,
    recovery_ack_path,
    recovery_decision,
    write_recovery_ack,
)
from trading_platform.live.config import (
    ACTIVE_STRATEGIES,
    DEFAULT_CONFIG_PATH,
    ConfigError,
    LiveConfig,
    load_live_config,
)
from trading_platform.live.contracts import ContractResolver, ContractSelectionError
from trading_platform.live.engine import EngineEvent, SessionEngine
from trading_platform.live.journal import EventJournal, TradeCsvJournal, get_error_logger
from trading_platform.live.levels import LiveReferenceBuilder
from trading_platform.live.lock import SingleInstanceLock, describe_started
from trading_platform.live.models import (
    CANDLE_MINUTES,
    IST,
    CompletedCandle,
    EngineState,
    ExecutionTier,
    RunMode,
    WarmupData,
)
from trading_platform.live.providers.base import (
    CheckResult,
    Clock,
    ManualClock,
    MarketDataProvider,
    PollResult,
    SystemClock,
)
from trading_platform.live.snapshot import SnapshotWriter
from trading_platform.live.terminal import WARN, TerminalUI, fmt_inr

APP_NAME = "NIFTY Options Paper Trading (VIRTUAL fills, read-only market data)"
REPLAY_BANNER = "SIMULATED — NOT MARKET DATA"
REPLAY_LABEL = "REPLAY_SIMULATED"
DASHBOARD_COMMAND = "streamlit run scripts/paper_trading_dashboard.py"

EXIT_OK = 0
EXIT_STARTUP_FAILED = 1
EXIT_USAGE = 2
EXIT_LOCK_HELD = 3
EXIT_CRASH = 5

#: The loop may end only this long after the configured market close (and only when flat).
POST_CLOSE_GRACE = timedelta(minutes=5)
#: Startup: a spot tick older than this during market hours blocks startup.
STARTUP_SPOT_MAX_AGE_SECONDS = 15.0
#: Minimum real seconds between two candle-fetch submissions (the provider throttles as well).
CANDLE_FETCH_RETRY_SECONDS = 2.0
UI_REFRESH_SECONDS = 0.5  # 2 Hz
UI_SLICE_SECONDS = 0.25

ProviderFactory = Callable[..., MarketDataProvider]


# =========================================================================== hooks / context


@dataclass
class AppHooks:
    """Test seams.  Production uses the defaults."""

    sleep_fn: Callable[[float], None] = time.sleep
    monotonic_fn: Callable[[], float] = time.monotonic
    executor_factory: Callable[[], Any] | None = None  # None -> ThreadPoolExecutor(1)
    install_signals: bool = True
    stop_event: threading.Event | None = None
    strategies_factory: Callable[[], dict[str, Any]] | None = None
    levels_builder_cls: Any = None


@dataclass
class AppContext:
    args: argparse.Namespace
    console: Console | None = None
    provider_factory: ProviderFactory | None = None
    clock: Clock | None = None
    hooks: AppHooks = field(default_factory=AppHooks)
    #: time source the terminal uses for ages (replay switches it to the simulated clock)
    ui_now: Callable[[], datetime] = lambda: datetime.now(IST)  # noqa: E731


# =========================================================================== runtime paths


@dataclass(frozen=True)
class RuntimePaths:
    root: Path

    @property
    def lock(self) -> Path:
        return self.root / "app.lock"

    @property
    def journal_dir(self) -> Path:
        return self.root / "journal"

    @property
    def trades_csv(self) -> Path:
        return self.journal_dir / "trades.csv"

    @property
    def checkpoint(self) -> Path:
        return self.root / "checkpoint.json"

    @property
    def snapshot(self) -> Path:
        return self.root / "live_state.json"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def errors_log(self) -> Path:
        return self.logs_dir / "errors.log"


def reset_replay_root(root: Path) -> list[Path]:
    """Delete the KNOWN replay output files inside ``root`` (a ``.../replay`` directory).

    Nothing else is touched: no directory is removed, nothing outside ``root`` is reachable and
    the lock file is kept (it is held by the running process).
    """
    root = Path(root)
    if root.name != "replay":
        raise ValueError(f"refusing to reset a directory not named 'replay': {root}")
    paths = RuntimePaths(root)
    candidates: list[Path] = [
        paths.trades_csv,
        paths.trades_csv.with_name("trades.csv.torn"),
        paths.checkpoint,
        paths.snapshot,
        paths.errors_log,
    ]
    for pattern, base in (
        ("events_*.jsonl", paths.journal_dir),
        ("checkpoint.recovered-*.json", root),
        ("errors.log.*", paths.logs_dir),
        (".*.tmp", root),
        (".*.tmp", paths.journal_dir),
    ):
        if base.is_dir():
            candidates.extend(sorted(base.glob(pattern)))
    removed: list[Path] = []
    resolved_root = root.resolve()
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        if resolved_root not in path.resolve().parents:
            continue
        path.unlink()
        removed.append(path)
    return removed


# =========================================================================== startup log


class StartupLog:
    """Ordered startup checks (one entry per numbered step) shown via ``startup_banner``."""

    def __init__(self) -> None:
        self.checks: list[CheckResult] = []

    def add(self, name: str, ok: bool, detail: str, blocking: bool = True) -> CheckResult:
        detail = redaction.redact_sensitive_text(detail)
        check = CheckResult(name, bool(ok), detail, blocking)
        self.checks.append(check)
        return check

    def set_detail(self, name: str, detail: str) -> None:
        """Replace the detail of the first check called ``name`` (keeps its position)."""
        for i, check in enumerate(self.checks):
            if check.name == name:
                self.checks[i] = CheckResult(
                    name, check.ok, redaction.redact_sensitive_text(detail), check.blocking
                )
                return

    @property
    def blocked(self) -> bool:
        return any(not c.ok and c.blocking for c in self.checks)


# =========================================================================== stop handling


class StopFlag:
    """Cooperative stop request set by SIGINT / SIGTERM (or by a test)."""

    def __init__(self, external: threading.Event | None = None) -> None:
        self.event = external or threading.Event()
        self.signal_name = ""
        self.signals_seen = 0

    def handler(self, signum: int, _frame: FrameType | None) -> None:
        self.signals_seen += 1
        if not self.signal_name:
            try:
                self.signal_name = signal.Signals(signum).name
            except ValueError:
                self.signal_name = str(signum)
        self.event.set()  # a repeated signal never skips the shutdown checkpoint

    def is_set(self) -> bool:
        return self.event.is_set()

    @property
    def reason(self) -> str:
        return f"operator stop ({self.signal_name})" if self.signal_name else "stop requested"


# =========================================================================== candle feed


class DaemonExecutor:
    """Single-worker executor whose thread is a DAEMON thread.

    ``ThreadPoolExecutor`` workers are joined at interpreter exit, so a candle fetch stuck inside
    an SDK/REST retry kept the process (and its lock) alive for up to a minute AFTER the final
    checkpoint was written, and a second Ctrl+C in that join printed a traceback.  A daemon
    worker dies with the process; ``shutdown`` never waits and cancels queued work.
    """

    def __init__(self, name: str = "candle-fetch") -> None:
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._lock = threading.Lock()
        self._closed = False

    @property
    def thread(self) -> threading.Thread:
        return self._thread

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("executor is shut down")
            if not self._thread.is_alive() and not self._thread.ident:
                self._thread.start()
            self._queue.put((future, fn, args, kwargs))
        return future

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            future, fn, args, kwargs = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as exc:  # noqa: BLE001 - delivered through the future
                future.set_exception(exc)

    def shutdown(self, wait: bool = False, cancel_futures: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if cancel_futures:
                while True:
                    try:
                        item = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if item is not None:
                        item[0].cancel()
            self._queue.put(None)
        if wait and self._thread.ident:
            self._thread.join()

    def join(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for the worker; True when it is gone (or never ran)."""
        if not self._thread.ident:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()


@dataclass
class FetchResult:
    candles: list[CompletedCandle]
    diagnostics: dict[str, Any]
    error: str | None = None


class CandleFeed:
    """Schedules provider candle fetches OFF the main loop and delivers new candles in order.

    * One fetch in flight at a time (single worker); polling is never blocked by it.
    * A candle is delivered with ``engine.on_candle`` exactly once, unless the engine reports it
      still missing (gap backfill), in which case it is re-offered.
    * If the expected candle is still absent ``provider_wait_max_seconds`` after its close, the
      engine is told (``on_candle_missing``) once per candle and strategies stay BLOCKED; the
      feed keeps retrying.  Sampled ticks are never substituted.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        engine: SessionEngine,
        cfg: LiveConfig,
        trading_date: date,
        executor: Any,
        monotonic: Callable[[], float],
    ) -> None:
        self.provider = provider
        self.engine = engine
        self.cfg = cfg
        self.trading_date = trading_date
        self.executor = executor
        self.monotonic = monotonic
        self.delivered: set[datetime] = set()
        self.missing_reported: set[datetime] = set()
        self.next_open = datetime.combine(trading_date, cfg.session.market_open, IST)
        self.last_open = datetime.combine(trading_date, cfg.session.market_close, IST) - timedelta(
            minutes=CANDLE_MINUTES
        )
        self.future: concurrent.futures.Future[FetchResult] | None = None
        self.last_submit = float("-inf")
        self.diagnostics: dict[str, Any] = {}
        self.last_error: str | None = None

    # ---- bookkeeping

    def mark_delivered(self, candles: Sequence[CompletedCandle]) -> None:
        self.delivered.update(c.open_time for c in candles)
        self._advance()

    def _advance(self) -> None:
        step = timedelta(minutes=CANDLE_MINUTES)
        while self.next_open in self.delivered and self.next_open <= self.last_open:
            self.next_open += step

    @property
    def finished(self) -> bool:
        return self.next_open > self.last_open

    # ---- worker

    def _fetch(self, as_of: datetime) -> FetchResult:
        try:
            candles = list(self.provider.completed_candles(self.trading_date, as_of))
            error = None
        except Exception as exc:  # noqa: BLE001 - provider trouble must never stop the app
            candles, error = [], f"candle fetch failed: {type(exc).__name__}: {exc}"
        diag = getattr(self.provider, "candle_diagnostics", None)
        return FetchResult(candles, dict(diag) if isinstance(diag, dict) else {}, error)

    # ---- main-thread step

    def _collect(self) -> list[EngineEvent]:
        if self.future is None or not self.future.done():
            return []
        fut, self.future = self.future, None
        try:
            result = fut.result()
        except Exception as exc:  # noqa: BLE001
            result = FetchResult([], {}, f"candle worker failed: {type(exc).__name__}: {exc}")
        self.diagnostics = result.diagnostics
        self.last_error = result.error
        return self._deliver(result.candles)

    def step(self, now: datetime) -> list[EngineEvent]:
        events = self._collect()
        if self.finished:
            return events
        wait = self.cfg.candles
        close = self.next_open + timedelta(minutes=CANDLE_MINUTES)
        due = close + timedelta(seconds=wait.provider_poll_delay_seconds)
        if now >= due and self.future is None:
            if self.monotonic() - self.last_submit >= CANDLE_FETCH_RETRY_SECONDS:
                self.last_submit = self.monotonic()
                self.future = self.executor.submit(self._fetch, now)
                events.extend(self._collect())  # an inline executor finishes immediately
        if self.finished:
            return events
        close = self.next_open + timedelta(minutes=CANDLE_MINUTES)
        deadline = close + timedelta(seconds=wait.provider_wait_max_seconds)
        if (
            now >= deadline
            and self.next_open not in self.delivered
            and self.next_open not in self.missing_reported
        ):
            self.missing_reported.add(self.next_open)
            diag = dict(self.diagnostics)
            if self.last_error:
                diag["fetch_error"] = self.last_error
            events.extend(self.engine.on_candle_missing(self.next_open, diag))
        return events

    def _deliver(self, candles: Sequence[CompletedCandle]) -> list[EngineEvent]:
        events: list[EngineEvent] = []
        needed = set(self.engine.missing_candle_opens())
        for candle in sorted(candles, key=lambda c: c.open_time):
            if candle.open_time in self.delivered and candle.open_time not in needed:
                continue
            events.extend(self.engine.on_candle(candle))
            self.delivered.add(candle.open_time)
            needed = set(self.engine.missing_candle_opens())
        self._advance()
        return events

    def notes(self) -> list[str]:
        out: list[str] = []
        if self.last_error:
            out.append(self.last_error[:200])
        summary = self.diagnostics.get("summary")
        if summary:
            out.append(f"candles: {summary}"[:200])
        for key in ("rejected", "missing"):
            value = self.diagnostics.get(key)
            if value:
                out.append(f"candles {key}: {value}"[:200])
        return out

    #: Seconds ``close`` waits for an in-flight fetch before abandoning it (daemon worker).
    CLOSE_JOIN_SECONDS = 2.0

    def close(self) -> bool:
        """Stop the worker without ever hanging behind it; False when a fetch was abandoned."""
        shutdown = getattr(self.executor, "shutdown", None)
        if shutdown is not None:
            try:
                shutdown(wait=False, cancel_futures=True)
            except TypeError:
                shutdown(wait=False)
        join = getattr(self.executor, "join", None)
        if join is None:
            return True
        try:
            return bool(join(self.CLOSE_JOIN_SECONDS))
        except Exception:  # noqa: BLE001 - shutdown must not raise
            return True


# =========================================================================== helpers


def _safe(text: object, limit: int = 400) -> str:
    return redaction.redact_sensitive_text(str(text))[:limit]


def _event_dict(event: EngineEvent) -> dict[str, str]:
    return {"t": event.t.isoformat(), "kind": event.kind, "text": event.text}


def _tier_summary(
    auth: dict[str, AuthDecision], cli_flag: bool, cfg: LiveConfig
) -> tuple[str, list[str]]:
    """(one-line tiers, extra lines explaining the research-paper status)."""
    order = [n for n in (*ACTIVE_STRATEGIES, "RF") if n in auth]
    line = " | ".join(f"{n} {auth[n].tier.value}" for n in order)
    granted = [n for n in ACTIVE_STRATEGIES if n in auth and auth[n].can_fill]
    shadow = [
        n for n in ACTIVE_STRATEGIES if n in auth and auth[n].tier == ExecutionTier.SHADOW_ONLY
    ]
    extra: list[str] = []
    if granted:
        extra.append(
            f"research-paper: ACTIVE for {', '.join(granted)} — SIMULATED fills labelled "
            f"{LABEL_RESEARCH} on PROVISIONAL rules (not author-approved)"
        )
    elif cli_flag:
        extra.append(
            "research-paper: requested (--research-paper) but NOT granted; strategies stay "
            "SHADOW_ONLY — NO virtual fills"
        )
    elif cfg.research_paper.enabled:
        extra.append(
            "research-paper: enabled in the config but the --research-paper CLI flag was not "
            "given; strategies stay SHADOW_ONLY — NO virtual fills"
        )
    else:
        extra.append(
            "research-paper: not requested (default) — every strategy is SHADOW_ONLY: signals are "
            "journaled, there are NO virtual fills"
        )
    if shadow and (cli_flag or granted or cfg.research_paper.enabled):
        grouped: dict[str, list[str]] = {}
        for name in shadow:
            why = auth[name].reasons[-1] if auth[name].reasons else "SHADOW_ONLY"
            grouped.setdefault(why, []).append(name)
        for why, names in grouped.items():
            extra.append(f"  why {'/'.join(names)} stay SHADOW_ONLY: {why}")
    disabled = [n for n in order if auth[n].tier == ExecutionTier.DISABLED]
    if disabled:
        extra.append(f"  disabled: {', '.join(disabled)}")
    return line, extra


def _replay_auth(cfg: LiveConfig) -> dict[str, AuthDecision]:
    """REPLAY tiers: RESEARCH_PAPER with label REPLAY_SIMULATED for the four strategies; RF off."""
    out: dict[str, AuthDecision] = {}
    for name in ACTIVE_STRATEGIES:
        out[name] = AuthDecision(
            ExecutionTier.RESEARCH_PAPER,
            REPLAY_LABEL,
            (f"{REPLAY_BANNER}: simulated replay; fills are SIMULATED only",),
            strategy=name,
            provisional_rules=provisional_rules(name),
        )
    out["RF"] = authorize("RF", cfg, False)
    return out


def replay_data_paths(cfg: LiveConfig) -> dict[str, Path]:
    """Historical datasets for the replay, anchored at the config base dir (never the CWD)."""
    from trading_platform.live.providers import replay as r

    def anchored(p: Path) -> Path:
        return p if p.is_absolute() else cfg.base_dir / p

    scrip = (
        cfg.scrip_master_path
        if cfg.scrip_master_path.exists()
        else anchored(r.DEFAULT_SCRIP_MASTER_PATH)
    )
    return {
        "nifty_5m_path": anchored(r.DEFAULT_5M_PATH),
        "nifty_1d_path": anchored(r.DEFAULT_1D_PATH),
        "scrip_master_path": scrip,
    }


def default_provider_factory(
    *, mode: RunMode, cfg: LiveConfig, clock: Clock, trading_date: date | None
) -> MarketDataProvider:
    """Real providers.  Imported lazily so tests / replay never touch the broker module."""
    if mode == RunMode.REPLAY:
        from trading_platform.live.providers.replay import ReplayProvider

        assert trading_date is not None
        assert isinstance(clock, ManualClock)
        return ReplayProvider(trading_date, clock=clock, **replay_data_paths(cfg))

    from trading_platform.brokers.fivepaisa import FivePaisaBroker
    from trading_platform.live.providers.fivepaisa import FivePaisaLiveProvider, ProviderConfig

    # The daily-refreshed live Scrip Master lives in the gitignored data/cache area.  It must
    # NEVER share a directory with data/reference/scrip_master.parquet: that file is hash-pinned
    # by the frozen baseline manifest and is only read (by the replay provider).
    broker = FivePaisaBroker(
        cache_dir=cfg.base_dir / "data" / "cache" / "live_scrip_master",
        token_cache_path=cfg.base_dir / "data" / "cache" / ".token_cache.json",
    )
    return FivePaisaLiveProvider(
        broker=broker,
        config=ProviderConfig.from_live_config(asdict(cfg.candles), asdict(cfg.polling)),
        clock=clock,
    )


# =========================================================================== the run


class _Run:
    """One process run (LIVE / REPLAY / doctor)."""

    def __init__(
        self,
        ctx: AppContext,
        cfg: LiveConfig,
        mode: RunMode,
        ui: TerminalUI,
        log: StartupLog,
        auth: dict[str, AuthDecision],
        trading_date: date,
        clock: Clock,
        *,
        doctor: bool = False,
    ) -> None:
        self.ctx = ctx
        self.args = ctx.args
        self.hooks = ctx.hooks
        self.cfg = cfg
        self.mode = mode
        self.ui = ui
        self.log = log
        self.auth = auth
        self.date = trading_date
        self.clock = clock
        self.doctor = doctor
        root = cfg.runtime_path
        self.paths = RuntimePaths(root / "replay" if mode == RunMode.REPLAY else root)
        self.stop = StopFlag(ctx.hooks.stop_event)
        self.calendar = NSECalendar()
        self.lock: SingleInstanceLock | None = None
        self.provider: MarketDataProvider | None = None
        self.engine: SessionEngine | None = None
        self.feed: CandleFeed | None = None
        self.warmup: WarmupData | None = None
        self.contracts: list[Any] = []
        self.error_logger: Any = None
        self.closed_trades: list[dict[str, Any]] = []
        self.opened_trades = 0
        self.last_valid_quote_at: datetime | None = None
        self._prev_handlers: dict[int, Any] = {}
        self._last_ui = float("-inf")
        self.resolver: ContractResolver | None = None
        self._start_events: list[dict[str, str]] = []
        self._final: list[tuple[str, str]] = []

    # ------------------------------------------------------------------ helpers

    def say(self, text: str, style: str = "") -> None:
        self.ui.show_message(text, style)

    def say_final(self, text: str, style: str = "") -> None:
        """Shutdown / summary lines: shown BELOW the live dashboard once it has closed."""
        if self.ui.interactive:
            self._final.append((text, style))
        else:
            self.say(text, style)

    def _banner(self) -> None:
        self.ui.startup_banner(self.log.checks)

    def _fail(self, name: str, detail: str, code: int = EXIT_STARTUP_FAILED) -> int:
        self.log.add(name, False, detail)
        return self._stopped(detail, code)

    def _stopped(self, detail: str = "", code: int = EXIT_STARTUP_FAILED) -> int:
        """Show the banner + the fail-closed message for an already-logged blocking check."""
        self._banner()
        if not detail:
            failed = [c for c in self.log.checks if not c.ok and c.blocking]
            detail = f"{failed[-1].name}: {failed[-1].detail}" if failed else ""
        if self.doctor:
            self.say(f"DOCTOR: FAILED — {_safe(detail)}", "bold red")
        else:
            self.say(f"Startup stopped (fail closed, no trading): {_safe(detail)}", "bold red")
        return code

    def _progress(self, text: str) -> None:
        self.say(f"  … {text}", "dim")

    @property
    def label(self) -> str:
        if self.mode == RunMode.REPLAY:
            return REPLAY_LABEL
        return LABEL_RESEARCH if any(a.can_fill for a in self.auth.values()) else LABEL_SHADOW

    # ------------------------------------------------------------------ signals

    def _install_signals(self) -> None:
        if not self.hooks.install_signals:
            return
        try:
            self._prev_handlers[signal.SIGTERM] = signal.getsignal(signal.SIGTERM)
            self._prev_handlers[signal.SIGINT] = signal.getsignal(signal.SIGINT)
            if self.lock is not None:
                self.lock.install_sigterm_release()  # fallback, replaced just below
            signal.signal(signal.SIGTERM, self.stop.handler)
            signal.signal(signal.SIGINT, self.stop.handler)
        except ValueError:  # not the main thread
            self._prev_handlers.clear()

    def _restore_signals(self) -> None:
        for signum, handler in self._prev_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, TypeError):
                pass
        self._prev_handlers.clear()

    # ------------------------------------------------------------------ entry point

    def execute(self) -> int:
        code = EXIT_OK
        try:
            with self.ui:
                code = self._run()
        except KeyboardInterrupt:
            # A repeated Ctrl+C while no StopFlag handler is installed (before startup finished):
            # shut down through the normal path, no traceback.
            code = self._interrupted_hard()
        except Exception as exc:  # noqa: BLE001 - last line of defence, state is checkpointed
            code = self._crash(exc)
        finally:
            try:
                self._cleanup()
            except KeyboardInterrupt:
                pass
        try:
            for text, style in self._final:
                self.say(text, style)
        except KeyboardInterrupt:
            pass
        self._final.clear()
        return code

    def _interrupted_hard(self) -> int:
        try:
            self._shutdown_engine("operator stop (repeated Ctrl+C)")
        except (Exception, KeyboardInterrupt):  # noqa: BLE001
            pass
        return EXIT_OK

    def _crash(self, exc: Exception) -> int:
        reason = f"crash: {type(exc).__name__}: {_safe(exc, 200)}"
        if self.error_logger is not None:
            try:
                self.error_logger.error("app crash: %s", reason, exc_info=exc)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.say(f"UNEXPECTED ERROR ({reason}); shutting down safely.", "bold red")
            self._shutdown_engine(reason)
        except Exception:  # noqa: BLE001
            pass
        return EXIT_CRASH

    def _cleanup(self) -> None:
        # Every step is isolated: a repeated Ctrl+C (handled by StopFlag while installed) or an
        # error in one step must not skip the remaining ones or print a traceback.
        if self.feed is not None:
            try:
                clean = self.feed.close()
            except (Exception, KeyboardInterrupt):  # noqa: BLE001
                clean = True
            if not clean:
                self.say(
                    "An in-flight candle fetch did not finish; abandoned (the checkpoint is "
                    "already written, the process will not wait for it).",
                    "dim",
                )
        if self.provider is not None:
            try:
                self.provider.close()
            except (Exception, KeyboardInterrupt):  # noqa: BLE001
                pass
        if self.lock is not None:
            try:
                self.lock.release()
            except (Exception, KeyboardInterrupt):  # noqa: BLE001
                pass
        self._restore_signals()

    # ------------------------------------------------------------------ the sequence

    def _run(self) -> int:
        rc = self._step_lock()
        if rc is not None:
            return rc
        self._install_signals()
        if self.mode == RunMode.LIVE:
            rc = self._holiday_exit()
            if rc is not None:
                return rc
        rc = self._steps_provider()
        if rc is not None:
            return rc
        if self.doctor:
            return self._finish_doctor()
        rc = self._step_account()
        if rc is not None:
            return rc
        self.log.add(
            "stream loop",
            True,
            (
                f"polling every {self.cfg.polling.interval_seconds:g}s, candles from the provider "
                f"(single background worker), terminal ~2 Hz; {self._request_budget_text()}"
                if self.mode == RunMode.LIVE
                else f"{REPLAY_BANNER}: replaying simulated ticks "
                + ("instantly" if not self.args.speed else f"at {self.args.speed:g} sim-min/s")
            ),
        )
        self._banner()
        self._announce_start()
        return self._loop_and_shutdown()

    def _request_budget_text(self) -> str:
        """One line: broker requests per second at the configured polling settings."""
        poll = self.cfg.polling
        per_scrip = 1.0 / max(poll.interval_seconds, poll.option_depth_interval_seconds)
        spot = 1.0 / poll.interval_seconds
        flat = spot + 2 * per_scrip
        held = spot + 3 * per_scrip
        return (
            f"request budget ~{flat:.1f} req/s flat (spot + CE/PE depth), up to ~{held:.1f} req/s "
            "with a position (+ held contract), + 1 historical GET per 5s while candles are "
            "outstanding; provider cap 4 req/s, 5paisa documents ~5 req/s (UNVERIFIED)"
        )

    # step 3 ----------------------------------------------------------------

    def _step_lock(self) -> int | None:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        lock = SingleInstanceLock(self.paths.lock, mode=self.mode.value)
        if not lock.acquire():
            holder = lock.holder or {}
            who = describe_started(holder) if holder else "holder details unavailable"
            self.log.add(
                "single-instance lock",
                False,
                f"another instance is already running ({who}); lock file {self.paths.lock}. "
                "Stop it first (Ctrl+C in its terminal); nothing was started.",
            )
            self._banner()
            return EXIT_LOCK_HELD
        self.lock = lock
        if self.mode == RunMode.LIVE:
            redaction.silence_sdk_logging(self.paths.errors_log)
        self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
        self.error_logger = get_error_logger(self.paths.logs_dir)
        detail = f"acquired {self.paths.lock.name}; SDK logging redacted to {self.paths.errors_log}"
        if self.mode == RunMode.REPLAY and not self.doctor:
            removed = reset_replay_root(self.paths.root)
            detail += f"; fresh replay state ({len(removed)} old replay file(s) removed)"
        self.log.add("single-instance lock", True, detail)
        return None

    def _holiday_exit(self) -> int | None:
        if self.calendar.is_entry_session_supported(self.date):
            return None
        special = self.calendar.is_trading_day(self.date)
        why = (
            "a special session this build does not support"
            if special
            else f"not an NSE trading day ({self.date:%A}: weekend or exchange holiday)"
        )
        self.log.add("broker login", True, "skipped: nothing to do today", blocking=False)
        self.log.add(
            "exchange calendar", True, f"{self.date}: {why} — nothing to do", blocking=False
        )
        self._banner()
        self.say(f"{self.date}: {why}. No session, nothing started. Exiting cleanly.", "bold")
        return EXIT_OK

    # steps 4-10 ---------------------------------------------------------------

    def _make_provider(self) -> int | None:
        factory = self.ctx.provider_factory or default_provider_factory
        try:
            self.provider = factory(
                mode=self.mode, cfg=self.cfg, clock=self.clock, trading_date=self.date
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                "broker login", f"provider unavailable: {type(exc).__name__}: {_safe(exc)}"
            )
        return None

    def _steps_provider(self) -> int | None:
        assert self.mode in (RunMode.LIVE, RunMode.REPLAY)
        if self.stop.is_set():
            return self._interrupted()
        rc = self._make_provider()
        if rc is not None:
            return rc
        provider = self.provider
        assert provider is not None

        # ---- 4 login / connectivity
        self._progress(
            "logging in to 5paisa (read-only) and running provider checks"
            if self.mode == RunMode.LIVE
            else f"{REPLAY_BANNER}: preparing the simulated replay (no broker login, no network)"
        )
        try:
            checks = list(provider.startup_checks(self.date))
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                "broker login", f"startup checks failed: {type(exc).__name__}: {_safe(exc)}"
            )
        by_name = {c.name: c for c in checks}
        if self.mode == RunMode.LIVE:
            login = [c for n, c in by_name.items() if n in ("login", "token_valid")]
            ok = bool(login) and all(c.ok for c in login)
            detail = "; ".join(f"{c.name}: {c.detail}" for c in login) or "no login check reported"
        else:
            login = [c for c in checks if c.name != "warmup"]
            ok = all(c.ok or not c.blocking for c in login)
            detail = "; ".join(c.detail for c in login[:3])
        if not ok:
            return self._fail("broker login", detail)
        if self.mode == RunMode.REPLAY:
            self.log.add("no broker login (replay)", True, f"{REPLAY_BANNER}: {detail}")
        else:
            self.log.add("broker login", True, detail)

        # ---- 5 calendar
        cal = by_name.get("calendar")
        if cal is not None and not cal.ok:
            return self._fail("exchange calendar", cal.detail)
        self.log.add(
            "exchange calendar",
            True,
            cal.detail if cal else f"{self.date} is an NSE trading day ({self.date:%A})",
        )
        if self.stop.is_set():
            return self._interrupted()

        # ---- 6 previous trading day OHLC (+ the warm-up fetch it comes with)
        self._progress("loading previous-session OHLC and warm-up history")
        try:
            warmup = provider.previous_session_and_warmup(self.date)
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                "previous trading-day OHLC",
                f"warm-up history unavailable (fail closed): {type(exc).__name__}: {_safe(exc)}",
            )
        self.warmup = warmup
        p = warmup.previous_session
        self.log.add(
            "previous trading-day OHLC",
            True,
            f"{p.session_date}: O {p.open:.2f} H {p.high:.2f} L {p.low:.2f} C {p.close:.2f}",
        )

        # ---- 7 warm-up history
        notes = "; ".join(_safe(n, 120) for n in warmup.source_notes[:2])
        counts = f"{len(warmup.daily_bars)} daily bars, {len(warmup.intraday_bars)} prior 5m bars"
        thin = not warmup.daily_bars or not warmup.intraday_bars
        self.log.add(
            "warm-up history",
            not thin,
            counts + (f" ({notes})" if notes else "") + (" — EMPTY history" if thin else ""),
        )
        if thin:
            return self._stopped()

        # ---- 8 reference levels
        builder_cls = self.hooks.levels_builder_cls or LiveReferenceBuilder
        try:
            builder = builder_cls(warmup, self.date)
            problems = [str(x) for x in builder.problems()]
            warnings = [str(x) for x in getattr(builder, "warnings", lambda: [])()]
            levels = builder.display_levels()
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                "reference levels", f"levels failed: {type(exc).__name__}: {_safe(exc)}"
            )
        if problems:
            return self._fail("reference levels", "NOT READY: " + "; ".join(problems))
        shown = ", ".join(
            f"{k} {levels[k]:.2f}"
            for k in ("PDH", "PDL", "PDC", "EMA200")
            if isinstance(levels.get(k), int | float)
        )
        self.log.add(
            "reference levels",
            True,
            (shown or "ready")
            + (f"; warnings: {'; '.join(warnings)}" if warnings else "")
            + (f"; {levels.get('notes')}" if levels.get("notes") else ""),
        )

        # ---- 9 contracts
        rc = self._step_contracts(by_name)
        if rc is not None:
            return rc
        if self.stop.is_set():
            return self._interrupted()

        # ---- 10 spot + quote freshness
        return self._step_freshness(by_name)

    def _step_contracts(self, by_name: dict[str, CheckResult]) -> int | None:
        assert self.provider is not None
        try:
            contracts = list(self.provider.load_contracts(self.date))
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                "contracts", f"contract load failed: {type(exc).__name__}: {_safe(exc)}"
            )
        if not contracts:
            return self._fail("contracts", "no NIFTY option contracts returned")
        self.contracts = contracts
        try:
            self.resolver = ContractResolver(contracts, self.cfg)
            expiry = self.resolver.target_expiry(self.date, self.clock.now())
        except (ContractSelectionError, ValueError) as exc:
            return self._fail("contracts", f"no usable expiry: {_safe(exc)}")
        lots = sorted({c.lot_size for c in contracts if c.expiry == expiry})
        sm = by_name.get("scrip_master")
        extra = ""
        blocking_stale = False
        if sm is not None and not sm.ok:
            extra = f"; scrip master check: {sm.detail}"
            blocking_stale = sm.blocking
        self.log.add(
            "contracts",
            not blocking_stale,
            f"{len(contracts)} contracts, trading expiry {expiry} ({self.cfg.execution.expiry_policy}), "
            f"lot size {', '.join(str(x) for x in lots)} (from the scrip master){extra}",
        )
        if blocking_stale:
            return self._stopped()
        return None

    def _in_market_hours(self, now: datetime) -> bool:
        s = self.cfg.session
        return self.calendar.is_entry_session_supported(now.date()) and (
            s.market_open <= now.time() < s.market_close
        )

    def _step_freshness(self, by_name: dict[str, CheckResult]) -> int | None:
        assert self.provider is not None
        provider = self.provider
        now = self.clock.now()
        in_hours = self._in_market_hours(now) if self.mode == RunMode.LIVE else False
        self._progress("polling spot and the ATM CE/PE quotes")
        try:
            first = provider.poll([])
        except Exception as exc:  # noqa: BLE001
            first = PollResult(
                spot=None, errors=[f"poll failed: {type(exc).__name__}: {_safe(exc)}"]
            )
        basis = (
            first.spot.price
            if first.spot
            else (self.warmup.previous_session.close if self.warmup else 0)
        )
        scrips: list[int] = []
        pair_text = "ATM CE/PE not resolvable"
        try:
            assert self.resolver is not None
            ce, pe = self.resolver.candidate_pair(float(basis), self.date, now)
            scrips = [ce.scrip_code, pe.scrip_code]
            pair_text = f"ATM {ce.strike:g} {ce.expiry}"
        except (ContractSelectionError, ValueError) as exc:
            pair_text = f"ATM CE/PE not resolvable: {_safe(exc, 160)}"
        try:
            poll = provider.poll(scrips) if scrips else first
        except Exception as exc:  # noqa: BLE001
            poll = PollResult(
                spot=None, errors=[f"poll failed: {type(exc).__name__}: {_safe(exc)}"]
            )
        spot = poll.spot or first.spot
        now = self.clock.now()
        problems: list[str] = []
        blocking = False
        parts: list[str] = []
        if spot is None:
            problems.append(
                "no spot tick" + (f" ({_safe(poll.errors[0], 120)})" if poll.errors else "")
            )
            blocking = in_hours
        else:
            age = (now - spot.exchange_time).total_seconds()
            parts.append(
                f"spot {spot.price:.2f} exchange time {spot.exchange_time:%H:%M:%S} (age {age:.0f}s)"
            )
            if age > STARTUP_SPOT_MAX_AGE_SECONDS:
                problems.append(
                    f"spot tick is {age:.0f}s old (> {STARTUP_SPOT_MAX_AGE_SECONDS:.0f}s)"
                    + ("" if in_hours else " — expected outside market hours")
                )
                blocking = blocking or in_hours
        fresh = 0
        for q in poll.quotes.values():
            qage = (now - q.received_at).total_seconds()
            if q.is_valid_book and qage <= self.cfg.execution.max_quote_age_seconds:
                fresh += 1
        if scrips:
            parts.append(f"{fresh}/{len(scrips)} option quotes fresh ({pair_text})")
            if fresh < len(scrips):
                problems.append(
                    "option quotes missing/stale"
                    + ("" if in_hours else " — expected before the open / after the close")
                )
        else:
            problems.append(pair_text)
        pf = by_name.get("spot_freshness")
        if pf is not None and not pf.ok and pf.blocking and in_hours:
            problems.append(f"provider: {pf.detail}")
            blocking = True
        ok = not blocking
        detail = "; ".join(parts + problems) or "ok"
        if problems and ok:
            detail = "WARNING (non-blocking): " + detail
        # A non-blocking warning stays "ok" (it never stops startup) but its detail starts with
        # "WARNING", which the startup table draws with the warning glyph, not the green tick.
        self.log.add("spot & quote freshness", ok, detail, blocking=blocking)
        if not ok:
            return self._stopped()
        return None

    # step 11 ----------------------------------------------------------------

    def _recovery_preview(self) -> RecoveryDecision:
        cp = load_checkpoint(self.paths.checkpoint)
        rows = TradeCsvJournal(self.paths.trades_csv).read_trades().rows
        return recovery_decision(
            cp,
            rows,
            today=self.date,
            now=self.clock.now(),
            run_mode=self.mode.value,
            recovery_ack=load_recovery_ack(recovery_ack_path(self.paths.checkpoint)),
        )

    def _finish_doctor(self) -> int:
        decision = self._recovery_preview()
        a = self.cfg.account
        ok = decision.action != RecoveryAction.RECOVERY_REQUIRED
        self.log.add(
            "virtual account",
            ok,
            f"starting capital {fmt_inr(a.starting_capital_inr)}; recovery check (read-only): "
            f"{decision.action.value}: {_safe(decision.reason, 300)}"
            + ("" if ok else " — resolve with --acknowledge-recovery after reviewing"),
        )
        self.log.add(
            "stream loop",
            True,
            "not started (--doctor: checks only, nothing written)",
            blocking=False,
        )
        self._banner()
        if self.log.blocked:
            self.say("DOCTOR: FAILED — see the ✗ lines above. No trading would start.", "bold red")
            return EXIT_STARTUP_FAILED
        self.say("DOCTOR: all checks passed.", "bold green")
        return EXIT_OK

    def _build_engine(self) -> SessionEngine:
        assert self.warmup is not None
        from trading_platform.backtesting.portfolio_runner import build_authorized_strategies

        strategies = (
            self.hooks.strategies_factory()
            if self.hooks.strategies_factory
            else build_authorized_strategies()
        )
        started = self.clock.now()
        prefix = "SIM" if self.mode == RunMode.REPLAY else "LIVE"
        session_id = (
            f"{prefix}-{self.date:%Y%m%d}"
            if self.mode == RunMode.REPLAY
            else f"{prefix}-{started:%Y%m%d-%H%M%S}"
        )
        writer = SnapshotWriter(
            self.paths.snapshot,
            session_id=session_id,
            session_date=self.date.isoformat(),
            mode=self.mode.value,
            label=self.label,
            pid=os.getpid(),
            started_at=started.isoformat(),
            min_interval_seconds=1.0 if self.mode == RunMode.LIVE else 0.5,
        )
        info: dict[str, Any] = {
            "provider": type(self.provider).__name__,
            "mode": self.mode.value,
            "read_only": True,
        }
        if self.mode == RunMode.REPLAY:
            info["banner"] = REPLAY_BANNER
        return SessionEngine(
            self.cfg,
            clock=self.clock,
            mode=self.mode,
            strategies=strategies,
            auth=self.auth,
            contracts=self.contracts,
            warmup=self.warmup,
            levels_builder_cls=self.hooks.levels_builder_cls,
            trades_journal=TradeCsvJournal(self.paths.trades_csv),
            events=EventJournal(self.paths.journal_dir),
            checkpoint_path=self.paths.checkpoint,
            snapshot_writer=writer,
            error_logger=self.error_logger,
            session_id=session_id,
            provider_info=info,
            label_for_records=self.label,
        )

    def _step_account(self) -> int | None:
        engine = self._build_engine()
        self.engine = engine
        result = engine.start_session(self.date)
        snap = engine.snapshot()
        acct = snap.get("account") or {}
        position = snap.get("position")
        detail = (
            f"{_safe(result.recovery, 260)}; state {result.state.value}; starting capital "
            f"{fmt_inr(acct.get('starting_capital'))}, equity {fmt_inr(acct.get('equity'))}, "
            f"session-open equity {fmt_inr(acct.get('session_open_equity'))}"
        )
        if position:
            detail += (
                f"; OPEN virtual position {position.get('trade_id')} {position.get('symbol')} "
                f"({position.get('status')}) restored from the checkpoint"
            )
        monitored = result.state == EngineState.RECOVERY_REQUIRED and engine.position is not None
        ok = result.ok or monitored
        if not ok:
            detail += (
                "; trading blocked. Review the details above/in runtime/journal, then run with "
                "--acknowledge-recovery if the state is understood."
                if result.state == EngineState.RECOVERY_REQUIRED
                else f"; {_safe(result.reason, 300)}"
            )
        elif monitored:
            detail += (
                "; RECOVERY_REQUIRED: the position is monitored but NO new entries are allowed"
            )
        self.log.add("virtual account", ok, detail)
        if not ok:
            self._shutdown_engine("startup blocked")
            return self._stopped()
        self._start_events = [_event_dict(ev) for ev in engine.start_events]
        self._prepare_feed()
        return None

    def _prepare_feed(self) -> None:
        """Silent catch-up of today's already-completed candles (also on RESUME)."""
        assert self.engine is not None and self.provider is not None
        factory = self.hooks.executor_factory or DaemonExecutor
        self.feed = CandleFeed(
            self.provider,
            self.engine,
            self.cfg,
            self.date,
            factory(),
            self.hooks.monotonic_fn,
        )
        try:
            done = list(self.provider.completed_candles(self.date, self.clock.now()))
        except Exception as exc:  # noqa: BLE001
            done = []
            self.feed.last_error = f"startup candle catch-up failed: {type(exc).__name__}: {exc}"
        if done:
            self.engine.restore_candles(done)
            self.feed.mark_delivered(done)
        self._warn_late_start(len(done))

    def _expected_closed_candles(self, now: datetime) -> int:
        """How many 5m candles of today's session have closed by ``now`` (0 before 09:20)."""
        first = datetime.combine(self.date, self.cfg.session.market_open, IST)
        last = datetime.combine(self.date, self.cfg.session.market_close, IST)
        elapsed = (min(now.astimezone(IST), last) - first).total_seconds()
        return max(0, int(elapsed // (CANDLE_MINUTES * 60)))

    def _warn_late_start(self, accepted: int) -> None:
        """STARTUP warning: a start/resume after 09:20 cannot cross-check earlier candles.

        The provider accepts a vendor candle only if THIS process sampled a spot tick near its
        close.  A process started later has no such ticks, so earlier candles are rejected
        (NO_SAMPLE_UNTRUSTED) and, because the candle chain must be contiguous, the strategies
        may stay BLOCKED for the rest of the session.  The fail-closed default is kept.
        """
        if self.mode != RunMode.LIVE:
            return
        now = self.clock.now().astimezone(IST)
        if not self.calendar.is_entry_session_supported(now.date()):
            return
        open_t = datetime.combine(now.date(), self.cfg.session.market_open, IST)
        if now <= open_t + timedelta(minutes=CANDLE_MINUTES):
            return  # on time: this process samples ticks from the start
        if now.time() >= self.cfg.session.market_close:
            return
        expected = self._expected_closed_candles(now)
        if accepted >= expected:
            return
        c = self.cfg.candles
        if c.require_sample_crosscheck:
            detail = (
                f"started/resumed after 09:20 ({now:%H:%M:%S}): this process has no spot ticks "
                "from before now, so the earlier candles cannot be cross-checked against its "
                f"own ticks ({accepted}/{expected} accepted). Fail-closed default kept: they are "
                "rejected (NO_SAMPLE_UNTRUSTED) and strategies may stay BLOCKED for the rest of "
                "the session; no signal is generated from unchecked candles. "
                "(candles.require_sample_crosscheck in the config relaxes this at your own risk)"
            )
        else:
            detail = (
                f"started/resumed after 09:20 ({now:%H:%M:%S}): earlier candles cannot be "
                "cross-checked against this process's ticks; candles.require_sample_crosscheck "
                f"is OFF in the config, so a vendor candle older than "
                f"{c.uncrosschecked_min_age_seconds:g}s is accepted UNCHECKED "
                f"({accepted}/{expected} accepted so far)"
            )
        self.log.add("candle cross-check", True, "WARNING (non-blocking): " + detail, blocking=False)

    def _announce_start(self) -> None:
        assert self.engine is not None
        self.ui.update(self.engine.snapshot(), self._start_events)
        if self.mode == RunMode.REPLAY:
            self.say(
                f"  {REPLAY_BANNER} — replay of {self.date}; label {REPLAY_LABEL}",
                "bold black on yellow",
            )
        for name in ACTIVE_STRATEGIES:
            rules = self.auth[name].provisional_rules if name in self.auth else ()
            if self.mode == RunMode.LIVE and self.auth[name].can_fill and rules:
                self.say(
                    f"  PROVISIONAL RULES {name}: " + " | ".join(_safe(r, 160) for r in rules), WARN
                )
        self.say(f"  Dashboard (separate terminal): {DASHBOARD_COMMAND}")
        self.say("  Stop safely with Ctrl+C: a virtual position is preserved, never faked closed.")

    # ------------------------------------------------------------------ interrupts

    def _interrupted(self) -> int:
        self._banner()
        self.say(f"Interrupted during startup ({self.stop.reason}); nothing was started.", "bold")
        return EXIT_OK

    # ------------------------------------------------------------------ events / UI

    def _emit(self, events: Sequence[EngineEvent]) -> None:
        if not events:
            return
        fresh: list[dict[str, str]] = []
        for ev in events:
            fresh.append(_event_dict(ev))
            if ev.kind == "TRADE_OPENED" and isinstance(ev.payload.get("position"), dict):
                self.opened_trades += 1
                self.ui.show_trade_opened(ev.payload["position"])
            elif ev.kind == "TRADE_CLOSED" and isinstance(ev.payload.get("trade"), dict):
                self.closed_trades.append(ev.payload["trade"])
                self.ui.show_trade_closed(ev.payload["trade"])
        assert self.engine is not None
        self.ui.update(self.engine.snapshot(), fresh)
        self._last_ui = self.hooks.monotonic_fn()

    def _ui_tick(self, force: bool = False) -> None:
        mono = self.hooks.monotonic_fn()
        if force or mono - self._last_ui >= UI_REFRESH_SECONDS:
            assert self.engine is not None
            self.ui.update(self.engine.snapshot(), [])
            self._last_ui = mono

    def _idle_until(self, deadline: float) -> None:
        mono, sleep = self.hooks.monotonic_fn, self.hooks.sleep_fn
        while not self.stop.is_set():
            remaining = deadline - mono()
            if remaining <= 0:
                break
            self._ui_tick()
            sleep(min(UI_SLICE_SECONDS, remaining))

    def _connection_info(self) -> dict[str, Any]:
        provider = self.provider
        info: dict[str, Any] = {
            "candle_source": "PROVIDER_COMPLETED"
            if self.mode == RunMode.LIVE
            else "SIMULATED_REPLAY"
        }
        status = getattr(provider, "connection_status", None)
        if self.mode == RunMode.REPLAY:
            info["status"] = "SIMULATED"  # no broker, no login, no network in a replay
            info["broker"] = "SIMULATED (no broker)"
        elif status in ("CONNECTED", "DEGRADED", "DISCONNECTED"):
            info["status"] = status
        notes: list[str] = []
        if self.mode == RunMode.REPLAY:
            notes.append(f"{REPLAY_BANNER} ({REPLAY_LABEL})")
        elif self.feed is not None:
            notes.extend(self.feed.notes())
        info["notes"] = notes
        if self.last_valid_quote_at is not None:
            info["last_valid_quote_at"] = self.last_valid_quote_at.isoformat()
        return info

    def _note_quotes(self, poll: PollResult) -> None:
        for q in poll.quotes.values():
            if q.is_valid_book and (
                self.last_valid_quote_at is None or q.received_at > self.last_valid_quote_at
            ):
                self.last_valid_quote_at = q.received_at

    def _forming(self) -> CompletedCandle | None:
        try:
            assert self.provider is not None
            return self.provider.sampled_forming_candle()
        except Exception:  # noqa: BLE001 - display only
            return None

    # ------------------------------------------------------------------ loops

    def _loop_and_shutdown(self) -> int:
        assert self.engine is not None
        reason = "session complete"
        try:
            if self.mode == RunMode.REPLAY:
                reason = self._replay_loop()
            else:
                reason = self._live_loop()
        except Exception as exc:  # noqa: BLE001
            return self._crash(exc)
        self._shutdown_engine(reason)
        return EXIT_OK

    def _live_loop(self) -> str:
        assert self.engine is not None and self.provider is not None and self.feed is not None
        engine, provider, feed = self.engine, self.provider, self.feed
        mono = self.hooks.monotonic_fn
        interval = self.cfg.polling.interval_seconds
        exit_after = (
            datetime.combine(self.date, self.cfg.session.market_close, IST) + POST_CLOSE_GRACE
        )
        pending_notice = False
        while not self.stop.is_set():
            started = mono()
            try:
                poll = provider.poll(engine.watch_scrips())
            except Exception as exc:  # noqa: BLE001
                poll = PollResult(
                    spot=None, errors=[f"poll failed: {type(exc).__name__}: {_safe(exc, 160)}"]
                )
            self._note_quotes(poll)
            engine.set_connection_info(self._connection_info())  # before the snapshot is written
            events = list(engine.on_poll(poll))
            events.extend(feed.step(self.clock.now()))
            engine.set_forming_candle(self._forming())
            engine.set_connection_info(self._connection_info())
            events.extend(engine.on_tick_clock())
            self._emit(events)
            now = self.clock.now()
            if now >= exit_after:
                flat = engine.position is None and engine.pending is None
                if flat:
                    return "session complete (market closed, flat)"
                if not pending_notice:
                    pending_notice = True
                    self.say(
                        "Market closed with an UNRESOLVED/EXIT PENDING virtual position: still "
                        "running; Ctrl+C keeps it in the checkpoint (never faked closed).",
                        "bold red",
                    )
            self._idle_until(started + interval)
        return self.stop.reason

    def _replay_loop(self) -> str:
        assert self.engine is not None and self.provider is not None
        engine, provider = self.engine, self.provider
        speed = float(self.args.speed or 0.0)
        mono = self.hooks.monotonic_fn
        prev: datetime | None = None
        steps = provider.run_events(engine.watch_scrips)  # type: ignore[attr-defined]
        for step in steps:
            if self.stop.is_set():
                return self.stop.reason
            if speed > 0 and prev is not None:
                self._idle_until(mono() + (step.now - prev).total_seconds() / (60.0 * speed))
                if self.stop.is_set():
                    return self.stop.reason
            prev = step.now
            self._note_quotes(step.poll_result)
            engine.set_connection_info(self._connection_info())  # before the snapshot is written
            events = list(engine.on_poll(step.poll_result))
            for candle in step.completed_candles_due:
                events.extend(engine.on_candle(candle))
            engine.set_forming_candle(self._forming())
            engine.set_connection_info(self._connection_info())
            events.extend(engine.on_tick_clock())
            if events:
                self._emit(events)
            else:
                self._ui_tick()
        return "replay complete"

    # ------------------------------------------------------------------ shutdown

    def _shutdown_engine(self, reason: str) -> None:
        engine = self.engine
        if engine is None:
            return
        self.engine = None  # idempotent
        result = engine.shutdown(reason)
        try:
            self.ui.update(engine.snapshot(), [])
        except Exception:  # noqa: BLE001
            pass
        self.say_final(
            f"Shutdown ({reason}): engine {result.final_state.value}; checkpoint "
            f"{'written' if result.checkpoint_written else 'not rewritten'}.",
            "bold",
        )
        for item in result.unresolved:
            self.say_final(
                f"UNRESOLVED: {item} — virtual position preserved, NOT closed. Restart the app "
                "to resume managing it.",
                "bold red",
            )
        if self.stop.signals_seen > 1:
            self.say_final(
                "Repeated stop signal ignored: the checkpoint is always written first.", WARN
            )
        if self.mode == RunMode.REPLAY:
            self._replay_summary()

    def _replay_summary(self) -> None:
        rows = TradeCsvJournal(self.paths.trades_csv).read_trades().rows
        net = sum(float(r["net_pnl"]) for r in rows)
        start = self.cfg.account.starting_capital_inr
        self.say_final(f"=== {REPLAY_BANNER} — {REPLAY_LABEL} ===", "bold black on yellow")
        self.say_final(
            f"Replay {self.date}: {len(rows)} closed virtual trade(s); net P&L {fmt_inr(net, signed=True)}; "
            f"final balance {fmt_inr(start + net)} (= starting {fmt_inr(start)} + net)."
        )
        self.say_final(
            f"Replay output only under {self.paths.root} (live ledger/checkpoint untouched)."
        )


# =========================================================================== public runners


def run_live(ctx: AppContext, cfg: LiveConfig, ui: TerminalUI, log: StartupLog) -> int:
    clock = ctx.clock or SystemClock()
    ctx.ui_now = clock.now
    auth = authorize_all(cfg, bool(ctx.args.research_paper))
    run = _Run(ctx, cfg, RunMode.LIVE, ui, log, auth, clock.now().astimezone(IST).date(), clock)
    return run.execute()


def run_doctor(ctx: AppContext, cfg: LiveConfig, ui: TerminalUI, log: StartupLog) -> int:
    if ctx.args.replay is not None:
        return run_replay(ctx, cfg, ui, log, doctor=True)
    clock = ctx.clock or SystemClock()
    auth = authorize_all(cfg, bool(ctx.args.research_paper))
    run = _Run(
        ctx,
        cfg,
        RunMode.LIVE,
        ui,
        log,
        auth,
        clock.now().astimezone(IST).date(),
        clock,
        doctor=True,
    )
    return run.execute()


def resolve_replay_date(spec: str, ui: TerminalUI, cfg: LiveConfig) -> date | None:
    if spec.lower() != "auto":
        return date.fromisoformat(spec)
    ui.show_message(
        "  … scanning the historical dataset for the first demo day (a few seconds)", "dim"
    )
    from trading_platform.live.providers.replay import first_demo_day

    paths = replay_data_paths(cfg)
    day = first_demo_day(paths["nifty_5m_path"], paths["nifty_1d_path"])
    return day.session_date if day else None


def run_replay(
    ctx: AppContext, cfg: LiveConfig, ui: TerminalUI, log: StartupLog, *, doctor: bool = False
) -> int:
    try:
        trading_date = resolve_replay_date(str(ctx.args.replay), ui, cfg)
    except ValueError:
        log.add(
            "replay date",
            False,
            f"invalid replay date {ctx.args.replay!r} (use YYYY-MM-DD or auto)",
        )
        ui.startup_banner(log.checks)
        return EXIT_USAGE
    except Exception as exc:  # noqa: BLE001
        log.add("replay date", False, f"cannot scan demo days: {type(exc).__name__}: {_safe(exc)}")
        ui.startup_banner(log.checks)
        return EXIT_STARTUP_FAILED
    if trading_date is None:
        log.add("replay date", False, "no replayable demo day found in the historical dataset")
        ui.startup_banner(log.checks)
        return EXIT_STARTUP_FAILED
    log.set_detail(
        "app",
        f"{APP_NAME}; replay of {trading_date:%Y-%m-%d %A}; REPLAY — {REPLAY_BANNER}",
    )
    start = datetime(trading_date.year, trading_date.month, trading_date.day, 9, 0, tzinfo=IST)
    clock = ManualClock(start)
    ctx.ui_now = clock.now
    run = _Run(
        ctx, cfg, RunMode.REPLAY, ui, log, _replay_auth(cfg), trading_date, clock, doctor=doctor
    )
    return run.execute()


def _write_recovery_ack(
    ui: TerminalUI,
    cfg: LiveConfig,
    paths: RuntimePaths,
    ledger: Any,
    now: datetime,
    *,
    renamed_to: str | None,
) -> int:
    """Record that today's closed trades were acknowledged, so the next start can be fresh.

    Written ONLY when the ledger is trustworthy and reconciles with the account
    (``starting capital + sum(net_pnl) == last balance_after``).  The next start honours it
    only while ``trades.csv`` still matches it exactly (row count, net P&L, last trade id).
    """
    today = [r for r in ledger.rows if str(r.get("session_date")) == now.date().isoformat()]
    if not today:
        return EXIT_OK  # nothing closed today: the next start is a plain START_FRESH anyway
    if (not ledger.schema_ok) or ledger.duplicate_ids or ledger.malformed:
        ui.show_message(
            "trades.csv is not trustworthy ("
            + _safe("; ".join(ledger.problems()), 300)
            + "): NOT recording a recovery acknowledgement; the next start stays "
            "RECOVERY_REQUIRED. Repair or archive the ledger first.",
            "bold red",
        )
        return EXIT_STARTUP_FAILED
    ok, why = ledger_reconciles(ledger.rows, cfg.account.starting_capital_inr)
    if not ok:
        ui.show_message(
            f"trades.csv does not reconcile with the account ({_safe(why, 300)}): NOT recording "
            "a recovery acknowledgement; the next start stays RECOVERY_REQUIRED.",
            "bold red",
        )
        return EXIT_STARTUP_FAILED
    ack = build_recovery_ack(
        ledger.rows, today=now.date(), run_mode="LIVE", renamed_to=renamed_to, now=now
    )
    write_recovery_ack(recovery_ack_path(paths.checkpoint), ack)
    ui.show_message(
        f"Recorded {recovery_ack_path(paths.checkpoint).name}: {len(today)} closed trade(s) "
        f"today, {ack['trades_csv_rows']} ledger row(s), net {ack['trades_csv_net_pnl']:+.2f}. "
        "The next start is a fresh session rebuilt from trades.csv (the daily-loss base is "
        "the equity before today's first trade; earlier signals are not remembered). The "
        "acknowledgement is void as soon as trades.csv changes.",
        "bold",
    )
    return EXIT_OK


def run_acknowledge_recovery(
    ctx: AppContext, cfg: LiveConfig, ui: TerminalUI, log: StartupLog
) -> int:
    """Operator action: show the recovery details, then set the checkpoint aside (never auto)."""
    clock = ctx.clock or SystemClock()
    now = clock.now().astimezone(IST)
    run = _Run(ctx, cfg, RunMode.LIVE, ui, log, authorize_all(cfg, False), now.date(), clock)
    paths = run.paths
    paths.root.mkdir(parents=True, exist_ok=True)
    lock = SingleInstanceLock(paths.lock, mode="LIVE")
    if not lock.acquire():
        ui.show_message(
            f"Cannot acknowledge: another instance holds {paths.lock} "
            f"({describe_started(lock.holder or {})}). Stop it first.",
            "bold red",
        )
        return EXIT_LOCK_HELD
    try:
        cp = load_checkpoint(paths.checkpoint)
        ledger = TradeCsvJournal(paths.trades_csv).read_trades()
        rows = ledger.rows
        if isinstance(cp, LoadFailure) and cp.kind.value == "MISSING":
            decision = recovery_decision(
                cp,
                rows,
                today=now.date(),
                now=now,
                run_mode="LIVE",
                recovery_ack=load_recovery_ack(recovery_ack_path(paths.checkpoint)),
            )
            if decision.action != RecoveryAction.RECOVERY_REQUIRED:
                ui.show_message(
                    f"No checkpoint at {paths.checkpoint}: nothing to acknowledge "
                    f"({decision.action.value}: {_safe(decision.reason, 200)})."
                )
                return EXIT_OK
            ui.show_message(f"Recovery decision: {decision.action.value}", "bold")
            ui.show_message(f"Reason: {_safe(decision.reason, 600)}")
            return _write_recovery_ack(ui, cfg, paths, ledger, now, renamed_to=None)
        decision = recovery_decision(cp, rows, today=now.date(), now=now, run_mode="LIVE")
        ui.show_message(f"Recovery decision: {decision.action.value}", "bold")
        ui.show_message(f"Reason: {_safe(decision.reason, 600)}")
        ui.show_message(f"Details: {_safe(json.dumps(decision.details, default=str), 800)}")
        position: dict[str, Any] | None = None
        if isinstance(cp, Checkpoint):
            ui.show_message(
                f"Checkpoint: session {cp.session_id} date {cp.session_date} mode {cp.run_mode} "
                f"state {cp.engine_state} written {cp.written_at}"
            )
            if cp.position:
                pos = cp.position
                position = {
                    "trade_id": pos.get("trade_id"),
                    "status": pos.get("status"),
                    "symbol": (pos.get("contract") or {}).get("symbol"),
                    "quantity": pos.get("quantity"),
                }
                ui.show_message(f"Virtual position recorded in the checkpoint: {position}", WARN)
        else:
            ui.show_message(f"Checkpoint unusable: {_safe(cp.reason, 300)}", WARN)
        if decision.action == RecoveryAction.RESUME:
            ui.show_message(
                "Refused: the checkpoint is consistent and resumable. Start the app normally.",
                "bold red",
            )
            return EXIT_STARTUP_FAILED
        if decision.action == RecoveryAction.START_FRESH:
            ui.show_message("Nothing to acknowledge: the next start is a clean START_FRESH.")
            return EXIT_OK
        stamp = f"{now:%Y%m%dT%H%M%S}"
        target = paths.checkpoint.with_name(f"checkpoint.recovered-{stamp}.json")
        counter = 1
        while target.exists():
            target = paths.checkpoint.with_name(f"checkpoint.recovered-{stamp}-{counter}.json")
            counter += 1
        os.rename(paths.checkpoint, target)
        EventJournal(paths.journal_dir).append_event(
            "RECOVERY_ACKNOWLEDGED",
            {
                "text": "operator acknowledged an uncertain restart; checkpoint set aside",
                "label": LABEL_SHADOW,
                "renamed_to": target.name,
                "decision": decision.action.value,
                "reason": decision.reason[:300],
                "position": position,
            },
            t=now,
        )
        ui.show_message(f"Checkpoint renamed to {target} and the event was journaled.", "bold")
        _write_recovery_ack(ui, cfg, paths, ledger, now, renamed_to=target.name)
        if position:
            ui.show_message(
                "WARNING: the virtual position above is NOT closed and its P&L is NOT booked; "
                "the app will no longer manage it. Reconcile it manually (runtime/journal).",
                "bold red",
            )
        return EXIT_OK
    finally:
        lock.release()


# =========================================================================== CLI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_paper_trading.py",
        description=(
            "NIFTY options VIRTUAL paper trading on read-only 5paisa data (no real orders, ever)."
        ),
    )
    p.add_argument(
        "--research-paper",
        action="store_true",
        help="CLI half of the research opt-in (config must also enable it, with the exact "
        "acknowledgement and the strategy listed); otherwise every strategy stays SHADOW_ONLY",
    )
    p.add_argument(
        "--replay",
        nargs="?",
        const="auto",
        default=None,
        metavar="DATE|auto",
        help="SIMULATED historical replay (no broker, separate runtime/replay root); "
        "'auto' = first demo day",
    )
    p.add_argument(
        "--speed",
        type=float,
        default=0.0,
        help="replay pacing: 0 = instant, N = N simulated minutes per real second",
    )
    p.add_argument("--doctor", action="store_true", help="run the startup checks only, exit 0/1")
    p.add_argument(
        "--config", type=Path, default=None, help=f"config YAML (default {DEFAULT_CONFIG_PATH})"
    )
    p.add_argument("--runtime-dir", type=Path, default=None, help="runtime directory override")
    p.add_argument("--no-ui", action="store_true", help="plain text output instead of the Rich UI")
    p.add_argument(
        "--acknowledge-recovery",
        action="store_true",
        help="operator action: print the recovery details and set an uncertain checkpoint aside",
    )
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    provider_factory: ProviderFactory | None = None,
    clock: Clock | None = None,
    console: Console | None = None,
    hooks: AppHooks | None = None,
) -> int:
    """CLI entry point.  Returns the process exit code (see the module docstring)."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:  # argparse already printed the usage error
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    ctx = AppContext(
        args=args,
        console=console,
        provider_factory=provider_factory,
        clock=clock,
        hooks=hooks or AppHooks(),
    )
    out = console or Console(force_terminal=False if args.no_ui else None)
    ui = TerminalUI(out, refresh_hz=2.0, now_fn=lambda: ctx.ui_now())
    log = StartupLog()

    problem = _validate_args(args)
    if problem:
        ui.show_message(f"usage error: {problem}", "bold red")
        return EXIT_USAGE

    # ---- steps 1-2: app + config (+ tiers)
    when = (ctx.clock.now() if ctx.clock else SystemClock().now()).astimezone(IST)
    mode_text = (
        f"REPLAY — {REPLAY_BANNER}" if args.replay is not None else "LIVE (read-only market data)"
    )
    if args.replay is not None:
        # A replay session is NOT today: show the replay date (resolved below for 'auto').
        try:
            day = date.fromisoformat(str(args.replay))
            replay_text = f"replay of {day:%Y-%m-%d %A}"
        except ValueError:
            replay_text = "replay date resolved below"
        log.add("app", True, f"{APP_NAME}; {replay_text}; {mode_text}")
    else:
        log.add("app", True, f"{APP_NAME}; {when:%Y-%m-%d %A} IST; {mode_text}")
    try:
        cfg = load_live_config(args.config) if args.config else load_live_config()
        if args.runtime_dir is not None:
            cfg = replace(
                cfg, paths=replace(cfg.paths, runtime_dir=str(Path(args.runtime_dir).resolve()))
            )
    except ConfigError as exc:
        log.add("config & authorization", False, f"configuration error: {_safe(exc, 600)}")
        ui.startup_banner(log.checks)
        return EXIT_USAGE
    if args.replay is not None:
        line = (
            "; ".join(
                f"{n} {labels.tier_label('RESEARCH_PAPER', REPLAY_LABEL, replay=True)}"
                for n in ACTIVE_STRATEGIES
            )
            + "; RF DISABLED"
        )
        extra = [
            f"{REPLAY_BANNER}: replay tiers are simulated, the research-paper opt-in does not apply"
        ]
    else:
        auth = authorize_all(cfg, bool(args.research_paper))
        line, extra = _tier_summary(auth, bool(args.research_paper), cfg)
    log.add(
        "config & authorization",
        True,
        "\n".join([f"{args.config or DEFAULT_CONFIG_PATH}: {line}", *extra]),
    )

    if args.acknowledge_recovery:
        return run_acknowledge_recovery(ctx, cfg, ui, log)
    if args.doctor:
        return run_doctor(ctx, cfg, ui, log)
    if args.replay is not None:
        return run_replay(ctx, cfg, ui, log)
    return run_live(ctx, cfg, ui, log)


def _validate_args(args: argparse.Namespace) -> str:
    if args.speed < 0:
        return "--speed must be >= 0"
    if args.speed and args.replay is None:
        return "--speed only applies to --replay"
    if args.acknowledge_recovery and (args.replay is not None or args.doctor):
        return "--acknowledge-recovery cannot be combined with --replay or --doctor"
    return ""


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
