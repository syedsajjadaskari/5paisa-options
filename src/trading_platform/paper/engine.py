"""Production-grade persistent NIFTY options paper trading orchestrator using authenticated 5paisa market data."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time as time_mod
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import yaml

from trading_platform.backtesting.indian_options_costs import IndianOptionsCostCalculator
from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    SignalDirection,
)
from trading_platform.core.models import Bar, Order, Signal, Trade
from trading_platform.data.live_market_feed import Level2Quote, LiveMarketFeed, SpotTick
from trading_platform.options.live_contract_selector import (
    LiveContractSelectionResult,
    LiveContractSelector,
)
from trading_platform.paper.broker import PaperBroker
from trading_platform.paper.portfolio import ActivePositionRecord, VirtualPortfolio
from trading_platform.paper.recorder import MarketDataRecorder
from trading_platform.paper.reporting import PaperTradingReporter
from trading_platform.paper.risk import PaperRiskManager
from trading_platform.strategies.base import StrategyContext
from trading_platform.strategies.irf import IRFStrategy
from trading_platform.strategies.orb import ORBStrategy
from trading_platform.strategies.pdr import PDRStrategy
from trading_platform.strategies.pds import PDSStrategy


def is_pid_alive(pid: int | None) -> bool:
    """Check if process with given PID is currently active on the OS."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_process_cmd(pid: int | None) -> str:
    """Retrieve process command line for identity verification on macOS."""
    if not pid or pid <= 0:
        return ""
    try:
        res = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        return res.stdout.strip()
    except Exception:
        return ""


logger = logging.getLogger(__name__)

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


@dataclass
class PaperEngineConfig:
    """Rigorous configuration for persistent paper trading."""

    mode: str = "REPLAY_TEST"  # 'REPLAY_TEST', 'LIVE_READ_ONLY', 'PAPER_TRADE'
    # Fail closed.  PAPER_TRADE must be enabled explicitly by a validated config
    # or by a deliberately constructed test configuration.
    paper_execution_enabled: bool = False
    starting_capital: float = 250000.0
    maximum_lots_per_trade: int = 2
    maximum_concurrent_positions: int = 1
    max_allocation_percent: float = 20.0  # Max 20% capital in single position
    daily_loss_limit_percent: float = 2.0  # Max 2% loss per day (₹5,000 on ₹250k)
    slippage_points: float = 0.50
    brokerage_per_order: float = 20.0
    maximum_quote_age_seconds: float = 5.0
    maximum_bid_ask_spread_percent: float = 2.0
    entry_cutoff_time: dt_time = dt_time(14, 30)  # No new entries after 14:30 IST
    square_off_time: dt_time = dt_time(15, 15)  # EOD exit at 15:15 IST
    poll_interval_seconds: float = 1.0
    simulated_fill_delay_seconds: float = 0.20  # Delay between signal and fill

    # Strategies enablement
    enable_pds: bool = True
    enable_orb: bool = True
    enable_irf: bool = True
    enable_pdr: bool = True
    enable_rf: bool = False  # Strictly disabled

    db_path: str = "data/processed/paper_trading.duckdb"
    state_file: str = "data/cache/paper_engine_state.json"
    reports_dir: str = "reports/paper_trading"
    options_archive_dir: str = "data/processed/options_archive_verified"
    account_namespace: str = "PAPER_VIRTUAL"

    @classmethod
    def from_yaml(cls, yaml_path: str | Path = "config/paper_trading.yaml") -> PaperEngineConfig:
        """Load engine configuration from YAML file with fallback to configs/."""
        path = Path(yaml_path)
        if not path.exists():
            path = Path("configs/paper_trading.yaml")

        if not path.exists():
            return cls(mode="LIVE_READ_ONLY", paper_execution_enabled=False)

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            p_cfg = data.get("portfolio", {})
            r_cfg = data.get("risk", {})
            o_cfg = data.get("options", {})
            s_cfg = data.get("strategies", {})
            t_cfg = data.get("trading", {})

            # Parse entry cutoff and square-off times
            cutoff_raw = p_cfg.get("entry_cutoff_ist", "14:30")
            c_h, c_m = [int(x) for x in cutoff_raw.split(":")]
            sq_raw = p_cfg.get("square_off_ist", "15:15")
            s_h, s_m = [int(x) for x in sq_raw.split(":")]

            return cls(
                mode=t_cfg.get("mode", "PAPER_TRADE"),
                paper_execution_enabled=bool(t_cfg.get("paper_execution_enabled", False)),
                starting_capital=float(p_cfg.get("starting_capital_inr", 250000.0)),
                maximum_lots_per_trade=int(p_cfg.get("maximum_lots_per_trade", 2)),
                maximum_concurrent_positions=int(p_cfg.get("maximum_concurrent_positions", 1)),
                max_allocation_percent=float(
                    r_cfg.get("maximum_open_premium_allocation_percent", 20.0)
                ),
                daily_loss_limit_percent=float(r_cfg.get("daily_loss_limit_percent", 2.0)),
                maximum_quote_age_seconds=float(o_cfg.get("maximum_quote_age_seconds", 5.0)),
                maximum_bid_ask_spread_percent=float(
                    o_cfg.get("maximum_bid_ask_spread_percent", 2.0)
                ),
                options_archive_dir=str(
                    o_cfg.get("verified_archive_dir", "data/processed/options_archive_verified")
                ),
                entry_cutoff_time=dt_time(c_h, c_m),
                square_off_time=dt_time(s_h, s_m),
                enable_pds=s_cfg.get("PDS", {}).get("enabled", True),
                enable_orb=s_cfg.get("ORB", {}).get("enabled", True),
                enable_irf=s_cfg.get("IRF", {}).get("enabled", True),
                enable_pdr=s_cfg.get("PDR", {}).get("enabled", True),
                enable_rf=s_cfg.get("RF", {}).get("enabled", False),
            )
        except Exception:
            logger.exception("Invalid paper trading configuration; forcing LIVE_READ_ONLY")
            return cls(mode="LIVE_READ_ONLY", paper_execution_enabled=False)


class PaperTradingEngine:
    """Production-grade NIFTY options paper trading platform orchestrator."""

    def __init__(self, config: PaperEngineConfig | None = None) -> None:
        self.config = config or PaperEngineConfig.from_yaml()
        self.run_id = f"RUN_{datetime.now(TZ_IST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        # Isolated Paper Broker
        self.cost_calculator = IndianOptionsCostCalculator(
            brokerage_per_order=self.config.brokerage_per_order,
            slippage_points_per_unit=self.config.slippage_points,
        )
        self.broker = PaperBroker(
            initial_balance=self.config.starting_capital,
            slippage_points=self.config.slippage_points,
            brokerage_per_order=self.config.brokerage_per_order,
            cost_calculator=self.cost_calculator,
        )

        # Virtual Portfolio
        self.portfolio = VirtualPortfolio(
            starting_capital=self.config.starting_capital,
            max_allocation_pct=self.config.max_allocation_percent,
        )

        # Institutional Risk Manager
        self.risk_manager = PaperRiskManager(
            starting_capital=self.config.starting_capital,
            max_lots_per_trade=self.config.maximum_lots_per_trade,
            max_open_positions=self.config.maximum_concurrent_positions,
            max_allocation_pct=self.config.max_allocation_percent,
            daily_loss_limit_pct=self.config.daily_loss_limit_percent,
            entry_cutoff_time=self.config.entry_cutoff_time,
            square_off_time=self.config.square_off_time,
            max_quote_age_seconds=self.config.maximum_quote_age_seconds,
            max_spread_pct=self.config.maximum_bid_ask_spread_percent,
        )

        # Read-Only Market Data Client
        self.live_data_broker = FivePaisaBroker()
        self.feed = LiveMarketFeed(
            broker=self.live_data_broker,
            poll_interval=self.config.poll_interval_seconds,
            max_staleness_seconds=self.config.maximum_quote_age_seconds,
        )

        # Contract Selector
        self.contract_selector = LiveContractSelector(
            broker=self.live_data_broker,
            feed=self.feed,
            max_spread_points=5.0,
            max_spread_pct=self.config.maximum_bid_ask_spread_percent,
            max_staleness_seconds=self.config.maximum_quote_age_seconds,
        )

        # Continuous Recorder & Reporter
        self.recorder = MarketDataRecorder(
            db_path=self.config.db_path,
            archive_dir=self.config.options_archive_dir,
            run_id=self.run_id,
            max_quote_age_seconds=self.config.maximum_quote_age_seconds,
            starting_capital=self.config.starting_capital,
        )
        self.reporter = PaperTradingReporter(
            db_path=self.config.db_path,
            reports_dir=self.config.reports_dir,
        )

        # Strategies
        self.strategies: dict[str, Any] = {}
        self.strategy_contexts: dict[str, StrategyContext] = {}
        self._init_strategies()

        # Engine Tracking State
        self.last_spot_price: float = 0.0
        self.last_spot_time: datetime | None = None
        self.closed_bars: list[Bar] = []
        self.completed_trades: list[Trade] = []
        self.signals_log: list[dict[str, Any]] = []
        self.contract_selections_log: list[dict[str, Any]] = []
        self.rejections_log: list[dict[str, Any]] = []
        self.pending_exit_reason: str | None = None
        self.executed_signal_keys: set[tuple[str, str, str]] = set()
        self._lock_acquired: bool = False

        # Config Hash
        self.config_hash = self._compute_config_hash()

        # Reload state from disk if recovering from crash
        self.load_state()

    def _init_strategies(self) -> None:
        """Initialize frozen BASELINE_V1 strategies."""
        if self.config.enable_pds:
            self.strategies["PDS"] = PDSStrategy(
                config={"method": "METHOD_1", "target_points": 70.0}
            )
            self.strategy_contexts["PDS"] = StrategyContext(strategy_name="PDS")

        if self.config.enable_orb:
            self.strategies["ORB"] = ORBStrategy(
                config={"method": "METHOD_1", "target_points": 60.0, "stop_points": 30.0}
            )
            self.strategy_contexts["ORB"] = StrategyContext(strategy_name="ORB")

        if self.config.enable_irf:
            self.strategies["IRF"] = IRFStrategy(
                config={"method": "METHOD_2", "target_points": 60.0}
            )
            self.strategy_contexts["IRF"] = StrategyContext(strategy_name="IRF")

        if self.config.enable_pdr:
            self.strategies["PDR"] = PDRStrategy(
                config={"method": "RETEST_BOUNCE", "target_points": 70.0}
            )
            self.strategy_contexts["PDR"] = StrategyContext(strategy_name="PDR")

        # RF is strictly not loaded

    def _compute_config_hash(self) -> str:
        """Create immutable hash of current engine configuration."""
        cfg_dict = asdict(self.config)
        cfg_str = json.dumps(cfg_dict, sort_keys=True, default=str)
        return hashlib.sha256(cfg_str.encode("utf-8")).hexdigest()[:16]

    def trigger_kill_switch(self, reason: str = "MANUAL_TRIGGER") -> None:
        """Emergency kill switch to halt all new entries and immediately square off open position."""
        logger.warning(f"KILL SWITCH TRIGGERED: {reason}")
        self.risk_manager.trigger_kill_switch(reason)
        if self.portfolio.active_position is not None:
            self._close_active_position(
                exit_reason="KILL_SWITCH",
                exit_time=datetime.now(TZ_IST),
                exit_spot=self.last_spot_price,
            )

    def on_tick(self, tick: SpotTick) -> Bar | None:
        """Process incoming spot tick, evaluate underlying exits, and aggregate candles."""
        raw_tick_age = (
            tick.ingestion_timestamp - tick.provider_timestamp
        ).total_seconds()
        if (
            tick.is_stale
            or abs(raw_tick_age) > self.config.maximum_quote_age_seconds
            or tick.last_rate <= 0
        ):
            self.recorder.record_feed_interruption(
                "REJECTED_STALE_OR_INVALID_SPOT",
                f"scrip={tick.scrip_code}; stale={tick.is_stale}; "
                f"age={raw_tick_age:.3f}; rate={tick.last_rate}",
            )
            logger.warning(
                "Rejected spot tick before strategy/exit processing: stale=%s rate=%s",
                tick.is_stale,
                tick.last_rate,
            )
            return None

        self.last_spot_price = tick.last_rate
        self.last_spot_time = tick.provider_timestamp
        logger.info(
            f"[TRANSITION] ENGINE_STATE_UPDATED: Spot={tick.last_rate:,.2f}, "
            f"TickTime={tick.provider_timestamp.strftime('%H:%M:%S')}, Stale={tick.is_stale}"
        )

        # Record raw spot event
        self.recorder.record_spot_tick(tick)

        # 1. Check Underlying Exits on live tick
        if self.portfolio.active_position is not None:
            if self.pending_exit_reason:
                self._close_active_position(
                    exit_reason=self.pending_exit_reason,
                    exit_time=tick.provider_timestamp,
                    exit_spot=tick.last_rate,
                )
            else:
                self.evaluate_exits(current_spot=tick.last_rate, tick_time=tick.provider_timestamp)

        # 2. Check 15:15 IST Forced Intraday Square-Off
        t_now = tick.provider_timestamp.astimezone(TZ_IST).time()
        if t_now >= self.config.square_off_time and self.portfolio.active_position is not None:
            self._close_active_position(
                exit_reason="EOD_SQUAREOFF",
                exit_time=tick.provider_timestamp,
                exit_spot=tick.last_rate,
            )

        # 3. Aggregate 5-minute candle
        closed_bar = self.feed.aggregator.process_tick(tick)
        if closed_bar is not None:
            self.closed_bars.append(closed_bar)
            self.recorder.record_candle(closed_bar, interval="5m", is_complete=True)
            self.on_candle_close(closed_bar)
            return closed_bar

        return None

    def on_candle_close(self, bar: Bar) -> list[Signal]:
        """Evaluate strategy signals strictly on 5-minute candle close."""
        generated_signals: list[Signal] = []

        from trading_platform.data.calendar import NSECalendar

        if not NSECalendar().is_entry_session_supported(bar.exchange_time.date()):
            return generated_signals

        # Check cutoff time for new entries (14:30 IST)
        bar_time = bar.exchange_time.time()
        if bar_time >= self.config.entry_cutoff_time:
            return generated_signals

        # Check Kill Switch
        if self.risk_manager.kill_switch_active:
            return generated_signals

        # Evaluate each active strategy
        for name, strategy in self.strategies.items():
            ctx = self.strategy_contexts[name]
            self._update_strategy_context_levels(ctx, bar)

            sig = strategy.on_bar(ctx, bar)
            if sig is not None:
                generated_signals.append(sig)
                self._record_signal(sig, is_executed=False)

        # If an active position is open, reject all signals under concurrency rule
        if self.portfolio.active_position is not None:
            for s in generated_signals:
                self._record_rejection(
                    s, "REJECTED_POSITION_CONCURRENCY", "Max 1 open position limit reached"
                )
            return generated_signals

        # Arbitrate signals (FIFO: first signal wins)
        if generated_signals:
            selected_signal = generated_signals[0]
            self.execute_signal(
                signal=selected_signal,
                spot_price=bar.close,
                candle_time=bar.exchange_time,
            )

        return generated_signals

    def _update_strategy_context_levels(self, ctx: StrategyContext, bar: Bar) -> None:
        """Inject previous day high/low/close into strategy context for gap & level checks."""
        # Live point-in-time reference loading is not yet independently verified.
        # Never fabricate prior-session levels from the current candle: strategies
        # that require missing context must remain silent.
        return None

    def execute_signal(
        self,
        signal: Signal,
        spot_price: float,
        candle_time: datetime,
        mock_quote: Level2Quote | None = None,
    ) -> bool:
        """Route strategy signal through contract selector, risk manager, and PaperBroker."""
        from trading_platform.data.calendar import NSECalendar

        if not NSECalendar().is_entry_session_supported(candle_time.astimezone(TZ_IST).date()):
            self._record_rejection(
                signal,
                "REJECTED_UNSUPPORTED_SPECIAL_SESSION",
                "Paper entries are disabled on unsupported special sessions",
            )
            return False
        # Concurrency check
        if self.portfolio.active_position is not None:
            self._record_rejection(
                signal, "REJECTED_POSITION_CONCURRENCY", "Max 1 open position limit reached"
            )
            return False

        # Pending exit check
        if self.pending_exit_reason is not None:
            self._record_rejection(
                signal, "REJECTED_PENDING_EXIT", "Existing position has pending exit awaiting execution"
            )
            return False

        # Market session gate for PAPER_TRADE mode
        if self.config.mode == "PAPER_TRADE":
            if not self.feed.is_market_session_open(candle_time):
                self._record_rejection(
                    signal, "REJECTED_MARKET_CLOSED", "Exchange market session is closed"
                )
                return False

        # Duplicate signal prevention
        sig_key = (str(signal.strategy_name), signal.method_id, candle_time.isoformat())
        if sig_key in self.executed_signal_keys:
            self._record_rejection(
                signal, "REJECTED_DUPLICATE_SIGNAL", f"Signal already executed for {sig_key}"
            )
            return False

        # Mode gate: In LIVE_READ_ONLY mode, do not execute
        if self.config.mode == "LIVE_READ_ONLY":
            self._record_rejection(
                signal, "LIVE_READ_ONLY_MODE", "Order simulation disabled in LIVE_READ_ONLY mode"
            )
            return False

        if self.config.mode == "PAPER_TRADE" and not self.config.paper_execution_enabled:
            self._record_rejection(
                signal,
                "PAPER_EXECUTION_DISABLED",
                "Independent audit safety gate is active; read-only observation only",
            )
            return False

        # Kill Switch check
        if self.risk_manager.kill_switch_active:
            self._record_rejection(
                signal, "REJECTED_KILL_SWITCH", "Kill switch is currently active"
            )
            return False

        # 1. Contract Selection
        t_date = candle_time.astimezone(TZ_IST).date()
        sel = self.contract_selector.select_contract(
            spot_price=spot_price,
            signal_direction=signal.direction,
            trading_date=t_date,
            mock_quote=mock_quote,
        )
        self._record_contract_selection(signal, sel)

        if sel.status != "APPROVED":
            self._record_rejection(signal, sel.status, sel.reason)
            return False

        # 2. Institutional Risk Sizing (20% Allocation Cap & 2-Lot Maximum)
        size_result = self.risk_manager.calculate_position_size(
            premium_ask=sel.best_ask,
            lot_size=sel.lot_size,
            portfolio=self.portfolio,
            check_time=candle_time,
        )

        if not size_result.is_approved:
            self._record_rejection(signal, "REJECTED_RISK_LIMIT", size_result.reason)
            return False

        chosen_lots = size_result.permitted_lots
        trade_qty = size_result.quantity

        # 3. Virtual Order Submission to PaperBroker
        order_id = f"VORD_{datetime.now(TZ_IST).strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}"
        order = Order(
            order_id=order_id,
            client_order_id=f"CL_{order_id}",
            symbol=sel.symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=trade_qty,
            price=sel.best_ask,
            status=OrderStatus.SUBMITTED,
            created_at=datetime.now(TZ_UTC),
            updated_at=datetime.now(TZ_UTC),
            strategy_name=str(signal.strategy_name),
            method_id=signal.method_id,
        )

        executed_order = self.broker.execute_option_order(
            order=order,
            bid=sel.best_bid,
            ask=sel.best_ask,
            trade_date=t_date,
            slippage_points=self.config.slippage_points,
        )

        if executed_order.status != OrderStatus.FILLED:
            self._record_rejection(
                signal,
                "ORDER_EXECUTION_FAILED",
                f"Virtual fill failed: status={executed_order.status}",
            )
            return False

        # 4. Open Position in VirtualPortfolio
        position_record = ActivePositionRecord(
            symbol=sel.symbol,
            scrip_code=sel.scrip_code,
            option_type=sel.option_type,
            strike=sel.strike,
            expiry=sel.expiry.isoformat(),
            lot_size=sel.lot_size,
            lots=chosen_lots,
            quantity=trade_qty,
            entry_premium=executed_order.average_fill_price,
            entry_time=candle_time.isoformat(),
            underlying_entry_spot=spot_price,
            underlying_stop_loss=signal.stop_loss,
            underlying_target=signal.target,
            strategy_name=str(signal.strategy_name),
            method_id=signal.method_id,
            direction=str(signal.direction),
            order_id=order_id,
        )

        # Single-leg entry transaction charges
        entry_charges = self.cost_calculator.calculate_single_leg_costs(
            trade_date=t_date,
            side="BUY",
            premium=executed_order.average_fill_price,
            quantity=trade_qty,
            apply_slippage=False,
        ).total_charges

        self.portfolio.open_position(
            contract=position_record,
            entry_fill_price=executed_order.average_fill_price,
            total_entry_charges=entry_charges,
        )
        self.executed_signal_keys.add(sig_key)

        # 5. Persist order and state
        self._record_virtual_order(executed_order)
        self._record_equity_snapshot()
        self.save_state()
        return True

    def evaluate_exits(
        self,
        current_spot: float,
        tick_time: datetime,
        mock_bid: float | None = None,
    ) -> bool:
        """Check if open option position has hit underlying Stop Loss or Target."""
        if self.portfolio.active_position is None:
            return False

        pos = self.portfolio.active_position
        direction = pos.direction
        sl = pos.underlying_stop_loss
        tp = pos.underlying_target

        exit_reason = None
        if direction in ("BUY", SignalDirection.BUY):
            # Bullish CE: Stop hit if spot <= SL; Target hit if spot >= TP
            if current_spot <= sl:
                exit_reason = "STOP_LOSS"
            elif current_spot >= tp:
                exit_reason = "TARGET"
        else:
            # Bearish PE: Stop hit if spot >= SL; Target hit if spot <= TP
            if current_spot >= sl:
                exit_reason = "STOP_LOSS"
            elif current_spot <= tp:
                exit_reason = "TARGET"

        if exit_reason is not None:
            self._close_active_position(
                exit_reason=exit_reason,
                exit_time=tick_time,
                exit_spot=current_spot,
                mock_bid=mock_bid,
            )
            return True

        return False

    def _close_active_position(
        self,
        exit_reason: str,
        exit_time: datetime,
        exit_spot: float,
        mock_bid: float | None = None,
    ) -> bool:
        """Simulate closing open option position at current live executable Bid quote."""
        pos = self.portfolio.active_position
        if pos is None:
            self.pending_exit_reason = None
            return False

        symbol = pos.symbol
        scrip_code = pos.scrip_code
        qty = pos.quantity
        entry_premium = pos.entry_premium
        entry_spot = pos.underlying_entry_spot
        t_date = exit_time.astimezone(TZ_IST).date()

        # Fetch live Bid depth
        if mock_bid is not None:
            bid_price = mock_bid
        else:
            depth = self.feed.fetch_option_depth(scrip_code=scrip_code, symbol=symbol)
            if depth and depth.best_bid > 0 and (not depth.is_stale or self.config.mode == "REPLAY_TEST"):
                bid_price = depth.best_bid
            elif self.config.mode == "REPLAY_TEST":
                # In offline deterministic replay, allow fallback estimate when live quotes unavailable
                bid_price = max(0.05, round(entry_premium * 0.90, 2))
                logger.info(f"REPLAY_TEST: Used estimated bid price {bid_price} for {symbol} on {exit_reason}")
            else:
                # In LIVE_READ_ONLY or PAPER_TRADE mode:
                # Mark exit as pending if executable bid quote is unavailable or stale.
                # Strictly DO NOT treat a missing/stale quote as a successful exit.
                logger.warning(
                    f"NO_EXECUTABLE_QUOTE on exit for {symbol} (depth={depth}). "
                    f"Marking exit as PENDING ({exit_reason}). Open position retained until valid executable quote observed."
                )
                self.pending_exit_reason = exit_reason
                self.save_state()
                return False

        # Valid quote obtained - clear any pending exit reason
        self.pending_exit_reason = None

        # Submit SELL Order
        order_id = f"VORD_EXIT_{datetime.now(TZ_IST).strftime('%H%M%S')}_{uuid.uuid4().hex[:4]}"
        order = Order(
            order_id=order_id,
            client_order_id=f"CL_{order_id}",
            symbol=symbol,
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=qty,
            price=bid_price,
            status=OrderStatus.SUBMITTED,
            created_at=datetime.now(TZ_UTC),
            updated_at=datetime.now(TZ_UTC),
            strategy_name=pos.strategy_name,
            method_id=pos.method_id,
        )

        executed_order = self.broker.execute_option_order(
            order=order,
            bid=bid_price,
            ask=None,
            trade_date=t_date,
            slippage_points=self.config.slippage_points,
        )

        exit_premium = executed_order.average_fill_price

        # Reconcile the exact independently rounded charges applied by PaperBroker
        # on each fill.  Entry charges were already deducted when the position
        # opened, so only the exit leg may be deducted from portfolio cash here.
        entry_leg_costs = self.cost_calculator.calculate_single_leg_costs(
            trade_date=t_date,
            side="BUY",
            premium=entry_premium,
            quantity=qty,
            apply_slippage=False,
        )
        exit_leg_costs = self.cost_calculator.calculate_single_leg_costs(
            trade_date=t_date,
            side="SELL",
            premium=exit_premium,
            quantity=qty,
            apply_slippage=False,
        )
        actual_total_costs = round(
            entry_leg_costs.total_charges + exit_leg_costs.total_charges,
            2,
        )

        # Dual Accounting
        if pos.direction in ("BUY", SignalDirection.BUY):
            underlying_pts_pnl = round(exit_spot - entry_spot, 2)
        else:
            underlying_pts_pnl = round(entry_spot - exit_spot, 2)

        option_gross_pnl = round((exit_premium - entry_premium) * qty, 2)
        option_net_pnl = round(option_gross_pnl - actual_total_costs, 2)

        # Update Portfolio and Risk Manager
        self.portfolio.close_position(
            exit_fill_price=exit_premium,
            total_exit_charges=exit_leg_costs.total_charges,
        )
        self.risk_manager.record_trade_exit(net_pnl=option_net_pnl)

        # Create Trade record
        trade = Trade(
            trade_id=f"TRD_{pos.order_id}",
            strategy_name=pos.strategy_name,
            method_id=pos.method_id,
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=qty,
            entry_time=datetime.fromisoformat(pos.entry_time),
            entry_price=entry_premium,
            exit_time=exit_time,
            exit_price=exit_premium,
            stop_loss=pos.underlying_stop_loss,
            target=pos.underlying_target,
            gross_pnl=option_gross_pnl,
            net_pnl=option_net_pnl,
            total_costs=actual_total_costs,
            exit_reason=exit_reason,
            entry_rule_description=f"{pos.strategy_name} {pos.method_id} {pos.option_type}",
        )
        self.completed_trades.append(trade)

        # Persist completed trade
        self._record_completed_trade(trade, entry_spot, exit_spot, underlying_pts_pnl)
        self._record_virtual_order(executed_order)
        self._record_equity_snapshot()
        self.save_state()
        return True

    def replay_bars(self, bars: list[Bar]) -> None:
        """Deterministic offline replay of 5-minute bars for pipeline testing."""
        for bar in bars:
            open_tick = SpotTick(
                symbol=bar.symbol,
                scrip_code=int(bar.instrument_id) if bar.instrument_id.isdigit() else 999920000,
                last_rate=bar.open,
                high=bar.high,
                low=bar.low,
                prev_close=bar.open,
                provider_timestamp=bar.exchange_time,
                ingestion_timestamp=bar.exchange_time,
            )
            self.on_tick(open_tick)

            # High tick
            high_tick = SpotTick(
                symbol=bar.symbol,
                scrip_code=open_tick.scrip_code,
                last_rate=bar.high,
                high=bar.high,
                low=bar.low,
                prev_close=bar.open,
                provider_timestamp=bar.exchange_time + timedelta(minutes=2),
                ingestion_timestamp=bar.exchange_time + timedelta(minutes=2),
            )
            self.on_tick(high_tick)

            # Low tick
            low_tick = SpotTick(
                symbol=bar.symbol,
                scrip_code=open_tick.scrip_code,
                last_rate=bar.low,
                high=bar.high,
                low=bar.low,
                prev_close=bar.open,
                provider_timestamp=bar.exchange_time + timedelta(minutes=3),
                ingestion_timestamp=bar.exchange_time + timedelta(minutes=3),
            )
            self.on_tick(low_tick)

            # Close boundary tick
            close_tick = SpotTick(
                symbol=bar.symbol,
                scrip_code=open_tick.scrip_code,
                last_rate=bar.close,
                high=bar.high,
                low=bar.low,
                prev_close=bar.open,
                provider_timestamp=bar.exchange_time + timedelta(minutes=5),
                ingestion_timestamp=bar.exchange_time + timedelta(minutes=5),
            )
            self.on_tick(close_tick)

    def run_live_read_only_cycle(self) -> SpotTick | None:
        """Execute a single polling and evaluation cycle in LIVE_READ_ONLY mode."""
        tick = self.feed.fetch_spot_tick()
        if tick is not None:
            self.on_tick(tick)
            # Prospective archive around ATM
            self.recorder.archive_atm_option_chain(
                broker=self.live_data_broker, spot_price=tick.last_rate
            )
        return tick

    def run_paper_trade_cycle(self) -> SpotTick | None:
        """Execute a single polling and execution cycle in PAPER_TRADE mode."""
        tick = self.feed.fetch_spot_tick()
        if tick is not None:
            self.on_tick(tick)
            self.recorder.archive_atm_option_chain(
                broker=self.live_data_broker, spot_price=tick.last_rate
            )
        return tick

    def get_dashboard_summary(self) -> dict[str, Any]:
        """Compile complete real-time telemetry metrics for the Streamlit dashboard."""
        self.broker.get_margins()

        current_opt_price = 0.0
        bid_ask_str = "N/A"
        active_opt_age: float | None = None
        if self.portfolio.active_position is not None:
            pos = self.portfolio.active_position
            depth = self.feed.fetch_option_depth(
                scrip_code=pos.scrip_code,
                symbol=pos.symbol,
            )
            if depth:
                current_opt_price = depth.mid_price
                bid_ask_str = f"{depth.best_bid:.2f} / {depth.best_ask:.2f}"
                active_opt_age = depth.quote_age_seconds
                self.portfolio.update_mtm(current_opt_price)
                self.broker.update_market_price(pos.symbol, current_opt_price)
                self.broker.get_margins()

        spot_age_sec: float | None = None
        if self.last_spot_time:
            now_ist = datetime.now(TZ_IST)
            spot_age_sec = max(0.0, (now_ist - self.last_spot_time.astimezone(TZ_IST)).total_seconds())

        total_brokerage = sum(t.total_costs for t in self.completed_trades)
        total_pts = sum(getattr(t, "underlying_pts_pnl", 0.0) for t in self.completed_trades)

        return {
            "run_id": self.run_id,
            "mode": self.config.mode,
            "config_hash": self.config_hash,
            "is_connected": self.feed.is_connected,
            "connection_errors": self.feed.connection_errors_count,
            "feed_interruptions": getattr(self.feed, "feed_interruptions_count", 0),
            "nifty_spot": self.last_spot_price,
            "last_spot_time": self.last_spot_time.isoformat() if self.last_spot_time else None,
            "spot_quote_age_seconds": round(spot_age_sec, 2) if spot_age_sec is not None else None,
            "active_quote_age_seconds": round(active_opt_age, 2) if active_opt_age is not None else None,
            "kill_switch_active": self.risk_manager.kill_switch_active,
            "consecutive_losses": self.risk_manager.consecutive_losses,
            "available_cash": self.portfolio.available_cash,
            "invested_capital": self.portfolio.invested_capital,
            "total_equity": self.portfolio.total_equity,
            "unrealized_pnl": self.portfolio.unrealized_pnl,
            "realized_pnl": self.portfolio.realized_pnl,
            "daily_drawdown_pct": self.portfolio.daily_drawdown_pct,
            "max_allowed_allocation": self.portfolio.max_allowed_allocation_inr,
            "active_position": self.portfolio.active_position.__dict__
            if self.portfolio.active_position
            else None,
            "active_bid_ask": bid_ask_str,
            "pending_exit_reason": self.pending_exit_reason,
            "completed_trades_count": len(self.completed_trades),
            "signals_count": len(self.signals_log),
            "rejections_count": len(self.rejections_log),
            "closed_candles_count": len(self.closed_bars),
            "active_strategies": list(self.strategies.keys()),
            "market_session_open": self.feed.is_market_session_open(datetime.now(TZ_IST)),
            "total_brokerage_paid": round(total_brokerage, 2),
            "total_underlying_pts_pnl": round(total_pts, 2),
        }

    # Persistence Helpers
    def _record_signal(self, sig: Signal, is_executed: bool) -> None:
        rec = {
            "signal_id": f"SIG_{uuid.uuid4().hex[:8]}",
            "run_id": self.run_id,
            "timestamp": sig.timestamp,
            "strategy_name": str(sig.strategy_name),
            "method_id": sig.method_id,
            "direction": str(sig.direction),
            "reference_price": sig.reference_price,
            "stop_loss": sig.stop_loss,
            "target": sig.target,
            "reason": sig.reason,
            "is_executed": is_executed,
        }
        self.signals_log.append(rec)
        try:
            con = duckdb.connect(str(self.config.db_path))
            con.execute(
                """
                INSERT INTO signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                list(rec.values()),
            )
            con.close()
        except Exception:
            pass

    def _record_contract_selection(
        self, sig: Signal, sel: LiveContractSelectionResult
    ) -> None:
        rec = {
            "selection_id": f"SEL_{uuid.uuid4().hex[:8]}",
            "run_id": self.run_id,
            "timestamp": sel.selection_timestamp,
            "signal_id": sig.metadata.get("signal_id", "SIG_UNKNOWN"),
            "status": sel.status,
            "option_type": sel.option_type,
            "strike": sel.strike,
            "expiry": sel.expiry,
            "dte": sel.dte,
            "scrip_code": sel.scrip_code,
            "symbol": sel.symbol,
            "best_bid": sel.best_bid,
            "best_ask": sel.best_ask,
            "spread": sel.spread,
            "reason": sel.reason,
        }
        self.contract_selections_log.append(rec)
        try:
            con = duckdb.connect(str(self.config.db_path))
            con.execute(
                """
                INSERT INTO contract_selections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                list(rec.values()),
            )
            con.close()
        except Exception:
            pass

    def _record_rejection(self, sig: Signal, status: str, reason: str) -> None:
        self.rejections_log.append(
            {
                "timestamp": datetime.now(TZ_IST).isoformat(),
                "strategy": str(sig.strategy_name),
                "status": status,
                "reason": reason,
            }
        )

    def _record_virtual_order(self, order: Order) -> None:
        try:
            con = duckdb.connect(str(self.config.db_path))
            con.execute(
                """
                INSERT INTO virtual_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    order.order_id,
                    self.run_id,
                    order.created_at,
                    order.symbol,
                    str(order.side),
                    order.quantity,
                    order.price or 0.0,
                    order.average_fill_price,
                    str(order.status),
                ],
            )
            fills = self.broker.get_fills()
            if fills:
                latest_fill = fills[-1]
                con.execute(
                    """
                    INSERT INTO virtual_fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [
                        latest_fill.fill_id,
                        latest_fill.order_id,
                        self.run_id,
                        latest_fill.timestamp,
                        latest_fill.symbol,
                        str(latest_fill.side),
                        latest_fill.quantity,
                        latest_fill.price,
                        latest_fill.slippage,
                        latest_fill.brokerage,
                        latest_fill.statutory_charges,
                    ],
                )
            con.close()
        except Exception:
            pass

    def _record_completed_trade(
        self,
        trade: Trade,
        entry_spot: float,
        exit_spot: float,
        underlying_pts_pnl: float,
    ) -> None:
        try:
            con = duckdb.connect(str(self.config.db_path))
            con.execute(
                """
                INSERT INTO completed_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    trade.trade_id,
                    self.run_id,
                    trade.strategy_name,
                    trade.method_id,
                    trade.symbol,
                    trade.quantity,
                    trade.entry_time,
                    trade.entry_price,
                    trade.exit_time,
                    trade.exit_price,
                    entry_spot,
                    exit_spot,
                    underlying_pts_pnl,
                    trade.gross_pnl,
                    trade.net_pnl,
                    trade.total_costs,
                    trade.exit_reason,
                ],
            )
            con.close()
        except Exception:
            pass

    def _record_equity_snapshot(self) -> None:
        snapshot_id = f"EQ_{uuid.uuid4().hex[:8]}"
        now = datetime.now(TZ_IST)
        try:
            con = duckdb.connect(str(self.config.db_path))
            con.execute(
                """
                INSERT INTO equity_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    snapshot_id,
                    self.run_id,
                    now,
                    self.portfolio.available_cash,
                    self.portfolio.invested_capital,
                    self.portfolio.unrealized_pnl,
                    self.portfolio.realized_pnl,
                    self.portfolio.total_equity,
                    self.portfolio.daily_drawdown_pct,
                ],
            )
            con.close()
        except Exception:
            pass

    def save_state(self, final_shutdown: bool = False) -> None:
        """Persist engine, portfolio, and broker state atomically to JSON with heartbeat telemetry."""
        state_path = Path(self.config.state_file)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_generation = uuid.uuid4().hex
        self.broker.save_state(
            state_path.with_name("paper_broker_state.json"), state_generation
        )
        self.portfolio.save_state(
            state_path.with_name("paper_portfolio_state.json"), state_generation
        )

        now_ist = datetime.now(TZ_IST)
        now_iso = now_ist.isoformat()

        # PID: Only persist process ID if mode is PAPER_TRADE and not final shutdown
        pid_val = None if final_shutdown or self.config.mode != "PAPER_TRADE" else os.getpid()

        # Connection and timing telemetry
        is_connected = False if final_shutdown else bool(getattr(self.feed, "is_connected", False))
        last_receipt_time = (
            self.feed.last_data_receipt_time.isoformat()
            if getattr(self.feed, "last_data_receipt_time", None)
            else None
        )
        last_exchange_time = (
            self.feed.last_exchange_time.isoformat()
            if getattr(self.feed, "last_exchange_time", None)
            else None
        )

        spot_quote_age: float | None = None
        if getattr(self.feed, "last_exchange_time", None):
            spot_quote_age = round(
                max(0.0, (now_ist - self.feed.last_exchange_time.astimezone(TZ_IST)).total_seconds()),
                2,
            )

        market_session_open = self.feed.is_market_session_open(now_ist)

        # Determine status string
        if final_shutdown:
            status = "STOPPED"
        elif not market_session_open:
            status = "MARKET_CLOSED"
        elif not is_connected:
            status = "RUNNING_DISCONNECTED"
        elif spot_quote_age is None or spot_quote_age > self.config.maximum_quote_age_seconds:
            status = "RUNNING_DATA_STALE"
        else:
            status = "RUNNING_CONNECTED"

        data = {
            "account_namespace": self.config.account_namespace,
            "run_id": self.run_id,
            "state_generation": state_generation,
            "pid": pid_val,
            "config_hash": self.config_hash,
            "mode": self.config.mode,
            "status": status,
            "heartbeat_time": now_iso,
            "is_connected": is_connected,
            "last_data_receipt_time": last_receipt_time,
            "last_exchange_time": last_exchange_time,
            "last_spot_price": self.last_spot_price,
            "spot_quote_age_seconds": spot_quote_age,
            "maximum_quote_age_seconds": self.config.maximum_quote_age_seconds,
            "market_session_open": market_session_open,
            "last_error": getattr(self.feed, "last_error", None),
            "connection_errors": getattr(self.feed, "connection_errors_count", 0),
            "feed_interruptions": getattr(self.feed, "feed_interruptions_count", 0),
            "active_position": (
                self.portfolio.active_position.__dict__
                if self.portfolio.active_position
                else None
            ),
            "pending_exit_reason": self.pending_exit_reason,
            "daily_realized_loss": self.risk_manager.daily_realized_loss,
            "consecutive_losses": self.risk_manager.consecutive_losses,
            "kill_switch_active": self.risk_manager.kill_switch_active,
            "available_cash": self.portfolio.available_cash,
            "invested_capital": self.portfolio.invested_capital,
            "total_equity": self.portfolio.total_equity,
            "realized_pnl": self.portfolio.realized_pnl,
            "unrealized_pnl": self.portfolio.unrealized_pnl,
            "daily_drawdown_pct": self.portfolio.daily_drawdown_pct,
            "max_allowed_allocation": self.portfolio.max_allowed_allocation_inr,
            "active_strategies": list(self.strategies.keys()),
            "completed_trades_count": len(self.completed_trades),
            "executed_signal_keys": [list(k) for k in self.executed_signal_keys],
            "saved_at": now_iso,
        }

        # Safe atomic write via temporary file and os.replace
        tmp_path = state_path.with_name(f"{state_path.name}.tmp.{os.getpid()}_{uuid.uuid4().hex[:6]}")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, state_path)

        logger.info(
            f"[TRANSITION] HEARTBEAT_PERSISTED: RunID={self.run_id}, "
            f"PID={pid_val}, Spot={self.last_spot_price:,.2f}"
        )

    def load_state(self) -> bool:
        """Restore state from disk for crash recovery and ensure consistent run ID."""
        state_path = Path(self.config.state_file)
        if not state_path.exists():
            return False

        try:
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)

            saved_run_id = data.get("run_id")
            if saved_run_id:
                self.run_id = saved_run_id
                self.recorder.run_id = saved_run_id

            broker_loaded = self.broker.load_state(
                state_path.with_name("paper_broker_state.json")
            )
            portfolio_loaded = self.portfolio.load_state(
                state_path.with_name("paper_portfolio_state.json")
            )
            expected_generation = data.get("state_generation")
            account_namespace = data.get("account_namespace")
            generations_match = bool(
                expected_generation
                and self.broker._loaded_state_generation == expected_generation
                and self.portfolio._loaded_state_generation == expected_generation
            )
            namespaces_match = (
                account_namespace == self.config.account_namespace == PaperBroker.ACCOUNT_NAMESPACE
            )
            cash_matches = abs(
                self.broker.current_cash - self.portfolio.available_cash
            ) <= 0.01
            broker_open = self.broker.get_open_positions()
            portfolio_position = self.portfolio.active_position
            positions_match = (
                not broker_open and portfolio_position is None
            ) or (
                len(broker_open) == 1
                and portfolio_position is not None
                and broker_open[0].symbol == portfolio_position.symbol
                and broker_open[0].quantity == portfolio_position.quantity
            )
            if not (
                broker_loaded
                and portfolio_loaded
                and generations_match
                and namespaces_match
                and cash_matches
                and positions_match
            ):
                self.config.paper_execution_enabled = False
                self.risk_manager.trigger_kill_switch("INCONSISTENT_RESTART_STATE")
                logger.error(
                    "Rejected inconsistent restart state: broker=%s portfolio=%s "
                    "generation=%s namespace=%s cash=%s positions=%s",
                    broker_loaded,
                    portfolio_loaded,
                    generations_match,
                    namespaces_match,
                    cash_matches,
                    positions_match,
                )
                return False

            self.risk_manager.daily_realized_loss = float(data.get("daily_realized_loss", 0.0))
            self.risk_manager.consecutive_losses = int(data.get("consecutive_losses", 0))
            self.risk_manager.kill_switch_active = bool(data.get("kill_switch_active", False))
            self.last_spot_price = float(data.get("last_spot_price", 0.0))
            self.pending_exit_reason = data.get("pending_exit_reason")

            loaded_keys = data.get("executed_signal_keys", [])
            self.executed_signal_keys = {tuple(k) for k in loaded_keys}
            return True
        except Exception:
            return False

    def acquire_process_lock(self) -> bool:
        """Acquire single-instance process lock for paper trading engine."""
        lock_path = Path(self.config.state_file).parent / "paper_engine.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self._lock_acquired:
            return True

        for _attempt in range(2):
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                existing_stat = None
                lock_data: dict[str, Any] = {}
                try:
                    existing_stat = lock_path.stat()
                    with open(lock_path) as f:
                        lock_data = json.load(f)
                except (OSError, json.JSONDecodeError):
                    pass

                existing_pid = lock_data.get("pid")
                existing_run_id = lock_data.get("run_id")
                existing_process_is_engine = False
                if existing_pid and is_pid_alive(existing_pid):
                    if existing_pid == os.getpid():
                        existing_process_is_engine = True
                    else:
                        cmd = get_process_cmd(existing_pid)
                        existing_process_is_engine = (
                            "trading_platform.paper" in cmd or "cli" in cmd
                        )

                if existing_process_is_engine:
                    raise RuntimeError(
                        f"Active paper trading engine already running (PID: {existing_pid}, RunID: {existing_run_id})! "
                        f"Duplicate engine launch rejected to prevent order conflicts."
                    ) from None

                # Remove only the same stale lock inode that was inspected.  If
                # another process replaced it meanwhile, retry without unlinking.
                try:
                    if existing_stat is not None and lock_path.stat().st_ino == existing_stat.st_ino:
                        lock_path.unlink()
                except OSError:
                    pass
                continue

            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(
                        {
                            "pid": os.getpid(),
                            "run_id": self.run_id,
                            "mode": self.config.mode,
                            "acquired_at": datetime.now(TZ_IST).isoformat(),
                        },
                        f,
                        indent=2,
                    )
                    f.flush()
                    os.fsync(f.fileno())
            except Exception:
                lock_path.unlink(missing_ok=True)
                raise

            self._lock_acquired = True
            logger.info(f"Acquired engine process lock on {lock_path} for PID {os.getpid()}")
            return True

        raise RuntimeError(f"Unable to acquire paper trading engine lock: {lock_path}")

    def release_process_lock(self) -> None:
        """Release single-instance process lock."""
        lock_path = Path(self.config.state_file).parent / "paper_engine.lock"
        if lock_path.exists():
            try:
                with open(lock_path) as f:
                    lock_data = json.load(f)
                if (
                    lock_data.get("pid") == os.getpid()
                    and lock_data.get("run_id") == self.run_id
                ):
                    lock_path.unlink(missing_ok=True)
                    logger.info(f"Released engine process lock for PID {os.getpid()}")
            except Exception:
                pass
        self._lock_acquired = False

    @classmethod
    def inspect_saved_state(
        cls, state_file_path: Path | str | None = None
    ) -> dict[str, Any]:
        """Read persisted engine telemetry and evaluate live process and connection health safely.

        Distinguishes:
        - RUNNING_CONNECTED
        - RUNNING_DATA_STALE
        - RUNNING_DISCONNECTED
        - STOPPED
        - MARKET_CLOSED
        """
        if state_file_path is None:
            state_file_path = Path("data/cache/paper_engine_state.json")
        else:
            state_file_path = Path(state_file_path)

        now_ist = datetime.now(TZ_IST)
        from trading_platform.data.calendar import NSECalendar

        is_market_open = NSECalendar().is_regular_trading_minute(now_ist)

        data: dict[str, Any] = {}
        if state_file_path.exists():
            for _ in range(2):
                try:
                    with open(state_file_path, encoding="utf-8") as f:
                        data = json.load(f)
                    break
                except (json.JSONDecodeError, OSError):
                    time_mod.sleep(0.05)

        run_id = data.get("run_id", "N/A")
        mode = data.get("mode", "STOPPED")
        pid = data.get("pid")

        alive = is_pid_alive(pid)
        cmd = get_process_cmd(pid) if alive else ""
        is_engine_proc = ("trading_platform.paper" in cmd or "cli" in cmd) if alive else False

        hb_str = data.get("heartbeat_time") or data.get("saved_at")
        hb_dt: datetime | None = None
        hb_age: float | None = None
        if hb_str:
            try:
                hb_dt = datetime.fromisoformat(hb_str).astimezone(TZ_IST)
                hb_age = max(0.0, (now_ist - hb_dt).total_seconds())
            except Exception:
                pass

        hb_fresh = (hb_age is not None and hb_age <= 15.0)
        process_running = bool(alive and is_engine_proc and hb_fresh and mode == "PAPER_TRADE")

        is_connected = bool(data.get("is_connected", False))
        last_receipt_str = data.get("last_data_receipt_time")
        last_exchange_str = data.get("last_exchange_time")
        exchange_dt: datetime | None = None
        quote_age: float | None = None
        if last_exchange_str:
            try:
                exchange_dt = datetime.fromisoformat(last_exchange_str).astimezone(TZ_IST)
                quote_age = max(0.0, (now_ist - exchange_dt).total_seconds())
            except Exception:
                pass

        maximum_quote_age_seconds = float(data.get("maximum_quote_age_seconds", 5.0))

        # Evaluate status
        if not process_running:
            if not is_market_open:
                status = "MARKET_CLOSED"
            else:
                status = "STOPPED"
        else:
            if not is_market_open:
                status = "MARKET_CLOSED"
            elif not is_connected:
                status = "RUNNING_DISCONNECTED"
            elif quote_age is None or quote_age > maximum_quote_age_seconds:
                status = "RUNNING_DATA_STALE"
            else:
                status = "RUNNING_CONNECTED"

        if process_running:
            engine_liveness = f"ALIVE (PID: {pid})"
        elif alive and is_engine_proc and not hb_fresh:
            engine_liveness = f"STALE_HEARTBEAT (PID: {pid})"
        else:
            engine_liveness = "STOPPED"

        if is_connected and quote_age is not None and quote_age <= maximum_quote_age_seconds:
            connection_status = "CONNECTED"
        elif is_connected and (quote_age is None or quote_age > maximum_quote_age_seconds):
            connection_status = "DATA_STALE"
        else:
            connection_status = "DISCONNECTED"

        hb_time_display = hb_dt.strftime("%Y-%m-%d %H:%M:%S IST") if hb_dt else "N/A"
        hb_age_display = f"{hb_age:.1f}s" if hb_age is not None else "N/A"
        receipt_display = (
            datetime.fromisoformat(last_receipt_str).astimezone(TZ_IST).strftime("%Y-%m-%d %H:%M:%S IST")
            if last_receipt_str
            else "N/A"
        )
        exchange_display = exchange_dt.strftime("%Y-%m-%d %H:%M:%S IST") if exchange_dt else "N/A"
        quote_age_display = f"{quote_age:.1f}s" if quote_age is not None else "N/A"

        spot_val = float(data.get("last_spot_price", 0.0))
        spot_display = f"₹{spot_val:,.2f}" if spot_val > 0 else "N/A"

        last_err = data.get("last_error")
        err_display = str(last_err) if last_err else "None"

        res = {
            "status": status,
            "engine_liveness": engine_liveness,
            "connection_status": connection_status,
            "run_id": run_id,
            "mode": mode,
            "pid": pid,
            "process_running": process_running,
            "process_running_str": "YES" if process_running else "NO",
            "market_session": "OPEN" if is_market_open else "CLOSED",
            "market_session_open": is_market_open,
            "heartbeat_time": hb_dt,
            "heartbeat_time_str": hb_time_display,
            "heartbeat_age_seconds": hb_age,
            "heartbeat_age_str": hb_age_display,
            "last_data_receipt_time": last_receipt_str,
            "last_data_receipt_str": receipt_display,
            "last_exchange_time": last_exchange_str,
            "last_exchange_time_str": exchange_display,
            "quote_age_seconds": quote_age,
            "quote_age_str": quote_age_display,
            "maximum_quote_age_seconds": maximum_quote_age_seconds,
            "nifty_spot": spot_val,
            "nifty_spot_str": spot_display,
            "last_error": err_display,
            "feed_interruptions": int(data.get("feed_interruptions", 0)),
            "connection_errors": int(data.get("connection_errors", 0)),
            "available_cash": float(data.get("available_cash", 250000.0)),
            "invested_capital": float(data.get("invested_capital", 0.0)),
            "total_equity": float(data.get("total_equity", 250000.0)),
            "realized_pnl": float(data.get("realized_pnl", 0.0)),
            "unrealized_pnl": float(data.get("unrealized_pnl", 0.0)),
            "daily_drawdown_pct": float(data.get("daily_drawdown_pct", 0.0)),
            "max_allowed_allocation": float(data.get("max_allowed_allocation", 50000.0)),
            "active_position": data.get("active_position"),
            "pending_exit_reason": data.get("pending_exit_reason"),
            "active_strategies": data.get("active_strategies", ["PDS", "ORB", "IRF", "PDR"]),
            "completed_trades_count": int(data.get("completed_trades_count", 0)),
            "active_bid_ask": data.get("active_bid_ask", "N/A"),
            "active_quote_age_seconds": data.get("active_quote_age_seconds"),
        }
        logger.info(
            f"[TRANSITION] CLI_STATUS_VISIBLE: Status={status}, "
            f"ProcessRunning={res['process_running_str']}, Spot={spot_display}, "
            f"QuoteAge={quote_age_display}"
        )
        return res

    def run_session_loop(
        self,
        poll_interval_seconds: float = 1.0,
        stop_time: dt_time | None = None,
        max_cycles: int | None = None,
    ) -> None:
        """Run continuous session loop until market close, configured stop time, or shutdown signal.

        Handles transient network errors, state persistence, scheduled square-off,
        and safe exit.
        """
        if stop_time is None:
            stop_time = dt_time(15, 30)

        if self.config.mode == "PAPER_TRADE":
            self.acquire_process_lock()

        logger.info(
            f"Starting session loop for {self.run_id} (mode={self.config.mode}) until {stop_time}"
        )
        cycles = 0

        try:
            while True:
                now_ist = datetime.now(TZ_IST)
                current_time = now_ist.time()

                if current_time >= stop_time:
                    logger.info(f"Reached configured session stop time {stop_time}. Ending session.")
                    break

                try:
                    if self.config.mode == "PAPER_TRADE":
                        self.run_paper_trade_cycle()
                    else:
                        self.run_live_read_only_cycle()
                except Exception as ex:
                    logger.error(f"Transient error in session loop cycle: {ex}", exc_info=True)
                    if hasattr(self.feed, "feed_interruptions_count"):
                        self.feed.feed_interruptions_count += 1
                        self.recorder.record_feed_interruption(
                            error_type="CYCLE_EXCEPTION",
                            error_message=str(ex),
                        )

                # Continuously persist engine state
                self.save_state()

                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    logger.info(f"Completed configured max cycles ({max_cycles}).")
                    break

                time_mod.sleep(poll_interval_seconds)

        except KeyboardInterrupt:
            logger.info("Safe shutdown requested via SIGINT / KeyboardInterrupt.")
        finally:
            if self.config.mode == "PAPER_TRADE":
                self.release_process_lock()
            self.save_state(final_shutdown=True)
            logger.info("Engine state safely persisted on session loop termination.")
