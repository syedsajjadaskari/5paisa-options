"""Entry/risk gate for VIRTUAL option trades (pure, clock-injected, no I/O).

All numbers come from :class:`~trading_platform.live.config.LiveConfig`:

* premium budget = ``max_premium_allocation_pct`` of SESSION-OPENING equity; the cost of a
  position is ``(ask + entry_slippage) * qty + entry_charges``,
* daily loss = equity MARKED TO BID (cash + open position at bid) minus session-opening
  equity; breached when ``<= -max_daily_loss_pct`` of session-opening equity,
* the entry cutoff is applied to the candle CLOSE (decision time) AND to the fill time:
  a candle closing at 14:30:00 or later cannot open a trade, nor can a fill at/after 14:30,
* quote freshness is judged at the moment of use (``now - received_at``) and the quote must
  have been received at/after the signal decision time.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from trading_platform.data.calendar import NSECalendar
from trading_platform.live import costs
from trading_platform.live.authorization import can_fill
from trading_platform.live.config import LiveConfig
from trading_platform.live.ledger import VirtualAccount
from trading_platform.live.models import (
    IST,
    AccountState,
    ContractSpec,
    EntryDecision,
    EntryRejection,
    ExecutionTier,
    OptionQuote,
    SignalRecord,
)

ChargesFn = Callable[[int], float]  # entry charges (INR) for a BUY of `qty` units

#: Tolerated clock skew when a quote appears to be received "after" now.
FUTURE_QUOTE_TOLERANCE_SECONDS = 1.0


def _ist(dt: datetime, what: str) -> datetime:
    if dt.tzinfo is None:
        raise ValueError(f"{what} must be timezone-aware")
    return dt.astimezone(IST)


@dataclass(frozen=True)
class Rejection:
    code: EntryRejection
    detail: str


@dataclass(frozen=True)
class EntryCheck:
    """Outcome of :meth:`RiskGate.check_entry` (all failing checks, not just the first)."""

    accepted: bool
    lots: int = 0
    quantity: int = 0
    fill_price: float | None = None
    entry_charges: float | None = None
    estimated_cost: float | None = None
    premium_budget: float = 0.0
    rejections: tuple[Rejection, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)  # informational, never reject

    @property
    def primary(self) -> Rejection | None:
        return self.rejections[0] if self.rejections else None

    def to_decision(self, signal_id: str) -> EntryDecision:
        if self.accepted:
            detail = (
                f"accepted: {self.lots} lot(s), qty {self.quantity}, est fill "
                f"{self.fill_price}, est cost {self.estimated_cost}"
            )
            if self.warnings:
                detail += " | warnings: " + "; ".join(self.warnings)
            return EntryDecision(signal_id, True, None, detail)
        first = self.rejections[0]
        extra = [f"{r.code.value}: {r.detail}" for r in self.rejections]
        return EntryDecision(signal_id, False, first.code, " | ".join(extra))


def _dec(v: float | int | str) -> Decimal:
    return Decimal(str(v))


class RiskGate:
    """Stateless risk/entry checks driven by config and the injected ``now``."""

    def __init__(self, cfg: LiveConfig, calendar: NSECalendar | None = None) -> None:
        self.cfg = cfg
        self._calendar = calendar or NSECalendar()

    # ------------------------------------------------------------------ time rules

    def market_open(self, now: datetime) -> bool:
        """Inside the regular session window ``[market_open, market_close)`` of a trading day."""
        t = _ist(now, "now")
        s = self.cfg.session
        return (
            self._calendar.is_entry_session_supported(t.date())
            and s.market_open <= t.time() < s.market_close
        )

    def square_off_due(self, now: datetime) -> bool:
        """True from the configured square-off time (default 15:15) onwards."""
        return _ist(now, "now").time() >= self.cfg.session.square_off

    def cutoff_reached(self, t: datetime) -> bool:
        """True if ``t`` (candle close or fill time) is at/after the entry cutoff."""
        return _ist(t, "time").time() >= self.cfg.session.entry_cutoff

    # ------------------------------------------------------------------ money rules

    def premium_budget_inr(self, equity_basis: float) -> float:
        """Allocation cap = pct of SESSION-OPENING equity (``equity_basis``)."""
        pct = _dec(self.cfg.account.max_premium_allocation_pct)
        return float(_dec(equity_basis) * pct / Decimal(100))

    def premium_budget_lots(
        self,
        ask_plus_slip: float,
        lot_size: int,
        charges_fn: ChargesFn,
        equity_basis: float,
    ) -> int:
        """Largest lot count <= ``max_lots`` with
        ``ask_plus_slip*lots*lot_size + charges_fn(lots*lot_size) <= budget``; 0 if not even one.
        """
        if not ask_plus_slip > 0 or lot_size <= 0 or not equity_basis > 0:
            return 0
        budget = _dec(self.premium_budget_inr(equity_basis))
        for lots in range(self.cfg.account.max_lots, 0, -1):
            qty = lots * lot_size
            cost = _dec(ask_plus_slip) * qty + _dec(charges_fn(qty))
            if cost <= budget:
                return lots
        return 0

    def marked_equity(
        self, account: VirtualAccount | AccountState, marked_bid_value: float | None
    ) -> Decimal:
        """Equity with the open position valued at ``marked_bid_value`` (= bid*qty).

        ``None`` means "use the account's own last mark" (flat accounts: cash).
        """
        st = account.state if isinstance(account, VirtualAccount) else account
        if marked_bid_value is None:
            return _dec(st.cash) + _dec(st.invested) + _dec(st.unrealized_pnl)
        return _dec(st.cash) + _dec(marked_bid_value)

    def daily_loss_limit_inr(self, session_open_equity: float) -> float:
        return float(_dec(session_open_equity) * _dec(self.cfg.account.max_daily_loss_pct) / 100)

    def daily_loss_breached(
        self, account: VirtualAccount | AccountState, marked_bid_value: float | None = None
    ) -> bool:
        """``equity(marked to bid) - session_open_equity <= -max_daily_loss_pct * session_open``.

        Fails closed (True) when the session-opening equity is unknown (<= 0).
        """
        st = account.state if isinstance(account, VirtualAccount) else account
        open_eq = _dec(st.session_open_equity)
        if open_eq <= 0:
            return True
        change = self.marked_equity(st, marked_bid_value) - open_eq
        return change <= -(open_eq * _dec(self.cfg.account.max_daily_loss_pct) / 100)

    # ------------------------------------------------------------------ entry check

    def check_entry(
        self,
        *,
        signal: SignalRecord,
        tier: ExecutionTier,
        contract: ContractSpec,
        quote: OptionQuote | None,
        account: VirtualAccount,
        now: datetime,
        exit_pending: bool = False,
        engine_running: bool = True,
    ) -> EntryCheck:
        """Evaluate every entry rule at ``now`` and return all failures + sizing."""
        cfg = self.cfg
        rej: list[Rejection] = []
        warn: list[str] = []
        now_i = _ist(now, "now")
        decision_i = _ist(signal.decision_time, "signal.decision_time")

        def add(code: EntryRejection, detail: str) -> None:
            rej.append(Rejection(code, detail))

        # authorization tier (defence in depth; authorization.py is the primary gate)
        if tier == ExecutionTier.DISABLED:
            add(EntryRejection.STRATEGY_DISABLED, f"{signal.strategy} is DISABLED")
        elif not can_fill(tier):
            add(EntryRejection.SHADOW_ONLY, f"{signal.strategy} tier {tier.value}: no fills")

        if not engine_running:
            add(EntryRejection.ENGINE_NOT_RUNNING, "engine not in a state that permits entries")

        # market session / time rules
        if decision_i.date() != now_i.date():
            add(
                EntryRejection.MARKET_CLOSED,
                f"signal decision date {decision_i.date()} != today {now_i.date()}",
            )
        if decision_i > now_i:
            add(
                EntryRejection.CANDLE_NOT_COMPLETE,
                f"decision time {decision_i:%H:%M:%S} is after now {now_i:%H:%M:%S}",
            )
        if not self.market_open(now_i):
            add(EntryRejection.MARKET_CLOSED, f"{now_i:%Y-%m-%d %H:%M:%S} outside regular session")
        cutoff = cfg.session.entry_cutoff
        if self.cutoff_reached(decision_i):
            add(
                EntryRejection.AFTER_ENTRY_CUTOFF,
                f"candle closes {decision_i:%H:%M:%S} >= entry cutoff {cutoff:%H:%M}",
            )
        if self.cutoff_reached(now_i):
            add(
                EntryRejection.AFTER_ENTRY_CUTOFF,
                f"fill time {now_i:%H:%M:%S} >= entry cutoff {cutoff:%H:%M}",
            )
        if self.square_off_due(now_i):
            add(
                EntryRejection.AFTER_ENTRY_CUTOFF,
                f"square-off due ({cfg.session.square_off:%H:%M})",
            )

        # position concurrency
        if exit_pending:
            add(EntryRejection.EXIT_PENDING, "an exit is pending; new entries blocked")
        open_count = 1 if account.has_open_position else 0
        if open_count >= cfg.account.max_open_positions:
            add(EntryRejection.POSITION_OPEN, f"{open_count} position(s) already open")

        # daily loss (equity marked to bid vs session-opening equity)
        if account.session_open_equity <= 0:
            add(
                EntryRejection.ENGINE_NOT_RUNNING,
                "session-opening equity not set (session not started)",
            )
        elif self.daily_loss_breached(account):
            add(
                EntryRejection.DAILY_LOSS_LIMIT,
                f"equity {account.equity:.2f} vs session open {account.session_open_equity:.2f}: "
                f"loss >= {cfg.account.max_daily_loss_pct}% limit",
            )

        # contract sanity
        lot_ok = isinstance(contract.lot_size, int) and contract.lot_size > 0
        if not lot_ok:
            add(
                EntryRejection.NO_CONTRACT,
                f"invalid lot_size {contract.lot_size!r} for {contract.symbol}",
            )

        # quote checks
        ex = cfg.execution
        lots = 0
        fill_price: float | None = None
        charges_total: float | None = None
        cost: float | None = None
        budget = (
            self.premium_budget_inr(account.session_open_equity)
            if account.session_open_equity > 0
            else 0.0
        )

        if quote is None:
            add(EntryRejection.NO_QUOTE, f"no quote for {contract.symbol}")
        else:
            if quote.scrip_code != contract.scrip_code:
                add(
                    EntryRejection.NO_QUOTE,
                    f"quote scrip {quote.scrip_code} != contract scrip {contract.scrip_code}",
                )
            recv = _ist(quote.received_at, "quote.received_at")
            age = (now_i - recv).total_seconds()
            if age < -FUTURE_QUOTE_TOLERANCE_SECONDS:
                add(
                    EntryRejection.STALE_QUOTE,
                    f"quote received {-age:.2f}s in the future (clock skew)",
                )
            elif age > ex.max_quote_age_seconds:
                add(
                    EntryRejection.STALE_QUOTE,
                    f"quote age {age:.2f}s > max {ex.max_quote_age_seconds}s",
                )
            if recv < decision_i:
                add(
                    EntryRejection.QUOTE_BEFORE_SIGNAL,
                    f"quote received {recv:%H:%M:%S.%f} before signal decision {decision_i:%H:%M:%S.%f}",
                )

            finite = all(math.isfinite(v) for v in (quote.bid, quote.ask))
            if not finite or not quote.is_valid_book:
                add(
                    EntryRejection.CROSSED_OR_WIDE_QUOTE,
                    f"invalid book bid={quote.bid} ask={quote.ask} (zero/crossed)",
                )
            else:
                spread = quote.ask - quote.bid
                mid = (quote.ask + quote.bid) / 2
                if spread > ex.max_spread_points:
                    add(
                        EntryRejection.CROSSED_OR_WIDE_QUOTE,
                        f"spread {spread:.2f} pts > max {ex.max_spread_points}",
                    )
                if spread / mid * 100 > ex.max_spread_pct:
                    add(
                        EntryRejection.CROSSED_OR_WIDE_QUOTE,
                        f"spread {spread / mid * 100:.2f}% of mid > max {ex.max_spread_pct}%",
                    )

            if quote.ask > 0 and lot_ok and finite:
                fill_price = costs.entry_fill_price(quote.ask, ex.entry_slippage_points)
                trade_date: date = now_i.date()

                def charges_fn(qty: int, _p: float = fill_price, _d: date = trade_date) -> float:
                    return costs.charges("BUY", _d, _p, qty).total

                lots = self.premium_budget_lots(
                    fill_price, contract.lot_size, charges_fn, account.session_open_equity
                )
                if lots < 1:
                    one = fill_price * contract.lot_size + charges_fn(contract.lot_size)
                    add(
                        EntryRejection.PREMIUM_BUDGET,
                        f"1 lot costs {one:.2f} > allocation budget {budget:.2f} "
                        f"({cfg.account.max_premium_allocation_pct}% of session-open equity)",
                    )
                else:
                    qty = lots * contract.lot_size
                    charges_total = charges_fn(qty)
                    cost = float(_dec(fill_price) * qty + _dec(charges_total))
                    if cost > account.cash:
                        add(
                            EntryRejection.INSUFFICIENT_CASH,
                            f"cost {cost:.2f} > cash {account.cash:.2f}",
                        )
                    if quote.ask_qty < qty:
                        warn.append(f"TOP_OF_BOOK_QTY: ask_qty {quote.ask_qty} < order qty {qty}")

        quantity = lots * contract.lot_size if lot_ok else 0
        accepted = not rej
        return EntryCheck(
            accepted=accepted,
            lots=lots,
            quantity=quantity,
            fill_price=fill_price,
            entry_charges=charges_total,
            estimated_cost=cost,
            premium_budget=budget,
            rejections=tuple(rej),
            warnings=tuple(warn),
        )
