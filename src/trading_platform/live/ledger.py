"""Single-ledger virtual account (paise-exact Decimal arithmetic, no I/O).

Bookkeeping:

* ``open``:  cash -= entry_premium*qty + entry_charges ; invested = entry_premium*qty
* ``mark``:  unrealized = (bid - entry_premium)*qty (GROSS, before any exit charges)
* ``close``: cash += exit_premium*qty - exit_charges ; invested = 0 ; unrealized = 0
             realized NET = (exit - entry)*qty - entry_charges - exit_charges
* ``equity = cash + invested + unrealized`` (``AccountState.equity``)

Each charge leg is applied exactly once.  One position at a time; a SELL must equal the
open quantity; closing twice raises.  :class:`models.AccountState` is kept in sync after
every mutation and is what the rest of the app reads.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from trading_platform.live.models import AccountState

CHECKPOINT_SCHEMA = 1
_CENT = Decimal("0.01")
_TOLERANCE = Decimal("0.01")


class LedgerError(Exception):
    """Base class for ledger invariant violations."""


class InsufficientFundsError(LedgerError):
    pass


class PositionStateError(LedgerError):
    """Open while already open, close with nothing open, etc."""


class DoubleCloseError(PositionStateError):
    """Attempt to close a trade that was already closed (closing is not idempotent-silent)."""


class QuantityMismatchError(LedgerError):
    pass


class CheckpointError(LedgerError):
    pass


def _dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool is not a money value")
    return Decimal(str(value))


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class OpenLeg:
    trade_id: str
    quantity: int
    entry_premium: Decimal
    entry_charges: Decimal

    @property
    def cost(self) -> Decimal:
        return _q(self.entry_premium * self.quantity)


@dataclass(frozen=True)
class CloseResult:
    trade_id: str
    quantity: int
    entry_premium: float
    exit_premium: float
    gross_pnl: float
    entry_charges: float
    exit_charges: float
    net_pnl: float
    balance_after: float  # cash after the close (== equity, flat)


class VirtualAccount:
    """Cash/position ledger for the single virtual option position."""

    def __init__(
        self,
        starting_capital: float,
        cash: float | None = None,
        *,
        realized_pnl: float = 0.0,
    ) -> None:
        start = _q(_dec(starting_capital))
        if start <= 0:
            raise ValueError(f"starting_capital must be positive, got {starting_capital!r}")
        self._starting = start
        self._cash = _q(_dec(cash)) if cash is not None else _q(start + _dec(realized_pnl))
        self._realized = _q(_dec(realized_pnl))
        self._session_open_equity = Decimal("0")
        self._session_realized = Decimal("0")
        self._unrealized = Decimal("0")
        self._leg: OpenLeg | None = None
        self._closed_ids: set[str] = set()
        self._trades_today = 0
        self._wins_today = 0
        self._losses_today = 0
        self._state = AccountState(starting_capital=float(start), cash=float(self._cash))
        self._sync()

    # ------------------------------------------------------------------ views

    @property
    def state(self) -> AccountState:
        """The live :class:`AccountState` (kept in sync; treat as read-only)."""
        return self._state

    def snapshot(self) -> AccountState:
        """Detached copy of the account state."""
        return replace(self._state)

    @property
    def has_open_position(self) -> bool:
        return self._leg is not None

    @property
    def open_leg(self) -> OpenLeg | None:
        return self._leg

    @property
    def open_quantity(self) -> int:
        return self._leg.quantity if self._leg else 0

    @property
    def cash(self) -> float:
        return float(self._cash)

    @property
    def equity(self) -> float:
        return self._state.equity

    @property
    def session_open_equity(self) -> float:
        return float(self._session_open_equity)

    @property
    def closed_trade_ids(self) -> frozenset[str]:
        return frozenset(self._closed_ids)

    def equity_decimal(self) -> Decimal:
        invested = self._leg.cost if self._leg else Decimal("0")
        return self._cash + invested + self._unrealized

    def _sync(self) -> None:
        s = self._state
        s.starting_capital = float(self._starting)
        s.cash = float(self._cash)
        s.realized_pnl = float(self._realized)
        s.session_open_equity = float(self._session_open_equity)
        s.session_realized_pnl = float(self._session_realized)
        s.unrealized_pnl = float(self._unrealized)
        s.invested = float(self._leg.cost) if self._leg else 0.0
        s.trades_today = self._trades_today
        s.wins_today = self._wins_today
        s.losses_today = self._losses_today

    # ------------------------------------------------------------------ session

    def start_session(self, equity: float | None = None) -> float:
        """Begin a trading session: fix session-open equity and reset daily counters.

        ``equity`` defaults to the current flat equity (``starting_capital`` + all prior
        realized P&L).  Returns the session-open equity.
        """
        if self._leg is not None:
            raise PositionStateError("cannot start a session while a position is open")
        value = _q(_dec(equity)) if equity is not None else self.equity_decimal()
        if value <= 0:
            raise ValueError(f"session-open equity must be positive, got {value}")
        self._session_open_equity = value
        self._session_realized = Decimal("0")
        self._trades_today = self._wins_today = self._losses_today = 0
        self._unrealized = Decimal("0")
        self._sync()
        return float(value)

    # ------------------------------------------------------------------ trading

    def open(
        self, trade_id: str, quantity: int, entry_premium: float, entry_charges: float
    ) -> None:
        """Debit ``entry_premium*quantity + entry_charges`` and record the open leg."""
        if self._leg is not None:
            raise PositionStateError(f"position {self._leg.trade_id} already open")
        if trade_id in self._closed_ids:
            raise PositionStateError(f"trade_id {trade_id} was already used")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError(f"quantity must be a positive integer, got {quantity!r}")
        prem = _dec(entry_premium)
        chg = _q(_dec(entry_charges))
        if prem <= 0 or chg < 0:
            raise ValueError(f"invalid entry premium/charges: {entry_premium!r}/{entry_charges!r}")
        leg = OpenLeg(trade_id, quantity, prem, chg)
        debit = leg.cost + chg
        if debit > self._cash:
            raise InsufficientFundsError(f"need {debit} but cash is {self._cash}")
        self._cash -= debit
        self._leg = leg
        self._unrealized = Decimal("0")
        self._sync()

    def mark(self, bid: float) -> float:
        """Mark the open leg to ``bid``; returns gross unrealized P&L (0 when flat).

        A bid of 0 is allowed (worthless) but negative/NaN is not.  Callers must not pass
        a stale/invalid quote: validity is the risk gate's job.
        """
        if self._leg is None:
            return 0.0
        b = _dec(bid)
        if not b.is_finite() or b < 0:
            raise ValueError(f"invalid bid {bid!r}")
        self._unrealized = _q((b - self._leg.entry_premium) * self._leg.quantity)
        self._sync()
        return float(self._unrealized)

    def equity_at_bid(self, bid: float) -> float:
        """Equity if the open leg were marked at ``bid`` (does not mutate)."""
        if self._leg is None:
            return float(self._cash)
        b = _dec(bid)
        if not b.is_finite() or b < 0:
            raise ValueError(f"invalid bid {bid!r}")
        return float(self._cash + _q(b * self._leg.quantity))

    def close(
        self,
        trade_id: str,
        exit_premium: float,
        exit_charges: float,
        quantity: int | None = None,
    ) -> CloseResult:
        """SELL the whole open leg at ``exit_premium`` (fill price incl. slippage).

        ``quantity`` (if given) must equal the open quantity.  A second close raises
        :class:`DoubleCloseError`; closing with nothing open raises :class:`PositionStateError`.
        """
        if trade_id in self._closed_ids:
            raise DoubleCloseError(f"trade {trade_id} already closed")
        leg = self._leg
        if leg is None:
            raise PositionStateError("no open position to close")
        if trade_id != leg.trade_id:
            raise PositionStateError(f"open trade is {leg.trade_id}, not {trade_id}")
        if quantity is not None and quantity != leg.quantity:
            raise QuantityMismatchError(
                f"SELL quantity {quantity} must equal open quantity {leg.quantity}"
            )
        prem = _dec(exit_premium)
        chg = _q(_dec(exit_charges))
        if prem <= 0 or chg < 0:
            raise ValueError(f"invalid exit premium/charges: {exit_premium!r}/{exit_charges!r}")

        proceeds = _q(prem * leg.quantity)
        gross = _q((prem - leg.entry_premium) * leg.quantity)
        net = gross - leg.entry_charges - chg
        self._cash += proceeds - chg
        self._realized += net
        self._session_realized += net
        self._unrealized = Decimal("0")
        self._leg = None
        self._closed_ids.add(trade_id)
        self._trades_today += 1
        if net > 0:
            self._wins_today += 1
        elif net < 0:
            self._losses_today += 1
        self._sync()
        return CloseResult(
            trade_id=trade_id,
            quantity=leg.quantity,
            entry_premium=float(leg.entry_premium),
            exit_premium=float(prem),
            gross_pnl=float(gross),
            entry_charges=float(leg.entry_charges),
            exit_charges=float(chg),
            net_pnl=float(net),
            balance_after=float(self._cash),
        )

    # ------------------------------------------------------------------ checkpoint

    def to_checkpoint(self) -> dict[str, Any]:
        """JSON-serialisable dict (money as exact decimal strings)."""
        leg = self._leg
        return {
            "schema": CHECKPOINT_SCHEMA,
            "starting_capital": str(self._starting),
            "cash": str(self._cash),
            "realized_pnl": str(self._realized),
            "session_open_equity": str(_q(self._session_open_equity)),
            "session_realized_pnl": str(_q(self._session_realized)),
            "unrealized_pnl": str(_q(self._unrealized)),
            "trades_today": self._trades_today,
            "wins_today": self._wins_today,
            "losses_today": self._losses_today,
            "closed_trade_ids": sorted(self._closed_ids),
            "open_leg": None
            if leg is None
            else {
                "trade_id": leg.trade_id,
                "quantity": leg.quantity,
                "entry_premium": str(leg.entry_premium),
                "entry_charges": str(leg.entry_charges),
            },
        }

    @classmethod
    def restore_from_checkpoint(cls, data: dict[str, Any]) -> VirtualAccount:
        """Rebuild an account and verify it reconciles; raises :class:`CheckpointError`."""
        try:
            if data.get("schema") != CHECKPOINT_SCHEMA:
                raise CheckpointError(f"unsupported checkpoint schema {data.get('schema')!r}")
            acct = cls(
                float(_dec(data["starting_capital"])),
                cash=float(_dec(data["cash"])),
                realized_pnl=float(_dec(data["realized_pnl"])),
            )
            acct._session_open_equity = _q(_dec(data["session_open_equity"]))
            acct._session_realized = _q(_dec(data["session_realized_pnl"]))
            acct._unrealized = _q(_dec(data["unrealized_pnl"]))
            acct._trades_today = int(data["trades_today"])
            acct._wins_today = int(data["wins_today"])
            acct._losses_today = int(data["losses_today"])
            acct._closed_ids = set(map(str, data["closed_trade_ids"]))
            leg = data["open_leg"]
            if leg is not None:
                acct._leg = OpenLeg(
                    str(leg["trade_id"]),
                    int(leg["quantity"]),
                    _dec(leg["entry_premium"]),
                    _q(_dec(leg["entry_charges"])),
                )
                if acct._leg.quantity <= 0 or acct._leg.entry_premium <= 0:
                    raise CheckpointError("invalid open leg in checkpoint")
            elif acct._unrealized != 0:
                raise CheckpointError("unrealized P&L recorded without an open position")
        except CheckpointError:
            raise
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise CheckpointError(f"malformed checkpoint: {exc!r}") from exc

        if acct._cash < 0:
            raise CheckpointError("negative cash in checkpoint")
        # Reconcile: starting + realized(net) - open entry charges == cash + invested(at cost)
        at_cost = acct._cash + (acct._leg.cost if acct._leg else Decimal("0"))
        expected = acct._starting + acct._realized - (acct._leg.entry_charges if acct._leg else 0)
        if abs(at_cost - expected) > _TOLERANCE:
            raise CheckpointError(
                f"checkpoint does not reconcile: cash+invested={at_cost} vs "
                f"starting+realized-open_charges={expected}"
            )
        acct._sync()
        return acct
