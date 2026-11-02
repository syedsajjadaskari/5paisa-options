"""Atomic restart checkpoint and the fail-closed restart recovery decision.

The checkpoint is a small JSON file (account, open virtual position, executed signal ids,
risk flags).  ``recovery_decision`` decides what a freshly started process may do with it.

Principles:

* Uncertainty never flattens, auto-resumes or invents a fill: it yields ``RECOVERY_REQUIRED``
  (no new entries until an operator resolves it).
* ``trades.csv`` is the authoritative ledger.  The engine writes the CSV row *before* the
  checkpoint, so a checkpoint may legitimately lag the ledger by one closed trade (crash between
  close and checkpoint).  That case is reconciled: the position is treated as CLOSED, no
  duplicate row is written, and the account is advanced from the ledger row.
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

from trading_platform.live.journal import atomic_write_json
from trading_platform.live.models import (
    IST,
    SCHEMA_VERSION,
    AccountState,
    ContractSpec,
    Direction,
    ExitReason,
    PositionStatus,
    SignalRecord,
    VirtualPosition,
    ist_now,
)

CHECKPOINT_SCHEMA_VERSION = SCHEMA_VERSION
DEFAULT_TOLERANCE_INR = 0.01
_VALID_POSITION_STATUSES = {PositionStatus.OPEN.value, PositionStatus.EXIT_PENDING.value}


class CheckpointError(ValueError):
    """A checkpoint (or part of one) is structurally invalid."""


# --------------------------------------------------------------------------- (de)serialisation


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    return value


def account_to_dict(account: AccountState) -> dict[str, Any]:
    return _jsonable(account)


def account_from_dict(data: Mapping[str, Any]) -> AccountState:
    names = AccountState.__dataclass_fields__.keys()
    try:
        return AccountState(**{k: data[k] for k in names if k in data})
    except TypeError as exc:
        raise CheckpointError(f"invalid account dict: {exc}") from exc


def position_to_dict(position: VirtualPosition) -> dict[str, Any]:
    """JSON-safe dict of a :class:`VirtualPosition` (incl. status and exit-pending fields)."""
    return _jsonable(position)


def _dt(value: Any, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise CheckpointError(f"{name}: invalid datetime {value!r}") from exc
    if parsed.tzinfo is None:
        raise CheckpointError(f"{name}: datetime must be timezone-aware ({value!r})")
    return parsed


def _day(value: Any, name: str) -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise CheckpointError(f"{name}: invalid date {value!r}") from exc


def position_from_dict(data: Mapping[str, Any]) -> VirtualPosition:
    """Rebuild a :class:`VirtualPosition`; raises :class:`CheckpointError` if it cannot be."""
    try:
        sig = data["signal"]
        con = data["contract"]
        signal = SignalRecord(
            signal_id=str(sig["signal_id"]),
            strategy=str(sig["strategy"]),
            method=str(sig["method"]),
            direction=Direction(sig["direction"]),
            candle_open_time=_dt(sig["candle_open_time"], "signal.candle_open_time"),
            decision_time=_dt(sig["decision_time"], "signal.decision_time"),
            spot_reference=float(sig["spot_reference"]),
            stop_spot=float(sig["stop_spot"]),
            target_spot=float(sig["target_spot"]),
            reason=str(sig.get("reason", "")),
            conditions=dict(sig.get("conditions") or {}),
            levels=dict(sig.get("levels") or {}),
            trigger_candle=dict(sig.get("trigger_candle") or {}),
        )
        contract = ContractSpec(
            scrip_code=int(con["scrip_code"]),
            symbol=str(con["symbol"]),
            expiry=_day(con["expiry"], "contract.expiry"),
            strike=float(con["strike"]),
            option_type=str(con["option_type"]),
            lot_size=int(con["lot_size"]),
        )
        pending_reason = data.get("exit_pending_reason")
        pending_since = data.get("exit_pending_since")
        return VirtualPosition(
            trade_id=str(data["trade_id"]),
            signal=signal,
            contract=contract,
            status=PositionStatus(data["status"]),
            lots=int(data["lots"]),
            quantity=int(data["quantity"]),
            entry_time=_dt(data["entry_time"], "entry_time"),
            entry_spot=float(data["entry_spot"]),
            entry_bid=float(data["entry_bid"]),
            entry_ask=float(data["entry_ask"]),
            entry_slippage=float(data["entry_slippage"]),
            entry_premium=float(data["entry_premium"]),
            entry_charges=float(data["entry_charges"]),
            premium_invested=float(data["premium_invested"]),
            stop_spot=float(data["stop_spot"]),
            target_spot=float(data["target_spot"]),
            exit_pending_reason=ExitReason(pending_reason) if pending_reason else None,
            exit_pending_since=_dt(pending_since, "exit_pending_since") if pending_since else None,
            label=str(data.get("label", "")),
        )
    except CheckpointError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise CheckpointError(f"position cannot be rebuilt: {type(exc).__name__}: {exc}") from exc


def validate_position_dict(data: Any) -> list[str]:
    """Return a list of consistency problems (empty list == consistent)."""
    if not isinstance(data, Mapping):
        return ["position is not an object"]
    problems: list[str] = []
    if not str(data.get("trade_id") or ""):
        problems.append("trade_id missing")
    if data.get("status") not in _VALID_POSITION_STATUSES:
        problems.append(f"status {data.get('status')!r} is not OPEN/EXIT_PENDING")
    try:
        lots, qty = int(data.get("lots", 0)), int(data.get("quantity", 0))
    except (TypeError, ValueError):
        lots = qty = 0
    if lots <= 0 or qty <= 0:
        problems.append(f"lots/quantity must be > 0 (lots={data.get('lots')}, qty={qty})")
    for key in (
        "entry_time", "entry_spot", "entry_bid", "entry_ask", "entry_premium", "entry_charges",
        "entry_slippage", "premium_invested", "stop_spot", "target_spot",
    ):  # fmt: skip
        value = data.get(key)
        if value is None or value == "":
            problems.append(f"entry field {key} missing")
        elif key != "entry_time" and not _finite(value):
            problems.append(f"entry field {key} not a finite number ({value!r})")
    if _finite(data.get("entry_premium")) and float(data["entry_premium"]) <= 0:
        problems.append("entry_premium must be > 0")
    contract = data.get("contract")
    if not isinstance(contract, Mapping):
        problems.append("contract missing")
    else:
        for key in ("scrip_code", "symbol", "expiry", "strike", "option_type", "lot_size"):
            if contract.get(key) in (None, ""):
                problems.append(f"contract.{key} missing")
        if contract.get("option_type") not in ("CE", "PE"):
            problems.append(f"contract.option_type {contract.get('option_type')!r} not CE/PE")
        try:
            lot_size = int(contract.get("lot_size", 0))
            if lot_size <= 0:
                problems.append("contract.lot_size must be > 0")
            elif qty > 0 and lots > 0 and qty != lots * lot_size:
                problems.append(f"quantity {qty} != lots {lots} x lot_size {lot_size}")
        except (TypeError, ValueError):
            problems.append("contract.lot_size invalid")
    if not isinstance(data.get("signal"), Mapping):
        problems.append("signal missing")
    if not problems:
        try:
            position_from_dict(data)
        except CheckpointError as exc:
            problems.append(str(exc))
    return problems


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- checkpoint


@dataclass
class Checkpoint:
    session_id: str
    session_date: str  # ISO IST date
    run_mode: str  # 'LIVE' | 'REPLAY'
    label: str
    account: dict[str, Any]
    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    written_at: str = ""
    position: dict[str, Any] | None = None
    executed_signal_ids: list[str] = field(default_factory=list)
    strategy_daily_flags: dict[str, Any] = field(default_factory=dict)
    engine_state: str = "RUNNING"
    risk_flags: dict[str, Any] = field(default_factory=lambda: {"halted": False, "reason": ""})
    last_candle_open: str | None = None
    last_event_seq: int = 0
    closed_trade_ids_today: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = _jsonable(asdict(self))
        out["executed_signal_ids"] = list(dict.fromkeys(self.executed_signal_ids))
        return out

    @classmethod
    def from_dict(cls, data: Any) -> Checkpoint:
        """Strict parse; raises :class:`CheckpointError` on any structural problem."""
        if not isinstance(data, Mapping):
            raise CheckpointError("checkpoint is not a JSON object")
        version = data.get("schema_version")
        if version != CHECKPOINT_SCHEMA_VERSION or isinstance(version, bool):
            raise CheckpointError(
                f"unsupported schema_version {version!r} (expected {CHECKPOINT_SCHEMA_VERSION})"
            )

        def need(key: str, kind: type | tuple[type, ...]) -> Any:
            if key not in data:
                raise CheckpointError(f"missing key {key!r}")
            if not isinstance(data[key], kind) or isinstance(data[key], bool) and kind is int:
                raise CheckpointError(f"key {key!r} has wrong type {type(data[key]).__name__}")
            return data[key]

        ids = need("executed_signal_ids", list)
        closed = need("closed_trade_ids_today", list)
        if not all(isinstance(i, str) for i in ids + closed):
            raise CheckpointError("signal/trade id lists must contain strings")
        position = data.get("position")
        if position is not None and not isinstance(position, dict):
            raise CheckpointError("position must be an object or null")
        risk = need("risk_flags", dict)
        last_open = data.get("last_candle_open")
        if last_open is not None and not isinstance(last_open, str):
            raise CheckpointError("last_candle_open must be a string or null")
        _day(need("session_date", str), "session_date")
        return cls(
            schema_version=int(version),
            session_id=need("session_id", str),
            session_date=data["session_date"],
            written_at=str(data.get("written_at", "")),
            run_mode=need("run_mode", str),
            label=str(data.get("label", "")),
            account=dict(need("account", dict)),
            position=position,
            executed_signal_ids=list(dict.fromkeys(ids)),
            strategy_daily_flags=dict(need("strategy_daily_flags", dict)),
            engine_state=need("engine_state", str),
            risk_flags=dict(risk),
            last_candle_open=last_open,
            last_event_seq=int(need("last_event_seq", int)),
            closed_trade_ids_today=list(dict.fromkeys(closed)),
        )


class LoadKind(StrEnum):
    MISSING = "MISSING"
    UNREADABLE = "UNREADABLE"
    CORRUPT = "CORRUPT"
    SCHEMA = "SCHEMA"


@dataclass(frozen=True)
class LoadFailure:
    kind: LoadKind
    reason: str


def save_checkpoint(path: str | Path, cp: Checkpoint, *, now: datetime | None = None) -> None:
    """Atomically persist ``cp`` (tmp + fsync + rename + dir fsync); stamps ``written_at``."""
    cp.written_at = (now or ist_now()).isoformat()
    atomic_write_json(path, cp.to_dict(), fsync=True)


def load_checkpoint(path: str | Path) -> Checkpoint | LoadFailure:
    """Load and validate a checkpoint.  Never raises for a missing/corrupt/old file."""
    target = Path(path)
    if not target.exists():
        return LoadFailure(LoadKind.MISSING, f"no checkpoint at {target}")
    try:
        raw = target.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return LoadFailure(LoadKind.UNREADABLE, f"cannot read checkpoint: {exc}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return LoadFailure(LoadKind.CORRUPT, f"checkpoint is not valid JSON: {exc}")
    try:
        return Checkpoint.from_dict(data)
    except CheckpointError as exc:
        return LoadFailure(LoadKind.SCHEMA, str(exc))
    except (TypeError, ValueError) as exc:  # e.g. int() on garbage
        return LoadFailure(LoadKind.SCHEMA, f"checkpoint fields invalid: {exc}")


# --------------------------------------------------------------------------- recovery ack

RECOVERY_ACK_FILENAME = "recovery_ack.json"
RECOVERY_ACK_SCHEMA = 1


def recovery_ack_path(checkpoint_path: str | Path) -> Path:
    """``recovery_ack.json`` lives next to the checkpoint (same runtime root, same run mode)."""
    return Path(checkpoint_path).with_name(RECOVERY_ACK_FILENAME)


def _ledger_totals(rows: Sequence[Mapping[str, Any]]) -> tuple[int, float, str]:
    total = sum(float(r["net_pnl"]) for r in rows)
    last_id = str(rows[-1].get("trade_id", "")) if rows else ""
    return len(rows), round(total, 2), last_id


def ledger_reconciles(
    rows: Sequence[Mapping[str, Any]], starting_capital: float, tolerance: float = 0.01
) -> tuple[bool, str]:
    """True when the last ``balance_after`` equals ``starting_capital + sum(net_pnl)``."""
    if not rows:
        return False, "the ledger has no rows"
    try:
        total = sum(float(r["net_pnl"]) for r in rows)
        last = float(rows[-1]["balance_after"])
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"ledger rows unreadable: {exc}"
    expected = starting_capital + total
    if abs(last - expected) > tolerance:
        return False, (
            f"ledger balance {last:.2f} != starting capital {starting_capital:.2f} + "
            f"sum(net_pnl) {total:.2f} = {expected:.2f}"
        )
    return True, f"ledger balance {last:.2f} = starting capital + sum(net_pnl)"


def build_recovery_ack(
    rows: Sequence[Mapping[str, Any]],
    *,
    today: date,
    run_mode: str,
    renamed_to: str | None,
    now: datetime,
) -> dict[str, Any]:
    count, net, last_id = _ledger_totals(rows)
    return {
        "schema": RECOVERY_ACK_SCHEMA,
        "session_date": today.isoformat(),
        "run_mode": run_mode,
        "acknowledged_at": now.isoformat(),
        "checkpoint_renamed_to": renamed_to,
        "trades_csv_rows": count,
        "trades_csv_net_pnl": net,
        "trades_csv_last_trade_id": last_id,
    }


def write_recovery_ack(path: str | Path, ack: Mapping[str, Any]) -> None:
    atomic_write_json(path, dict(ack), fsync=True)


def load_recovery_ack(path: str | Path) -> dict[str, Any] | None:
    """The acknowledgement dict, or ``None`` (missing / unreadable / wrong schema).  Never raises."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != RECOVERY_ACK_SCHEMA:
        return None
    return data


def recovery_ack_matches(
    ack: Mapping[str, Any] | None,
    rows: Sequence[Mapping[str, Any]],
    *,
    today: date,
    run_mode: str | None,
    tolerance: float = DEFAULT_TOLERANCE_INR,
) -> tuple[bool, str]:
    """An acknowledgement is honoured ONLY when it matches today's ledger exactly."""
    if not ack:
        return False, "no recovery acknowledgement"
    if str(ack.get("session_date")) != today.isoformat():
        return False, f"acknowledgement is for {ack.get('session_date')}, not {today}"
    if run_mode is not None and str(ack.get("run_mode")) != run_mode:
        return False, f"acknowledgement run_mode {ack.get('run_mode')!r} != {run_mode!r}"
    try:
        count, net, last_id = _ledger_totals(rows)
        ack_rows = int(ack["trades_csv_rows"])
        ack_net = float(ack["trades_csv_net_pnl"])
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"acknowledgement or ledger unreadable: {exc}"
    if count != ack_rows:
        return False, f"trades.csv has {count} row(s) but {ack_rows} were acknowledged"
    if abs(net - ack_net) > tolerance:
        return False, f"trades.csv net P&L {net:.2f} != acknowledged {ack_net:.2f}"
    if str(ack.get("trades_csv_last_trade_id", "")) != last_id:
        return False, "last trade id differs from the acknowledged ledger"
    return True, f"matches the acknowledged ledger ({count} row(s), net {net:+.2f})"


# --------------------------------------------------------------------------- recovery decision


class RecoveryAction(StrEnum):
    START_FRESH = "START_FRESH"
    RESUME = "RESUME"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass
class RecoveryDecision:
    """Outcome of :func:`recovery_decision`.

    ``checkpoint``       - for RESUME (after any ledger reconciliation) the state to restore;
                           for RECOVERY_REQUIRED the loaded checkpoint (if any) for inspection.
    ``carried_account``  - for START_FRESH after an earlier date: the account dict to carry
                           forward (cash / realized / starting capital; session counters reset).
    """

    action: RecoveryAction
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    checkpoint: Checkpoint | None = None
    carried_account: dict[str, Any] | None = None


def _rr(reason: str, cp: Checkpoint | None = None, **details: Any) -> RecoveryDecision:
    return RecoveryDecision(RecoveryAction.RECOVERY_REQUIRED, reason, details, checkpoint=cp)


def _num(mapping: Mapping[str, Any], key: str, default: float | None = None) -> float:
    value = mapping.get(key, default)
    if not _finite(value):
        raise CheckpointError(f"account.{key} missing or not numeric ({value!r})")
    return float(value)


def _carry_forward(account: Mapping[str, Any]) -> dict[str, Any]:
    cash = _num(account, "cash")
    return {
        "starting_capital": _num(account, "starting_capital"),
        "cash": cash,
        "realized_pnl": _num(account, "realized_pnl"),
        "session_open_equity": cash,
        "session_realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "invested": 0.0,
        "trades_today": 0,
        "wins_today": 0,
        "losses_today": 0,
    }


def recovery_decision(
    cp: Checkpoint | LoadFailure | None,
    trades: Sequence[Mapping[str, Any]] | None = None,
    *,
    trades_csv_ids: Collection[str] | None = None,
    today: date | None = None,
    now: datetime | None = None,
    run_mode: str | None = None,
    tolerance: float = DEFAULT_TOLERANCE_INR,
    recovery_ack: Mapping[str, Any] | None = None,
) -> RecoveryDecision:
    """Decide how a starting process may use ``cp`` given the authoritative ledger.

    ``trades``          rows from ``TradeCsvJournal.load_trades()`` (needed for the P&L checks);
    ``trades_csv_ids``  ids only (P&L checks are then skipped and a close-reconcile that needs
                        the ledger row becomes RECOVERY_REQUIRED).
    ``None`` for both means an empty ledger.  Decision table (first match wins):

    * no checkpoint -> START_FRESH (RECOVERY_REQUIRED if today's trades exist in the ledger:
      daily-loss / executed-signal state was lost; START_FRESH again only if ``recovery_ack``
      -- written by ``--acknowledge-recovery`` -- matches today's ledger EXACTLY);
    * unreadable / corrupt / wrong-schema / other run mode / dated in the future / bad account
      -> RECOVERY_REQUIRED;
    * position's trade_id already in trades.csv (crash between close and checkpoint) ->
      reconciled as CLOSED (position None, account advanced from the ledger row, no duplicate
      row ever written), then evaluated as a flat checkpoint;
    * open/pending position on an earlier IST date -> RECOVERY_REQUIRED (overnight position:
      never auto-flatten, never auto-resume);
    * same-day position -> RESUME only if internally consistent, else RECOVERY_REQUIRED;
    * ledger inconsistent with the account (realized vs sum(net_pnl); flat cash vs
      starting_capital + realized; checkpoint-closed trades missing from the ledger) ->
      RECOVERY_REQUIRED with the exact numbers;
    * flat checkpoint from an earlier date -> START_FRESH carrying only the account;
    * flat same-day checkpoint -> RESUME (executed signal ids / flags / halt preserved; a
      persisted RECOVERY_REQUIRED engine state stays RECOVERY_REQUIRED).
    """
    now = now or ist_now()
    today = today or now.astimezone(IST).date()
    have_rows = trades is not None or trades_csv_ids is None  # ids-only => P&L checks skipped
    rows = list(trades or [])
    ids = {str(r["trade_id"]) for r in rows if r.get("trade_id")}
    if trades_csv_ids is not None:
        ids |= {str(i) for i in trades_csv_ids}
    today_rows = [r for r in rows if str(r.get("session_date", "")) == today.isoformat()]

    # ---- no usable checkpoint
    if cp is None or (isinstance(cp, LoadFailure) and cp.kind is LoadKind.MISSING):
        if today_rows:
            ok, why = recovery_ack_matches(recovery_ack, rows, today=today, run_mode=run_mode)
            if ok:
                return RecoveryDecision(
                    RecoveryAction.START_FRESH,
                    "operator acknowledged the lost checkpoint and trades.csv " + why
                    + "; starting fresh from the ledger (executed-signal state is not "
                    "recoverable, the daily-loss base is rebuilt from the ledger)",
                    {
                        "ledger_rows": len(rows),
                        "recovery_ack": True,
                        "trades_today": len(today_rows),
                    },
                )
            return _rr(
                "no checkpoint but today's trades exist in trades.csv: daily-loss and "
                "executed-signal state cannot be trusted",
                trades_today=len(today_rows),
            )
        return RecoveryDecision(
            RecoveryAction.START_FRESH,
            "no checkpoint; starting a fresh session",
            {"ledger_rows": len(rows)},
        )
    if isinstance(cp, LoadFailure):
        return _rr(f"checkpoint {cp.kind.value}: {cp.reason}", kind=cp.kind.value)

    # ---- header checks
    if run_mode is not None and cp.run_mode != run_mode:
        return _rr(
            f"checkpoint run_mode {cp.run_mode!r} != current {run_mode!r}; "
            "LIVE and REPLAY state must never mix",
            cp,
        )
    try:
        cp_date = date.fromisoformat(cp.session_date)
    except ValueError:
        return _rr(f"checkpoint session_date invalid: {cp.session_date!r}", cp)
    if cp_date > today:
        return _rr(f"checkpoint dated {cp_date} is in the future (today {today})", cp)
    try:
        account = dict(cp.account)
        for key in ("starting_capital", "cash", "realized_pnl"):
            _num(account, key)
    except CheckpointError as exc:
        return _rr(f"checkpoint account invalid: {exc}", cp)

    details: dict[str, Any] = {"checkpoint_date": cp_date.isoformat(), "ledger_rows": len(rows)}
    effective = Checkpoint.from_dict(cp.to_dict())  # private copy we may reconcile
    notes: list[str] = []

    # ---- crash between close and checkpoint: position already in the ledger
    pos = effective.position
    if isinstance(pos, Mapping) and str(pos.get("trade_id") or "") in ids:
        tid = str(pos["trade_id"])
        row = next((r for r in rows if str(r.get("trade_id")) == tid), None)
        if row is None:
            return _rr(
                f"position {tid} is already in trades.csv but the ledger rows were not supplied; "
                "cannot reconcile the account",
                cp,
                trade_id=tid,
            )
        try:
            net, balance = float(row["net_pnl"]), float(row["balance_after"])
            total = sum(float(r["net_pnl"]) for r in rows)
        except (KeyError, TypeError, ValueError) as exc:
            return _rr(f"ledger row for {tid} unreadable: {exc}", cp, trade_id=tid)
        realized = float(account["realized_pnl"])
        if abs(realized - total) <= tolerance:
            applied = False  # checkpoint account already reflects the close
        elif abs(realized - (total - net)) <= tolerance:
            applied = True
        else:
            return _rr(
                f"position {tid} is in trades.csv but account.realized_pnl {realized:.2f} matches "
                f"neither sum(net_pnl) {total:.2f} nor sum without that trade {total - net:.2f}",
                cp,
                trade_id=tid,
                checkpoint_realized_pnl=realized,
                csv_sum_net_pnl=total,
                trade_net_pnl=net,
            )
        if applied:
            account["realized_pnl"] = realized + net
            account["cash"] = balance
            account["session_realized_pnl"] = float(account.get("session_realized_pnl", 0.0)) + net
            account["trades_today"] = int(account.get("trades_today", 0)) + 1
            if net > 0:
                account["wins_today"] = int(account.get("wins_today", 0)) + 1
            elif net < 0:
                account["losses_today"] = int(account.get("losses_today", 0)) + 1
        account["invested"] = 0.0
        account["unrealized_pnl"] = 0.0
        effective.account = account
        effective.position = None
        if tid not in effective.closed_trade_ids_today:
            effective.closed_trade_ids_today.append(tid)
        details.update(reconciled_trade_id=tid, account_advanced_from_ledger=applied)
        notes.append(
            f"position {tid} was already closed in trades.csv (crash before checkpoint); "
            "treated as CLOSED, no duplicate row"
        )
        pos = None

    # ---- open position handling
    if pos is not None:
        status = pos.get("status") if isinstance(pos, Mapping) else None
        details["open_trade_id"] = pos.get("trade_id") if isinstance(pos, Mapping) else None
        details["open_status"] = status
        if cp_date < today:
            return _rr(
                f"overnight position {details['open_trade_id']} ({status}) from {cp_date}; "
                "never auto-flattened or auto-resumed - operator decision required",
                cp,
                **details,
            )
        problems = validate_position_dict(pos)
        if problems:
            return _rr("checkpoint position is inconsistent: " + "; ".join(problems), cp, **details)
        sig_id = pos["signal"].get("signal_id")
        if sig_id and sig_id not in effective.executed_signal_ids:
            effective.executed_signal_ids.append(sig_id)

    # ---- ledger vs account
    closed_missing = [t for t in effective.closed_trade_ids_today if t not in ids]
    if closed_missing:
        return _rr(
            "checkpoint lists closed trades that are missing from trades.csv: "
            + ", ".join(closed_missing),
            cp,
            missing_trade_ids=closed_missing,
            **details,
        )
    realized = float(effective.account["realized_pnl"])
    cash = float(effective.account["cash"])
    if have_rows:
        try:
            total = sum(float(r["net_pnl"]) for r in rows)
        except (KeyError, TypeError, ValueError) as exc:
            return _rr(f"trades.csv net_pnl unreadable: {exc}", cp, **details)
        details["csv_sum_net_pnl"] = total
        if abs(realized - total) > tolerance:
            return _rr(
                f"account.realized_pnl {realized:.2f} != sum(trades.csv net_pnl) {total:.2f} "
                f"(difference {realized - total:+.2f})",
                cp,
                checkpoint_realized_pnl=realized,
                csv_sum_net_pnl=total,
                difference=realized - total,
                **{k: v for k, v in details.items() if k != "csv_sum_net_pnl"},
            )
        if pos is None:
            expected = float(effective.account["starting_capital"]) + total
            if abs(cash - expected) > tolerance:
                return _rr(
                    f"flat account.cash {cash:.2f} != starting_capital + sum(net_pnl) "
                    f"{expected:.2f} (difference {cash - expected:+.2f})",
                    cp,
                    checkpoint_cash=cash,
                    expected_cash=expected,
                    csv_sum_net_pnl=total,
                    difference=cash - expected,
                    **{k: v for k, v in details.items() if k != "csv_sum_net_pnl"},
                )
    else:
        details["account_check"] = "SKIPPED_NO_ROWS"

    # ---- decisions
    if cp_date < today:
        carried = _carry_forward(effective.account)
        return RecoveryDecision(
            RecoveryAction.START_FRESH,
            "; ".join(
                [*notes, f"earlier session {cp_date} was flat; fresh session carrying account only"]
            ),
            details,
            carried_account=carried,
        )
    reason = "; ".join(
        [*notes, "same-day checkpoint consistent with trades.csv; resuming"]
        if pos is None
        else [*notes, f"same-day open position {details['open_trade_id']} ({details['open_status']}) "
              "consistent; resuming"]
    )  # fmt: skip
    if effective.engine_state == "RECOVERY_REQUIRED":
        return _rr(
            "checkpoint already carries engine_state RECOVERY_REQUIRED (unresolved earlier)",
            effective,
            **details,
        )
    return RecoveryDecision(RecoveryAction.RESUME, reason, details, checkpoint=effective)
