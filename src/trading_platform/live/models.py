"""Shared types for the in-memory NIFTY options paper-trading application.

This module is the *contract* between the live data providers, the session engine,
the journal, the terminal and the dashboard.  Everything here is plain data (no I/O).

Safety invariants encoded in these types:
* There is no "order" type: the application can only create *virtual* fills.
* Every fill-like record carries ``source`` / ``label`` fields so simulated or
  provisional output can never be mistaken for author-approved live output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SCHEMA_VERSION = 1
CANDLE_MINUTES = 5


def ist_now() -> datetime:
    """Timezone-aware current time in IST (wall clock; use the injected Clock in engines)."""
    return datetime.now(IST)


# --------------------------------------------------------------------------- enums


class ExecutionTier(StrEnum):
    """Per-strategy execution authorization.  Deny-by-default (see authorization.py)."""

    DISABLED = "DISABLED"  # never evaluated, never fills (RF)
    SHADOW_ONLY = "SHADOW_ONLY"  # evaluated + signals journaled, NO virtual fills
    RESEARCH_PAPER = "RESEARCH_PAPER"  # simulated fills allowed on PROVISIONAL rules (explicit opt-in)
    AUTHOR_APPROVED = "AUTHOR_APPROVED"  # reserved: all rules confirmed by the author


class RunMode(StrEnum):
    """How the process is sourcing market data."""

    LIVE = "LIVE"  # real read-only 5paisa data
    REPLAY = "REPLAY"  # SIMULATED historical replay (test/demo only, separate output dir)


class EngineState(StrEnum):
    STARTING = "STARTING"
    READY = "READY"  # startup checks passed, waiting for / inside session
    RUNNING = "RUNNING"
    HALTED_RISK = "HALTED_RISK"  # daily loss limit etc: no new entries
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"  # uncertain restart: no new entries
    BLOCKED = "BLOCKED"  # fail-closed data/safety problem: no new entries
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STOPPED = "STOPPED"


class StrategyRunState(StrEnum):
    DISABLED = "DISABLED"
    WARMING_UP = "WARMING_UP"  # reference levels / history not ready
    MONITORING = "MONITORING"
    SIGNAL_SHADOW = "SIGNAL_SHADOW"  # signal produced but strategy is SHADOW_ONLY
    IN_TRADE = "IN_TRADE"
    DONE_FOR_DAY = "DONE_FOR_DAY"
    BLOCKED = "BLOCKED"  # e.g. no trustworthy completed candle


class PositionStatus(StrEnum):
    OPEN = "OPEN"
    EXIT_PENDING = "EXIT_PENDING"  # exit triggered but no valid bid yet; entries blocked
    CLOSED = "CLOSED"


class ExitReason(StrEnum):
    TARGET = "TARGET"
    STOP = "STOP"
    EOD = "EOD"  # 15:15 square-off
    RISK = "RISK"  # daily loss / kill switch
    STRATEGY_EXIT = "STRATEGY_EXIT"
    SHUTDOWN_UNRESOLVED = "SHUTDOWN_UNRESOLVED"  # never used to fabricate a fill


class EntryRejection(StrEnum):
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    SHADOW_ONLY = "SHADOW_ONLY"
    STRATEGY_DISABLED = "STRATEGY_DISABLED"
    MARKET_CLOSED = "MARKET_CLOSED"
    AFTER_ENTRY_CUTOFF = "AFTER_ENTRY_CUTOFF"
    POSITION_OPEN = "POSITION_OPEN"
    EXIT_PENDING = "EXIT_PENDING"
    ENGINE_NOT_RUNNING = "ENGINE_NOT_RUNNING"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    NO_CONTRACT = "NO_CONTRACT"
    NO_QUOTE = "NO_QUOTE"
    STALE_QUOTE = "STALE_QUOTE"
    QUOTE_BEFORE_SIGNAL = "QUOTE_BEFORE_SIGNAL"
    CROSSED_OR_WIDE_QUOTE = "CROSSED_OR_WIDE_QUOTE"
    PREMIUM_BUDGET = "PREMIUM_BUDGET"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    DUPLICATE_SIGNAL = "DUPLICATE_SIGNAL"
    ARBITRATION_LOST = "ARBITRATION_LOST"
    CANDLE_NOT_COMPLETE = "CANDLE_NOT_COMPLETE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    PRICE_MOVED = "PRICE_MOVED"  # spot already through stop/target when the fill became possible
    CANDLE_NOT_TRUSTED = "CANDLE_NOT_TRUSTED"
    ENTRY_TIMEOUT = "ENTRY_TIMEOUT"  # no valid post-signal ask within the allowed delay


class Direction(StrEnum):
    BULLISH = "BULLISH"  # buy CE
    BEARISH = "BEARISH"  # buy PE


class CandleSource(StrEnum):
    PROVIDER_COMPLETED = "PROVIDER_COMPLETED"  # genuine provider candle, proven complete
    SAMPLED_TICKS = "SAMPLED_TICKS"  # built from polled ticks: NOT authoritative
    HISTORICAL_FILE = "HISTORICAL_FILE"
    SIMULATED_REPLAY = "SIMULATED_REPLAY"


# --------------------------------------------------------------------------- market data


@dataclass(frozen=True)
class SpotTick:
    """NIFTY index tick.  ``exchange_time`` is the provider's tick time (TickDt)."""

    price: float
    exchange_time: datetime  # tz-aware IST
    received_at: datetime  # tz-aware IST (local clock when the response arrived)
    day_high: float | None = None
    day_low: float | None = None
    prev_close: float | None = None
    source: str = "FIVEPAISA_REST"


@dataclass(frozen=True)
class OptionQuote:
    """Top-of-book quote for one option contract.

    ``server_time`` is the provider response timestamp.  For 5paisa depth this is
    API-server time, not a proven matching-engine time; ``received_at`` is local time.
    Freshness is judged at *use* time by the engine, never frozen at fetch time.
    """

    scrip_code: int
    symbol: str
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    server_time: datetime  # tz-aware IST
    received_at: datetime  # tz-aware IST
    source: str = "FIVEPAISA_REST_DEPTH"

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def is_valid_book(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid


@dataclass(frozen=True)
class CompletedCandle:
    """A 5-minute NIFTY candle that is provably complete.

    ``open_time`` is the bar-OPEN label (09:15 for the 09:15-09:20 candle), matching
    the historical datasets and ``core.models.Bar.exchange_time``.
    """

    open_time: datetime  # tz-aware IST
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: CandleSource

    @property
    def close_time(self) -> datetime:
        return self.open_time + timedelta(minutes=CANDLE_MINUTES)


@dataclass(frozen=True)
class ContractSpec:
    """A real NIFTY option contract from the live scrip master (never hard-coded)."""

    scrip_code: int
    symbol: str  # full scrip-master name, e.g. 'NIFTY 03 Nov 2026 CE 25900.00'
    expiry: date
    strike: float
    option_type: str  # 'CE' | 'PE'
    lot_size: int  # from the master, never assumed


@dataclass(frozen=True)
class DailyOhlc:
    session_date: date
    open: float
    high: float
    low: float
    close: float


@dataclass
class WarmupData:
    """History needed to build strategy reference levels (loaded once at startup)."""

    previous_session: DailyOhlc
    daily_bars: list[Any]  # list[core.models.Bar] interval 1d, ascending, < today
    intraday_bars: list[Any]  # list[core.models.Bar] 5m, prior sessions only, ascending
    source_notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- decisions


@dataclass(frozen=True)
class SignalRecord:
    """A strategy signal as seen by the engine (immutable, journaled)."""

    signal_id: str  # f"{strategy}-{method}-{signal_candle_open:%Y%m%dT%H%M}"
    strategy: str  # 'PDS' | 'ORB' | 'IRF' | 'PDR'
    method: str  # 'METHOD_1' | 'METHOD_2' | 'RETEST_BOUNCE'
    direction: Direction
    candle_open_time: datetime
    decision_time: datetime  # candle close time = earliest moment the signal is knowable
    spot_reference: float
    stop_spot: float
    target_spot: float
    reason: str
    conditions: dict[str, Any] = field(default_factory=dict)  # satisfied/failed conditions
    levels: dict[str, Any] = field(default_factory=dict)  # PDH/PDL/zones etc. used
    trigger_candle: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EntryDecision:
    signal_id: str
    accepted: bool
    rejection: EntryRejection | None
    detail: str  # exact human-readable reason, shown in terminal/dashboard


@dataclass
class VirtualPosition:
    """The single open (or exit-pending) virtual option position."""

    trade_id: str
    signal: SignalRecord
    contract: ContractSpec
    status: PositionStatus
    lots: int
    quantity: int
    entry_time: datetime
    entry_spot: float
    entry_bid: float
    entry_ask: float
    entry_slippage: float
    entry_premium: float  # fill price = ask + slippage
    entry_charges: float
    premium_invested: float  # entry_premium * quantity (excl. charges)
    stop_spot: float
    target_spot: float
    exit_pending_reason: ExitReason | None = None
    exit_pending_since: datetime | None = None
    label: str = ""  # authorization basis / mode label copied to every record


@dataclass(frozen=True)
class TradeRecord:
    """A closed virtual trade.  Field order == trades.csv column order."""

    trade_id: str
    session_date: date
    session_id: str
    label: str  # e.g. 'RESEARCH_PAPER_PROVISIONAL' or 'REPLAY_SIMULATED'
    strategy: str
    method: str
    direction: str
    option_type: str
    symbol: str
    expiry: date
    strike: float
    scrip_code: int
    lot_size: int
    lots: int
    quantity: int
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry_spot: float
    exit_spot: float
    stop_spot: float
    target_spot: float
    entry_bid: float
    entry_ask: float
    entry_premium: float
    exit_bid: float
    exit_premium: float
    entry_slippage: float
    exit_slippage: float
    gross_pnl: float
    entry_charges: float
    exit_charges: float
    total_charges: float
    net_pnl: float
    exit_reason: str
    entry_reason: str
    entry_conditions_json: str
    levels_json: str
    trigger_candle_json: str
    exit_detail_json: str
    balance_after: float

    @staticmethod
    def csv_columns() -> list[str]:
        return list(TradeRecord.__dataclass_fields__.keys())

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        for k, v in row.items():
            if isinstance(v, datetime | date):
                row[k] = v.isoformat()
        return row


@dataclass
class AccountState:
    starting_capital: float
    cash: float
    realized_pnl: float = 0.0  # cumulative net realized (all sessions)
    session_open_equity: float = 0.0
    session_realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0  # marked to BID, gross of exit charges
    invested: float = 0.0  # open premium at cost
    trades_today: int = 0
    wins_today: int = 0
    losses_today: int = 0

    @property
    def equity(self) -> float:
        return self.cash + self.invested + self.unrealized_pnl


@dataclass
class StrategyMonitor:
    """Per-strategy status row for terminal + dashboard (Strategy Monitor section)."""

    name: str  # 'PDS'
    label: str  # 'PDS M1'
    tier: ExecutionTier
    state: StrategyRunState
    last_evaluated_candle: datetime | None = None
    levels: dict[str, Any] = field(default_factory=dict)
    conditions_satisfied: list[str] = field(default_factory=list)
    conditions_failed: list[str] = field(default_factory=list)
    last_signal: dict[str, Any] | None = None
    last_rejection: str | None = None
    trades_today: int = 0
    pnl_today: float = 0.0
    note: str = ""  # e.g. 'provisional rules: PDS zone proxy'
