"""Live paper-trading session engine (event-driven, clock-injected, deterministic).

The engine is the heart of the VIRTUAL paper-trading application.  It owns the single virtual
account, the single virtual position and every money rule.  It never talks to a broker: it is
*driven* by whatever a provider returns (real read-only 5paisa data in LIVE mode, a clearly
labelled SIMULATED replay in REPLAY mode, fakes in tests) through five entry points:

``start_session`` / ``restore_candles``   recovery + (silent) catch-up of today's candles
``on_candle``                             a provider-COMPLETED 5m candle -> strategies -> signals
``on_poll``                               spot + option quotes -> entry fill, SL/TP, marks, risk
``on_tick_clock``                         time-driven transitions (cutoff, square-off, 15:30)
``snapshot`` / ``shutdown``               UI snapshot and final checkpoint

There is no network, no thread, no run loop and no wall clock in here: ``clock.now()`` is the
only source of time, so the whole lifecycle is replayable and unit-testable.

Safety rules enforced here (docs/live_app_architecture.md, "Money rules")
------------------------------------------------------------------------
* Candles: LIVE accepts only ``CandleSource.PROVIDER_COMPLETED``; REPLAY only
  ``SIMULATED_REPLAY``.  Anything else is ignored with a ``CANDLE_BLOCKED`` event.  Candles must
  be in order and contiguous; a gap blocks every strategy until the missing candle(s) arrive.
* Fills are VIRTUAL only: entry = observed ASK + slippage (a quote received at/after the signal
  decision time and still fresh at use), exit = observed BID - slippage (a bid received at/after
  the trigger and fresh at use).  If no such quote exists the entry stays PENDING (-> rejected
  ``ENTRY_TIMEOUT``) and an exit becomes ``EXIT_PENDING``.  A price is never invented.
* Stop/target are evaluated on the UNDERLYING (NIFTY spot) from a fresh, monotonic spot tick.
  Square-off (15:15) is clock driven and does not need spot.  Daily loss is judged on equity
  marked to a valid fresh BID (never mid).
* Persistence order: open -> checkpoint, event journal, snapshot, event; close -> trades.csv
  row, checkpoint, event journal, snapshot, event.  A crash between the two persistence steps
  of a close is reconciled by ``checkpoint.recovery_decision`` on the next start.
* Any unexpected exception is logged, turned into an ERROR event and fails SAFE: the position is
  preserved (never faked closed), new entries are blocked and the state becomes BLOCKED.

Event kinds emitted: SIGNAL, ENTRY_PENDING, ENTRY_REJECTED, TRADE_OPENED, TRADE_CLOSED,
EXIT_PENDING, STATE_CHANGE, RISK, CANDLE_BLOCKED, CANDLE_IGNORED, RECOVERY, ERROR, SHUTDOWN.
``TRADE_OPENED.payload['position']`` is the position dict for the terminal panel (incl.
``remaining_cash``); ``TRADE_CLOSED.payload['trade']`` is ``TradeRecord.to_row()``.
"""

from __future__ import annotations

import copy
import inspect
import json
import logging
import math
import os
import re
import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from trading_platform.backtesting.constants import PORTFOLIO_PRIORITY
from trading_platform.backtesting.engine import build_signal_id
from trading_platform.core.enums import (
    DataSource,
    Exchange,
    InstrumentType,
    SignalDirection,
    Timeframe,
)
from trading_platform.core.models import Bar, Signal
from trading_platform.data.calendar import NSECalendar
from trading_platform.live import costs, labels
from trading_platform.live.authorization import AuthDecision, can_fill
from trading_platform.live.checkpoint import (
    Checkpoint,
    CheckpointError,
    LoadFailure,
    RecoveryAction,
    RecoveryDecision,
    account_to_dict,
    load_checkpoint,
    load_recovery_ack,
    position_from_dict,
    position_to_dict,
    recovery_ack_path,
    recovery_decision,
    save_checkpoint,
    validate_position_dict,
)
from trading_platform.live.config import LiveConfig
from trading_platform.live.contracts import ContractResolver, ContractSelectionError
from trading_platform.live.journal import EventJournal, EventRejectedError, TradeCsvJournal
from trading_platform.live.ledger import VirtualAccount
from trading_platform.live.models import (
    CANDLE_MINUTES,
    IST,
    CompletedCandle,
    ContractSpec,
    Direction,
    EngineState,
    EntryDecision,
    EntryRejection,
    ExecutionTier,
    ExitReason,
    OptionQuote,
    PositionStatus,
    RunMode,
    SignalRecord,
    SpotTick,
    StrategyMonitor,
    StrategyRunState,
    TradeRecord,
    VirtualPosition,
    WarmupData,
    ist_now,
)
from trading_platform.live.providers.base import Clock, PollResult
from trading_platform.live.risk import FUTURE_QUOTE_TOLERANCE_SECONDS, RiskGate
from trading_platform.strategies.base import BaseStrategy, StrategyContext

try:  # live/levels.py is a sibling module written concurrently; keep the engine importable
    from trading_platform.live.levels import LiveReferenceBuilder
except ModuleNotFoundError as _exc:  # pragma: no cover - only while levels.py is absent
    if _exc.name != "trading_platform.live.levels":
        raise
    LiveReferenceBuilder = None  # type: ignore[assignment,misc]

UTC = ZoneInfo("UTC")
R = EntryRejection

#: A pending entry that has no valid post-signal ask this long after the engine accepted the
#: signal is rejected ``ENTRY_TIMEOUT``.
ENTRY_MAX_DELAY_SECONDS = 30.0
#: Spot older than this (exchange tick time vs local receive time) is treated as stale.
SPOT_EXCHANGE_MAX_LAG_SECONDS = 60.0
#: A candle may not be evaluated before it has closed (small clock tolerance).
CANDLE_FUTURE_TOLERANCE_SECONDS = 1.0
#: Extra seconds allowed beyond ``candles.provider_wait_max_seconds`` before a delivered candle
#: is "late" and its signals are no longer actionable.
LATE_CANDLE_GRACE_SECONDS = 5.0
ERROR_EVENT_THROTTLE_SECONDS = 60.0
MAX_WATCH_SCRIPS = 8
MAX_DISPLAY_CANDLES = 80
MAX_MARKERS = 200
MAX_RECENT_EVENTS = 50
MAX_HELD_CANDLES = 100
MAX_QUOTE_CACHE = 64
#: A live-session spot gap this long (flat or not) is surfaced in ``state_reason`` as DEGRADED.
DATA_OUTAGE_REASON_SECONDS = 30.0
_RULE_ID_RE = re.compile(r"[A-Za-z0-9_]+")

#: Entry-check failures that can heal by themselves while a fill is pending.
_RETRYABLE_ENTRY: frozenset[EntryRejection] = frozenset(
    {R.NO_QUOTE, R.STALE_QUOTE, R.QUOTE_BEFORE_SIGNAL, R.CROSSED_OR_WIDE_QUOTE}
)
_ENTRY_STATES = (EngineState.READY, EngineState.RUNNING)

F = TypeVar("F", bound=Callable[..., Any])


# --------------------------------------------------------------------------- public types


@dataclass(frozen=True)
class EngineEvent:
    kind: str
    t: datetime
    text: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StartResult:
    ok: bool
    state: EngineState
    reason: str
    recovery: str


@dataclass(frozen=True)
class ShutdownResult:
    ok: bool
    reason: str
    final_state: EngineState
    position_preserved: bool
    unresolved: list[str] = field(default_factory=list)
    checkpoint_written: bool = False


@dataclass
class _PendingEntry:
    signal: SignalRecord
    contract: ContractSpec
    tier: ExecutionTier
    label: str
    created_at: datetime
    last_detail: str = "waiting for a quote"


@dataclass
class _ExitContext:
    reason: ExitReason
    trigger_time: datetime  # a bid must be received at/after this moment
    condition: str
    spot_at_trigger: float | None
    detected_at: datetime
    stop_spot: float
    target_spot: float
    detail: str = ""


# --------------------------------------------------------------------------- helpers


def _locked(fn: F) -> F:
    @wraps(fn)
    def wrapper(self: SessionEngine, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _finite(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime | date) else None


def _as_str_list(value: Any, limit: int = 30) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [str(k) for k, v in value.items() if v][:limit]
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(v) for v in list(value)[:limit]]
    return [str(value)]


def _compact(value: Any, depth: int = 0) -> Any:
    """JSON-safe, size-bounded copy of ``value`` for the event journal."""
    if value is None or isinstance(value, bool | int | str):
        return value[:300] if isinstance(value, str) else value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime | date):
        return value.isoformat()
    if hasattr(value, "value") and isinstance(getattr(value, "value", None), str | int):
        return value.value
    if depth >= 4:
        return str(value)[:120]
    if isinstance(value, Mapping):
        return {str(k)[:60]: _compact(v, depth + 1) for k, v in list(value.items())[:30]}
    if isinstance(value, list | tuple | set | frozenset):
        return [_compact(v, depth + 1) for v in list(value)[:30]]
    return str(value)[:200]


def _json_dumps(value: Any) -> str:
    return json.dumps(_compact(value), sort_keys=True, separators=(",", ":"))


def _method_short(method: str | None) -> str:
    m = (method or "").upper()
    if m.startswith("METHOD_"):
        return "M" + m.removeprefix("METHOD_")
    if m == "RETEST_BOUNCE":
        return "Retest Bounce"
    return m or ""


# --------------------------------------------------------------------------- engine


class SessionEngine:
    """Event-driven session engine for ONE trading date (see module docstring)."""

    def __init__(
        self,
        cfg: LiveConfig,
        *,
        clock: Clock,
        mode: RunMode,
        strategies: dict[str, BaseStrategy],
        auth: dict[str, AuthDecision],
        contracts: list[ContractSpec],
        warmup: WarmupData,
        levels_builder_cls: Any = None,
        trades_journal: TradeCsvJournal,
        events: EventJournal,
        checkpoint_path: Path,
        snapshot_writer: Any,
        error_logger: logging.Logger,
        session_id: str,
        provider_info: dict[str, Any] | None = None,
        label_for_records: str,
    ) -> None:
        self._lock = threading.RLock()
        self.cfg = cfg
        self.clock = clock
        self.mode = RunMode(mode)
        self.strategies = dict(strategies)
        self.auth = dict(auth)
        self.warmup = warmup
        self.levels_builder_cls = levels_builder_cls or LiveReferenceBuilder
        self.trades_journal = trades_journal
        self.events = events
        self.checkpoint_path = Path(checkpoint_path)
        self.snapshot_writer = snapshot_writer
        self.error_logger = error_logger
        self.session_id = session_id
        self.provider_info = dict(provider_info or {})
        self.label = label_for_records
        self._calendar = NSECalendar()
        self.risk = RiskGate(cfg, self._calendar)
        self.resolver = ContractResolver(contracts, cfg)
        self._expected_candle_source = (
            "PROVIDER_COMPLETED" if self.mode == RunMode.LIVE else "SIMULATED_REPLAY"
        )

        self.names = self._ordered_names()
        self.monitors: dict[str, StrategyMonitor] = {}
        self._reset_runtime()

    # ================================================================== runtime state

    def _reset_runtime(self) -> None:
        self.trading_date: date | None = None
        self.state = EngineState.STARTING
        self.state_reason = "not started"
        self.account = VirtualAccount(self.cfg.account.starting_capital_inr)
        self.position: VirtualPosition | None = None
        self.pending: _PendingEntry | None = None
        self._exit_ctx: _ExitContext | None = None
        self._exit_pending_detail = ""
        self._exit_pending_announced = False
        self.executed_signal_ids: list[str] = []
        self.closed_trade_ids: list[str] = []
        self._risk_halted = False
        self._risk_reason = ""
        self._risk_trigger_time: datetime | None = None
        self._monitor_allowed = True  # RECOVERY_REQUIRED: only when the checkpoint is consistent
        self._checkpoint_writes = True
        self._recovery_info: dict[str, Any] | None = None
        self._started_at: datetime | None = None
        self.start_events: list[EngineEvent] = []

        self.levels: Any = None
        self._levels_problems: list[str] = []
        self._levels_failed = ""
        self._contexts: dict[str, StrategyContext] = {}
        self._base_state: dict[str, StrategyRunState] = {}
        self._failed_strategies: set[str] = set()
        self._base_note: dict[str, str] = {}
        self._shadow_signalled: set[str] = set()
        self.monitors = {}
        for name in self.names:
            tier = self._tier(name)
            self.monitors[name] = StrategyMonitor(
                name=name,
                label=self._label_for(name),
                tier=tier,
                state=StrategyRunState.DISABLED
                if tier == ExecutionTier.DISABLED
                else StrategyRunState.WARMING_UP,
                note=self._provisional_note(name),
            )

        self._candles: list[CompletedCandle] = []
        self._held: dict[datetime, CompletedCandle] = {}
        self._missing_opens: set[datetime] = set()
        self._missing_reason = ""
        self._expected_open: datetime | None = None
        self._last_candle_open: datetime | None = None
        self._candle_trusted = False
        self._candle_source_seen: str | None = None
        self._forming: CompletedCandle | None = None

        self._spot: SpotTick | None = None
        self._day_high: float | None = None
        self._day_low: float | None = None
        self._quotes: dict[int, OptionQuote] = {}
        self._last_bid: tuple[float, datetime] | None = None
        self._last_valid_quote_at: datetime | None = None
        self._conn_info: dict[str, Any] = {}
        self._poll_notes: list[str] = []
        self._stale = True

        self._markers: list[dict[str, Any]] = []
        self._recent: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT_EVENTS)
        self._last_event: dict[str, Any] | None = None
        self._last_closed_trade: dict[str, Any] | None = None
        self._cutoff_announced = False
        self._close_announced = False
        self._error_seen: dict[tuple[str, str, str], datetime] = {}
        # No-fresh-spot episode with an open position (STOP/TARGET cannot be evaluated).
        self._spot_gap_announced = False
        # Poisoned-cache guard: scrips whose future-stamped quote was already logged.
        self._future_quote_logged: set[int] = set()
        # Per-trade entry facts that have no column in VirtualPosition (display / journal only).
        self._entry_extras: dict[str, dict[str, Any]] = {}
        # (state, reason) to return to once a failed checkpoint write succeeds again.
        self._ckpt_prior: tuple[EngineState, str] | None = None

    # ------------------------------------------------------------------ names / labels

    def _ordered_names(self) -> list[str]:
        known = set(self.strategies) | set(self.auth) | set(self.cfg.strategies)
        head = [n for n in PORTFOLIO_PRIORITY if n in known]
        tail = sorted(n for n in known if n not in PORTFOLIO_PRIORITY)
        return head + tail

    def _tier(self, name: str) -> ExecutionTier:
        decision = self.auth.get(name)
        if decision is None or name not in self.strategies:
            return ExecutionTier.DISABLED  # deny by default
        return decision.tier

    def _auth_label(self, name: str) -> str:
        decision = self.auth.get(name)
        label = (decision.label if decision and decision.label else "") or self.label
        if self.mode == RunMode.REPLAY and "REPLAY" not in label.upper():
            return "REPLAY_SIMULATED"  # simulated output can never carry a live-looking label
        return label

    def _method_of(self, name: str) -> str:
        scfg = self.cfg.strategies.get(name)
        if scfg is not None and scfg.method:
            return scfg.method
        strat = self.strategies.get(name)
        return str(getattr(strat, "method", "") or "")

    @staticmethod
    def _reason_text(reason: str | None) -> str:
        """Plain-English sentence for a fired rule id (free text is passed through unchanged)."""
        text = (reason or "").strip()
        if text and _RULE_ID_RE.fullmatch(text):
            return labels.rule_sentence(text)
        return text or "not recorded"

    def _label_for(self, name: str) -> str:
        short = _method_short(self._method_of(name))
        return f"{name} {short}".strip()

    def _provisional_note(self, name: str) -> str:
        decision = self.auth.get(name)
        if decision is not None and decision.provisional_rules:
            return f"provisional rules: {len(decision.provisional_rules)} unresolved"
        return ""

    # ------------------------------------------------------------------ time

    def _now(self) -> datetime:
        now = self.clock.now()
        if now.tzinfo is None:
            raise ValueError("clock.now() must be timezone-aware")
        return now.astimezone(IST)

    # ================================================================== events / logging

    def _log_error(self, message: str, exc: BaseException | None = None) -> None:
        try:
            self.error_logger.error(message, exc_info=exc if exc is not None else None)
        except Exception:  # noqa: BLE001 - logging must never break the engine
            pass

    def _journal(self, kind: str, text: str, payload: Mapping[str, Any], when: datetime) -> None:
        body = {"text": text, "label": self.label, **_compact(dict(payload))}
        try:
            self.events.append_event(kind, body, t=when)
        except EventRejectedError as exc:
            self._log_error(f"event {kind} rejected by the journal guard: {exc}")
            try:
                self.events.append_event(
                    kind,
                    {"text": text[:300], "label": self.label, "journal_note": "payload dropped"},
                    t=when,
                )
            except Exception as exc2:  # noqa: BLE001
                self._log_error(f"event {kind} could not be journaled: {exc2}")
        except Exception as exc:  # noqa: BLE001
            self._log_error(f"event {kind} journal write failed: {exc}", exc)

    def _emit(
        self,
        kind: str,
        text: str,
        payload: Mapping[str, Any] | None = None,
        *,
        t: datetime | None = None,
        journal_payload: Mapping[str, Any] | None = None,
        journal: bool = True,
    ) -> EngineEvent:
        when = t or self._now()
        body = dict(payload or {})
        event = EngineEvent(kind, when, text, body)
        if journal:
            self._journal(
                kind, text, journal_payload if journal_payload is not None else body, when
            )
        entry = {"t": when.isoformat(), "kind": kind, "text": text[:400]}
        self._recent.append(entry)
        self._last_event = entry
        return event

    def _fail(self, where: str, exc: Exception, out: list[EngineEvent]) -> None:
        """Fail safe: log, surface an ERROR event, block entries (position is never touched)."""
        self._log_error(f"engine error in {where}: {type(exc).__name__}: {exc}", exc)
        now = self._now()
        key = (where, type(exc).__name__, str(exc)[:100])
        last = self._error_seen.get(key)
        if last is None or (now - last).total_seconds() >= ERROR_EVENT_THROTTLE_SECONDS:
            self._error_seen[key] = now
            out.append(
                self._emit(
                    "ERROR",
                    f"internal error in {where}: {type(exc).__name__}: {exc}",
                    {"where": where, "error": type(exc).__name__, "position_preserved": True},
                )
            )
        if self.state not in (
            EngineState.STOPPED,
            EngineState.SHUTTING_DOWN,
            EngineState.RECOVERY_REQUIRED,
            EngineState.BLOCKED,
        ):
            self._set_state(
                EngineState.BLOCKED, f"internal error in {where}: {type(exc).__name__}", out
            )

    def _set_state(self, new: EngineState, reason: str, out: list[EngineEvent]) -> None:
        if new == self.state and reason == self.state_reason:
            return
        old = self.state
        self.state, self.state_reason = new, reason
        if old != new:
            out.append(
                self._emit(
                    "STATE_CHANGE",
                    f"engine {old.value} -> {new.value}: {reason}",
                    {"from": old.value, "to": new.value, "reason": reason},
                )
            )

    # ================================================================== start / recovery

    @_locked
    def start_session(self, trading_date: date) -> StartResult:
        """Recover or start fresh for ``trading_date`` (call once, before data flows)."""
        self._reset_runtime()
        self.trading_date = trading_date
        out: list[EngineEvent] = []
        try:
            self._started_at = self._now()
            result = self._start(trading_date, out)
        except Exception as exc:  # noqa: BLE001
            self._log_error(f"start_session failed: {exc}", exc)
            reason = f"start_session failed: {type(exc).__name__}: {exc}"
            self._set_state(EngineState.BLOCKED, reason, out)
            result = StartResult(False, self.state, reason, "ERROR")
        self.start_events = out
        self._write_snapshot(force=True)
        return result

    def _start(self, trading_date: date, out: list[EngineEvent]) -> StartResult:
        now = self._now()
        self._expected_open = datetime.combine(trading_date, self.cfg.session.market_open, IST)

        for name, strat in self.strategies.items():
            try:
                strat.reset_state()
            except Exception as exc:  # noqa: BLE001
                self._log_error(f"{name}.reset_state failed: {exc}", exc)
        self._contexts = {name: StrategyContext() for name in self.strategies}

        levels_note = self._build_levels(trading_date)

        # ---- ledger + checkpoint -> recovery decision
        ledger_problems: list[str] = []
        try:
            load = self.trades_journal.read_trades()
            rows = load.rows
            ledger_problems = load.problems()
            ledger_blocking = (not load.schema_ok) or bool(load.duplicate_ids)
        except Exception as exc:  # noqa: BLE001
            rows, ledger_blocking = [], True
            ledger_problems = [f"trades ledger unreadable: {type(exc).__name__}: {exc}"]
        cp = load_checkpoint(self.checkpoint_path)
        decision = recovery_decision(
            cp,
            rows,
            today=trading_date,
            now=now,
            run_mode=self.mode.value,
            recovery_ack=load_recovery_ack(recovery_ack_path(self.checkpoint_path)),
        )
        if ledger_blocking and decision.action != RecoveryAction.RECOVERY_REQUIRED:
            decision = RecoveryDecision(
                RecoveryAction.RECOVERY_REQUIRED,
                "trades ledger is not trustworthy: " + "; ".join(ledger_problems),
                {"ledger_problems": ledger_problems},
                checkpoint=cp if isinstance(cp, Checkpoint) else None,
            )

        if decision.action == RecoveryAction.RECOVERY_REQUIRED:
            self._enter_recovery_required(decision, cp, rows, out)
        else:
            try:
                if decision.action == RecoveryAction.RESUME:
                    self._resume(decision, out)
                else:
                    self._fresh(decision, rows, trading_date)
            except (CheckpointError, ValueError) as exc:
                decision = RecoveryDecision(
                    RecoveryAction.RECOVERY_REQUIRED,
                    f"checkpoint could not be restored: {exc}",
                    dict(decision.details),
                    checkpoint=cp if isinstance(cp, Checkpoint) else None,
                )
                self._enter_recovery_required(decision, cp, rows, out)

        reason = decision.reason
        if self.state != EngineState.RECOVERY_REQUIRED:
            if self._risk_halted:
                self._set_state(EngineState.HALTED_RISK, self._risk_reason or "halted", out)
            elif self.state == EngineState.STARTING:
                if levels_note and self.levels is None:
                    self._set_state(EngineState.BLOCKED, levels_note, out)
                else:
                    self._set_state(EngineState.READY, "session started", out)
        if levels_note:
            reason = f"{reason} | {levels_note}"

        payload = {
            "action": decision.action.value,
            "reason": decision.reason,
            "details": decision.details,
            "ledger_problems": ledger_problems,
            "open_position": self.position.trade_id if self.position else None,
            "monitor_allowed": self._monitor_allowed,
        }
        out.append(self._emit("RECOVERY", f"{decision.action.value}: {decision.reason}", payload))
        self._refresh_monitor_overlays()
        self._save_checkpoint(out)
        ok = self.state in (EngineState.READY, EngineState.RUNNING, EngineState.HALTED_RISK)
        return StartResult(ok, self.state, reason, f"{decision.action.value}: {decision.reason}")

    def _build_levels(self, trading_date: date) -> str:
        self.levels = None
        self._levels_problems = []
        self._levels_failed = ""
        if self.levels_builder_cls is None:
            self._levels_failed = "reference-level builder (live/levels.py) is unavailable"
            return self._levels_failed
        try:
            self.levels = self.levels_builder_cls(self.warmup, trading_date)
            self._levels_problems = list(self.levels.problems())
        except Exception as exc:  # noqa: BLE001
            self.levels = None
            self._levels_failed = f"reference levels failed: {type(exc).__name__}: {exc}"
            self._log_error(self._levels_failed, exc)
            return self._levels_failed
        if self._levels_problems:
            return "levels not ready: " + "; ".join(self._levels_problems)
        return ""

    def _restore_account(
        self, acct: Mapping[str, Any], position: Mapping[str, Any] | None, closed: list[str]
    ) -> VirtualAccount:
        """Rebuild (and reconcile) a ledger from the checkpoint's AccountState-style dict."""
        leg = None
        if position is not None:
            leg = {
                "trade_id": str(position["trade_id"]),
                "quantity": int(position["quantity"]),
                "entry_premium": str(position["entry_premium"]),
                "entry_charges": str(position["entry_charges"]),
            }
        data = {
            "schema": 1,
            "starting_capital": str(acct["starting_capital"]),
            "cash": str(acct["cash"]),
            "realized_pnl": str(acct["realized_pnl"]),
            "session_open_equity": str(acct.get("session_open_equity", 0.0)),
            "session_realized_pnl": str(acct.get("session_realized_pnl", 0.0)),
            "unrealized_pnl": str(acct.get("unrealized_pnl", 0.0) if leg else 0.0),
            "trades_today": int(acct.get("trades_today", 0)),
            "wins_today": int(acct.get("wins_today", 0)),
            "losses_today": int(acct.get("losses_today", 0)),
            "closed_trade_ids": list(closed),
            "open_leg": leg,
        }
        try:
            account = VirtualAccount.restore_from_checkpoint(data)
        except Exception as exc:  # LedgerError / CheckpointError from ledger.py
            raise CheckpointError(f"account does not reconcile: {exc}") from exc
        if account.session_open_equity <= 0:
            if account.has_open_position:
                raise CheckpointError("session-opening equity missing with an open position")
            account.start_session()
        return account

    def _resume(self, decision: RecoveryDecision, out: list[EngineEvent]) -> None:
        cp = decision.checkpoint
        assert cp is not None
        self.account = self._restore_account(cp.account, cp.position, cp.closed_trade_ids_today)
        self.position = position_from_dict(cp.position) if cp.position else None
        self.executed_signal_ids = list(cp.executed_signal_ids)
        self.closed_trade_ids = list(cp.closed_trade_ids_today)
        flags = cp.risk_flags or {}
        if flags.get("halted"):
            self._risk_halted = True
            self._risk_reason = str(flags.get("reason") or "daily loss limit (restored)")
        for name, row in (cp.strategy_daily_flags or {}).items():
            mon = self.monitors.get(name)
            if mon is not None and isinstance(row, Mapping):
                mon.trades_today = int(row.get("trades_today", 0) or 0)
                mon.pnl_today = float(row.get("pnl_today", 0.0) or 0.0)
        self._rebuild_exit_context()

    def _fresh(
        self, decision: RecoveryDecision, rows: list[dict[str, str]], trading_date: date
    ) -> None:
        start = self.cfg.account.starting_capital_inr
        carried = decision.carried_account
        if carried is not None:
            self.account = VirtualAccount(
                float(carried["starting_capital"]),
                cash=float(carried["cash"]),
                realized_pnl=float(carried["realized_pnl"]),
            )
        elif rows:
            total = sum(float(r["net_pnl"]) for r in rows)
            expected = start + total
            last = float(rows[-1]["balance_after"])
            if abs(last - expected) > 0.01:
                raise CheckpointError(
                    f"no checkpoint and the ledger balance {last:.2f} != configured starting "
                    f"capital {start:.2f} + sum(net_pnl) {total:.2f}"
                )
            self.account = VirtualAccount(start, cash=expected, realized_pnl=total)
        else:
            self.account = VirtualAccount(start)
        session_open: float | None = None
        if decision.details.get("recovery_ack"):
            # Operator-acknowledged restart on a day that already has closed trades: the daily
            # loss base is the equity BEFORE today's first trade, not the post-loss equity.
            today_rows = [r for r in rows if str(r.get("session_date")) == trading_date.isoformat()]
            if today_rows:
                first = today_rows[0]
                session_open = round(float(first["balance_after"]) - float(first["net_pnl"]), 2)
            self.closed_trade_ids = [str(r["trade_id"]) for r in today_rows]
        self.account.start_session(session_open)

    def _enter_recovery_required(
        self,
        decision: RecoveryDecision,
        cp: Checkpoint | LoadFailure | None,
        rows: list[dict[str, str]],
        out: list[EngineEvent],
    ) -> None:
        """Entries blocked.  Manage an existing position ONLY if the checkpoint is consistent."""
        self.position = None  # drop anything a failed resume may have half-restored
        self._exit_ctx = None
        self._set_state(EngineState.RECOVERY_REQUIRED, decision.reason, out)
        self._monitor_allowed = False
        self._checkpoint_writes = False  # never overwrite evidence we could not reconcile
        info: dict[str, Any] = {"reason": decision.reason, "details": _compact(decision.details)}
        if isinstance(cp, Checkpoint) and cp.position:
            pos = cp.position
            info["checkpoint_position"] = {
                k: pos.get(k) for k in ("trade_id", "status", "label", "quantity", "entry_time")
            }
            info["checkpoint_position"]["symbol"] = (pos.get("contract") or {}).get("symbol")
        self._recovery_info = info
        self.executed_signal_ids = (
            list(cp.executed_signal_ids) if isinstance(cp, Checkpoint) else []
        )
        if not isinstance(cp, Checkpoint):
            return
        try:
            same_day = cp.session_date == (
                self.trading_date.isoformat() if self.trading_date else ""
            )
            if (
                same_day
                and cp.run_mode == self.mode.value
                and cp.position
                and not validate_position_dict(cp.position)
            ):
                account = self._restore_account(cp.account, cp.position, cp.closed_trade_ids_today)
                self.account = account
                self.position = position_from_dict(cp.position)
                self.closed_trade_ids = list(cp.closed_trade_ids_today)
                self._rebuild_exit_context()
                self._monitor_allowed = True
                self._checkpoint_writes = (
                    True  # keeps the checkpoint, engine_state=RECOVERY_REQUIRED
                )
                info["monitoring"] = "position is consistent; monitoring allowed, entries blocked"
            else:
                try:
                    self.account = self._restore_account(
                        cp.account, None, cp.closed_trade_ids_today
                    )
                except Exception:  # noqa: BLE001 - display only
                    self.account = VirtualAccount(self.cfg.account.starting_capital_inr)
                    self.account.start_session()
        except Exception as exc:  # noqa: BLE001
            self._log_error(f"recovery: checkpoint position not usable: {exc}", exc)
            self.position = None
            self._monitor_allowed = False
            self._checkpoint_writes = False

    def _rebuild_exit_context(self) -> None:
        pos = self.position
        if pos is None or pos.status != PositionStatus.EXIT_PENDING:
            self._exit_ctx = None
            return
        since = pos.exit_pending_since or pos.entry_time
        self._exit_ctx = _ExitContext(
            reason=pos.exit_pending_reason or ExitReason.EOD,
            trigger_time=since,
            condition=f"resumed after restart: exit pending since {since:%H:%M:%S}",
            spot_at_trigger=None,
            detected_at=since,
            stop_spot=pos.stop_spot,
            target_spot=pos.target_spot,
        )
        self._exit_pending_announced = True

    @_locked
    def restore_candles(self, candles: list[CompletedCandle]) -> list[EngineEvent]:
        """Replay today's already-completed candles SILENTLY (no signal is ever acted on)."""
        out: list[EngineEvent] = []
        try:
            for candle in sorted(candles, key=lambda c: c.open_time):
                self._accept_candle(candle, out, silent=True)
        except Exception as exc:  # noqa: BLE001
            self._fail("restore_candles", exc, out)
        self._write_snapshot()
        return out

    # ================================================================== candles

    def missing_candle_opens(self) -> list[datetime]:
        """Open labels the engine still needs (gap) -- for the app to backfill via ``on_candle``."""
        with self._lock:
            return sorted(self._missing_opens)

    def _gap_reason(self) -> str:
        if self._missing_opens or self._held:
            opens = sorted(self._missing_opens)
            shown = ", ".join(o.strftime("%H:%M") for o in opens[:6])
            more = f" (+{len(opens) - 6} more)" if len(opens) > 6 else ""
            return self._missing_reason or f"missing completed candle(s): {shown}{more}"
        return ""

    def _validate_candle(self, c: CompletedCandle, now: datetime) -> str | None:
        if self.trading_date is None:
            return "session not started"
        if not isinstance(c, CompletedCandle):
            return f"not a CompletedCandle ({type(c).__name__})"
        if str(c.source.value if hasattr(c.source, "value") else c.source) != (
            self._expected_candle_source
        ):
            return (
                f"candle source {getattr(c.source, 'value', c.source)} is not accepted in "
                f"{self.mode.value} mode (only {self._expected_candle_source}); fail closed"
            )
        if c.open_time.tzinfo is None:
            return "candle open_time is not timezone-aware"
        opened = c.open_time.astimezone(IST)
        if opened.date() != self.trading_date:
            return f"candle {opened:%Y-%m-%d %H:%M} is not for session date {self.trading_date}"
        if opened.second or opened.microsecond or opened.minute % CANDLE_MINUTES:
            return f"candle label {opened:%H:%M:%S} is not on a 5-minute boundary"
        first = datetime.combine(self.trading_date, self.cfg.session.market_open, IST)
        last = datetime.combine(self.trading_date, self.cfg.session.market_close, IST) - timedelta(
            minutes=CANDLE_MINUTES
        )
        if opened < first or opened > last:
            return f"candle label {opened:%H:%M} outside {first:%H:%M}..{last:%H:%M}"
        if (c.close_time - now).total_seconds() > CANDLE_FUTURE_TOLERANCE_SECONDS:
            return f"candle {opened:%H:%M} closes {c.close_time:%H:%M:%S}, after now {now:%H:%M:%S}"
        return None

    def _to_bar(self, c: CompletedCandle) -> Bar:
        opened = c.open_time.astimezone(IST)
        return Bar(
            timestamp=opened.astimezone(UTC),
            exchange_time=opened,
            symbol="NIFTY",
            instrument_id="999920000",
            exchange=Exchange.NSE,
            instrument_type=InstrumentType.INDEX,
            interval=Timeframe.M5,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=int(c.volume),
            source=DataSource.FIVEPAISA_HISTORICAL
            if self.mode == RunMode.LIVE
            else DataSource.SYNTHETIC,
        )

    @_locked
    def on_candle(self, candle: CompletedCandle) -> list[EngineEvent]:
        out: list[EngineEvent] = []
        if self.state == EngineState.STOPPED:
            return out
        try:
            self._tick_state(self._now(), out)
            self._accept_candle(candle, out, silent=False)
        except Exception as exc:  # noqa: BLE001
            self._fail("on_candle", exc, out)
        self._write_snapshot()
        return out

    def _accept_candle(self, c: CompletedCandle, out: list[EngineEvent], *, silent: bool) -> None:
        now = self._now()
        problem = self._validate_candle(c, now)
        if problem is not None:
            self._candle_trusted = False
            out.append(
                self._emit(
                    "CANDLE_BLOCKED",
                    f"candle ignored: {problem}",
                    {"reason": problem, "source": str(getattr(c.source, "value", c.source))},
                )
            )
            return
        opened = c.open_time.astimezone(IST)
        assert self._expected_open is not None
        if opened < self._expected_open or opened in self._held:
            out.append(
                self._emit(
                    "CANDLE_IGNORED",
                    f"duplicate/older candle {opened:%H:%M} ignored (expected "
                    f"{self._expected_open:%H:%M}); never re-evaluated",
                    {"open_time": opened.isoformat(), "expected": self._expected_open.isoformat()},
                )
            )
            return
        self._candle_source_seen = str(c.source.value)
        self._display_candle(c)
        if opened > self._expected_open:
            self._hold_candle(c, opened, out)
            return
        self._process_candle(c, out, silent=silent)
        self._drain_held(out, silent=silent)

    def _display_candle(self, c: CompletedCandle) -> None:
        if all(x.open_time != c.open_time for x in self._candles):
            self._candles.append(c)
            self._candles.sort(key=lambda x: x.open_time)
            del self._candles[:-MAX_DISPLAY_CANDLES]

    def _hold_candle(self, c: CompletedCandle, opened: datetime, out: list[EngineEvent]) -> None:
        assert self._expected_open is not None
        if len(self._held) >= MAX_HELD_CANDLES:
            return
        self._held[opened] = c
        slot = self._expected_open
        gaps: list[datetime] = []
        while slot < opened:
            if slot not in self._held:
                gaps.append(slot)
            slot += timedelta(minutes=CANDLE_MINUTES)
        self._missing_opens |= set(gaps)
        self._missing_reason = ""
        reason = self._gap_reason()
        self._candle_trusted = False
        out.append(
            self._emit(
                "CANDLE_BLOCKED",
                f"gap: expected {self._expected_open:%H:%M} but got {opened:%H:%M}; strategies "
                f"BLOCKED until the missing candle(s) arrive ({reason})",
                {
                    "reason": reason,
                    "missing": [g.strftime("%H:%M") for g in gaps[:20]],
                    "held": opened.isoformat(),
                },
            )
        )
        self._refresh_monitor_overlays()

    def _drain_held(self, out: list[EngineEvent], *, silent: bool) -> None:
        while self._expected_open is not None and self._expected_open in self._held:
            nxt = self._held.pop(self._expected_open)
            self._process_candle(nxt, out, silent=silent)
        if not self._held and not self._missing_opens:
            self._missing_reason = ""

    @_locked
    def on_candle_missing(
        self, candle_open: datetime, diagnostics: dict[str, Any]
    ) -> list[EngineEvent]:
        """The provider could not supply the expected completed candle: BLOCK the strategies."""
        out: list[EngineEvent] = []
        try:
            opened = candle_open.astimezone(IST)
            if self._expected_open is None or opened >= self._expected_open:
                self._missing_opens.add(opened)
            self._missing_reason = (
                f"completed candle {opened:%H:%M} not supplied by the provider "
                "(no sampled-tick substitution)"
            )
            self._candle_trusted = False
            out.append(
                self._emit(
                    "CANDLE_BLOCKED",
                    f"{self._missing_reason}; strategies BLOCKED",
                    {"open_time": opened.isoformat(), "diagnostics": _compact(diagnostics)},
                )
            )
            self._refresh_monitor_overlays()
        except Exception as exc:  # noqa: BLE001
            self._fail("on_candle_missing", exc, out)
        self._write_snapshot()
        return out

    # ------------------------------------------------------------------ evaluation

    def _process_candle(self, c: CompletedCandle, out: list[EngineEvent], *, silent: bool) -> None:
        now = self._now()
        opened = c.open_time.astimezone(IST)
        try:
            bar = self._to_bar(c)
        except ValueError as exc:
            self._missing_opens.add(opened)
            self._missing_reason = f"candle {opened:%H:%M} has invalid OHLC: {exc}".splitlines()[0]
            self._candle_trusted = False
            out.append(
                self._emit("CANDLE_BLOCKED", f"{self._missing_reason}; strategies BLOCKED", {})
            )
            self._refresh_monitor_overlays()
            return

        self._expected_open = opened + timedelta(minutes=CANDLE_MINUTES)
        self._last_candle_open = opened
        self._candle_trusted = True
        self._missing_opens = {m for m in self._missing_opens if m > opened}
        if not self._missing_opens and not self._held:
            self._missing_reason = ""

        state_dict = self._state_for_bar(bar, opened)
        signals: list[tuple[Signal, str]] = []
        for name in self.names:
            strat = self.strategies.get(name)
            if strat is None or self._tier(name) == ExecutionTier.DISABLED:
                continue
            if name in self._failed_strategies:
                continue  # raised earlier: its state machine may have missed a bar -> stay BLOCKED
            ctx = self._contexts[name]
            ctx.state = dict(state_dict)
            try:
                sig = strat.on_bar(ctx, bar)
            except Exception as exc:  # noqa: BLE001
                sig = None
                self._failed_strategies.add(name)
                self._log_error(f"{name}.on_bar failed: {exc}", exc)
                self._base_state[name] = StrategyRunState.BLOCKED
                self._base_note[name] = f"strategy error: {type(exc).__name__}: {exc}"[:200]
                out.append(
                    self._emit(
                        "ERROR",
                        f"strategy {name} raised {type(exc).__name__}: {exc}; BLOCKED",
                        {"strategy": name},
                    )
                )
            else:
                self._refresh_monitor(name, strat, ctx, bar)
            if sig is not None and sig.direction in (SignalDirection.BUY, SignalDirection.SELL):
                signals.append((sig, name))

        if self.levels is not None:
            try:
                self.levels.on_bar_completed(bar)
                self._levels_problems = list(self.levels.problems())
            except Exception as exc:  # noqa: BLE001
                self._levels_failed = f"reference levels failed: {type(exc).__name__}: {exc}"
                self._log_error(self._levels_failed, exc)

        self._refresh_monitor_overlays()
        if signals:
            self._handle_signals(signals, c, out, silent=silent, now=now)

    def _state_for_bar(self, bar: Bar, opened: datetime) -> dict[str, Any]:
        fallback = {
            "pdh": None,
            "pdl": None,
            "prev_close": None,
            "pd_res_body": None,
            "pd_supp_body": None,
            "pd_supp_body_top": None,
            "ema_200": None,
            "levels_date": opened.date().isoformat(),
        }
        if self.levels is None or self._levels_failed:
            return fallback
        try:
            return dict(self.levels.state_for_bar(bar))
        except Exception as exc:  # noqa: BLE001
            self._levels_failed = f"reference levels failed: {type(exc).__name__}: {exc}"
            self._log_error(self._levels_failed, exc)
            return fallback

    # ------------------------------------------------------------------ strategy monitors

    @staticmethod
    def _call_eval_snapshot(
        strat: BaseStrategy, day_iso: str, ctx: StrategyContext
    ) -> dict[str, Any]:
        fn = getattr(strat, "evaluation_snapshot", None)
        if fn is None:
            return {}
        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            params = {}  # type: ignore[assignment]
        snap = fn(day_iso, ctx) if "context" in params else fn(day_iso)
        return dict(snap) if isinstance(snap, Mapping) else {}

    @staticmethod
    def _map_run_state(snap: Mapping[str, Any]) -> StrategyRunState:
        raw = str(snap.get("state") or "").upper()
        if raw in StrategyRunState.__members__:
            return StrategyRunState(raw)
        status = str(snap.get("status") or "").upper()
        if status in ("DATA_GAP", "NON_5M_BAR"):
            return StrategyRunState.BLOCKED
        if status in ("SIGNALLED", "NO_IR_BAR"):
            return StrategyRunState.DONE_FOR_DAY
        return StrategyRunState.MONITORING

    def _refresh_monitor(
        self, name: str, strat: BaseStrategy, ctx: StrategyContext, bar: Bar
    ) -> None:
        mon = self.monitors[name]
        mon.last_evaluated_candle = bar.exchange_time
        try:
            snap = self._call_eval_snapshot(strat, bar.exchange_time.date().isoformat(), ctx)
        except Exception as exc:  # noqa: BLE001
            self._log_error(f"{name}.evaluation_snapshot failed: {exc}", exc)
            snap = {}
            self._base_note[name] = f"evaluation_snapshot failed: {type(exc).__name__}"
        else:
            note_bits = [self._provisional_note(name)]
            for key in ("note", "state_reason", "reason"):
                if snap.get(key):
                    note_bits.append(str(snap[key])[:160])
                    break
            self._base_note[name] = " | ".join(b for b in note_bits if b)
        self._base_state[name] = self._map_run_state(snap)
        lv = snap.get("levels")
        mon.levels = _compact(lv) if isinstance(lv, Mapping) else {}
        mon.conditions_satisfied = _as_str_list(snap.get("conditions_satisfied"))
        mon.conditions_failed = _as_str_list(snap.get("conditions_failed"))

    def _overlay_state(self, name: str) -> tuple[StrategyRunState, str]:
        mon = self.monitors[name]
        base_note = self._base_note.get(name, mon.note)
        tier = self._tier(name)
        if tier == ExecutionTier.DISABLED:
            return StrategyRunState.DISABLED, self._disabled_note(name)
        pos = self.position
        if pos is not None and pos.signal.strategy == name:
            return StrategyRunState.IN_TRADE, base_note
        if self.pending is not None and self.pending.signal.strategy == name:
            return StrategyRunState.IN_TRADE, f"entry pending: {self.pending.last_detail}"
        gap = self._gap_reason()
        if gap:
            return StrategyRunState.BLOCKED, gap
        if self._levels_failed:
            return StrategyRunState.BLOCKED, self._levels_failed
        if self._levels_problems:
            return StrategyRunState.WARMING_UP, "; ".join(self._levels_problems)
        if self._base_state.get(name) == StrategyRunState.BLOCKED and name in self._base_note:
            return StrategyRunState.BLOCKED, base_note
        if name in self._shadow_signalled and not can_fill(tier):
            return StrategyRunState.SIGNAL_SHADOW, "signal logged, no virtual fill (SHADOW_ONLY)"
        base = self._base_state.get(name, StrategyRunState.MONITORING)
        if base == StrategyRunState.MONITORING and self._entries_closed(self._now()):
            cutoff = self.cfg.session.entry_cutoff
            tail = f" | {base_note}" if base_note else ""
            return (
                StrategyRunState.DONE_FOR_DAY,
                f"entries closed (NO_NEW_ENTRIES after {cutoff:%H:%M}){tail}",
            )
        if base == StrategyRunState.DONE_FOR_DAY and base_note.startswith("waiting:"):
            base_note = "not met: " + base_note.removeprefix("waiting:").lstrip()
        return base, base_note

    def _disabled_note(self, name: str) -> str:
        decision = self.auth.get(name)
        if decision is not None and decision.reasons:
            return decision.reasons[0]
        return "disabled"

    def _refresh_monitor_overlays(self) -> None:
        for name, mon in self.monitors.items():
            state, note = self._overlay_state(name)
            mon.state, mon.note = state, note

    # ------------------------------------------------------------------ signals

    def _signal_record(self, sig: Signal, name: str, candle: CompletedCandle) -> SignalRecord:
        opened = candle.open_time.astimezone(IST)
        meta = dict(sig.metadata or {})
        method = str(meta.get("method") or self._method_of(name) or sig.method_id)
        ref = float(sig.reference_price)
        stop_pts = _finite(meta.get("stop_points"))
        target_pts = _finite(meta.get("target_points"))
        if stop_pts is None or stop_pts <= 0:
            stop_pts = sig.risk_points
        if target_pts is None or target_pts <= 0:
            target_pts = sig.reward_points
        bullish = sig.direction == SignalDirection.BUY
        sign = 1.0 if bullish else -1.0
        satisfied = _as_str_list(meta.get("conditions_satisfied"))
        failed = _as_str_list(meta.get("conditions_failed"))
        levels = meta.get("levels")
        trigger = meta.get("trigger_candle")
        return SignalRecord(
            signal_id=build_signal_id(name, method, opened),
            strategy=name,
            method=method,
            direction=Direction.BULLISH if bullish else Direction.BEARISH,
            candle_open_time=opened,
            decision_time=opened + timedelta(minutes=CANDLE_MINUTES),
            spot_reference=ref,
            stop_spot=round(ref - sign * stop_pts, 2),
            target_spot=round(ref + sign * target_pts, 2),
            reason=str(sig.reason),
            conditions={"satisfied": satisfied, "failed": failed},
            levels=_compact(levels) if isinstance(levels, Mapping) else {},
            trigger_candle=_compact(trigger) if isinstance(trigger, Mapping) else {},
        )

    def _handle_signals(
        self,
        signals: list[tuple[Signal, str]],
        candle: CompletedCandle,
        out: list[EngineEvent],
        *,
        silent: bool,
        now: datetime,
    ) -> None:
        records: list[tuple[SignalRecord, str]] = []
        for sig, name in signals:
            try:
                records.append((self._signal_record(sig, name, candle), name))
            except Exception as exc:  # noqa: BLE001
                self._fail(f"signal_record({name})", exc, out)
        prio = {n: i for i, n in enumerate(PORTFOLIO_PRIORITY)}
        records.sort(key=lambda r: (r[0].decision_time, prio.get(r[1], len(prio)), r[1]))
        winner: SignalRecord | None = None
        late_limit = self.cfg.candles.provider_wait_max_seconds + LATE_CANDLE_GRACE_SECONDS
        for rec, name in records:
            tier = self._tier(name)
            auth_ok = self.auth[name].can_fill if name in self.auth else False
            fill_capable = can_fill(tier) and auth_ok
            out.append(self._signal_event(rec, tier, silent))
            self._record_last_signal(name, rec, tier)
            if not fill_capable and tier != ExecutionTier.DISABLED:
                self._shadow_signalled.add(name)
            self._add_marker("SIGNAL", rec.decision_time, rec.spot_reference, rec.strategy, None,
                             f"{rec.strategy} {rec.direction.value} signal",
                             direction=rec.direction.value)  # fmt: skip

            decision = self._decide_signal(
                rec, name, tier, fill_capable, winner, silent, now, late_limit
            )
            if decision is None:
                if self._begin_entry(rec, name, tier, now, out):
                    winner = rec
                continue
            self._reject_signal(rec, name, decision, out)
        self._refresh_monitor_overlays()
        self._save_checkpoint(out)  # executed signal ids must survive a restart (no re-trading)

    def _decide_signal(
        self,
        rec: SignalRecord,
        name: str,
        tier: ExecutionTier,
        fill_capable: bool,
        winner: SignalRecord | None,
        silent: bool,
        now: datetime,
        late_limit: float,
    ) -> EntryDecision | None:
        """``None`` == accepted for entry; otherwise the exact rejection."""
        sid = rec.signal_id

        def rej(code: EntryRejection, detail: str) -> EntryDecision:
            return EntryDecision(sid, False, code, detail)

        if sid in self.executed_signal_ids:
            return rej(
                R.DUPLICATE_SIGNAL, f"signal {sid} was already processed (this or an earlier run)"
            )
        self.executed_signal_ids.append(sid)
        if silent:
            return rej(
                R.CANDLE_NOT_TRUSTED,
                "replayed/restored candle: signal is not actionable (silent catch-up)",
            )
        if tier == ExecutionTier.DISABLED:
            return rej(R.STRATEGY_DISABLED, f"{rec.strategy} is DISABLED")
        if not fill_capable:
            return rej(
                R.SHADOW_ONLY, f"{rec.strategy} tier {tier.value}: signal logged, no virtual fill"
            )
        lateness = (now - rec.decision_time).total_seconds()
        if lateness > late_limit:
            return rej(
                R.CANDLE_NOT_TRUSTED,
                f"candle closed {rec.decision_time:%H:%M:%S} but was evaluated {lateness:.0f}s "
                f"later (> {late_limit:.0f}s): signal no longer actionable",
            )
        if self._levels_failed or self._levels_problems:
            why = self._levels_failed or "; ".join(self._levels_problems)
            return rej(R.ENGINE_NOT_RUNNING, f"reference levels not ready: {why}")
        if self.state == EngineState.RECOVERY_REQUIRED:
            return rej(R.RECOVERY_REQUIRED, f"recovery required: {self.state_reason}")
        if self.state == EngineState.HALTED_RISK or self._risk_halted:
            return rej(
                R.DAILY_LOSS_LIMIT, f"risk halt active: {self._risk_reason or self.state_reason}"
            )
        if self.state not in _ENTRY_STATES:
            return rej(
                R.ENGINE_NOT_RUNNING, f"engine state {self.state.value}: {self.state_reason}"
            )
        if rec.decision_time.date() != now.date() or not self.risk.market_open(now):
            return rej(R.MARKET_CLOSED, f"market not open at {now:%Y-%m-%d %H:%M:%S}")
        if self.risk.cutoff_reached(rec.decision_time):
            return rej(
                R.AFTER_ENTRY_CUTOFF,
                f"candle closes {rec.decision_time:%H:%M:%S} >= entry cutoff "
                f"{self.cfg.session.entry_cutoff:%H:%M}",
            )
        if self.risk.cutoff_reached(now):
            return rej(
                R.AFTER_ENTRY_CUTOFF,
                f"now {now:%H:%M:%S} >= entry cutoff {self.cfg.session.entry_cutoff:%H:%M}",
            )
        if winner is not None:
            return rej(
                R.ARBITRATION_LOST,
                f"lost the single position slot to {winner.signal_id} "
                f"(FIFO decision time, then {'>'.join(PORTFOLIO_PRIORITY)})",
            )
        pos = self.position
        if pos is not None:
            if pos.status == PositionStatus.EXIT_PENDING:
                return rej(R.EXIT_PENDING, f"exit pending for {pos.trade_id}; new entries blocked")
            return rej(R.POSITION_OPEN, f"position {pos.trade_id} already open")
        if self.pending is not None:
            return rej(
                R.POSITION_OPEN, f"entry for {self.pending.signal.signal_id} is already pending"
            )
        if self.cfg.account.max_open_positions < 1:
            return rej(R.POSITION_OPEN, "max_open_positions < 1")
        return None

    def _tier_text(self, tier: ExecutionTier) -> str:
        """Tier as shown to the operator: a replay is REPLAY_SIMULATED, never an opt-in."""
        if self.mode == RunMode.REPLAY and tier != ExecutionTier.DISABLED:
            return "REPLAY_SIMULATED"
        return tier.value

    def _signal_event(self, rec: SignalRecord, tier: ExecutionTier, silent: bool) -> EngineEvent:
        replay = self.mode == RunMode.REPLAY
        payload = {
            "signal_id": rec.signal_id,
            "strategy": rec.strategy,
            "method": rec.method,
            "direction": rec.direction.value,
            "candle_open_time": rec.candle_open_time.isoformat(),
            "decision_time": rec.decision_time.isoformat(),
            "spot_reference": rec.spot_reference,
            "stop_spot": rec.stop_spot,
            "target_spot": rec.target_spot,
            "reason": rec.reason,
            "reason_text": self._reason_text(rec.reason),
            "strategy_label": labels.strategy_label(rec.strategy, rec.reason),
            "tier": self._tier_text(tier),
            "tier_text": labels.tier_label(
                tier.value, self._auth_label(rec.strategy), replay=replay
            ),
            "label": self._auth_label(rec.strategy),
            "silent_replay": silent,
            "conditions": rec.conditions,
            "levels": rec.levels,
            "trigger_candle": rec.trigger_candle,
        }
        return self._emit(
            "SIGNAL",
            f"{rec.strategy} {rec.method} {rec.direction.value} signal on {rec.candle_open_time:%H:%M}"
            f" candle @ {rec.spot_reference:.2f} [{self._tier_text(tier)}]",
            payload,
        )

    def _record_last_signal(self, name: str, rec: SignalRecord, tier: ExecutionTier) -> None:
        mon = self.monitors[name]
        mon.last_signal = {
            "signal_id": rec.signal_id,
            "direction": rec.direction.value,
            "candle_open_time": rec.candle_open_time.isoformat(),
            "decision_time": rec.decision_time.isoformat(),
            "reason": rec.reason,
            "reason_text": self._reason_text(rec.reason),
            "tier": self._tier_text(tier),
            "stop_spot": rec.stop_spot,
            "target_spot": rec.target_spot,
        }

    def _reject_signal(
        self, rec: SignalRecord, name: str, decision: EntryDecision, out: list[EngineEvent]
    ) -> None:
        code = decision.rejection.value if decision.rejection else "REJECTED"
        self.monitors[name].last_rejection = f"{code}: {decision.detail}"[:300]
        out.append(
            self._emit(
                "ENTRY_REJECTED",
                f"{rec.strategy} {rec.signal_id} rejected {code}: {decision.detail}",
                {
                    "signal_id": rec.signal_id,
                    "strategy": rec.strategy,
                    "rejection": code,
                    "detail": decision.detail,
                    "label": self._auth_label(rec.strategy),
                },
            )
        )

    # ================================================================== entry

    def _fresh_spot(self, now: datetime) -> SpotTick | None:
        tick = self._spot
        if tick is None:
            return None
        age = (now - tick.received_at).total_seconds()
        if age > self.cfg.execution.max_quote_age_seconds or age < -FUTURE_QUOTE_TOLERANCE_SECONDS:
            return None
        if (tick.received_at - tick.exchange_time).total_seconds() > SPOT_EXCHANGE_MAX_LAG_SECONDS:
            return None
        return tick

    def _begin_entry(
        self,
        rec: SignalRecord,
        name: str,
        tier: ExecutionTier,
        now: datetime,
        out: list[EngineEvent],
    ) -> bool:
        """Create the PENDING entry (True) or reject it with NO_CONTRACT (False)."""
        assert self.trading_date is not None
        spot = self._fresh_spot(now)
        basis = spot.price if spot is not None else rec.spot_reference
        contract, err = self.resolver.try_select(rec.direction, basis, self.trading_date, now)
        if contract is None:
            detail = err.detail if err is not None else "no contract"
            self._reject_signal(
                rec, name, EntryDecision(rec.signal_id, False, R.NO_CONTRACT, detail), out
            )
            return False
        self.pending = _PendingEntry(
            signal=rec,
            contract=contract,
            tier=tier,
            label=self._auth_label(name),
            created_at=now,
        )
        out.append(
            self._emit(
                "ENTRY_PENDING",
                f"{rec.strategy} {rec.direction.value}: waiting for a fresh ask on "
                f"{contract.symbol} (quote must be received at/after {rec.decision_time:%H:%M:%S})",
                {
                    "signal_id": rec.signal_id,
                    "scrip_code": contract.scrip_code,
                    "symbol": contract.symbol,
                    "strike": contract.strike,
                    "option_type": contract.option_type,
                    "selection_spot": basis,
                    "selection_spot_source": "live" if spot is not None else "signal_reference",
                },
            )
        )
        self._try_fill(now, out)
        return True

    def _reject_pending(self, code: EntryRejection, detail: str, out: list[EngineEvent]) -> None:
        p = self.pending
        assert p is not None
        self.pending = None
        self._reject_signal(
            p.signal, p.signal.strategy, EntryDecision(p.signal.signal_id, False, code, detail), out
        )
        self._refresh_monitor_overlays()

    def _try_fill(self, now: datetime, out: list[EngineEvent]) -> None:
        p = self.pending
        if p is None:
            return
        assert self.trading_date is not None
        elapsed = (now - p.created_at).total_seconds()
        if elapsed > ENTRY_MAX_DELAY_SECONDS:
            self._reject_pending(
                R.ENTRY_TIMEOUT,
                f"no valid post-signal ask for {p.contract.symbol} within "
                f"{ENTRY_MAX_DELAY_SECONDS:.0f}s of the signal being accepted "
                f"(decision {p.signal.decision_time:%H:%M:%S}); last check: {p.last_detail}",
                out,
            )
            return
        quote = self._quotes.get(p.contract.scrip_code)
        check = self.risk.check_entry(
            signal=p.signal,
            tier=p.tier,
            contract=p.contract,
            quote=quote,
            account=self.account,
            now=now,
            exit_pending=False,
            engine_running=self.state in _ENTRY_STATES and not self._risk_halted,
        )
        if not check.accepted:
            codes = {r.code for r in check.rejections}
            decision = check.to_decision(p.signal.signal_id)
            p.last_detail = decision.detail
            if codes <= _RETRYABLE_ENTRY:
                return  # retry on the next poll until ENTRY_TIMEOUT
            assert decision.rejection is not None
            self._reject_pending(decision.rejection, decision.detail, out)
            return
        spot = self._fresh_spot(now)
        if spot is None:
            p.last_detail = "no fresh NIFTY spot to rebase stop/target"
            return
        sig = p.signal
        bullish = sig.direction == Direction.BULLISH
        moved = (
            spot.price <= sig.stop_spot or spot.price >= sig.target_spot
            if bullish
            else spot.price >= sig.stop_spot or spot.price <= sig.target_spot
        )
        if moved:
            self._reject_pending(
                R.PRICE_MOVED,
                f"NIFTY {spot.price:.2f} is already through the signal stop {sig.stop_spot:.2f} "
                f"or target {sig.target_spot:.2f} at fill time",
                out,
            )
            return
        assert (
            quote is not None and check.fill_price is not None and check.entry_charges is not None
        )
        self._open_position(p, quote, spot, check, now, out)

    def _open_position(
        self,
        p: _PendingEntry,
        quote: OptionQuote,
        spot: SpotTick,
        check: Any,
        now: datetime,
        out: list[EngineEvent],
    ) -> None:
        sig, contract = p.signal, p.contract
        bullish = sig.direction == Direction.BULLISH
        risk_pts = abs(sig.spot_reference - sig.stop_spot)
        reward_pts = abs(sig.target_spot - sig.spot_reference)
        stop = round(spot.price - risk_pts if bullish else spot.price + risk_pts, 2)
        target = round(spot.price + reward_pts if bullish else spot.price - reward_pts, 2)
        trade_id = f"{self.trading_date:%Y%m%d}-{sig.strategy}-{sig.candle_open_time:%H%M}"
        premium = float(check.fill_price)
        qty = int(check.quantity)
        charges = float(check.entry_charges)
        try:
            self.account.open(trade_id, qty, premium, charges)
        except Exception as exc:  # noqa: BLE001 - InsufficientFundsError, PositionStateError...
            self._reject_pending(R.INSUFFICIENT_CASH, f"ledger refused the entry: {exc}", out)
            return
        pos = VirtualPosition(
            trade_id=trade_id,
            signal=sig,
            contract=contract,
            status=PositionStatus.OPEN,
            lots=int(check.lots),
            quantity=qty,
            entry_time=now,
            entry_spot=spot.price,
            entry_bid=quote.bid,
            entry_ask=quote.ask,
            entry_slippage=self.cfg.execution.entry_slippage_points,
            entry_premium=premium,
            entry_charges=charges,
            premium_invested=costs.round2(premium * qty),
            stop_spot=stop,
            target_spot=target,
            label=p.label,
        )
        self.pending = None
        self.position = pos
        self._exit_ctx = None
        self._exit_pending_announced = False
        self.account.mark(quote.bid)
        self._last_bid = (quote.bid, quote.received_at)
        entry_warnings = [str(w) for w in getattr(check, "warnings", ())]
        self._entry_extras[trade_id] = {
            "entry_ask_qty": quote.ask_qty,
            "entry_warnings": entry_warnings,
        }
        self._add_marker("ENTRY", now, spot.price, sig.strategy, trade_id,
                         f"{sig.strategy} {contract.option_type} @ {premium:.2f}", premium=premium,
                         direction=sig.direction.value, option_type=contract.option_type)  # fmt: skip
        # 1. checkpoint FIRST (position OPEN), 2. event journal, 3. snapshot, 4. emit
        self._save_checkpoint(out)
        panel = self._position_dict(pos, now)
        panel["remaining_cash"] = self.account.cash
        text = (
            f"VIRTUAL TRADE OPENED {sig.strategy} {contract.symbol} {pos.lots} lot(s) x "
            f"{contract.lot_size} @ {premium:.2f} (ASK {quote.ask:.2f} + {pos.entry_slippage:.2f} "
            f"slippage); NIFTY {spot.price:.2f} stop {stop:.2f} target {target:.2f} [{pos.label}]"
        )
        if entry_warnings:
            text += " | WARNING: " + "; ".join(entry_warnings)
        event = self._emit(
            "TRADE_OPENED",
            text,
            {"position": panel, "signal_id": sig.signal_id, "trade_id": trade_id},
            journal_payload={
                k: panel.get(k)
                for k in (
                    "trade_id",
                    "strategy",
                    "method",
                    "direction",
                    "option_type",
                    "symbol",
                    "strike",
                    "expiry",
                    "lots",
                    "quantity",
                    "entry_time",
                    "entry_spot",
                    "entry_bid",
                    "entry_ask",
                    "entry_premium",
                    "entry_charges",
                    "stop_spot",
                    "target_spot",
                    "premium_invested",
                    "remaining_cash",
                    "label",
                    "entry_reason",
                    "rule_id",
                    "reason_text",
                    "strategy_label",
                    "direction_label",
                    "entry_ask_qty",
                    "entry_warnings",
                )
            },
        )
        self._refresh_monitor_overlays()
        self._write_snapshot(force=True)
        out.append(event)

    # ================================================================== poll / monitoring

    @_locked
    def on_poll(self, poll: PollResult) -> list[EngineEvent]:
        out: list[EngineEvent] = []
        if self.state == EngineState.STOPPED:
            return out
        try:
            now = self._now()
            self._ingest(poll, now)
            self._retry_checkpoint(now, out)
            self._tick_state(now, out)
            self._session_clock(now, out)
            if self.pending is not None:
                self._try_fill(now, out)
            if self.position is not None and self._monitor_allowed:
                self._monitor_position(now, out)
            self._check_flat_risk(out)
        except Exception as exc:  # noqa: BLE001
            self._fail("on_poll", exc, out)
        self._write_snapshot()
        return out

    @_locked
    def on_tick_clock(self) -> list[EngineEvent]:
        out: list[EngineEvent] = []
        if self.state == EngineState.STOPPED:
            return out
        try:
            now = self._now()
            self._retry_checkpoint(now, out)
            self._tick_state(now, out)
            self._session_clock(now, out)
            if self.pending is not None:
                self._try_fill(now, out)
            if self.position is not None and self._monitor_allowed:
                self._monitor_position(now, out)
        except Exception as exc:  # noqa: BLE001
            self._fail("on_tick_clock", exc, out)
        self._write_snapshot()
        return out

    def _ingest(self, poll: PollResult, now: datetime) -> None:
        notes: list[str] = []
        tick = poll.spot
        if tick is not None:
            price = _finite(tick.price)
            if price is None or price <= 0 or tick.exchange_time.tzinfo is None:
                notes.append("invalid spot tick ignored")
            elif self._spot is not None and tick.exchange_time < self._spot.exchange_time:
                notes.append("out-of-order spot tick ignored (exchange time went backwards)")
            else:
                self._spot = tick
                self._day_high = max(filter(None, [self._day_high, tick.day_high, price]))
                self._day_low = min(filter(None, [self._day_low, tick.day_low, price]))
        for q in poll.quotes.values():
            skew = (q.received_at - now).total_seconds()
            if skew > FUTURE_QUOTE_TOLERANCE_SECONDS:
                # A future-stamped quote would poison the cache: it would sort as the "latest"
                # quote and hide every genuine one until the clock caught up.  Ignore it.
                notes.append(
                    f"quote {q.scrip_code} ignored: received {skew:.1f}s in the future (clock skew)"
                )
                if q.scrip_code not in self._future_quote_logged:
                    self._future_quote_logged.add(q.scrip_code)
                    self._log_error(
                        f"quote for scrip {q.scrip_code} stamped {skew:.1f}s in the future "
                        f"(received_at {q.received_at.isoformat()}, now {now.isoformat()}): "
                        "ignored, not cached"
                    )
                continue
            cur = self._quotes.get(q.scrip_code)
            if cur is None or q.received_at >= cur.received_at:
                self._quotes[q.scrip_code] = q
            if q.is_valid_book and (
                self._last_valid_quote_at is None or q.received_at > self._last_valid_quote_at
            ):
                self._last_valid_quote_at = q.received_at
        if len(self._quotes) > MAX_QUOTE_CACHE:
            for code, _ in sorted(self._quotes.items(), key=lambda kv: kv[1].received_at)[
                : len(self._quotes) - MAX_QUOTE_CACHE
            ]:
                if self.position is None or code != self.position.contract.scrip_code:
                    self._quotes.pop(code, None)
        notes.extend(str(e)[:160] for e in poll.errors[:3])
        if poll.rate_limited:
            notes.append("rate limited by provider")
        self._poll_notes = notes
        self._update_staleness(now)

    def _update_staleness(self, now: datetime) -> None:
        self._stale = self._fresh_spot(now) is None

    def _after_close(self, now: datetime) -> bool:
        """True once the session's market close (15:30) has been reached (or a later date)."""
        if self.trading_date is None:
            return False
        if now.date() != self.trading_date:
            return now.date() > self.trading_date
        return now.time() >= self.cfg.session.market_close

    def _entries_closed(self, now: datetime) -> bool:
        """True from the entry cutoff (14:30) onwards on the session date."""
        if self.trading_date is None:
            return False
        if now.date() != self.trading_date:
            return now.date() > self.trading_date
        return now.time() >= self.cfg.session.entry_cutoff

    def _running_reason(self, now: datetime) -> str:
        """Honest ``state_reason`` for a RUNNING engine (never 'market open' when it is not)."""
        s = self.cfg.session
        if self._after_close(now):
            return f"market closed ({s.market_close:%H:%M}); no new entries, no fills"
        spot_fresh = self._fresh_spot(now) is not None
        pos = self.position
        if pos is not None and self._monitor_allowed and not spot_fresh:
            if pos.status == PositionStatus.OPEN:
                return "stop/target not evaluated: no fresh spot"
        if not spot_fresh:
            ref = self._spot.received_at if self._spot is not None else self._started_at
            gap = (now - ref).total_seconds() if ref is not None else 0.0
            if gap >= DATA_OUTAGE_REASON_SECONDS:
                return f"DEGRADED: no fresh market data for {gap:.0f}s"
        if self._entries_closed(now):
            return f"NO_NEW_ENTRIES: entry cutoff {s.entry_cutoff:%H:%M} reached"
        return "market open"

    def _tick_state(self, now: datetime, out: list[EngineEvent]) -> None:
        if self.state == EngineState.READY and self.risk.market_open(now):
            self._set_state(EngineState.RUNNING, "market open", out)
        if self.state == EngineState.RUNNING:
            reason = self._running_reason(now)
            if reason != self.state_reason:
                self._set_state(EngineState.RUNNING, reason, out)  # same state: no event

    def _session_clock(self, now: datetime, out: list[EngineEvent]) -> None:
        s = self.cfg.session
        t = now.time()
        if t >= s.entry_cutoff and not self._cutoff_announced and self.risk.market_open(now):
            self._cutoff_announced = True
            out.append(
                self._emit(
                    "STATE_CHANGE",
                    f"entry cutoff {s.entry_cutoff:%H:%M} reached: no new entries "
                    "(NO_NEW_ENTRIES)",
                    {"cutoff": s.entry_cutoff.strftime("%H:%M"), "reason": "NO_NEW_ENTRIES"},
                )
            )
        if t >= s.market_close and not self._close_announced:
            self._close_announced = True
            pos = self.position
            if pos is not None:
                msg = (
                    f"MARKET CLOSED with {pos.status.value} position {pos.trade_id} UNRESOLVED: "
                    "NOT closed, no fill invented (no fill is booked after the close); preserved "
                    "in the checkpoint. The next session start will be RECOVERY_REQUIRED "
                    "(operator decision)."
                )
                out.append(self._emit("STATE_CHANGE", msg, {"trade_id": pos.trade_id,
                                                              "status": pos.status.value}))  # fmt: skip
                self._set_state(
                    EngineState.BLOCKED, "market closed with an unresolved position", out
                )
                self._save_checkpoint(out)
            else:
                out.append(self._emit("STATE_CHANGE", "market closed", {"reason": "market closed"}))

    # ------------------------------------------------------------------ bids / marks

    def _bid_quote(
        self, scrip: int, now: datetime, not_before: datetime | None = None
    ) -> tuple[OptionQuote | None, str]:
        q = self._quotes.get(scrip)
        if q is None:
            return None, "no quote received for the held contract"
        bid, ask = _finite(q.bid), _finite(q.ask)
        if bid is None or bid <= 0:
            return None, f"no valid bid (bid={q.bid})"
        if ask is not None and 0 < ask < bid:
            return None, f"crossed book (bid {bid} > ask {ask})"
        age = (now - q.received_at).total_seconds()
        if age > self.cfg.execution.max_quote_age_seconds:
            return (
                None,
                f"bid is stale ({age:.1f}s old > {self.cfg.execution.max_quote_age_seconds}s)",
            )
        if age < -FUTURE_QUOTE_TOLERANCE_SECONDS:
            return None, f"bid received {-age:.1f}s in the future (clock skew)"
        if not_before is not None and q.received_at < not_before:
            return None, (
                f"latest bid was received {q.received_at:%H:%M:%S.%f} before the trigger "
                f"{not_before:%H:%M:%S.%f}"
            )
        return q, ""

    def _monitor_position(self, now: datetime, out: list[EngineEvent]) -> None:
        pos = self.position
        if pos is None:
            return
        self._update_staleness(now)
        bid_q, _ = self._bid_quote(pos.contract.scrip_code, now)
        if bid_q is not None:
            self.account.mark(bid_q.bid)
            self._last_bid = (bid_q.bid, bid_q.received_at)
            self._check_daily_loss(out, bid_q.received_at)

        self._track_spot_gap(pos, now, out)
        if pos.status == PositionStatus.OPEN:
            ctx = self._exit_trigger(pos, now, bid_q)
            if ctx is not None:
                self._exit_ctx = ctx
                self._try_exit(now, out)
        else:
            self._try_exit(now, out)

    def _track_spot_gap(self, pos: VirtualPosition, now: datetime, out: list[EngineEvent]) -> None:
        """One RISK/WARNING per no-fresh-spot episode while a position is OPEN (silent resume).

        Without a fresh underlying tick the STOP/TARGET levels cannot be evaluated; only the
        clock-driven square-off and the daily-loss halt still work.  The operator must know.
        """
        if pos.status != PositionStatus.OPEN:
            self._spot_gap_announced = False  # an exit is already in progress
            return
        if self._fresh_spot(now) is not None:
            self._spot_gap_announced = False
            return
        if self._spot_gap_announced:
            return
        self._spot_gap_announced = True
        last = self._spot.received_at if self._spot is not None else None
        age = f"last spot tick {(now - last).total_seconds():.0f}s ago" if last else "no spot yet"
        out.append(
            self._emit(
                "RISK",
                f"stop/target not evaluated: no fresh spot ({age}); position {pos.trade_id} is "
                "not managed on the underlying until spot recovers (square-off at "
                f"{self.cfg.session.square_off:%H:%M} and the daily-loss halt still apply)",
                {
                    "severity": "WARNING",
                    "trade_id": pos.trade_id,
                    "condition": "NO_FRESH_SPOT",
                    "last_spot_received_at": _iso(last),
                },
            )
        )

    def _exit_trigger(
        self, pos: VirtualPosition, now: datetime, bid_q: OptionQuote | None
    ) -> _ExitContext | None:
        """First exit condition met: STOP > TARGET (underlying) > EOD (clock) > RISK."""
        spot = self._fresh_spot(now)
        bullish = pos.signal.direction == Direction.BULLISH
        if spot is not None:
            px = spot.price
            hit_stop = px <= pos.stop_spot if bullish else px >= pos.stop_spot
            hit_target = px >= pos.target_spot if bullish else px <= pos.target_spot
            if hit_stop:
                op = "<=" if bullish else ">="
                return _ExitContext(
                    ExitReason.STOP, spot.received_at,
                    f"NIFTY {px:.2f} {op} stop {pos.stop_spot:.2f}", px, now,
                    pos.stop_spot, pos.target_spot,
                )  # fmt: skip
            if hit_target:
                op = ">=" if bullish else "<="
                return _ExitContext(
                    ExitReason.TARGET, spot.received_at,
                    f"NIFTY {px:.2f} {op} target {pos.target_spot:.2f}", px, now,
                    pos.stop_spot, pos.target_spot,
                )  # fmt: skip
        if self.risk.square_off_due(now):
            so = datetime.combine(now.date(), self.cfg.session.square_off, IST)
            return _ExitContext(
                ExitReason.EOD, so,
                f"square-off {self.cfg.session.square_off:%H:%M} reached (clock {now:%H:%M:%S})",
                spot.price if spot else None, now, pos.stop_spot, pos.target_spot,
            )  # fmt: skip
        if self._risk_halted:
            return _ExitContext(
                ExitReason.RISK, self._risk_trigger_time or now,
                f"daily loss limit: {self._risk_reason}", spot.price if spot else None, now,
                pos.stop_spot, pos.target_spot,
            )  # fmt: skip
        return None

    def _check_daily_loss(self, out: list[EngineEvent], when: datetime) -> None:
        if self._risk_halted or self.account.session_open_equity <= 0:
            return
        if not self.risk.daily_loss_breached(self.account):
            return
        st = self.account.state
        limit = self.risk.daily_loss_limit_inr(st.session_open_equity)
        self._risk_halted = True
        self._risk_trigger_time = when
        self._risk_reason = (
            f"equity {st.equity:.2f} vs session open {st.session_open_equity:.2f}: "
            f"loss {st.session_open_equity - st.equity:.2f} >= limit {limit:.2f} "
            f"({self.cfg.account.max_daily_loss_pct}%)"
        )
        out.append(
            self._emit(
                "RISK",
                f"DAILY LOSS LIMIT BREACHED: {self._risk_reason}; entries halted"
                + ("; open position will be closed" if self.position else ""),
                {
                    "equity": st.equity,
                    "session_open_equity": st.session_open_equity,
                    "limit": limit,
                },
            )
        )
        if self.state in _ENTRY_STATES:
            self._set_state(EngineState.HALTED_RISK, self._risk_reason, out)
        self._save_checkpoint(out)

    def _check_flat_risk(self, out: list[EngineEvent]) -> None:
        if self.position is None and not self._risk_halted and self.trading_date is not None:
            if self.state in _ENTRY_STATES:
                self._check_daily_loss(out, self._now())

    # ------------------------------------------------------------------ exit

    def _try_exit(self, now: datetime, out: list[EngineEvent]) -> None:
        pos, ctx = self.position, self._exit_ctx
        if pos is None or ctx is None:
            return
        if self._after_close(now):
            # A post-close book is not a tradable price: never book a fill after 15:30.  The
            # position stays EXIT_PENDING and persists in the checkpoint (next session:
            # RECOVERY_REQUIRED, an operator decision).
            q = None
            why = (
                f"market closed ({self.cfg.session.market_close:%H:%M}): no fill is booked after "
                "the close; position kept EXIT_PENDING in the checkpoint"
            )
        else:
            q, why = self._bid_quote(pos.contract.scrip_code, now, not_before=ctx.trigger_time)
        if q is None:
            self._exit_pending_detail = why
            if pos.status == PositionStatus.OPEN:
                pos.status = PositionStatus.EXIT_PENDING
                pos.exit_pending_reason = ctx.reason
                pos.exit_pending_since = now
            if not self._exit_pending_announced:
                self._exit_pending_announced = True
                self._save_checkpoint(out)
                out.append(
                    self._emit(
                        "EXIT_PENDING",
                        f"EXIT PENDING ({ctx.reason.value}: {ctx.condition}): {why}; position "
                        "kept, entries blocked, retrying every poll; no fill is invented",
                        {
                            "trade_id": pos.trade_id,
                            "reason": ctx.reason.value,
                            "condition": ctx.condition,
                            "why_pending": why,
                            "trigger_time": ctx.trigger_time.isoformat(),
                        },
                    )
                )
            return
        self._finalize_exit(pos, ctx, q, now, out)

    def _finalize_exit(
        self,
        pos: VirtualPosition,
        ctx: _ExitContext,
        q: OptionQuote,
        now: datetime,
        out: list[EngineEvent],
    ) -> None:
        if pos is not self.position or pos.status == PositionStatus.CLOSED:
            raise AssertionError("position already closed; closing twice is impossible")
        if pos.trade_id in self.account.closed_trade_ids or pos.trade_id in self.closed_trade_ids:
            raise AssertionError(f"trade {pos.trade_id} already closed")
        ex = self.cfg.execution
        exit_premium = costs.exit_fill_price(q.bid, ex.exit_slippage_points)
        leg = costs.charges("SELL", now.date(), exit_premium, pos.quantity)
        spot = self._spot
        spot_px = spot.price if spot is not None else (ctx.spot_at_trigger or pos.entry_spot)
        spot_fresh = self._fresh_spot(now) is not None
        spot_note = "live" if spot_fresh else "last_known_or_trigger"
        spot_age = (now - spot.received_at).total_seconds() if spot is not None else None
        exit_warnings: list[str] = []
        if q.bid_qty < pos.quantity:
            exit_warnings.append(
                f"TOP_OF_BOOK_QTY: bid_qty {q.bid_qty} < exit qty {pos.quantity} "
                "(whole quantity filled at the best bid; depth not modelled)"
            )
        if not spot_fresh:
            exit_warnings.append(
                "STALE_EXIT_SPOT: the exit_spot recorded here was not a fresh tick "
                f"({'no spot ever received' if spot_age is None else f'{spot_age:.0f}s old'})"
            )

        backup = copy.deepcopy(self.account)
        try:
            res = self.account.close(pos.trade_id, exit_premium, leg.total, quantity=pos.quantity)
            total_charges = costs.round2(res.entry_charges + res.exit_charges)
            detail = {
                "condition": ctx.condition,
                "reason": ctx.reason.value,
                "spot_at_trigger": ctx.spot_at_trigger,
                "exit_spot": spot_px,
                "exit_spot_basis": spot_note,
                "exit_spot_age_seconds": None if spot_age is None else round(spot_age, 2),
                "exit_spot_stale": not spot_fresh,
                "exit_bid_qty": q.bid_qty,
                "exit_warnings": exit_warnings,
                "entry_ask_qty": self._entry_extras.get(pos.trade_id, {}).get("entry_ask_qty"),
                "entry_warnings": self._entry_extras.get(pos.trade_id, {}).get("entry_warnings", []),
                "trigger_time": ctx.trigger_time.isoformat(),
                "detected_at": ctx.detected_at.isoformat(),
                "original_stop_spot": ctx.stop_spot,
                "original_target_spot": ctx.target_spot,
                "signal_stop_spot": pos.signal.stop_spot,
                "signal_target_spot": pos.signal.target_spot,
                "option_exit_bid": q.bid,
                "option_exit_ask": q.ask,
                "bid_received_at": q.received_at.isoformat(),
                "bid_age_seconds": round((now - q.received_at).total_seconds(), 3),
                "exit_premium": exit_premium,
                "exit_slippage": ex.exit_slippage_points,
                "exit_charges": {
                    "brokerage": leg.brokerage,
                    "stt": leg.stt,
                    "exchange": leg.exchange_charges,
                    "gst": leg.gst,
                    "sebi": leg.sebi_charges,
                    "stamp": leg.stamp_duty,
                    "total": leg.total,
                },  # fmt: skip
                "was_exit_pending": pos.status == PositionStatus.EXIT_PENDING,
                "pending_since": _iso(pos.exit_pending_since),
                "pending_seconds": (now - pos.exit_pending_since).total_seconds()
                if pos.exit_pending_since
                else 0.0,
                "label": pos.label,
            }
            trade = TradeRecord(
                trade_id=pos.trade_id,
                session_date=self.trading_date,  # type: ignore[arg-type]
                session_id=self.session_id,
                label=pos.label,
                strategy=pos.signal.strategy,
                method=pos.signal.method,
                direction=pos.signal.direction.value,
                option_type=pos.contract.option_type,
                symbol=pos.contract.symbol,
                expiry=pos.contract.expiry,
                strike=pos.contract.strike,
                scrip_code=pos.contract.scrip_code,
                lot_size=pos.contract.lot_size,
                lots=pos.lots,
                quantity=pos.quantity,
                signal_time=pos.signal.decision_time,
                entry_time=pos.entry_time,
                exit_time=now,
                entry_spot=pos.entry_spot,
                exit_spot=spot_px,
                stop_spot=pos.stop_spot,
                target_spot=pos.target_spot,
                entry_bid=pos.entry_bid,
                entry_ask=pos.entry_ask,
                entry_premium=pos.entry_premium,
                exit_bid=q.bid,
                exit_premium=exit_premium,
                entry_slippage=pos.entry_slippage,
                exit_slippage=ex.exit_slippage_points,
                gross_pnl=res.gross_pnl,
                entry_charges=res.entry_charges,
                exit_charges=res.exit_charges,
                total_charges=total_charges,
                net_pnl=res.net_pnl,
                exit_reason=ctx.reason.value,
                entry_reason=pos.signal.reason,
                entry_conditions_json=_json_dumps(pos.signal.conditions),
                levels_json=_json_dumps(pos.signal.levels),
                trigger_candle_json=_json_dumps(pos.signal.trigger_candle),
                exit_detail_json=_json_dumps(detail),
                balance_after=res.balance_after,
            )
            appended = self.trades_journal.append_trade(trade)
        except BaseException:
            self.account = backup  # nothing was recorded: keep the position exactly as it was
            raise
        if not appended:
            self._log_error(f"trade {pos.trade_id} was already present in trades.csv (idempotent)")

        # ---- the trade is now durable in the ledger: update memory, then checkpoint
        pos.status = PositionStatus.CLOSED
        self.position = None
        self._exit_ctx = None
        self._exit_pending_detail = ""
        self._exit_pending_announced = False
        self.closed_trade_ids.append(pos.trade_id)
        mon = self.monitors.get(pos.signal.strategy)
        if mon is not None:
            mon.trades_today += 1
            mon.pnl_today = round(mon.pnl_today + res.net_pnl, 2)
        row = trade.to_row()
        self._last_closed_trade = row
        self._add_marker("EXIT", now, spot_px, pos.signal.strategy, pos.trade_id,
                         f"{ctx.reason.value} @ {exit_premium:.2f}", premium=exit_premium,
                         exit_reason=ctx.reason.value, direction=pos.signal.direction.value,
                         option_type=pos.contract.option_type)  # fmt: skip
        self._entry_extras.pop(pos.trade_id, None)
        self._check_daily_loss(out, now)
        self._save_checkpoint(out)
        text = (
            f"VIRTUAL TRADE CLOSED {pos.signal.strategy} {ctx.reason.value} ({ctx.condition}): "
            f"entry {pos.entry_premium:.2f} exit {exit_premium:.2f} (BID {q.bid:.2f} - "
            f"{ex.exit_slippage_points} slippage); gross {res.gross_pnl:+.2f} charges "
            f"{total_charges:.2f} NET {res.net_pnl:+.2f}; balance {res.balance_after:.2f} [{pos.label}]"
        )
        if exit_warnings:
            text += " | WARNING: " + "; ".join(exit_warnings)
        event = self._emit(
            "TRADE_CLOSED",
            text,
            {"trade": row, "trade_id": pos.trade_id, "exit_warnings": exit_warnings},
            journal_payload={
                "exit_warnings": exit_warnings,
                "exit_bid_qty": q.bid_qty,
                **{
                    k: row[k]
                    for k in (
                        "trade_id",
                        "strategy",
                        "method",
                        "direction",
                        "symbol",
                        "lots",
                        "quantity",
                        "entry_time",
                        "exit_time",
                        "entry_spot",
                        "exit_spot",
                        "entry_premium",
                        "exit_premium",
                        "gross_pnl",
                        "entry_charges",
                        "exit_charges",
                        "total_charges",
                        "net_pnl",
                        "exit_reason",
                        "balance_after",
                        "label",
                    )
                },
            },
        )
        self._refresh_monitor_overlays()
        self._write_snapshot(force=True)
        out.append(event)

    # ================================================================== persistence

    def _checkpoint_obj(self) -> Checkpoint:
        flags = {
            name: {"trades_today": m.trades_today, "pnl_today": m.pnl_today}
            for name, m in self.monitors.items()
        }
        try:
            seq = int(self.events.last_seq)
        except Exception:  # noqa: BLE001
            seq = 0
        return Checkpoint(
            session_id=self.session_id,
            session_date=(self.trading_date.isoformat() if self.trading_date else ""),
            run_mode=self.mode.value,
            label=self.label,
            account=account_to_dict(self.account.state),
            position=position_to_dict(self.position) if self.position else None,
            executed_signal_ids=list(self.executed_signal_ids),
            strategy_daily_flags=flags,
            engine_state=self.state.value,
            risk_flags={"halted": self._risk_halted, "reason": self._risk_reason},
            last_candle_open=_iso(self._last_candle_open),
            last_event_seq=seq,
            closed_trade_ids_today=list(self.closed_trade_ids),
        )

    def _save_checkpoint(self, out: list[EngineEvent] | None = None) -> bool:
        if not self._checkpoint_writes or self.trading_date is None:
            return False
        try:
            save_checkpoint(self.checkpoint_path, self._checkpoint_obj(), now=self._now())
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_error(f"checkpoint write failed: {exc}", exc)
            if out is not None:
                if self._ckpt_prior is None and self.state in (
                    EngineState.READY,
                    EngineState.RUNNING,
                    EngineState.HALTED_RISK,
                ):
                    self._ckpt_prior = (self.state, self.state_reason)  # retried on the next tick
                self._fail("save_checkpoint", exc, out)
            return False

    def _position_inconsistency(self) -> str:
        """Empty string when memory (position, account, ledger ids) agrees; else the problem."""
        pos, acct = self.position, self.account
        if (pos is None) == acct.has_open_position:
            return "position and account open-leg disagree"
        if pos is not None:
            leg = acct.open_leg
            if leg is None or leg.trade_id != pos.trade_id or leg.quantity != pos.quantity:
                return f"account open leg does not match position {pos.trade_id}"
            if pos.status not in (PositionStatus.OPEN, PositionStatus.EXIT_PENDING):
                return f"position {pos.trade_id} has status {pos.status.value}"
            if pos.trade_id in self.closed_trade_ids or pos.trade_id in acct.closed_trade_ids:
                return f"position {pos.trade_id} is already in the closed-trade ids"
        try:
            VirtualAccount.restore_from_checkpoint(acct.to_checkpoint())
        except Exception as exc:  # noqa: BLE001 - LedgerError / CheckpointError
            return f"account does not reconcile: {exc}"
        return ""

    def _retry_checkpoint(self, now: datetime, out: list[EngineEvent]) -> None:
        """A failed checkpoint write blocks entries only until a write succeeds again.

        BLOCKED is left (back to the prior state, with a STATE_CHANGE event) only when the
        retried write succeeds AND position / account / ledger ids are consistent.
        """
        prior = self._ckpt_prior
        if prior is None:
            return
        if self.state != EngineState.BLOCKED or not self.state_reason.startswith(
            "internal error in save_checkpoint"
        ):
            self._ckpt_prior = None  # something else took over; stay as that decided
            return
        problem = self._position_inconsistency()
        if problem:
            key = ("checkpoint_retry", "inconsistent", problem[:100])
            last = self._error_seen.get(key)
            if last is None or (now - last).total_seconds() >= ERROR_EVENT_THROTTLE_SECONDS:
                self._error_seen[key] = now
                self._log_error(f"checkpoint retry skipped, state inconsistent: {problem}")
            return  # stay BLOCKED
        blocked = (self.state, self.state_reason)
        self.state, self.state_reason = prior
        try:
            save_checkpoint(self.checkpoint_path, self._checkpoint_obj(), now=now)
        except Exception as exc:  # noqa: BLE001
            self.state, self.state_reason = blocked
            key = ("checkpoint_retry", type(exc).__name__, str(exc)[:100])
            last = self._error_seen.get(key)
            if last is None or (now - last).total_seconds() >= ERROR_EVENT_THROTTLE_SECONDS:
                self._error_seen[key] = now
                self._log_error(f"checkpoint retry failed: {exc}", exc)
            return
        self._ckpt_prior = None
        self.state, self.state_reason = blocked  # let _set_state emit the transition event
        self._set_state(prior[0], f"{prior[1]} (checkpoint writes recovered)", out)

    def _write_snapshot(self, *, force: bool = False) -> None:
        writer = self.snapshot_writer
        if writer is None:
            return
        try:
            data = self.snapshot()
            saved = getattr(writer, "min_interval_seconds", None)
            if force and saved:
                writer.min_interval_seconds = 0.0
            try:
                result = writer.write(
                    data, now=ist_now() if self.mode == RunMode.REPLAY else self._now()
                )
            finally:
                if force and saved:
                    writer.min_interval_seconds = saved
            if not getattr(result, "ok", True):
                self._log_error(f"snapshot write failed: {getattr(result, 'error', '')}")
        except Exception as exc:  # noqa: BLE001 - the UI file must never stop trading
            self._log_error(f"snapshot failed: {type(exc).__name__}: {exc}", exc)

    # ================================================================== markers / UI

    def _add_marker(
        self,
        kind: str,
        t: datetime,
        price: float,
        strategy: str,
        trade_id: str | None,
        text: str,
        **extra: Any,
    ) -> None:
        self._markers.append(
            {
                "kind": kind,
                "t": t.isoformat(),
                "price": price,
                "strategy": strategy,
                "trade_id": trade_id,
                "text": text,
                **extra,
            }
        )
        del self._markers[:-MAX_MARKERS]

    @_locked
    def set_forming_candle(self, candle: CompletedCandle | None) -> None:
        """Non-authoritative forming candle for display only (never evaluated)."""
        self._forming = candle

    @_locked
    def set_connection_info(self, info: dict[str, Any]) -> None:
        self._conn_info = dict(info or {})

    def _market_status(self, now: datetime) -> str:
        s = self.cfg.session
        if not self._calendar.is_entry_session_supported(now.date()):
            return "CLOSED"
        if now.time() < s.market_open:
            return "PRE_OPEN"
        return "OPEN" if now.time() < s.market_close else "CLOSED"

    def _est_exit_charges(
        self, pos: VirtualPosition, bid: float, now: datetime
    ) -> tuple[float, float]:
        """(exit fill price, exit charges) if the position were sold now at ``bid``."""
        fill = costs.exit_fill_price(bid, self.cfg.execution.exit_slippage_points)
        return fill, costs.charges("SELL", now.date(), fill, pos.quantity).total

    def _position_dict(self, pos: VirtualPosition, now: datetime) -> dict[str, Any]:
        spot = self._spot.price if self._spot is not None else None
        bullish = pos.signal.direction == Direction.BULLISH
        dist_stop = dist_target = None
        if spot is not None:
            dist_stop = round(spot - pos.stop_spot if bullish else pos.stop_spot - spot, 2)
            dist_target = round(pos.target_spot - spot if bullish else spot - pos.target_spot, 2)
        bid = self._last_bid[0] if self._last_bid else None
        bid_age = (now - self._last_bid[1]).total_seconds() if self._last_bid else None
        gross = net = pct = None
        if bid is not None:
            gross = costs.round2((bid - pos.entry_premium) * pos.quantity)
            fill, exit_chg = self._est_exit_charges(pos, bid, now)
            net = costs.round2(
                (fill - pos.entry_premium) * pos.quantity - pos.entry_charges - exit_chg
            )
            pct = round(net / pos.premium_invested * 100, 2) if pos.premium_invested else None
        max_age = self.cfg.execution.max_quote_age_seconds
        extras = self._entry_extras.get(pos.trade_id, {})
        ctx = self._exit_ctx
        return {
            "trade_id": pos.trade_id,
            "signal_id": pos.signal.signal_id,
            "strategy": pos.signal.strategy,
            "strategy_label": labels.strategy_label(pos.signal.strategy, pos.signal.method),
            "method": pos.signal.method,
            "direction": pos.signal.direction.value,
            "direction_label": labels.direction_label(
                pos.signal.direction.value, pos.contract.option_type
            ),
            "option_type": pos.contract.option_type,
            "symbol": pos.contract.symbol,
            "scrip_code": pos.contract.scrip_code,
            "strike": pos.contract.strike,
            "expiry": pos.contract.expiry.isoformat(),
            "lots": pos.lots,
            "quantity": pos.quantity,
            "lot_size": pos.contract.lot_size,
            "entry_time": pos.entry_time.isoformat(),
            "entry_spot": pos.entry_spot,
            "entry_bid": pos.entry_bid,
            "entry_ask": pos.entry_ask,
            "entry_slippage": pos.entry_slippage,
            "entry_premium": pos.entry_premium,
            "entry_charges": pos.entry_charges,
            "premium_invested": pos.premium_invested,
            "current_bid": bid,
            "current_bid_age_seconds": None if bid_age is None else round(bid_age, 2),
            "bid_is_stale": bid_age is None or bid_age > max_age,
            "stop_spot": pos.stop_spot,
            "target_spot": pos.target_spot,
            "dist_to_stop": dist_stop,
            "dist_to_target": dist_target,
            "gross_pnl": gross,
            "net_pnl_estimate": net,
            "net_pnl_estimate_basis": "exit at BID - exit slippage, incl. entry+exit charges",
            "pnl_pct": pct,
            "holding_seconds": max(0.0, (now - pos.entry_time).total_seconds()),
            "status": pos.status.value,
            "entry_reason": pos.signal.reason,
            "rule_id": pos.signal.reason,
            "reason_text": self._reason_text(pos.signal.reason),
            "entry_ask_qty": extras.get("entry_ask_qty"),
            "entry_warnings": list(extras.get("entry_warnings", [])),
            "label": pos.label,
            "exit_pending_reason": pos.exit_pending_reason.value
            if pos.exit_pending_reason
            else None,
            "exit_pending_reason_text": labels.exit_sentence(pos.exit_pending_reason.value)
            if pos.exit_pending_reason
            else None,
            "exit_pending_condition": ctx.condition
            if ctx is not None and pos.status == PositionStatus.EXIT_PENDING
            else None,
            "exit_pending_since": _iso(pos.exit_pending_since),
            "exit_pending_detail": self._exit_pending_detail or None,
            "remaining_cash": self.account.cash,
        }

    def _quote_block(self, now: datetime) -> dict[str, Any]:
        out: dict[str, Any] = {"CE": None, "PE": None}
        if self.trading_date is None:
            return out
        basis = self._spot.price if self._spot else self._previous_close()
        pair: tuple[ContractSpec, ContractSpec] | None = None
        if basis:
            try:
                pair = self.resolver.candidate_pair(basis, self.trading_date, now)
            except ContractSelectionError:
                pair = None
        contracts = {"CE": pair[0], "PE": pair[1]} if pair else {}
        if self.position is not None:
            contracts[self.position.contract.option_type] = self.position.contract
        for side, contract in contracts.items():
            q = self._quotes.get(contract.scrip_code)
            out[side] = {
                "symbol": contract.symbol,
                "scrip_code": contract.scrip_code,
                "strike": contract.strike,
                "expiry": contract.expiry.isoformat(),
                "bid": q.bid if q else None,
                "ask": q.ask if q else None,
                "bid_qty": q.bid_qty if q else None,
                "ask_qty": q.ask_qty if q else None,
                "server_time": _iso(q.server_time) if q else None,
                "received_at": _iso(q.received_at) if q else None,
            }
        return out

    def _previous_close(self) -> float | None:
        prev = getattr(self.warmup, "previous_session", None)
        return _finite(getattr(prev, "close", None))

    def _strategy_rows(self) -> list[dict[str, Any]]:
        rows = []
        for name in self.names:
            m = self.monitors[name]
            state, note = self._overlay_state(name)
            rows.append(
                {
                    "name": m.name,
                    "label": m.label,
                    "method": self._method_of(name),
                    "tier": self._tier_text(m.tier),
                    "tier_text": labels.tier_label(
                        m.tier.value,
                        None if m.tier == ExecutionTier.DISABLED else self._auth_label(name),
                        replay=self.mode == RunMode.REPLAY and m.tier != ExecutionTier.DISABLED,
                    ),
                    "strategy_label": labels.strategy_label(name, self._method_of(name)),
                    "auth_label": self._auth_label(name),
                    "state": state.value,
                    "last_evaluated_candle": _iso(m.last_evaluated_candle),
                    "levels": m.levels,
                    "conditions_satisfied": m.conditions_satisfied,
                    "conditions_failed": m.conditions_failed,
                    "last_signal": m.last_signal,
                    "last_rejection": m.last_rejection,
                    "trades_today": m.trades_today,
                    "pnl_today": m.pnl_today,
                    "note": note,
                }
            )
        return rows

    @_locked
    def snapshot(self) -> dict[str, Any]:
        """The ``live_state.json`` document (schema_version 1, see the architecture doc)."""
        now = self._now()
        s = self.cfg.session
        acct = self.account.state
        limit = (
            self.risk.daily_loss_limit_inr(acct.session_open_equity)
            if acct.session_open_equity
            else 0.0
        )
        used = max(0.0, acct.session_open_equity - acct.equity) if acct.session_open_equity else 0.0
        spot = self._spot
        spot_age = (now - spot.received_at).total_seconds() if spot else None
        self._update_staleness(now)
        # Computed from THIS engine's own last-poll data, so it can never lag a poll behind
        # ``written_at``; the app-supplied provider status can only make it worse.
        replay = self.mode == RunMode.REPLAY
        own_status = (
            "CONNECTED"
            if spot is not None and not self._stale
            else ("DEGRADED" if spot else "DISCONNECTED")
        )
        rank = {"CONNECTED": 0, "DEGRADED": 1, "DISCONNECTED": 2}
        provider_status = self._conn_info.get("status")
        status = own_status
        if provider_status in rank and rank[provider_status] > rank[own_status]:
            status = str(provider_status)
        if replay:
            status = "SIMULATED"
        notes = [str(n) for n in self._conn_info.get("notes", [])] + self._poll_notes
        gap = self._gap_reason()
        if gap:
            notes.append(gap)
        try:
            levels = dict(self.levels.display_levels()) if self.levels is not None else {}
        except Exception as exc:  # noqa: BLE001
            levels = {"notes": f"levels unavailable: {type(exc).__name__}"}
        forming = self._forming
        # A replay runs on a simulated clock: ``written_at`` is the WALL clock (so readers can
        # judge whether the writer is alive) and ``sim_time`` carries the simulated time.
        written_at = ist_now() if replay else now
        return {
            "schema_version": 1,
            "written_at": written_at.isoformat(),
            "sim_time": now.isoformat() if replay else None,
            "heartbeat_age_note": "reader computes age from written_at",
            "session": {
                "session_id": self.session_id,
                "date": self.trading_date.isoformat() if self.trading_date else None,
                "mode": self.mode.value,
                "label": self.label,
                "pid": os.getpid(),
                "started_at": _iso(self._started_at),
                "engine_state": self.state.value,
                "state_reason": self.state_reason,
                "market_status": self._market_status(now),
                "provider": _compact(self.provider_info),
            },
            "connection": {
                "broker": "SIMULATED (no broker)" if replay else "5paisa",
                "status": status,
                "last_valid_quote_at": _iso(self._last_valid_quote_at)
                or self._conn_info.get("last_valid_quote_at"),
                "data_age_seconds": None if spot_age is None else round(spot_age, 2),
                "stale": bool(self._stale),
                "candle_source": self._conn_info.get("candle_source") or self._candle_source_seen,
                "candle_trusted": bool(self._candle_trusted and not gap),
                "notes": notes[:12],
            },
            "spot": {
                "price": spot.price if spot else None,
                "exchange_time": _iso(spot.exchange_time) if spot else None,
                "received_at": _iso(spot.received_at) if spot else None,
                "day_high": self._day_high,
                "day_low": self._day_low,
                "prev_close": (
                    spot.prev_close if spot and spot.prev_close else self._previous_close()
                ),
            },
            "current_candle": None
            if forming is None
            else {
                "open_time": forming.open_time.isoformat(),
                "o": forming.open,
                "h": forming.high,
                "l": forming.low,
                "c": forming.close,
                "source": forming.source.value,
                "authoritative": False,
            },  # fmt: skip
            "quotes": self._quote_block(now),
            "levels": _compact(levels),
            "candles": [
                {
                    "t": c.open_time.isoformat(),
                    "o": c.open,
                    "h": c.high,
                    "l": c.low,
                    "c": c.close,
                    "source": c.source.value,
                }
                for c in self._candles[-MAX_DISPLAY_CANDLES:]
            ],  # fmt: skip
            "markers": list(self._markers),
            "account": {
                "starting_capital": acct.starting_capital,
                "cash": acct.cash,
                "equity": acct.equity,
                "realized_pnl": acct.realized_pnl,
                "session_realized_pnl": acct.session_realized_pnl,
                "unrealized_pnl": acct.unrealized_pnl,
                "invested": acct.invested,
                "session_open_equity": acct.session_open_equity,
                "trades_today": acct.trades_today,
                "wins_today": acct.wins_today,
                "losses_today": acct.losses_today,
                "daily_loss_limit": limit,
                "daily_loss_used": used,
            },
            "risk": {
                "state": "HALTED" if self._risk_halted else "OK",
                "reason": self._risk_reason,
                "max_daily_loss_inr": limit,
                "entry_cutoff": s.entry_cutoff.strftime("%H:%M"),
                "square_off": s.square_off.strftime("%H:%M"),
                "cutoff_passed": now.time() >= s.entry_cutoff,
            },
            "strategies": self._strategy_rows(),
            "position": self._position_dict(self.position, now) if self.position else None,
            "pending_entry": None
            if self.pending is None
            else {
                "signal_id": self.pending.signal.signal_id,
                "strategy": self.pending.signal.strategy,
                "symbol": self.pending.contract.symbol,
                "since": self.pending.created_at.isoformat(),
                "detail": self.pending.last_detail,
            },
            "recovery": self._recovery_info,
            "last_event": self._last_event,
            "recent_events": list(self._recent),
            "last_closed_trade": self._last_closed_trade,
        }

    # ================================================================== watch / shutdown

    @_locked
    def watch_scrips(self) -> list[int]:
        """Held contract FIRST, then the pending-entry contract, then the CE/PE candidate pair."""
        codes: list[int] = []

        def add(code: int) -> None:
            if code not in codes and len(codes) < MAX_WATCH_SCRIPS:
                codes.append(code)

        if self.position is not None:
            add(self.position.contract.scrip_code)
        if self.pending is not None:
            add(self.pending.contract.scrip_code)
        if self.trading_date is not None:
            basis = self._spot.price if self._spot else self._previous_close()
            if basis:
                try:
                    ce, pe = self.resolver.candidate_pair(basis, self.trading_date, self._now())
                    add(ce.scrip_code)
                    add(pe.scrip_code)
                except ContractSelectionError:
                    pass
        return codes

    @_locked
    def shutdown(self, reason: str) -> ShutdownResult:
        """Write the final checkpoint.  An open/exit-pending position is PRESERVED, never faked."""
        out: list[EngineEvent] = []
        unresolved: list[str] = []
        written = False
        try:
            if self.pending is not None:
                p = self.pending
                self.pending = None
                unresolved.append(f"pending entry {p.signal.signal_id} cancelled (never filled)")
                self.monitors[p.signal.strategy].last_rejection = "shutdown before fill"
            pos = self.position
            if pos is not None:
                unresolved.append(
                    f"position {pos.trade_id} {pos.contract.symbol} is {pos.status.value}: NOT closed; "
                    "preserved in the checkpoint"
                )
            if self.state != EngineState.RECOVERY_REQUIRED:
                self.state, self.state_reason = EngineState.SHUTTING_DOWN, reason
            written = self._save_checkpoint()
            out.append(
                self._emit(
                    "SHUTDOWN",
                    f"engine shutdown ({reason}); unresolved: {unresolved or 'none'}",
                    {"reason": reason, "unresolved": unresolved, "checkpoint_written": written},
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._fail("shutdown", exc, out)
        if self.state != EngineState.RECOVERY_REQUIRED:
            self.state, self.state_reason = EngineState.STOPPED, reason
        self._write_snapshot(force=True)
        return ShutdownResult(
            ok=written or not self._checkpoint_writes,
            reason=reason,
            final_state=self.state,
            position_preserved=self.position is not None,
            unresolved=unresolved,
            checkpoint_written=written,
        )
