"""Live NIFTY option contract resolution (pure, deterministic, no I/O).

* bullish -> CE, bearish -> PE (we only ever BUY options),
* expiry: the nearest/next *eligible* expiry per ``execution`` config (min DTE, expiry-day
  handling) via :func:`trading_platform.options.expiry_calendar.select_target_expiry`,
* strike: ATM among the ACTUALLY LISTED strikes of that expiry, via
  :func:`trading_platform.options.moneyness.find_atm_strike` (no ``round(spot/50)*50``, no
  banker's rounding; an exact tie between two listed strikes resolves to the LOWER strike),
  restricted to strikes where both a CE and a PE are listed so :meth:`candidate_pair` and
  :meth:`select` always agree,
* the ATM strike must lie within ``execution.strike_step`` of spot, else fail closed,
* the lot size always comes from the contract master row and must be a positive integer.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from trading_platform.live.config import ExecutionConfig, LiveConfig
from trading_platform.live.models import (
    IST,
    ContractSpec,
    Direction,
    EntryRejection,
)
from trading_platform.options.expiry_calendar import (
    ExpiryPolicy,
    filter_eligible_expiries,
    select_target_expiry,
)
from trading_platform.options.moneyness import find_atm_strike

_POLICY_MAP = {"NEAREST": ExpiryPolicy.NEAREST_ELIGIBLE, "NEXT": ExpiryPolicy.NEXT_ELIGIBLE}


class ContractSelectionError(Exception):
    """Typed failure: no safe contract could be selected (maps to ``NO_CONTRACT``)."""

    rejection = EntryRejection.NO_CONTRACT

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ContractResolver:
    """Selects the contract for a signal from the real contract list."""

    def __init__(self, contracts: list[ContractSpec], cfg: LiveConfig) -> None:
        self._exec: ExecutionConfig = cfg.execution
        self._market_close = cfg.session.market_close
        self._by_key: dict[tuple[date, float, str], list[ContractSpec]] = defaultdict(list)
        self._malformed = 0
        for c in contracts:
            if c.option_type not in ("CE", "PE") or not c.strike > 0:
                self._malformed += 1  # not tradable; never selectable
                continue
            self._by_key[(c.expiry, float(c.strike), c.option_type)].append(c)
        self._expiries = sorted({k[0] for k in self._by_key})

    @property
    def malformed_count(self) -> int:
        """Master rows ignored because option_type/strike were unusable."""
        return self._malformed

    # ------------------------------------------------------------------ expiry

    def eligible_expiries(self, trading_date: date, now: datetime | None = None) -> list[date]:
        ex = self._exec
        expiries = self._expiries
        if now is not None:
            now_ist = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
            # An expiry whose session is over (market close on expiry day) is no longer tradable.
            if now_ist.date() == trading_date and now_ist.time() >= self._market_close:
                expiries = [e for e in expiries if e > trading_date]
        return filter_eligible_expiries(
            trading_date,
            expiries,
            min_dte=ex.min_days_to_expiry,
            allow_expiry_day=ex.allow_expiry_day,
        )

    def target_expiry(self, trading_date: date, now: datetime | None = None) -> date:
        eligible = self.eligible_expiries(trading_date, now)
        if not eligible:
            raise ContractSelectionError(
                f"no eligible expiry on {trading_date} (min_days_to_expiry="
                f"{self._exec.min_days_to_expiry}, allow_expiry_day={self._exec.allow_expiry_day}, "
                f"listed={[e.isoformat() for e in self._expiries]})"
            )
        if self._exec.expiry_policy == "NEXT" and len(eligible) < 2:
            raise ContractSelectionError(
                f"expiry_policy NEXT needs 2 eligible expiries, found {len(eligible)}"
            )
        try:
            return select_target_expiry(
                trading_date,
                eligible,
                policy=_POLICY_MAP[self._exec.expiry_policy],
                min_dte=self._exec.min_days_to_expiry,
                allow_expiry_day=self._exec.allow_expiry_day,
            )
        except ValueError as exc:
            raise ContractSelectionError(str(exc)) from exc

    # ------------------------------------------------------------------ strikes

    def _pair_strikes(self, expiry: date) -> list[float]:
        ce = {k[1] for k in self._by_key if k[0] == expiry and k[2] == "CE"}
        pe = {k[1] for k in self._by_key if k[0] == expiry and k[2] == "PE"}
        return sorted(ce & pe)

    def _pick(self, expiry: date, strike: float, option_type: str) -> ContractSpec:
        rows = self._by_key.get((expiry, strike, option_type), [])
        if len({r.scrip_code for r in rows}) != 1:
            raise ContractSelectionError(
                f"ambiguous or missing master rows for {option_type} {strike} {expiry}: "
                f"{[r.scrip_code for r in rows]}"
            )
        c = rows[0]
        if isinstance(c.lot_size, bool) or not isinstance(c.lot_size, int) or c.lot_size <= 0:
            raise ContractSelectionError(
                f"invalid lot_size {c.lot_size!r} in master for {c.symbol} (scrip {c.scrip_code})"
            )
        return c

    def atm_strike(
        self, spot: float, trading_date: date, now: datetime | None = None
    ) -> tuple[date, float]:
        """(expiry, ATM strike) among listed CE+PE strikes; raises ContractSelectionError."""
        if not spot > 0:
            raise ContractSelectionError(f"invalid spot {spot!r}")
        expiry = self.target_expiry(trading_date, now)
        strikes = self._pair_strikes(expiry)
        if not strikes:
            raise ContractSelectionError(f"no strike with both CE and PE listed for {expiry}")
        atm = find_atm_strike(spot, strikes)
        if abs(atm - spot) > self._exec.strike_step:
            raise ContractSelectionError(
                f"nearest listed strike {atm} is {abs(atm - spot):.2f} from spot {spot} "
                f"(> strike_step {self._exec.strike_step}); listed grid has a gap"
            )
        return expiry, atm

    # ------------------------------------------------------------------ public API

    def candidate_pair(
        self, spot: float, trading_date: date, now: datetime | None = None
    ) -> tuple[ContractSpec, ContractSpec]:
        """(CE, PE) at the current ATM strike, for pre-subscribing quotes."""
        expiry, atm = self.atm_strike(spot, trading_date, now)
        return self._pick(expiry, atm, "CE"), self._pick(expiry, atm, "PE")

    def nearby_pairs(
        self, spot: float, trading_date: date, width: int = 1, now: datetime | None = None
    ) -> list[tuple[ContractSpec, ContractSpec]]:
        """(CE, PE) pairs for ATM and ``width`` listed strikes on each side (ascending)."""
        expiry, atm = self.atm_strike(spot, trading_date, now)
        strikes = self._pair_strikes(expiry)
        i = strikes.index(atm)
        chosen = strikes[max(0, i - width) : i + width + 1]
        return [(self._pick(expiry, k, "CE"), self._pick(expiry, k, "PE")) for k in chosen]

    def select(
        self, direction: Direction, spot: float, trading_date: date, now: datetime | None = None
    ) -> ContractSpec:
        """Contract to BUY for ``direction`` (BULLISH -> CE, BEARISH -> PE).

        Raises :class:`ContractSelectionError` (``rejection == NO_CONTRACT``) when no safe
        contract exists.
        """
        try:
            d = Direction(direction)
        except ValueError as exc:
            raise ContractSelectionError(f"unknown direction {direction!r}") from exc
        expiry, atm = self.atm_strike(spot, trading_date, now)
        return self._pick(expiry, atm, "CE" if d == Direction.BULLISH else "PE")

    def try_select(
        self, direction: Direction, spot: float, trading_date: date, now: datetime | None = None
    ) -> tuple[ContractSpec | None, ContractSelectionError | None]:
        """Non-raising variant: ``(contract, None)`` or ``(None, error)``."""
        try:
            return self.select(direction, spot, trading_date, now), None
        except ContractSelectionError as exc:
            return None, exc
