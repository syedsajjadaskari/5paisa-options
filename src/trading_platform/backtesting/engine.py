"""Unified Dual Backtesting Engine for Indian Index Strategies.

Architecture:
1. UnderlyingBacktestEngine:
   - Simulates pure NIFTY index point-based strategy rules.
   - Emits underlying trade journals with exact candle, entry reason, points gained/lost.
   - Independent of option premium noise or missing contracts.
   - ONE simulation core (``simulate``) serves both a single strategy run (``run``) and the
     joint chronological portfolio run (backtesting.portfolio_runner), so standalone and
     combined results can never be produced by different execution logic.

2. OptionsBacktestEngine:
   - Connects verified underlying signals to genuine historical option chains.
   - Implements contract selection, executable next-bar fills, three exit modes,
     time-versioned transaction costs, and portfolio risk management.
   - Enforces the Data Availability Gate: Strictly rejects trades when genuine historical
     options prices are absent (Zero fake prices or Black-Scholes substitution).

Execution conventions of the underlying engine (engine version ``V3_PDF_CORRECTED``):

* Bars are BAR-OPEN stamped.  A signal is produced on the CLOSE of a candle and filled at
  the NEXT bar's open (plus adverse slippage).  Stop/target distances are rebased to the
  actual fill.
* Slippage (default 0.5 pt) is paid on EVERY fill: entry, and every exit type (target, stop,
  gap-through-stop, same-bar conflict, 15:15 square-off, forced session-end close).
* Same-bar stop/target ambiguity is resolved against the trader (stop first, at the worse of
  open and stop).  A target that is gapped through still fills at the target (conservative).
* Entry cutoff 14:30 is applied to the CLOSE of the signal candle: a candle that closes at or
  after 14:30 cannot create a trade (its fill would be at/after 14:30).  Such signals are
  still recorded, as rejected with reason AFTER_ENTRY_CUTOFF.
* A position still open at a date change (no 15:15+ bar) or at the end of the data is closed
  at the last seen bar's close with slippage, reason FORCED_SESSION_END (flagged uncertain).
* Every bar is fed to every strategy, always (state machines never miss a bar, even while a
  position is open).  Signals are never used to change strategy state: a rejected signal is
  simply lost (no re-arm), because strategies hold their own once-per-day flags.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any

from trading_platform.backtesting.constants import (
    BAR_MINUTES,
    DEFAULT_SLIPPAGE_POINTS,
    ENTRY_CUTOFF_TIME,
    SQUARE_OFF_TIME,
)
from trading_platform.backtesting.indian_options_costs import IndianOptionsCostCalculator
from trading_platform.backtesting.metrics import compute_points_metrics
from trading_platform.backtesting.option_execution import OptionExecutionSimulator
from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar, Signal
from trading_platform.data.contract_archive import ContractArchive
from trading_platform.data.option_chain import OptionBar
from trading_platform.data.reference_levels import build_strategy_state
from trading_platform.execution.options_risk_manager import OptionsRiskManager, OptionsRiskPolicy
from trading_platform.options.contract_selector import ContractSelector
from trading_platform.options.moneyness import Moneyness
from trading_platform.options.pnl import OptionTradeResult
from trading_platform.options.premium_exits import ExitEvaluator, ExitMode
from trading_platform.strategies.base import BaseStrategy, StrategyContext


class SignalStatus(StrEnum):
    """Final disposition of an engine signal."""

    FILLED = "FILLED"
    REJECTED = "REJECTED"


class RejectionReason(StrEnum):
    """Why a signal did not become a trade."""

    OVERLAP_POSITION_OPEN = "OVERLAP_POSITION_OPEN"  # a position was open when it arrived
    AFTER_ENTRY_CUTOFF = "AFTER_ENTRY_CUTOFF"  # signal candle closes / entry bar opens >= 14:30
    LOST_TIE_BREAK = "LOST_TIE_BREAK"  # same candle, a higher-priority signal took the slot
    NO_ENTRY_BAR = "NO_ENTRY_BAR"  # no next bar in the same session to fill on
    DUPLICATE_SIGNAL = "DUPLICATE_SIGNAL"  # same signal_id already seen


def build_signal_id(strategy_name: str, method: str, signal_candle_open: datetime) -> str:
    """Deterministic id: strategy + method + OPEN time of the signal candle."""
    return f"{strategy_name}:{method}:{signal_candle_open.strftime('%Y%m%dT%H%M')}"


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
    #: id of the signal that created this trade (strategy:method:signal-candle-open-time).
    signal_id: str = ""
    #: OPEN time of the signal candle (the signal is produced on that candle's close).
    signal_time: datetime | None = None
    #: slippage paid on EACH side, index points (entry and exit both pay it).
    slippage_points: float = DEFAULT_SLIPPAGE_POINTS


@dataclass
class EngineSignalRecord:
    """One signal emitted by a strategy, with its final disposition in this simulation."""

    signal_id: str
    strategy_name: str
    method: str
    direction: str
    date: date
    signal_candle_open: datetime
    signal_candle_close: datetime
    reference_price: float
    stop_points: float
    target_points: float
    reason: str
    status: SignalStatus = SignalStatus.REJECTED
    rejection_reason: str = ""
    blocking_trade_id: str | None = None
    blocking_signal_id: str | None = None
    trade_id: str | None = None


@dataclass
class UnderlyingSimulationResult:
    """Raw output of ``UnderlyingBacktestEngine.simulate``."""

    signals: list[EngineSignalRecord] = field(default_factory=list)
    trades: list[UnderlyingTradeRecord] = field(default_factory=list)


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
    signals: list[EngineSignalRecord] = field(default_factory=list)


class UnderlyingBacktestEngine:
    """Backtests pure NIFTY 50 index point-based strategies without lookahead bias."""

    def __init__(
        self,
        slippage_points: float = DEFAULT_SLIPPAGE_POINTS,
        eod_exit_time: time = SQUARE_OFF_TIME,
        entry_cutoff_time: time = ENTRY_CUTOFF_TIME,
        trailing_stop_policy: str | None = None,
        bar_minutes: int = BAR_MINUTES,
    ) -> None:
        self.slippage_points = slippage_points
        self.eod_exit_time = eod_exit_time
        self.entry_cutoff_time = entry_cutoff_time
        self.trailing_stop_policy = trailing_stop_policy
        self.bar_minutes = bar_minutes

    # ------------------------------------------------------------------ public API

    def run(
        self,
        strategy: BaseStrategy,
        bars: list[Bar],
        day_context_states: dict[str, dict[str, Any]] | None = None,
        daily_history_bars: list[Bar] | None = None,
    ) -> UnderlyingBacktestReport:
        """Execute underlying backtest of ONE strategy over 5-minute historical bars."""
        result = self.simulate(
            [strategy],
            bars,
            day_context_states=day_context_states,
            daily_history_bars=daily_history_bars,
        )
        return self._generate_report(strategy.name, result.trades, result.signals)

    def simulate(
        self,
        strategies: Sequence[BaseStrategy],
        bars: list[Bar],
        day_context_states: dict[str, dict[str, Any]] | None = None,
        daily_history_bars: list[Bar] | None = None,
        enriched_contexts: dict[datetime, Any] | None = None,
        priority: Sequence[str] | None = None,
    ) -> UnderlyingSimulationResult:
        """Joint chronological simulation of one or more strategies sharing ONE position slot.

        ``strategies`` are all fed every bar.  When several signals arrive on the same candle
        the one whose strategy comes first in ``priority`` (default: order of ``strategies``)
        takes the next-bar entry; later ones are rejected as LOST_TIE_BREAK.  A signal that
        arrives while a position is open, or whose candle closes at/after the entry cutoff,
        is rejected and recorded; it is never carried forward.
        """
        names = [s.name for s in strategies]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate strategy names in simulation: {names}")
        order = list(priority) if priority is not None else names
        rank = {name: i for i, name in enumerate(order)}
        ordered = sorted(strategies, key=lambda s: (rank.get(s.name, len(rank)), s.name))

        result = UnderlyingSimulationResult()
        if not bars or not strategies:
            return result

        sorted_bars = sorted(bars, key=lambda b: b.exchange_time)
        for earlier, later in zip(sorted_bars, sorted_bars[1:], strict=False):
            if earlier.exchange_time == later.exchange_time:
                raise ValueError(f"Duplicate bar timestamp {earlier.exchange_time.isoformat()}")

        if enriched_contexts is None:
            from trading_platform.data.reference_levels import ReferenceLevelCalculator

            calculator = ReferenceLevelCalculator()
            enriched = calculator.enrich_bars_with_context(
                sorted_bars, daily_history_bars=daily_history_bars
            )
            enriched_contexts = {ctx.bar.exchange_time: ctx for ctx in enriched}

        day_states = day_context_states or {}
        contexts: dict[str, StrategyContext] = {}
        for strategy in ordered:
            strategy.reset_state()
            contexts[strategy.name] = StrategyContext()

        trade_counters: dict[str, int] = {}
        seen_signal_ids: set[str] = set()
        active: dict[str, Any] | None = None
        pending: tuple[Signal, EngineSignalRecord] | None = None
        prev_bar: Bar | None = None

        for bar in sorted_bars:
            bar_date = bar.exchange_time.date()

            # 0. Session rollover: nothing may carry across a date change.
            if prev_bar is not None and prev_bar.exchange_time.date() != bar_date:
                if active is not None:
                    result.trades.append(self._force_close(active, prev_bar))
                    active = None
                if pending is not None:
                    self._reject(pending[1], RejectionReason.NO_ENTRY_BAR)
                    pending = None

            # Point-in-time reference levels / indicators for every strategy context.
            updates = self._context_updates(enriched_contexts.get(bar.exchange_time), bar_date)
            day_str = bar_date.isoformat()
            for ctx in contexts.values():
                ctx.state.update(updates)
                if day_str in day_states:
                    ctx.state.update(day_states[day_str])

            # 1. Fill the signal confirmed at the previous bar close, at THIS bar's open.
            if pending is not None:
                signal, record = pending
                pending = None
                if bar.exchange_time.time() >= self.entry_cutoff_time:
                    self._reject(record, RejectionReason.AFTER_ENTRY_CUTOFF)
                elif active is not None:
                    self._reject(
                        record,
                        RejectionReason.OVERLAP_POSITION_OPEN,
                        blocking_trade_id=active["trade_id"],
                    )
                else:
                    count = trade_counters.get(record.strategy_name, 0) + 1
                    trade_counters[record.strategy_name] = count
                    active = self._open_trade(signal, record, bar, count)

            # 2. Evaluate exits of the open position on the current bar.
            if active is not None:
                exit_dec = self._evaluate_underlying_exit(active, bar)
                if exit_dec["should_exit"]:
                    result.trades.append(
                        self._close_trade(
                            active,
                            exit_time=bar.exchange_time,
                            exit_price=exit_dec["exit_price"],
                            reason=exit_dec["reason"],
                            uncertain=exit_dec.get("is_uncertain", False),
                        )
                    )
                    active = None

            # 3. EVERY strategy sees EVERY bar; signals are dispositioned in priority order.
            new_pending: tuple[Signal, EngineSignalRecord] | None = None
            for strategy in ordered:
                sig = strategy.on_bar(contexts[strategy.name], bar)
                if sig is None or sig.direction == SignalDirection.HOLD:
                    continue
                record = self._signal_record(strategy.name, sig, bar)
                result.signals.append(record)
                if record.signal_id in seen_signal_ids:
                    self._reject(record, RejectionReason.DUPLICATE_SIGNAL)
                    continue
                seen_signal_ids.add(record.signal_id)
                close = record.signal_candle_close
                cutoff_ok = close.date() == bar_date and close.time() < self.entry_cutoff_time
                if not cutoff_ok:
                    self._reject(record, RejectionReason.AFTER_ENTRY_CUTOFF)
                elif active is not None:
                    self._reject(
                        record,
                        RejectionReason.OVERLAP_POSITION_OPEN,
                        blocking_trade_id=active["trade_id"],
                    )
                elif new_pending is not None:
                    self._reject(
                        record,
                        RejectionReason.LOST_TIE_BREAK,
                        blocking_signal_id=new_pending[1].signal_id,
                    )
                else:
                    new_pending = (sig, record)
            pending = new_pending
            prev_bar = bar

        # End of data: nothing carries past the last bar.
        if active is not None and prev_bar is not None:
            result.trades.append(self._force_close(active, prev_bar))
        if pending is not None:
            self._reject(pending[1], RejectionReason.NO_ENTRY_BAR)
        return result

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _reject(
        record: EngineSignalRecord,
        reason: RejectionReason,
        blocking_trade_id: str | None = None,
        blocking_signal_id: str | None = None,
    ) -> None:
        record.status = SignalStatus.REJECTED
        record.rejection_reason = reason.value
        record.blocking_trade_id = blocking_trade_id
        record.blocking_signal_id = blocking_signal_id

    @staticmethod
    def _context_updates(bar_ctx: Any, bar_date: date | None = None) -> dict[str, Any]:
        """COMPLETE point-in-time context state for one bar (assigned over the old state).

        Every level key is present on every bar: an unavailable value is an explicit ``None``
        (never the previous day's value) and ``levels_date`` names the session the levels are
        for, so strategies can reject stale or missing levels.
        """
        if bar_ctx is None:
            return build_strategy_state(None, levels_date=bar_date)
        return bar_ctx.strategy_state()

    def _signal_record(self, strategy_name: str, sig: Signal, bar: Bar) -> EngineSignalRecord:
        method = str(sig.metadata.get("method") or sig.method_id)
        direction = sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction)
        return EngineSignalRecord(
            signal_id=build_signal_id(strategy_name, method, bar.exchange_time),
            strategy_name=strategy_name,
            method=method,
            direction=direction,
            date=bar.exchange_time.date(),
            signal_candle_open=bar.exchange_time,
            signal_candle_close=bar.exchange_time + timedelta(minutes=self.bar_minutes),
            reference_price=sig.reference_price,
            stop_points=float(sig.metadata.get("stop_points", sig.risk_points)),
            target_points=float(sig.metadata.get("target_points", sig.reward_points)),
            reason=sig.reason,
        )

    def _open_trade(
        self, signal: Signal, record: EngineSignalRecord, bar: Bar, count: int
    ) -> dict[str, Any]:
        """Fill ``signal`` at ``bar``'s open with adverse slippage; rebase stop/target."""
        if signal.direction == SignalDirection.BUY:
            entry_p = round(bar.open + self.slippage_points, 2)
            executed_target = round(entry_p + record.target_points, 2)
            executed_stop = round(entry_p - record.stop_points, 2)
        else:
            entry_p = round(bar.open - self.slippage_points, 2)
            executed_target = round(entry_p - record.target_points, 2)
            executed_stop = round(entry_p + record.stop_points, 2)

        trade_id = f"{record.strategy_name}_TR_{count:04d}"
        record.status = SignalStatus.FILLED
        record.rejection_reason = ""
        record.trade_id = trade_id
        return {
            "trade_id": trade_id,
            "strategy_name": record.strategy_name,
            "signal_id": record.signal_id,
            "signal_time": record.signal_candle_open,
            "entry_time": bar.exchange_time,
            "entry_price": entry_p,
            "date": bar.exchange_time.date(),
            "target": executed_target,
            "stop_loss": executed_stop,
            "direction": signal.direction,
            "method": record.method,
        }

    def _close_trade(
        self,
        active: dict[str, Any],
        exit_time: datetime,
        exit_price: float,
        reason: str,
        uncertain: bool,
    ) -> UnderlyingTradeRecord:
        direction = active["direction"]
        if direction == SignalDirection.BUY:
            pts = round(exit_price - active["entry_price"], 2)
        else:
            pts = round(active["entry_price"] - exit_price, 2)
        return UnderlyingTradeRecord(
            trade_id=active["trade_id"],
            date=active["date"],
            strategy_name=active["strategy_name"],
            direction=direction,
            method=active["method"],
            entry_time=active["entry_time"],
            entry_price=active["entry_price"],
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason=reason,
            points_pnl=pts,
            is_win=pts > 0,
            target=active["target"],
            stop_loss=active["stop_loss"],
            is_uncertain_fill=uncertain,
            signal_id=active["signal_id"],
            signal_time=active["signal_time"],
            slippage_points=self.slippage_points,
        )

    def _force_close(self, active: dict[str, Any], last_bar: Bar) -> UnderlyingTradeRecord:
        """Close at the last seen bar's close (with slippage) when no square-off bar arrived."""
        if active["direction"] == SignalDirection.BUY:
            exit_p = round(last_bar.close - self.slippage_points, 2)
        else:
            exit_p = round(last_bar.close + self.slippage_points, 2)
        return self._close_trade(
            active,
            exit_time=last_bar.exchange_time,
            exit_price=exit_p,
            reason="FORCED_SESSION_END",
            uncertain=True,
        )

    def _evaluate_underlying_exit(self, active_trade: dict[str, Any], bar: Bar) -> dict[str, Any]:
        """Evaluate target, stop loss, or EOD exit for underlying position.

        Every exit type pays ``slippage_points`` against the trader.
        """
        target = active_trade["target"]
        stop = active_trade["stop_loss"]
        direction = active_trade["direction"]
        slip = self.slippage_points

        # EOD Check (15:15 IST)
        if bar.exchange_time.time() >= self.eod_exit_time:
            exit_p = round(
                bar.open - slip if direction == SignalDirection.BUY else bar.open + slip,
                2,
            )
            return {"should_exit": True, "exit_price": exit_p, "reason": "EOD_SQUAREOFF"}

        # Optional Trailing Stop Policy Evaluation (e.g. Move to BE at +30 / +40 pts)
        if self.trailing_stop_policy is not None:
            entry_p = active_trade["entry_price"]
            if direction == SignalDirection.BUY:
                if self.trailing_stop_policy == "MOVE_TO_BE_AT_30" and bar.high >= entry_p + 30.0:
                    active_trade["stop_loss"] = max(stop, entry_p)
                    active_trade["is_trailing_be"] = True
                    stop = active_trade["stop_loss"]
                elif self.trailing_stop_policy == "MOVE_TO_BE_AT_40" and bar.high >= entry_p + 40.0:
                    active_trade["stop_loss"] = max(stop, entry_p)
                    active_trade["is_trailing_be"] = True
                    stop = active_trade["stop_loss"]
            elif direction == SignalDirection.SELL:
                if self.trailing_stop_policy == "MOVE_TO_BE_AT_30" and bar.low <= entry_p - 30.0:
                    active_trade["stop_loss"] = min(stop, entry_p)
                    active_trade["is_trailing_be"] = True
                    stop = active_trade["stop_loss"]
                elif self.trailing_stop_policy == "MOVE_TO_BE_AT_40" and bar.low <= entry_p - 40.0:
                    active_trade["stop_loss"] = min(stop, entry_p)
                    active_trade["is_trailing_be"] = True
                    stop = active_trade["stop_loss"]

        if direction == SignalDirection.BUY:
            hit_sl = bar.low <= stop
            hit_tp = bar.high >= target

            sl_reason = (
                "TRAILING_STOP_BE"
                if active_trade.get("is_trailing_be", False) and stop == active_trade["entry_price"]
                else "STOP_LOSS"
            )

            if hit_sl and hit_tp:
                # Same-bar conflict: conservative worst-case SL priority
                exit_p = round(min(bar.open, stop) - slip, 2)
                return {
                    "should_exit": True,
                    "exit_price": exit_p,
                    "reason": "INTRABAR_SL_CONFLICT",
                    "is_uncertain": True,
                }
            elif hit_sl:
                if bar.open <= stop:
                    exit_p = round(bar.open - slip, 2)  # Gap through stop
                else:
                    exit_p = round(stop - slip, 2)
                return {"should_exit": True, "exit_price": exit_p, "reason": sl_reason}
            elif hit_tp:
                # Target fills at the target level (even if gapped through: conservative),
                # and pays exit slippage like every other exit type.
                return {
                    "should_exit": True,
                    "exit_price": round(target - slip, 2),
                    "reason": "TARGET",
                }

        elif direction == SignalDirection.SELL:
            hit_sl = bar.high >= stop
            hit_tp = bar.low <= target

            sl_reason = (
                "TRAILING_STOP_BE"
                if active_trade.get("is_trailing_be", False) and stop == active_trade["entry_price"]
                else "STOP_LOSS"
            )

            if hit_sl and hit_tp:
                exit_p = round(max(bar.open, stop) + slip, 2)
                return {
                    "should_exit": True,
                    "exit_price": exit_p,
                    "reason": "INTRABAR_SL_CONFLICT",
                    "is_uncertain": True,
                }
            elif hit_sl:
                if bar.open >= stop:
                    exit_p = round(bar.open + slip, 2)  # Gap through stop
                else:
                    exit_p = round(stop + slip, 2)
                return {"should_exit": True, "exit_price": exit_p, "reason": sl_reason}
            elif hit_tp:
                return {
                    "should_exit": True,
                    "exit_price": round(target + slip, 2),
                    "reason": "TARGET",
                }

        return {"should_exit": False, "exit_price": 0.0, "reason": "HOLD"}

    def _generate_report(
        self,
        strategy_name: str,
        trades: list[UnderlyingTradeRecord],
        signals: list[EngineSignalRecord] | None = None,
    ) -> UnderlyingBacktestReport:
        """Compute performance statistics from trade list (shared metrics module)."""
        m = compute_points_metrics(trades)
        return UnderlyingBacktestReport(
            strategy_name=strategy_name,
            total_trades=m.trades,
            winning_trades=m.wins,
            losing_trades=m.trades - m.wins,
            win_rate_pct=m.win_rate_pct,
            total_points=m.net_points,
            profit_factor=m.profit_factor,
            max_drawdown_points=m.max_drawdown_points,
            avg_points_per_trade=m.avg_points_per_trade,
            trades=trades,
            signals=signals or [],
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
            sorted_times = sorted([t for t in bar_map.keys() if t > sig.timestamp])
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
