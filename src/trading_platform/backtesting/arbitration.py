"""Portfolio Arbitration Engine and Capacity Management.

Implements deterministic portfolio-level exposure, capital constraint policies,
and signal arbitration (FIFO single-position, priority ordering, multi-position)
without lookahead bias or retroactive selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from trading_platform.backtesting.engine import UnderlyingTradeRecord


class PortfolioArbitrationPolicy(StrEnum):
    """Supported portfolio arbitration policies."""

    UNARBITRATED = "UNARBITRATED"  # Parallel independent execution across all strategies
    FIFO_SINGLE_POSITION = "FIFO_SINGLE_POSITION"  # Strict 1-position limit; first signal wins
    PRIORITY_SINGLE_POSITION = (
        "PRIORITY_SINGLE_POSITION"  # Strict 1-position limit; predeclared priority wins
    )
    DIRECTIONAL_NETTING_MAX_2 = (
        "DIRECTIONAL_NETTING_MAX_2"  # Max 2 concurrent positions; opposing directions rejected
    )


@dataclass(frozen=True)
class ArbitrationDecisionRecord:
    """Record of a trade signal evaluated by the portfolio arbitrator."""

    trade_id: str
    strategy: str
    method: str
    direction: str
    date: str
    entry_time: str
    exit_time: str
    status: str  # ACCEPTED or REJECTED
    rejection_reason: str
    blocking_trade_id: str | None
    points_pnl: float
    is_win: bool


@dataclass
class PortfolioArbitrationReport:
    """Summary metrics of portfolio arbitration execution."""

    policy: PortfolioArbitrationPolicy
    total_signals_received: int
    accepted_trades_count: int
    rejected_signals_count: int
    accepted_trades: list[UnderlyingTradeRecord]
    decision_records: list[ArbitrationDecisionRecord]
    gross_index_points: float
    slippage_deducted: float
    net_index_points: float
    win_rate_pct: float
    profit_factor: float
    max_drawdown_points: float
    max_consecutive_losses: int
    avg_holding_time_minutes: float


class PortfolioArbitrator:
    """Arbitrates multiple incoming strategy signals under explicit capital and capacity limits."""

    # Predeclared canonical priority hierarchy (zero lookahead; defined before evaluation)
    CANONICAL_PRIORITY: dict[str, int] = {
        "PDS": 1,
        "ORB": 2,
        "IRF": 3,
        "PDR": 4,
        "RF": 5,
    }

    def __init__(
        self,
        policy: PortfolioArbitrationPolicy = PortfolioArbitrationPolicy.FIFO_SINGLE_POSITION,
        max_capacity: int = 1,
        strategy_priority: dict[str, int] | None = None,
    ) -> None:
        self.policy = policy
        self.max_capacity = max_capacity
        self.strategy_priority = strategy_priority or self.CANONICAL_PRIORITY

    def arbitrate(
        self, candidate_trades: list[UnderlyingTradeRecord]
    ) -> PortfolioArbitrationReport:
        """Arbitrate candidate trades chronologically according to the configured policy."""
        if not candidate_trades:
            return PortfolioArbitrationReport(
                policy=self.policy,
                total_signals_received=0,
                accepted_trades_count=0,
                rejected_signals_count=0,
                accepted_trades=[],
                decision_records=[],
                gross_index_points=0.0,
                slippage_deducted=0.0,
                net_index_points=0.0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                max_drawdown_points=0.0,
                max_consecutive_losses=0,
                avg_holding_time_minutes=0.0,
            )

        # 1. Deterministic Tie-Breaking Sort
        # Secondary sort key: Predeclared Lexicographical / Strategy Priority (no future performance)
        if self.policy == PortfolioArbitrationPolicy.PRIORITY_SINGLE_POSITION:
            sorted_candidates = sorted(
                candidate_trades,
                key=lambda t: (
                    t.date,
                    t.entry_time,
                    self.strategy_priority.get(t.strategy_name, 99),
                    t.trade_id,
                ),
            )
        else:
            # Deterministic Lexicographical Tie-Breaker: (entry_time, strategy_name, method, trade_id)
            sorted_candidates = sorted(
                candidate_trades,
                key=lambda t: (t.entry_time, t.strategy_name, t.method, t.trade_id),
            )

        accepted_trades: list[UnderlyingTradeRecord] = []
        decision_records: list[ArbitrationDecisionRecord] = []

        def _dir_str(d: Any) -> str:
            return d.value if hasattr(d, "value") else str(d)

        if self.policy == PortfolioArbitrationPolicy.UNARBITRATED:
            for t in sorted_candidates:
                accepted_trades.append(t)
                decision_records.append(
                    ArbitrationDecisionRecord(
                        trade_id=t.trade_id,
                        strategy=t.strategy_name,
                        method=t.method,
                        direction=_dir_str(t.direction),
                        date=t.date.isoformat(),
                        entry_time=t.entry_time.strftime("%H:%M"),
                        exit_time=t.exit_time.strftime("%H:%M"),
                        status="ACCEPTED",
                        rejection_reason="UNARBITRATED_PARALLEL_EXECUTION",
                        blocking_trade_id=None,
                        points_pnl=t.points_pnl,
                        is_win=t.is_win,
                    )
                )
            return self._compute_report(accepted_trades, decision_records)

        # 2. Strict Capacity Arbitration (FIFO or Priority)
        active_positions: list[UnderlyingTradeRecord] = []

        for candidate in sorted_candidates:
            # Purge positions that have closed at or before candidate entry_time
            active_positions = [
                pos for pos in active_positions if pos.exit_time > candidate.entry_time
            ]

            is_accepted = False
            rejection_reason = "ACCEPTED"
            blocking_id = None

            cand_dir_str = _dir_str(candidate.direction)

            if self.policy in (
                PortfolioArbitrationPolicy.FIFO_SINGLE_POSITION,
                PortfolioArbitrationPolicy.PRIORITY_SINGLE_POSITION,
            ):
                if len(active_positions) < self.max_capacity:
                    is_accepted = True
                    active_positions.append(candidate)
                else:
                    blocking_trade = active_positions[0]
                    blocking_id = blocking_trade.trade_id
                    rejection_reason = (
                        f"PORTFOLIO_CAPACITY_LIMIT_REACHED: Active position {blocking_id} "
                        f"({blocking_trade.strategy_name} {_dir_str(blocking_trade.direction)}) "
                        f"held until {blocking_trade.exit_time.strftime('%H:%M')} IST."
                    )

            elif self.policy == PortfolioArbitrationPolicy.DIRECTIONAL_NETTING_MAX_2:
                # Max 2 concurrent positions; reject opposing BUY/SELL conflicts
                has_opposing = any(
                    _dir_str(pos.direction) != cand_dir_str for pos in active_positions
                )
                if has_opposing:
                    blocking_trade = next(
                        pos for pos in active_positions if _dir_str(pos.direction) != cand_dir_str
                    )
                    blocking_id = blocking_trade.trade_id
                    rejection_reason = (
                        f"OPPOSING_DIRECTION_CONFLICT: Active position {blocking_id} is "
                        f"{_dir_str(blocking_trade.direction)}; candidate is {cand_dir_str}."
                    )
                elif len(active_positions) >= 2:
                    blocking_trade = active_positions[0]
                    blocking_id = blocking_trade.trade_id
                    rejection_reason = (
                        f"MAX_CONCURRENT_POSITIONS_REACHED (2): Active positions: "
                        f"{[p.trade_id for p in active_positions]}."
                    )
                else:
                    is_accepted = True
                    active_positions.append(candidate)

            if is_accepted:
                accepted_trades.append(candidate)
                decision_records.append(
                    ArbitrationDecisionRecord(
                        trade_id=candidate.trade_id,
                        strategy=candidate.strategy_name,
                        method=candidate.method,
                        direction=cand_dir_str,
                        date=candidate.date.isoformat(),
                        entry_time=candidate.entry_time.strftime("%H:%M"),
                        exit_time=candidate.exit_time.strftime("%H:%M"),
                        status="ACCEPTED",
                        rejection_reason="CAPACITY_AVAILABLE",
                        blocking_trade_id=None,
                        points_pnl=candidate.points_pnl,
                        is_win=candidate.is_win,
                    )
                )
            else:
                decision_records.append(
                    ArbitrationDecisionRecord(
                        trade_id=candidate.trade_id,
                        strategy=candidate.strategy_name,
                        method=candidate.method,
                        direction=cand_dir_str,
                        date=candidate.date.isoformat(),
                        entry_time=candidate.entry_time.strftime("%H:%M"),
                        exit_time=candidate.exit_time.strftime("%H:%M"),
                        status="REJECTED",
                        rejection_reason=rejection_reason,
                        blocking_trade_id=blocking_id,
                        points_pnl=candidate.points_pnl,
                        is_win=candidate.is_win,
                    )
                )

        return self._compute_report(accepted_trades, decision_records)

    def _compute_report(
        self,
        accepted: list[UnderlyingTradeRecord],
        records: list[ArbitrationDecisionRecord],
    ) -> PortfolioArbitrationReport:
        """Compute institutional summary metrics on the accepted portfolio trades."""
        if not accepted:
            return PortfolioArbitrationReport(
                policy=self.policy,
                total_signals_received=len(records),
                accepted_trades_count=0,
                rejected_signals_count=len(records),
                accepted_trades=[],
                decision_records=records,
                gross_index_points=0.0,
                slippage_deducted=0.0,
                net_index_points=0.0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                max_drawdown_points=0.0,
                max_consecutive_losses=0,
                avg_holding_time_minutes=0.0,
            )

        wins = [t for t in accepted if t.is_win]
        losses = [t for t in accepted if not t.is_win]
        wr = (len(wins) / len(accepted)) * 100.0

        net_pts = sum(t.points_pnl for t in accepted)
        slip = len(accepted) * 1.00  # 0.50 entry + 0.50 exit
        gross_pts = net_pts + slip

        g_wins = sum(t.points_pnl for t in wins)
        g_losses = abs(sum(t.points_pnl for t in losses))
        pf = (g_wins / g_losses) if g_losses > 0 else (99.0 if g_wins > 0 else 0.0)

        # Drawdown and consecutive losses
        max_dd = 0.0
        cum = 0.0
        peak = 0.0
        max_cl = 0
        curr_cl = 0
        hold_times = []

        for t in accepted:
            cum += t.points_pnl
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

            if not t.is_win:
                curr_cl += 1
                if curr_cl > max_cl:
                    max_cl = curr_cl
            else:
                curr_cl = 0

            if isinstance(t.exit_time, time) and isinstance(t.entry_time, time):
                mins = (
                    datetime.combine(date.min, t.exit_time)
                    - datetime.combine(date.min, t.entry_time)
                ).total_seconds() / 60.0
                hold_times.append(mins)
            elif isinstance(t.exit_time, datetime) and isinstance(t.entry_time, datetime):
                hold_times.append((t.exit_time - t.entry_time).total_seconds() / 60.0)
            else:
                hold_times.append(0.0)

        return PortfolioArbitrationReport(
            policy=self.policy,
            total_signals_received=len(records),
            accepted_trades_count=len(accepted),
            rejected_signals_count=len(records) - len(accepted),
            accepted_trades=accepted,
            decision_records=records,
            gross_index_points=round(gross_pts, 2),
            slippage_deducted=round(slip, 2),
            net_index_points=round(net_pts, 2),
            win_rate_pct=round(wr, 2),
            profit_factor=round(pf, 2),
            max_drawdown_points=round(max_dd, 2),
            max_consecutive_losses=max_cl,
            avg_holding_time_minutes=(
                round(sum(hold_times) / len(hold_times), 1) if hold_times else 0.0
            ),
        )
