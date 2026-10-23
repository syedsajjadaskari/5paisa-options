"""Time-versioned statutory charges and brokerage model for Indian Index Options.

Regulatory Schedule and Sources:
1. Securities Transaction Tax (STT):
   - Prior to Oct 1, 2024: 0.0625% on sell-side premium.
   - Oct 1, 2024 to March 31, 2026: 0.1000% on sell-side premium (Union Budget 2024 amendment).
   - April 1, 2026 onwards: 0.1500% on sell-side premium (Union Budget 2026 / Finance Act 2026 amendment).
   - Exercise STT on ITM buyer:
     - Prior to April 1, 2026: 0.1250% on intrinsic/settlement value.
     - April 1, 2026 onwards: 0.1500% on intrinsic/settlement value.

2. Exchange Transaction Charges (NSE Equity Options Segment):
   - Prior to Oct 1, 2024: 0.0505% (slab-based blended effective rate up to ~0.0505%).
   - Oct 1, 2024 onwards: 0.03503% (₹35.03 per lakh of premium value) flat uniform rate.
     Source: NSE Circular Ref No: 5/2024, Download Ref No: NSE/FA/64232 (Dated Sep 27, 2024, Effective Oct 1, 2024),
     implementing SEBI Master Circular on True-to-Label charges (SEBI/HO/MRD/TPD-1/P/CIR/2024/92).

3. SEBI Turnover Charges:
   - ₹10 per crore (0.0001% or 0.000001 per rupee of turnover) on both buy and sell.

4. Stamp Duty (Indian Stamp Act):
   - 0.003% (0.00003) on BUY-side premium turnover only.

5. Goods and Services Tax (GST):
   - 18% on (Brokerage + Exchange Transaction Charges + SEBI Turnover Charges).

6. Brokerage (5paisa):
   - Standard tariff: ₹20 per executed order.
   - Account-specific tariff: Configurable per broker agreement.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SingleLegCostBreakdown:
    """Detailed line-item cost attribution for a single options order leg."""

    side: str
    brokerage: float
    stt: float
    exchange_charges: float
    gst: float
    sebi_charges: float
    stamp_duty: float
    slippage_cost: float
    total_charges: float


@dataclass(frozen=True)
class OptionTradeCostBreakdown:
    """Detailed line-item cost attribution for an options round-trip trade."""

    brokerage: float
    stt: float
    exchange_charges: float
    gst: float
    sebi_charges: float
    stamp_duty: float
    slippage_cost: float
    total_transaction_costs: float
    exercise_stt: float = 0.0


class IndianOptionsCostCalculator:
    """Calculates time-versioned statutory taxes and 5paisa brokerage for NSE options."""

    # Key regulatory dates
    DATE_OCT_2024_STT_HIKE = date(2024, 10, 1)
    DATE_OCT_2024_NSE_REVISION = date(2024, 10, 1)
    DATE_APR_2026_STT_HIKE = date(2026, 4, 1)

    def __init__(
        self,
        brokerage_per_order: float = 20.0,
        slippage_points_per_unit: float = 0.50,
        sebi_turnover_rate: float = 0.000001,  # ₹10 per crore (0.0001%)
        stamp_duty_rate: float = 0.00003,  # 0.003% on BUY premium only
        gst_rate: float = 0.18,  # 18% on (brokerage + exchange + sebi)
        custom_exchange_rate: float | None = None,
    ) -> None:
        self.brokerage_per_order = brokerage_per_order
        self.slippage_points_per_unit = slippage_points_per_unit
        self.sebi_turnover_rate = sebi_turnover_rate
        self.stamp_duty_rate = stamp_duty_rate
        self.gst_rate = gst_rate
        self.custom_exchange_rate = custom_exchange_rate

    @classmethod
    def get_stt_rate(cls, trade_date: date) -> float:
        """Return the time-versioned STT rate on options premium sell side.

        Regulatory schedule:
        - Prior to 1-Oct-2024: 0.0625% (0.000625)
        - 1-Oct-2024 to 31-Mar-2026: 0.1000% (0.001000) under Union Budget 2024
        - 1-Apr-2026 onwards: 0.1500% (0.001500) under Union Budget 2026
        """
        if trade_date >= cls.DATE_APR_2026_STT_HIKE:
            return 0.001500
        elif trade_date >= cls.DATE_OCT_2024_STT_HIKE:
            return 0.001000
        return 0.000625

    @classmethod
    def get_exercise_stt_rate(cls, trade_date: date) -> float:
        """Return the time-versioned STT rate applicable on exercised ITM options."""
        if trade_date >= cls.DATE_APR_2026_STT_HIKE:
            return 0.001500
        return 0.001250

    def get_exchange_turnover_rate(self, trade_date: date) -> float:
        """Return the time-versioned NSE Equity Options transaction charge rate.

        Regulatory schedule:
        - Prior to 1-Oct-2024: 0.0505% (0.000505)
        - 1-Oct-2024 onwards: 0.03503% (0.0003503) (NSE Circular Ref 5/2024, Ref No NSE/FA/64232)
        """
        if self.custom_exchange_rate is not None:
            return self.custom_exchange_rate
        if trade_date >= self.DATE_OCT_2024_NSE_REVISION:
            return 0.0003503
        return 0.000505

    def calculate_costs(
        self,
        trade_date: date,
        entry_premium: float,
        exit_premium: float,
        quantity: int,
        apply_slippage: bool = True,
        is_exercised: bool = False,
        settlement_price: float | None = None,
        strike_price: float | None = None,
    ) -> OptionTradeCostBreakdown:
        """Calculate complete round-trip transaction costs for a long option position.

        Supports time-versioned statutory taxes, account-specific brokerage,
        regulatory contract-note rounding, and separate exercise STT.
        """
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")

        buy_turnover = entry_premium * quantity
        sell_turnover = exit_premium * quantity
        total_turnover = buy_turnover + sell_turnover

        # 1. Brokerage: Account-specific flat fee per executed order
        brokerage = round(self.brokerage_per_order * 2.0, 2)

        # 2. STT: Charged on SELL side premium (or exercise settlement if exercised)
        exercise_stt = 0.0
        if is_exercised and settlement_price is not None and strike_price is not None:
            intrinsic_value = max(0.0, settlement_price - strike_price)
            exercise_stt_rate = self.get_exercise_stt_rate(trade_date)
            exercise_stt = round(intrinsic_value * quantity * exercise_stt_rate, 2)
            stt = exercise_stt
        else:
            stt_rate = self.get_stt_rate(trade_date)
            stt = round(sell_turnover * stt_rate, 2)

        # 3. Exchange turnover charges: Time-versioned on both buy and sell premium
        exchange_rate = self.get_exchange_turnover_rate(trade_date)
        exchange_charges = round(total_turnover * exchange_rate, 2)

        # 4. Stamp duty: Charged on BUY side premium only
        stamp_duty = round(buy_turnover * self.stamp_duty_rate, 2)

        # 5. SEBI turnover charges: ₹10 per crore on both buy and sell
        sebi_charges = round(total_turnover * self.sebi_turnover_rate, 2)

        # 6. GST: 18% on (Brokerage + Exchange Charges + SEBI Charges)
        taxable_services = brokerage + exchange_charges + sebi_charges
        gst = round(taxable_services * self.gst_rate, 2)

        # 7. Slippage cost (entry + exit impact)
        slippage_cost = 0.0
        if apply_slippage:
            slippage_cost = round((self.slippage_points_per_unit * 2.0) * quantity, 2)

        total_costs = round(
            brokerage + stt + exchange_charges + gst + sebi_charges + stamp_duty + slippage_cost,
            2,
        )
        return OptionTradeCostBreakdown(
            brokerage=brokerage,
            stt=stt,
            exchange_charges=exchange_charges,
            gst=gst,
            sebi_charges=sebi_charges,
            stamp_duty=stamp_duty,
            slippage_cost=slippage_cost,
            total_transaction_costs=total_costs,
            exercise_stt=exercise_stt,
        )

    def calculate_single_leg_costs(
        self,
        trade_date: date,
        side: str,
        premium: float,
        quantity: int,
        apply_slippage: bool = True,
    ) -> SingleLegCostBreakdown:
        """Calculate line-item transaction charges for a single order leg (BUY or SELL)."""
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {quantity}")

        turnover = premium * quantity
        brokerage = round(self.brokerage_per_order, 2)
        exchange_rate = self.get_exchange_turnover_rate(trade_date)
        exchange_charges = round(turnover * exchange_rate, 2)
        sebi_charges = round(turnover * self.sebi_turnover_rate, 2)
        taxable_services = brokerage + exchange_charges + sebi_charges
        gst = round(taxable_services * self.gst_rate, 2)

        side_upper = side.upper()
        if side_upper == "BUY":
            stamp_duty = round(turnover * self.stamp_duty_rate, 2)
            stt = 0.0
        elif side_upper == "SELL":
            stamp_duty = 0.0
            stt_rate = self.get_stt_rate(trade_date)
            stt = round(turnover * stt_rate, 2)
        else:
            stamp_duty = 0.0
            stt = 0.0

        slippage_cost = 0.0
        if apply_slippage:
            slippage_cost = round(self.slippage_points_per_unit * quantity, 2)

        total_charges = round(
            brokerage + stt + exchange_charges + gst + sebi_charges + stamp_duty + slippage_cost,
            2,
        )

        return SingleLegCostBreakdown(
            side=side_upper,
            brokerage=brokerage,
            stt=stt,
            exchange_charges=exchange_charges,
            gst=gst,
            sebi_charges=sebi_charges,
            stamp_duty=stamp_duty,
            slippage_cost=slippage_cost,
            total_charges=total_charges,
        )
