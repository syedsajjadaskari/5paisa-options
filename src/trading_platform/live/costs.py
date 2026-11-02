"""Fill-price and charge helpers for VIRTUAL option trades (single implementation).

Rules (``docs/live_app_architecture.md``, "Money rules"):

* Entry fill = observed ASK + entry slippage.
* Exit fill  = observed BID - exit slippage, floored at 0.05.
* Charges come from :class:`IndianOptionsCostCalculator` with ``apply_slippage=False``:
  slippage lives ONLY in the fill price and is never charged a second time.
* BUY charges are applied once at entry, SELL charges once at exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from trading_platform.backtesting.indian_options_costs import (
    IndianOptionsCostCalculator,
    SingleLegCostBreakdown,
)

MIN_EXIT_PRICE = 0.05
_CENT = Decimal("0.01")

_DEFAULT_CALCULATOR = IndianOptionsCostCalculator()


def _d(value: float | int | str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def round2(value: float | int | str | Decimal) -> float:
    """Round half-up to paise and return a float (no float-noise in stored prices)."""
    return float(_d(value).quantize(_CENT, rounding=ROUND_HALF_UP))


def entry_fill_price(ask: float, slip: float) -> float:
    """Virtual BUY fill: observed ask + slippage."""
    if not ask > 0:
        raise ValueError(f"entry fill needs a positive ask, got {ask!r}")
    if slip < 0:
        raise ValueError(f"slippage must be >= 0, got {slip!r}")
    return round2(_d(ask) + _d(slip))


def exit_fill_price(bid: float, slip: float, floor: float = MIN_EXIT_PRICE) -> float:
    """Virtual SELL fill: observed bid - slippage, floored (default 0.05)."""
    if not bid > 0:
        raise ValueError(f"exit fill needs a positive bid, got {bid!r}")
    if slip < 0:
        raise ValueError(f"slippage must be >= 0, got {slip!r}")
    return max(round2(floor), round2(_d(bid) - _d(slip)))


@dataclass(frozen=True)
class LegCharges:
    side: str
    brokerage: float
    stt: float
    exchange_charges: float
    gst: float
    sebi_charges: float
    stamp_duty: float
    total: float

    @staticmethod
    def from_breakdown(b: SingleLegCostBreakdown) -> LegCharges:
        return LegCharges(
            b.side,
            b.brokerage,
            b.stt,
            b.exchange_charges,
            b.gst,
            b.sebi_charges,
            b.stamp_duty,
            b.total_charges,
        )


def charges(
    side: str,
    trade_date: date,
    premium: float,
    qty: int,
    calculator: IndianOptionsCostCalculator | None = None,
) -> LegCharges:
    """Statutory charges + brokerage for ONE leg (``BUY`` or ``SELL``).

    ``apply_slippage`` is hard-wired to False: the slippage is already in the fill price.
    """
    side_u = side.upper()
    if side_u not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if qty <= 0:
        raise ValueError(f"quantity must be positive, got {qty!r}")
    if not premium > 0:
        raise ValueError(f"premium must be positive, got {premium!r}")
    calc = calculator or _DEFAULT_CALCULATOR
    return LegCharges.from_breakdown(
        calc.calculate_single_leg_costs(trade_date, side_u, premium, qty, apply_slippage=False)
    )


@dataclass(frozen=True)
class RoundTripEstimate:
    entry_charges: float
    exit_charges: float
    total_charges: float
    gross_pnl: float
    net_pnl: float


def round_trip_estimate(
    trade_date: date,
    entry_fill: float,
    exit_fill: float,
    qty: int,
    calculator: IndianOptionsCostCalculator | None = None,
) -> RoundTripEstimate:
    """Gross/net P&L estimate for a long option round trip at the given FILL prices."""
    entry = charges("BUY", trade_date, entry_fill, qty, calculator)
    exit_ = charges("SELL", trade_date, exit_fill, qty, calculator)
    total = round2(_d(entry.total) + _d(exit_.total))
    gross = round2((_d(exit_fill) - _d(entry_fill)) * qty)
    return RoundTripEstimate(entry.total, exit_.total, total, gross, round2(_d(gross) - _d(total)))
