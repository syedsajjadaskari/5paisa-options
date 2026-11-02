"""SYNTHETIC builders shared by the test_persist_* tests (no I/O, no network)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from trading_platform.live.checkpoint import Checkpoint, position_to_dict
from trading_platform.live.models import (
    IST,
    ContractSpec,
    Direction,
    PositionStatus,
    SignalRecord,
    TradeRecord,
    VirtualPosition,
)

START = 250000.0
DAY = date(2026, 10, 27)


def make_trade(tid: str = "T1", net: float = 100.0, balance: float | None = None,
               day: date = DAY, **over: Any) -> TradeRecord:  # fmt: skip
    t0 = datetime(day.year, day.month, day.day, 10, 0, tzinfo=IST)
    fields: dict[str, Any] = dict(
        trade_id=tid, session_date=day, session_id="S1", label="SYNTHETIC_TEST", strategy="PDS",
        method="METHOD_1", direction="BEARISH", option_type="PE",
        symbol="NIFTY 03 Nov 2026 PE 25900.00", expiry=date(2026, 11, 3), strike=25900.0,
        scrip_code=12345, lot_size=75, lots=1, quantity=75, signal_time=t0, entry_time=t0,
        exit_time=t0.replace(hour=11), entry_spot=25900.0, exit_spot=25850.0, stop_spot=25950.0,
        target_spot=25800.0, entry_bid=100.0, entry_ask=101.0, entry_premium=101.5,
        exit_bid=110.0, exit_premium=109.5, entry_slippage=0.5, exit_slippage=0.5,
        gross_pnl=net + 50.0, entry_charges=25.0, exit_charges=25.0, total_charges=50.0,
        net_pnl=net, exit_reason="TARGET", entry_reason="multi\nline, with comma",
        entry_conditions_json='{"a": 1}', levels_json="{}", trigger_candle_json="{}",
        exit_detail_json="{}", balance_after=START + net if balance is None else balance,
    )  # fmt: skip
    fields.update(over)
    return TradeRecord(**fields)


def make_position(tid: str = "P1", status: PositionStatus = PositionStatus.OPEN,
                  day: date = DAY) -> VirtualPosition:  # fmt: skip
    t0 = datetime(day.year, day.month, day.day, 10, 5, tzinfo=IST)
    sig = SignalRecord(
        signal_id=f"PDS-METHOD_1-{day:%Y%m%d}T1000", strategy="PDS", method="METHOD_1",
        direction=Direction.BEARISH, candle_open_time=t0.replace(minute=0), decision_time=t0,
        spot_reference=25900.0, stop_spot=25950.0, target_spot=25800.0, reason="synthetic",
        conditions={"x": True},
    )  # fmt: skip
    con = ContractSpec(12345, "NIFTY 03 Nov 2026 PE 25900.00", date(2026, 11, 3), 25900.0, "PE", 75)
    return VirtualPosition(
        trade_id=tid, signal=sig, contract=con, status=status, lots=1, quantity=75,
        entry_time=t0, entry_spot=25900.0, entry_bid=100.0, entry_ask=101.0, entry_slippage=0.5,
        entry_premium=101.5, entry_charges=25.0, premium_invested=7612.5, stop_spot=25950.0,
        target_spot=25800.0, label="SYNTHETIC_TEST",
    )  # fmt: skip


def flat_account(realized: float = 0.0, cash: float | None = None) -> dict[str, Any]:
    return {
        "starting_capital": START, "cash": START + realized if cash is None else cash,
        "realized_pnl": realized, "session_open_equity": START, "session_realized_pnl": 0.0,
        "unrealized_pnl": 0.0, "invested": 0.0, "trades_today": 0, "wins_today": 0,
        "losses_today": 0,
    }  # fmt: skip


def make_cp(day: date = DAY, position: VirtualPosition | None = None,
            account: dict[str, Any] | None = None, **over: Any) -> Checkpoint:  # fmt: skip
    cp = Checkpoint(
        session_id="S1", session_date=day.isoformat(), run_mode="LIVE", label="SYNTHETIC_TEST",
        account=account if account is not None else flat_account(),
        position=position_to_dict(position) if position else None,
    )  # fmt: skip
    for k, v in over.items():
        setattr(cp, k, v)
    return cp


def row_of(trade: TradeRecord) -> dict[str, str]:
    return {k: str(v) for k, v in trade.to_row().items()}
