"""Unified Dual Backtesting Engine for Indian Index Strategies.

Architecture:
1. UnderlyingBacktestEngine:
   - Simulates pure NIFTY index point-based strategy rules.
   - Emits underlying trade journals with exact candle, entry reason, points gained/lost.
   - Independent of option premium noise or missing contracts.

2. OptionsBacktestEngine:
   - Connects verified underlying signals to genuine historical option chains.
   - Implements contract selection, executable next-bar fills, three exit modes,
     time-versioned transaction costs, and portfolio risk management.
   - Enforces the Data Availability Gate: Strictly rejects trades when genuine historical
     options prices are absent (Zero fake prices or Black-Scholes substitution).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from trading_platform.backtesting.indian_options_costs import IndianOptionsCostCalculator
from trading_platform.backtesting.option_execution import OptionExecutionSimulator
from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar, Signal
from trading_platform.data.contract_archive import ContractArchive
from trading_platform.data.option_chain import OptionBar
from trading_platform.execution.options_risk_manager import OptionsRiskManager, OptionsRiskPolicy
from trading_platform.options.contract_selector import ContractSelector
from trading_platform.options.moneyness import Moneyness
from trading_platform.options.pnl import OptionTradeResult
from trading_platform.options.premium_exits import ExitEvaluator, ExitMode
from trading_platform.strategies.base import BaseStrategy, StrategyContext


@dataclass(frozen=True)
class UnderlyingTradeRecord:
    """Detailed record of an executed underlying NIFTY index trade."""

    trade_id: str
    date: date
    strategy_name: str
    direction: SignalDirection
    method: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    points_pnl: float
    is_win: bool
    target: float
    stop_loss: float
    is_uncertain_fill: bool = False


@dataclass
class UnderlyingBacktestReport:
    """Comprehensive performance summary for underlying strategy backtest."""

    strategy_name: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    total_points: float
    profit_factor: float
    max_drawdown_points: float
    avg_points_per_trade: float
    trades: list[UnderlyingTradeRecord] = field(default_factory=list)


class UnderlyingBacktestEngine:
    """Backtests pure NIFTY 50 index point-based strategies without lookahead bias."""

    def __init__(self, slippage_points: float = 0.50, eod_exit_time: time = time(15, 15)) -> None:
        self.slippage_points = slippage_points
        self.eod_exit_time = eod_exit_time

    def run(
        self,
        strategy: BaseStrategy,
        bars: list[Bar],
        day_context_states: dict[str, dict[str, Any]] | None = None,
    ) -> UnderlyingBacktestReport:
        """Execute underlying backtest over a sequence of 5-minute historical bars."""
        if not bars:
            return UnderlyingBacktestReport(
                strategy_name=strategy.name,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                total_points=0.0,
                profit_factor=0.0,
                max_drawdown_points=0.0,
                avg_points_per_trade=0.0,
                trades=[],
            )

        strategy.reset_state()
        context = StrategyContext()
        trades: list[UnderlyingTradeRecord] = []
        trade_counter = 1

        active_trade: dict[str, Any] | None = None
        pending_signal: Signal | None = None

        day_states = day_context_states or {}

        # Automatically calculate point-in-time reference levels
        from trading_platform.data.reference_levels import ReferenceLevelCalculator

        calculator = ReferenceLevelCalculator()
        enriched_contexts = calculator.enrich_bars_with_context(bars)
        enriched_map = {ctx.bar.exchange_time: ctx for ctx in enriched_contexts}

        for bar in bars:
            day_str = bar.exchange_time.date().isoformat()

            # Automatically populate context state from point-in-time calculations
            bar_ctx = enriched_map.get(bar.exchange_time)
            if bar_ctx is not None:
                s_lev = bar_ctx.session_levels
                if s_lev.pdh is not None:
                    context.state["pdh"] = s_lev.pdh
                if s_lev.pdl is not None:
                    context.state["pdl"] = s_lev.pdl
                if s_lev.pdc is not None:
                    context.state["prev_close"] = s_lev.pdc
                if s_lev.pd_res_body is not None:
                    context.state["pd_res_body"] = s_lev.pd_res_body
                if s_lev.pd_supp_body is not None:
                    context.state["pd_supp_body"] = s_lev.pd_supp_body
                if s_lev.dma_200 is not None:
                    context.state["dma_200"] = s_lev.dma_200
                context.state["has_sufficient_dma_warmup"] = s_lev.has_sufficient_dma_warmup
                if bar_ctx.ema_200 is not None:
                    context.state["ema_200"] = bar_ctx.ema_200
                context.state["has_sufficient_ema_warmup"] = bar_ctx.has_sufficient_ema_warmup
                if bar_ctx.recent_swing_high is not None:
                    context.state["recent_swing_high"] = bar_ctx.recent_swing_high
                if bar_ctx.recent_swing_low is not None:
                    context.state["recent_swing_low"] = bar_ctx.recent_swing_low

            # Manual day_states overrides take precedence if provided
            if day_str in day_states:
                context.state.update(day_states[day_str])

            # 1. If we have a pending signal confirmed at previous bar close, enter at this bar's Open
            if pending_signal is not None and active_trade is None:
                if pending_signal.direction == SignalDirection.BUY:
                    entry_p = round(bar.open + self.slippage_points, 2)
                else:
                    entry_p = round(bar.open - self.slippage_points, 2)

                active_trade = {
                    "trade_id": f"{strategy.name}_TR_{trade_counter:04d}",
                    "signal": pending_signal,
                    "entry_time": bar.exchange_time,
                    "entry_price": entry_p,
                    "date": bar.exchange_time.date(),
                    "target": pending_signal.target,
                    "stop_loss": pending_signal.stop_loss,
                    "direction": pending_signal.direction,
                    "method": pending_signal.metadata.get("method", "DEFAULT"),
                }
                trade_counter += 1
                pending_signal = None

            # 2. If we have an active position, evaluate exits on current bar
            if active_trade is not None:
                exit_dec = self._evaluate_underlying_exit(active_trade, bar)
                if exit_dec["should_exit"]:
                    exit_price = exit_dec["exit_price"]
                    direction = active_trade["direction"]

                    if direction == SignalDirection.BUY:
                        pts = round(exit_price - active_trade["entry_price"], 2)
                    else:
                        pts = round(active_trade["entry_price"] - exit_price, 2)

                    record = UnderlyingTradeRecord(
                        trade_id=active_trade["trade_id"],
                        date=active_trade["date"],
                        strategy_name=strategy.name,
                        direction=direction,
                        method=active_trade["method"],
                        entry_time=active_trade["entry_time"],
                        entry_price=active_trade["entry_price"],
                        exit_time=bar.exchange_time,
                        exit_price=exit_price,
                        exit_reason=exit_dec["reason"],
                        points_pnl=pts,
                        is_win=pts > 0,
                        target=active_trade["target"],
                        stop_loss=active_trade["stop_loss"],
                        is_uncertain_fill=exit_dec.get("is_uncertain", False),
                    )
                    trades.append(record)
                    active_trade = None

            # 3. Process bar through strategy to see if a new signal confirms at this bar's close
            if active_trade is None and pending_signal is None:
                sig = strategy.on_bar(context, bar)
                if sig is not None and sig.direction != SignalDirection.HOLD:
                    pending_signal = sig
            else:
                context.add_bar(bar)

        return self._generate_report(strategy.name, trades)

    def _evaluate_underlying_exit(self, active_trade: dict[str, Any], bar: Bar) -> dict[str, Any]:
        """Evaluate target, stop loss, or EOD exit for underlying position."""
        target = active_trade["target"]
        stop = active_trade["stop_loss"]
        direction = active_trade["direction"]

        # EOD Check (15:15 IST)
        if bar.exchange_time.time() >= self.eod_exit_time:
            exit_p = round(
                bar.open - self.slippage_points
                if direction == SignalDirection.BUY
                else bar.open + self.slippage_points,
                2,
            )
            return {"should_exit": True, "exit_price": exit_p, "reason": "EOD_SQUAREOFF"}

        if direction == SignalDirection.BUY:
            hit_sl = bar.low <= stop
            hit_tp = bar.high >= target

            if hit_sl and hit_tp:
                # Same-bar conflict: conservative worst-case SL priority
                exit_p = round(min(bar.open, stop) - self.slippage_points, 2)
                return {
                    "should_exit": True,
                    "exit_price": exit_p,
                    "reason": "INTRABAR_SL_CONFLICT",
                    "is_uncertain": True,
                }
            elif hit_sl:
                if bar.open <= stop:
                    exit_p = round(bar.open - self.slippage_points, 2)  # Gap through stop
                else:
                    exit_p = round(stop - self.slippage_points, 2)
                return {"should_exit": True, "exit_price": exit_p, "reason": "STOP_LOSS"}
            elif hit_tp:
                exit_p = round(target, 2)
                return {"should_exit": True, "exit_price": exit_p, "reason": "TARGET"}

        elif direction == SignalDirection.SELL:
            hit_sl = bar.high >= stop
            hit_tp = bar.low <= target

            if hit_sl and hit_tp:
                exit_p = round(max(bar.open, stop) + self.slippage_points, 2)
                return {
                    "should_exit": True,
                    "exit_price": exit_p,
                    "reason": "INTRABAR_SL_CONFLICT",
                    "is_uncertain": True,
                }
            elif hit_sl:
                if bar.open >= stop:
                    exit_p = round(bar.open + self.slippage_points, 2)  # Gap through stop
                else:
                    exit_p = round(stop + self.slippage_points, 2)
                return {"should_exit": True, "exit_price": exit_p, "reason": "STOP_LOSS"}
            elif hit_tp:
                exit_p = round(target, 2)
                return {"should_exit": True, "exit_price": exit_p, "reason": "TARGET"}

        return {"should_exit": False, "exit_price": 0.0, "reason": "HOLD"}

    def _generate_report(
        self, strategy_name: str, trades: list[UnderlyingTradeRecord]
    ) -> UnderlyingBacktestReport:
        """Compute performance statistics from trade list."""
        if not trades:
            return UnderlyingBacktestReport(
                strategy_name=strategy_name,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                total_points=0.0,
                profit_factor=0.0,
                max_drawdown_points=0.0,
                avg_points_per_trade=0.0,
                trades=[],
            )

        wins = [t for t in trades if t.is_win]
        losses = [t for t in trades if not t.is_win]

        gross_gains = sum(t.points_pnl for t in wins)
        gross_losses = abs(sum(t.points_pnl for t in losses))
        total_pts = round(sum(t.points_pnl for t in trades), 2)

        win_rate = round((len(wins) / len(trades)) * 100.0, 2)
        profit_factor = round(gross_gains / gross_losses, 2) if gross_losses > 0 else float("inf")

        # Drawdown calculation
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            cumulative += t.points_pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        avg_pts = round(total_pts / len(trades), 2)

        return UnderlyingBacktestReport(
            strategy_name=strategy_name,
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate_pct=win_rate,
            total_points=total_pts,
            profit_factor=profit_factor,
            max_drawdown_points=round(max_dd, 2),
            avg_points_per_trade=avg_pts,
            trades=trades,
        )


@dataclass
class OptionsBacktestReport:
    """Comprehensive performance summary for options backtest with data coverage."""

    strategy_name: str
    underlying_signals_count: int
    executable_options_trades_count: int
    data_coverage_pct: float
    gross_pnl: float
    total_costs: float
    net_pnl: float
    win_rate_pct: float
    profit_factor: float
    trades: list[OptionTradeResult] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)


class OptionsBacktestEngine:
    """Simulates realistic options trades matching underlying strategy signals."""

    def __init__(
        self,
        archive: ContractArchive | None = None,
        moneyness: Moneyness | str = Moneyness.ATM,
        exit_mode: ExitMode | str = ExitMode.UNDERLYING_BASED,
        premium_target_pct: float | None = 0.50,
        premium_stop_pct: float | None = 0.25,
        risk_policy: OptionsRiskPolicy | None = None,
        slippage_points: float = 0.50,
    ) -> None:
        self.archive = archive or ContractArchive()
        self.selector = ContractSelector(archive=self.archive, moneyness=moneyness)
        self.cost_calc = IndianOptionsCostCalculator(slippage_points_per_unit=slippage_points)
        self.risk_mgr = OptionsRiskManager(policy=risk_policy)
        self.simulator = OptionExecutionSimulator(
            cost_calculator=self.cost_calc,
            risk_manager=self.risk_mgr,
            slippage_points=slippage_points,
        )
        self.exit_evaluator = ExitEvaluator(
            mode=exit_mode,
            premium_target_pct=premium_target_pct,
            premium_stop_pct=premium_stop_pct,
        )
        self.premium_stop_pct = premium_stop_pct

    def run(
        self,
        signals: list[Signal],
        option_bars_by_contract: dict[str, list[OptionBar]],
        underlying_bars_by_time: dict[datetime, Bar],
    ) -> OptionsBacktestReport:
        """Execute options backtest strictly on available historical option candle datasets.

        Enforces Data Availability Gate: Missing data is logged and rejected; zero synthetic data used.
        """
        executed_trades: list[OptionTradeResult] = []
        rejections: list[str] = []

        for i, sig in enumerate(signals):
            # 1. Select Contract
            try:
                contract = self.selector.select_contract(
                    trading_date=sig.timestamp.date(),
                    spot_price=sig.reference_price,
                    signal_direction=sig.direction,
                )
            except Exception as e:
                rej = f"CONTRACT_SELECTION_FAILED: {e}"
                rejections.append(rej)
                continue

            contract_bars = option_bars_by_contract.get(contract.tradingsymbol, [])
            if not contract_bars:
                rej = f"MISSING_OPTION_DATA: No historical candles available for {contract.tradingsymbol}"
                rejections.append(rej)
                continue

            # Map option bars by timestamp
            bar_map = {b.exchange_time: b for b in contract_bars}

            # Locate next bar after signal
            sorted_times = sorted([t for t in bar_map.keys() if t >= sig.timestamp])
            if not sorted_times:
                rej = f"NO_NEXT_BAR: No candle at or after {sig.timestamp} for {contract.tradingsymbol}"
                rejections.append(rej)
                continue

            next_bar_time = sorted_times[0]
            entry_option_bar = bar_map[next_bar_time]

            # 2. Simulate Entry
            entry_price, size_res, reason = self.simulator.simulate_entry(
                signal=sig,
                contract=contract,
                next_option_bar=entry_option_bar,
                premium_stop_pct=self.premium_stop_pct,
            )

            if entry_price is None or not size_res.is_approved:
                rejections.append(reason)
                continue

            # 3. Simulate Forward Bars for Exit
            remaining_times = [t for t in sorted_times if t > next_bar_time]
            trade_exited = False
            mfe = entry_price
            mae = entry_price

            for t in remaining_times:
                opt_bar = bar_map[t]
                und_bar = underlying_bars_by_time.get(t)

                if und_bar is None:
                    continue

                mfe = max(mfe, opt_bar.high)
                mae = min(mae, opt_bar.low)

                exit_dec = self.exit_evaluator.evaluate_exit(
                    underlying_bar=und_bar,
                    option_bar=opt_bar,
                    entry_premium=entry_price,
                    signal_direction=sig.direction,
                    underlying_target=sig.target,
                    underlying_stop=sig.stop_loss,
                )

                if exit_dec.should_exit:
                    trade_res = self.simulator.simulate_exit(
                        exit_decision=exit_dec,
                        current_option_bar=opt_bar,
                        entry_price=entry_price,
                        quantity=size_res.quantity,
                        lots=size_res.lots,
                        contract=contract,
                        entry_time=next_bar_time,
                        trade_id=f"OPT_TR_{i + 1:04d}",
                        mfe_premium=mfe,
                        mae_premium=mae,
                    )
                    executed_trades.append(trade_res)
                    trade_exited = True
                    break

            if not trade_exited and remaining_times:
                # Force EOD close at final bar
                last_bar = bar_map[remaining_times[-1]]
                exit_dec = ExitEvaluator().evaluate_exit(
                    underlying_bar=underlying_bars_by_time.get(last_bar.exchange_time, last_bar),  # type: ignore
                    option_bar=last_bar,
                    entry_premium=entry_price,
                    signal_direction=sig.direction,
                    underlying_target=sig.target,
                    underlying_stop=sig.stop_loss,
                )
                trade_res = self.simulator.simulate_exit(
                    exit_decision=exit_dec,
                    current_option_bar=last_bar,
                    entry_price=entry_price,
                    quantity=size_res.quantity,
                    lots=size_res.lots,
                    contract=contract,
                    entry_time=next_bar_time,
                    trade_id=f"OPT_TR_{i + 1:04d}",
                    mfe_premium=mfe,
                    mae_premium=mae,
                )
                executed_trades.append(trade_res)

        # Performance summary
        total_signals = len(signals)
        executed_count = len(executed_trades)
        coverage_pct = (
            round((executed_count / total_signals * 100.0), 2) if total_signals > 0 else 0.0
        )

        gross = round(sum(t.gross_pnl for t in executed_trades), 2)
        costs = round(sum(t.costs.total_transaction_costs for t in executed_trades), 2)
        net = round(sum(t.net_pnl for t in executed_trades), 2)

        wins = [t for t in executed_trades if t.net_pnl > 0]
        losses = [t for t in executed_trades if t.net_pnl <= 0]
        win_rate = round(len(wins) / executed_count * 100.0, 2) if executed_count > 0 else 0.0

        gross_wins = sum(t.net_pnl for t in wins)
        gross_losses = abs(sum(t.net_pnl for t in losses))
        pf = round(gross_wins / gross_losses, 2) if gross_losses > 0 else float("inf")

        return OptionsBacktestReport(
            strategy_name="OPTIONS_COMBINED",
            underlying_signals_count=total_signals,
            executable_options_trades_count=executed_count,
            data_coverage_pct=coverage_pct,
            gross_pnl=gross,
            total_costs=costs,
            net_pnl=net,
            win_rate_pct=win_rate,
            profit_factor=pf,
            trades=executed_trades,
            rejection_reasons=rejections,
        )
