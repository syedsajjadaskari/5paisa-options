"""Independent cent-precision reference ledger for virtual paper accounting."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(value: Decimal | float | int | str) -> Decimal:
    """Return a monetary value rounded to paise."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class ReferenceLedger:
    """Independent double-entry-style ledger for one virtual account."""

    starting_cash: Decimal
    cash: Decimal
    invested_premium: Decimal = Decimal("0.00")
    entry_charges: Decimal = Decimal("0.00")
    exit_charges: Decimal = Decimal("0.00")
    realized_pnl: Decimal = Decimal("0.00")
    quantity: int = 0
    entry_premium: Decimal = Decimal("0.00")

    @classmethod
    def open(
        cls,
        starting_cash: float | Decimal,
        premium: float,
        quantity: int,
        charges: float,
    ) -> ReferenceLedger:
        start = money(starting_cash)
        premium_value = money(premium * quantity)
        entry_cost = money(charges)
        return cls(
            starting_cash=start,
            cash=money(start - premium_value - entry_cost),
            invested_premium=premium_value,
            entry_charges=entry_cost,
            quantity=quantity,
            entry_premium=money(premium),
        )

    @property
    def equity(self) -> Decimal:
        return money(self.cash + self.invested_premium)

    def mark(self, premium: float) -> Decimal:
        """Mark a position without treating market value as available cash."""
        return money(self.cash + money(premium * self.quantity))

    def close(self, premium: float, quantity: int, charges: float) -> Decimal:
        if quantity != self.quantity:
            raise ValueError("ReferenceLedger only supports a full-position close")
        proceeds = money(premium * quantity)
        exit_cost = money(charges)
        gross = money(proceeds - self.invested_premium)
        net = money(gross - self.entry_charges - exit_cost)
        self.cash = money(self.cash + proceeds - exit_cost)
        self.exit_charges = money(self.exit_charges + exit_cost)
        self.realized_pnl = money(self.realized_pnl + net)
        self.invested_premium = Decimal("0.00")
        self.quantity = 0
        return net

    def assert_flat_invariant(self) -> None:
        if self.quantity != 0 or self.invested_premium != Decimal("0.00"):
            raise AssertionError("Reference ledger is not flat")
        expected = money(self.starting_cash + self.realized_pnl)
        if self.cash != expected:
            raise AssertionError(f"cash={self.cash} expected={expected}")
