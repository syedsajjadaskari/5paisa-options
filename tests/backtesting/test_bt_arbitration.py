"""Post-hoc arbitrator tests: equality boundary, explicit tie-break, actual slippage (SYNTHETIC)."""

from __future__ import annotations

from datetime import datetime

from test_bt_support import at

from trading_platform.backtesting.arbitration import (
    PortfolioArbitrationPolicy,
    PortfolioArbitrator,
)
from trading_platform.backtesting.constants import PORTFOLIO_PRIORITY
from trading_platform.backtesting.engine import UnderlyingTradeRecord
from trading_platform.core.enums import SignalDirection


def _trade(tid: str, strategy: str, entry: datetime, exit_: datetime, pnl: float = 10.0):
    return UnderlyingTradeRecord(
        trade_id=tid,
        date=entry.date(),
        strategy_name=strategy,
        direction=SignalDirection.BUY,
        method="SYNTH",
        entry_time=entry,
        entry_price=100.0,
        exit_time=exit_,
        exit_price=100.0 + pnl,
        exit_reason="TARGET",
        points_pnl=pnl,
        is_win=pnl > 0,
        target=160.0,
        stop_loss=70.0,
    )


def test_canonical_priority_is_the_explicit_list():
    assert tuple(PORTFOLIO_PRIORITY) == ("PDS", "ORB", "IRF", "PDR")
    prio = PortfolioArbitrator.CANONICAL_PRIORITY
    assert [n for n, _ in sorted(prio.items(), key=lambda kv: kv[1])][:4] == list(PORTFOLIO_PRIORITY)
    assert prio["RF"] > prio["PDR"]


def test_fifo_tie_break_follows_explicit_priority_not_alphabetical():
    t = at(10, 0)
    cands = [
        _trade("IRF_1", "IRF", t, at(10, 30)),
        _trade("ORB_1", "ORB", t, at(10, 30)),
        _trade("PDR_1", "PDR", t, at(10, 30)),
        _trade("PDS_1", "PDS", t, at(10, 30)),
    ]
    report = PortfolioArbitrator(PortfolioArbitrationPolicy.FIFO_SINGLE_POSITION).arbitrate(cands)
    assert [x.trade_id for x in report.accepted_trades] == ["PDS_1"]  # alphabetical would be IRF
    rejected = {r.trade_id for r in report.decision_records if r.status == "REJECTED"}
    assert rejected == {"IRF_1", "ORB_1", "PDR_1"}


def test_fifo_prefers_earlier_entry_over_priority():
    cands = [
        _trade("PDR_1", "PDR", at(10, 0), at(10, 30)),
        _trade("PDS_1", "PDS", at(10, 5), at(10, 40)),
    ]
    report = PortfolioArbitrator().arbitrate(cands)
    assert [x.trade_id for x in report.accepted_trades] == ["PDR_1"]


def test_equality_boundary_entry_on_exit_stamp_is_rejected_strictly_later_is_accepted():
    first = _trade("A", "ORB", at(9, 30), at(9, 40))
    on_exit = _trade("B", "PDS", at(9, 40), at(10, 0))  # exit_time == entry_time -> reject (>=)
    after = _trade("C", "IRF", at(9, 45), at(10, 0))  # strictly after A's exit -> accept
    report = PortfolioArbitrator().arbitrate([first, on_exit, after])
    status = {r.trade_id: r.status for r in report.decision_records}
    assert status == {"A": "ACCEPTED", "B": "REJECTED", "C": "ACCEPTED"}
    blocked = next(r for r in report.decision_records if r.trade_id == "B")
    assert blocked.blocking_trade_id == "A"


def test_zero_duration_trade_blocks_a_second_entry_in_the_same_bar():
    zero = _trade("A", "PDS", at(9, 30), at(9, 30), pnl=-30.5)
    other = _trade("B", "PDR", at(9, 30), at(10, 0))
    report = PortfolioArbitrator().arbitrate([other, zero])
    assert [t.trade_id for t in report.accepted_trades] == ["A"]


def test_slippage_deducted_is_computed_from_trades_not_assumed():
    t1 = _trade("A", "PDS", at(9, 30), at(9, 40))
    t2 = _trade("B", "ORB", at(10, 0), at(10, 10))
    report = PortfolioArbitrator().arbitrate([t1, t2])
    assert report.slippage_deducted == 2.0  # 2 trades x (0.5 entry + 0.5 exit)
    assert report.gross_index_points == report.net_index_points + 2.0
