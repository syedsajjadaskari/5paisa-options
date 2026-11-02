"""Shared SYNTHETIC fixtures for the tests/live/test_core_*.py files (not market data)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from trading_platform.live.config import LiveConfig, load_live_config
from trading_platform.live.models import (
    IST,
    ContractSpec,
    Direction,
    OptionQuote,
    SignalRecord,
)

TRADE_DATE = date(2026, 10, 28)  # a Wednesday, NSE trading day (SYNTHETIC scenario date)
EXPIRY_NEAR = date(2026, 11, 3)
EXPIRY_NEXT = date(2026, 11, 10)
EXPIRY_PAST = date(2026, 10, 27)


def at(hh: int, mm: int, ss: int = 0, micro: int = 0, day: date = TRADE_DATE) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, micro, tzinfo=IST)


def cfg() -> LiveConfig:
    return load_live_config()


def with_exec(c: LiveConfig, **kw) -> LiveConfig:
    return replace(c, execution=replace(c.execution, **kw))


def with_account(c: LiveConfig, **kw) -> LiveConfig:
    return replace(c, account=replace(c.account, **kw))


def make_chain(
    expiry: date,
    strikes: list[float],
    lot_size: int,
    code_base: int,
    types: tuple[str, ...] = ("CE", "PE"),
) -> list[ContractSpec]:
    out: list[ContractSpec] = []
    for i, k in enumerate(strikes):
        for j, t in enumerate(types):
            out.append(
                ContractSpec(
                    scrip_code=code_base + i * 10 + j,
                    symbol=f"NIFTY {expiry:%d %b %Y} {t} {k:.2f}",
                    expiry=expiry,
                    strike=float(k),
                    option_type=t,
                    lot_size=lot_size,
                )
            )
    return out


def grid(lo: int = 25700, hi: int = 26100, step: int = 50) -> list[float]:
    return [float(k) for k in range(lo, hi + 1, step)]


def standard_contracts() -> list[ContractSpec]:
    """Near expiry lot 75, next expiry lot 65 (SYNTHETIC lot sizes, only to prove they come from the rows)."""
    return (
        make_chain(EXPIRY_PAST, grid(), 75, 1000)
        + make_chain(EXPIRY_NEAR, grid(), 75, 2000)
        + make_chain(EXPIRY_NEXT, grid(), 65, 3000)
    )


def make_contract(
    lot_size: int = 75, option_type: str = "CE", strike: float = 25850.0
) -> ContractSpec:
    return ContractSpec(
        scrip_code=555,
        symbol=f"NIFTY 03 Nov 2026 {option_type} {strike:.2f}",
        expiry=EXPIRY_NEAR,
        strike=strike,
        option_type=option_type,
        lot_size=lot_size,
    )


def make_signal(candle_open: datetime, strategy: str = "PDS") -> SignalRecord:
    return SignalRecord(
        signal_id=f"{strategy}-METHOD_1-{candle_open:%Y%m%dT%H%M}",
        strategy=strategy,
        method="METHOD_1",
        direction=Direction.BEARISH,
        candle_open_time=candle_open,
        decision_time=candle_open + timedelta(minutes=5),
        spot_reference=25850.0,
        stop_spot=25890.0,
        target_spot=25780.0,
        reason="SYNTHETIC test signal",
    )


def make_quote(
    received_at: datetime,
    bid: float = 99.0,
    ask: float = 100.0,
    bid_qty: int = 1000,
    ask_qty: int = 1000,
    scrip_code: int = 555,
) -> OptionQuote:
    return OptionQuote(
        scrip_code=scrip_code,
        symbol="NIFTY SYNTHETIC",
        bid=bid,
        ask=ask,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        server_time=received_at,
        received_at=received_at,
    )
