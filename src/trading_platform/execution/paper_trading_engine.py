"""Persistent, strictly paper-only NIFTY options trading engine with authenticated live data, BASELINE_V1 strategies, and DuckDB/Parquet event persistence."""

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from trading_platform.backtesting.indian_options_costs import (
    IndianOptionsCostCalculator,
)
from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.brokers.paper_broker import PaperBroker
from trading_platform.core.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    SignalDirection,
    Timeframe,
)
from trading_platform.core.models import Bar, Order, Signal, Trade
from trading_platform.data.live_market_feed import (
    Level2Quote,
    LiveMarketFeed,
    SpotTick,
)
from trading_platform.execution.options_risk_manager import (
    OptionsRiskManager,
    OptionsRiskPolicy,
)
from trading_platform.options.live_contract_selector import (
    LiveContractSelectionResult,
    LiveContractSelector,
)
from trading_platform.strategies.base import StrategyContext
from trading_platform.strategies.irf import IRFStrategy
from trading_platform.strategies.orb import ORBStrategy
from trading_platform.strategies.pdr import PDRStrategy
from trading_platform.strategies.pds import PDSStrategy

logger = logging.getLogger(__name__)

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


@dataclass
class PaperTradingConfig:
    """Rigorous configuration for persistent paper trading."""

    mode: str = "REPLAY_TEST"  # 'REPLAY_TEST', 'LIVE_READ_ONLY', 'PAPER_TRADE'
    initial_capital: float = 100000.0
    max_order_lots: int = 1
    max_open_positions: int = 1
    max_daily_loss_pct: float = 2.0  # Max 2% loss per day (₹2,000 on ₹100k)
    max_consecutive_losses: int = 3
    slippage_points: float = 0.50
    brokerage_per_order: float = 20.0
    max_spread_points: float = 5.0
    max_spread_pct: float = 5.0
    max_staleness_seconds: float = 30.0
    entry_cutoff_time: dt_time = dt_time(15, 0)  # No new entries after 15:00 IST
    forced_squareoff_time: dt_time = dt_time(15, 15)  # EOD exit at 15:15 IST
    poll_interval_seconds: float = 1.0

    # Strategy Enablement: BASELINE_V1 reference
    enable_pds: bool = True  # PDS Method 1
    enable_orb: bool = True  # ORB Method 1
    enable_irf: bool = True  # IRF Method 2
    enable_pdr: bool = False  # PDR available but disabled
    enable_rf: bool = False  # RF strictly excluded

    db_path: str = "data/processed/paper_trading.duckdb"
    state_file: str = "data/cache/paper_engine_state.json"
    reports_dir: str = "reports/paper_trading"


class PaperTradingEngine:
    """Persistent, institutional-grade NIFTY options paper trading orchestrator.

    STRICT GUARANTEE:
    This engine uses a dedicated PaperBroker. It is physically incapable of placing
    live orders with 5paisa or any broker.
    """

    def __init__(self, config: PaperTradingConfig | None = None) -> None:
        self.config = config or PaperTradingConfig()
        self.run_id = f"RUN_{datetime.now(TZ_IST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        # Validate mode
        valid_modes = ("REPLAY_TEST", "LIVE_READ_ONLY", "PAPER_TRADE")
        if self.config.mode not in valid_modes:
            raise ValueError(f"Invalid mode '{self.config.mode}'. Must be one of {valid_modes}")

        # Safety Gate: PaperBroker only
        self.cost_calculator = IndianOptionsCostCalculator(
            brokerage_per_order=self.config.brokerage_per_order,
            slippage_points_per_unit=self.config.slippage_points,
        )
        self.broker = PaperBroker(
            initial_balance=self.config.initial_capital,
            slippage_points=self.config.slippage_points,
            brokerage_per_order=self.config.brokerage_per_order,
            cost_calculator=self.cost_calculator,
        )

        # Live Market Data Client (Strictly Read-Only)
        self.live_data_broker = FivePaisaBroker()
        self.feed = LiveMarketFeed(
            broker=self.live_data_broker,
            poll_interval=self.config.poll_interval_seconds,
            max_staleness_seconds=self.config.max_staleness_seconds,
        )

        # Risk Management
        risk_policy = OptionsRiskPolicy(
            initial_capital=self.config.initial_capital,
            max_position_capital_pct=100.0,  # Max 1 lot can take up to available capital
            max_risk_per_trade_pct=2.0,
            max_daily_loss_pct=self.config.max_daily_loss_pct,
            max_open_positions=self.config.max_open_positions,
            no_averaging_down=True,
        )
        self.risk_manager = OptionsRiskManager(policy=risk_policy)

        # Contract Selector
        self.contract_selector = LiveContractSelector(
            broker=self.live_data_broker,
            feed=self.feed,
            max_spread_points=self.config.max_spread_points,
            max_spread_pct=self.config.max_spread_pct,
            max_staleness_seconds=self.config.max_staleness_seconds,
        )

        # Strategies
        self.strategies: dict[str, Any] = {}
        self.strategy_contexts: dict[str, StrategyContext] = {}
        self._init_strategies()

        # Engine Tracking State
        self.active_position_contract: dict[str, Any] | None = None
        self.consecutive_losses: int = 0
        self.kill_switch_active: bool = False
        self.completed_trades: list[Trade] = []
        self.signals_log: list[dict[str, Any]] = []
        self.contract_selections_log: list[dict[str, Any]] = []
        self.rejections_log: list[dict[str, Any]] = []
        self.last_spot_price: float = 0.0
        self.last_spot_time: datetime | None = None
        self.closed_bars: list[Bar] = []

        # DuckDB Connection
        self.db_path = Path(self.config.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_duckdb()

        # Configuration Hash
        self.config_hash = self._compute_config_hash()

        # Restore state if present
        self.load_state()

    def _init_strategies(self) -> None:
        """Initialize frozen BASELINE_V1 strategies."""
        if self.config.enable_pds:
            pds = PDSStrategy(config={"method": "METHOD_1", "target_points": 70.0})
            self.strategies["PDS"] = pds
            self.strategy_contexts["PDS"] = StrategyContext(strategy_name="PDS")

        if self.config.enable_orb:
            orb = ORBStrategy(
                config={"method": "METHOD_1", "target_points": 60.0, "stop_points": 30.0}
            )
            self.strategies["ORB"] = orb
            self.strategy_contexts["ORB"] = StrategyContext(strategy_name="ORB")

        if self.config.enable_irf:
            irf = IRFStrategy(config={"method": "METHOD_2", "target_points": 60.0})
            self.strategies["IRF"] = irf
            self.strategy_contexts["IRF"] = StrategyContext(strategy_name="IRF")

        if self.config.enable_pdr:
            pdr = PDRStrategy(config={"method": "METHOD_1", "target_points": 70.0})
            self.strategies["PDR"] = pdr
            self.strategy_contexts["PDR"] = StrategyContext(strategy_name="PDR")

        # RF is strictly not loaded

    def _init_duckdb(self) -> None:
        """Initialize DuckDB tables for event capture."""
        con = duckdb.connect(str(self.db_path))
        con.execute("""
            CREATE TABLE IF NOT EXISTS market_events (
                event_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                scrip_code BIGINT,
                symbol VARCHAR,
                last_rate DOUBLE,
                high DOUBLE,
                low DOUBLE,
                is_stale BOOLEAN
            );
            CREATE TABLE IF NOT EXISTS candles_5m (
                bar_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                exchange_time TIMESTAMP,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT
            );
            CREATE TABLE IF NOT EXISTS signals (
                signal_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                strategy_name VARCHAR,
                method_id VARCHAR,
                direction VARCHAR,
                reference_price DOUBLE,
                stop_loss DOUBLE,
                target DOUBLE,
                reason VARCHAR,
                is_executed BOOLEAN
            );
            CREATE TABLE IF NOT EXISTS contract_selections (
                selection_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                signal_id VARCHAR,
                status VARCHAR,
                option_type VARCHAR,
                strike DOUBLE,
                expiry DATE,
                dte INTEGER,
                scrip_code BIGINT,
                symbol VARCHAR,
                best_bid DOUBLE,
                best_ask DOUBLE,
                spread DOUBLE,
                reason VARCHAR
            );
            CREATE TABLE IF NOT EXISTS virtual_orders (
                order_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                symbol VARCHAR,
                side VARCHAR,
                quantity INTEGER,
                requested_price DOUBLE,
                fill_price DOUBLE,
                status VARCHAR
            );
            CREATE TABLE IF NOT EXISTS virtual_fills (
                fill_id VARCHAR PRIMARY KEY,
                order_id VARCHAR,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                symbol VARCHAR,
                side VARCHAR,
                quantity INTEGER,
                price DOUBLE,
                slippage DOUBLE,
                brokerage DOUBLE,
                statutory_charges DOUBLE
            );
            CREATE TABLE IF NOT EXISTS completed_trades (
                trade_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                strategy_name VARCHAR,
                method_id VARCHAR,
                symbol VARCHAR,
                quantity INTEGER,
                entry_time TIMESTAMP,
                entry_premium DOUBLE,
                exit_time TIMESTAMP,
                exit_premium DOUBLE,
                underlying_entry_spot DOUBLE,
                underlying_exit_spot DOUBLE,
                underlying_points_pnl DOUBLE,
                option_gross_pnl DOUBLE,
                option_net_pnl DOUBLE,
                total_costs DOUBLE,
                exit_reason VARCHAR
            );
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                available_cash DOUBLE,
                invested_capital DOUBLE,
                unrealized_pnl DOUBLE,
                realized_pnl DOUBLE,
                total_equity DOUBLE,
                daily_drawdown DOUBLE
            );
        """)
        con.close()

    def _compute_config_hash(self) -> str:
        """Create immutable hash of current engine configuration."""
        cfg_dict = asdict(self.config)
        cfg_str = json.dumps(cfg_dict, sort_keys=True, default=str)
        return hashlib.sha256(cfg_str.encode("utf-8")).hexdigest()[:16]

    def trigger_kill_switch(self, reason: str = "MANUAL_TRIGGER") -> None:
        """Emergency kill switch to halt all new entries and immediately square off open position."""
        logger.warning(f"KILL SWITCH TRIGGERED: {reason}")
        self.kill_switch_active = True
        if self.active_position_contract is not None:
            self._close_active_position(
                exit_reason="KILL_SWITCH",
                exit_time=datetime.now(TZ_IST),
                exit_spot=self.last_spot_price,
            )

    def on_tick(self, tick: SpotTick) -> Bar | None:
        """Process incoming spot tick, evaluate open position exits, and pass to candle aggregator."""
        self.last_spot_price = tick.last_rate
        self.last_spot_time = tick.provider_timestamp

        # Persist tick event
        self._record_market_event(tick)

        # 1. Evaluate underlying exits on live tick
        if self.active_position_contract is not None:
            self.evaluate_exits(current_spot=tick.last_rate, tick_time=tick.provider_timestamp)

        # 2. Check intraday cutoff (15:15 IST)
        t = tick.provider_timestamp.astimezone(TZ_IST).time()
        if (
            t >= self.config.forced_squareoff_time
            and self.active_position_contract is not None
        ):
            self._close_active_position(
                exit_reason="EOD_SQUAREOFF",
                exit_time=tick.provider_timestamp,
                exit_spot=tick.last_rate,
            )

        # 3. Aggregate 5-minute candle
        closed_bar = self.feed.aggregator.process_tick(tick)
        if closed_bar is not None:
            self.closed_bars.append(closed_bar)
            self._record_candle(closed_bar)
            self.on_candle_close(closed_bar)
            return closed_bar

        return None

    def on_candle_close(self, bar: Bar) -> list[Signal]:
        """Evaluate strategy signals strictly on 5-minute candle close."""
        generated_signals: list[Signal] = []

        # Check cutoff time for new entries
        bar_time = bar.exchange_time.time()
        if bar_time >= self.config.entry_cutoff_time:
            return generated_signals

        # Check Kill Switch
        if self.kill_switch_active:
            return generated_signals

        # Evaluate each active strategy
        for name, strategy in self.strategies.items():
            ctx = self.strategy_contexts[name]
            # Update day reference levels in context if needed
            self._update_strategy_context_levels(ctx, bar)

            sig = strategy.on_bar(ctx, bar)
            if sig is not None:
                generated_signals.append(sig)
                self._record_signal(sig, is_executed=False)

        # If we already have an open position, do not execute new signals
        if self.active_position_contract is not None:
            for s in generated_signals:
                self._record_rejection(
                    s, "REJECTED_POSITION_CONCURRENCY", "Max 1 open position limit reached"
                )
            return generated_signals

        # Arbitrate signals (First valid signal wins)
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
        # In a live session, if not set, set from bar or cache
        if "pdl" not in ctx.state:
            # Approximate reference or load from daily bar
            ctx.state["pdl"] = bar.low - 50.0  # fallback baseline
            ctx.state["pdh"] = bar.high + 50.0
            ctx.state["prev_close"] = bar.open
            ctx.state["ema200"] = bar.close
            ctx.state["dma200"] = bar.close

    def execute_signal(
        self,
        signal: Signal,
        spot_price: float,
        candle_time: datetime,
        mock_quote: Level2Quote | None = None,
    ) -> bool:
        """Route strategy signal through contract selector, risk manager, and PaperBroker."""
        # Check active position concurrency
        if self.active_position_contract is not None:
            self._record_rejection(
                signal,
                "REJECTED_POSITION_CONCURRENCY",
                "Max 1 open position limit reached",
            )
            return False

        # Mode gate: In LIVE_READ_ONLY mode, do not execute
        if self.config.mode == "LIVE_READ_ONLY":
            self._record_rejection(
                signal, "LIVE_READ_ONLY_MODE", "Order simulation disabled in LIVE_READ_ONLY mode"
            )
            return False

        # 1. Kill Switch check
        if self.kill_switch_active:
            self._record_rejection(
                signal, "REJECTED_KILL_SWITCH", "Kill switch is currently active"
            )
            return False

        # 2. Consecutive losses check
        if self.consecutive_losses >= self.config.max_consecutive_losses:
            self._record_rejection(
                signal,
                "REJECTED_MAX_CONSECUTIVE_LOSSES",
                f"Consecutive losses {self.consecutive_losses} >= {self.config.max_consecutive_losses}",
            )
            return False

        # 3. Contract Selection
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

        # 4. Risk Manager Evaluation
        size_result = self.risk_manager.calculate_position_size(
            entry_premium=sel.best_ask,
            lot_size=sel.lot_size,
            contract_id=sel.symbol,
        )

        if not size_result.is_approved:
            self._record_rejection(signal, "REJECTED_RISK_LIMIT", size_result.reason)
            return False

        # Enforce max 1 lot
        lots_to_trade = min(self.config.max_order_lots, size_result.lots)
        trade_qty = lots_to_trade * sel.lot_size

        # 5. Virtual Order Submission to PaperBroker
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
            strategy_name=signal.strategy_name,
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

        # 6. Track Active Position
        self.risk_manager.register_position_opened(sel.symbol)
        self.active_position_contract = {
            "symbol": sel.symbol,
            "scrip_code": sel.scrip_code,
            "lot_size": sel.lot_size,
            "quantity": trade_qty,
            "entry_premium": executed_order.average_fill_price,
            "entry_time": candle_time,
            "underlying_entry_spot": spot_price,
            "underlying_stop_loss": signal.stop_loss,
            "underlying_target": signal.target,
            "strategy_name": signal.strategy_name,
            "method_id": signal.method_id,
            "direction": signal.direction,
            "option_type": sel.option_type,
            "strike": sel.strike,
            "expiry": sel.expiry,
            "order_id": order_id,
        }

        # Persist order and fill
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
        if self.active_position_contract is None:
            return False

        pos = self.active_position_contract
        direction = pos["direction"]
        sl = pos["underlying_stop_loss"]
        tp = pos["underlying_target"]

        exit_reason = None

        if direction == SignalDirection.BUY:
            # Bullish (CE): Spot below SL is Stop Loss; Spot above TP is Target
            if current_spot <= sl:
                exit_reason = "STOP_LOSS"
            elif current_spot >= tp:
                exit_reason = "TARGET"
        elif direction == SignalDirection.SELL:
            # Bearish (PE): Spot above SL is Stop Loss; Spot below TP is Target
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
    ) -> None:
        """Simulate closing open option position at current live executable Bid quote."""
        pos = self.active_position_contract
        if pos is None:
            return

        symbol = pos["symbol"]
        scrip_code = pos["scrip_code"]
        qty = pos["quantity"]
        entry_premium = pos["entry_premium"]
        entry_spot = pos["underlying_entry_spot"]
        t_date = exit_time.astimezone(TZ_IST).date()

        # Fetch live Bid depth
        if mock_bid is not None:
            bid_price = mock_bid
        else:
            depth = self.feed.fetch_option_depth(scrip_code=scrip_code, symbol=symbol)
            if depth and depth.best_bid > 0:
                bid_price = depth.best_bid
            else:
                # If no executable bid quote is available
                bid_price = max(0.05, round(entry_premium * 0.95, 2))  # Conservative estimate
                logger.warning(
                    f"NO_EXECUTABLE_QUOTE on exit for {symbol}. Using conservative estimate {bid_price}."
                )

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
            strategy_name=pos["strategy_name"],
            method_id=pos["method_id"],
        )

        executed_order = self.broker.execute_option_order(
            order=order,
            bid=bid_price,
            ask=None,
            trade_date=t_date,
            slippage_points=self.config.slippage_points,
        )

        exit_premium = executed_order.average_fill_price

        # Calculate round-trip costs
        cost_breakdown = self.cost_calculator.calculate_costs(
            trade_date=t_date,
            entry_premium=entry_premium,
            exit_premium=exit_premium,
            quantity=qty,
            apply_slippage=False,  # Slippage was already applied in execute_option_order
        )

        # Dual Accounting
        # 1. Underlying result in points
        if pos["direction"] == SignalDirection.BUY:
            underlying_pts_pnl = round(exit_spot - entry_spot, 2)
        else:
            underlying_pts_pnl = round(entry_spot - exit_spot, 2)

        # 2. Option Rupees result
        option_gross_pnl = round((exit_premium - entry_premium) * qty, 2)
        option_net_pnl = round(option_gross_pnl - cost_breakdown.total_transaction_costs, 2)

        # Update Risk Manager
        self.risk_manager.record_trade_completion(net_pnl=option_net_pnl, contract_id=symbol)

        # Consecutive loss tracking
        if option_net_pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        # Create Trade record
        trade = Trade(
            trade_id=f"TRD_{pos['order_id']}",
            strategy_name=pos["strategy_name"],
            method_id=pos["method_id"],
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=qty,
            entry_time=pos["entry_time"],
            entry_price=entry_premium,
            exit_time=exit_time,
            exit_price=exit_premium,
            stop_loss=pos["underlying_stop_loss"],
            target=pos["underlying_target"],
            gross_pnl=option_gross_pnl,
            net_pnl=option_net_pnl,
            total_costs=cost_breakdown.total_transaction_costs,
            exit_reason=exit_reason,
            entry_rule_description=f"{pos['strategy_name']} {pos['method_id']} {pos['option_type']}",
        )
        self.completed_trades.append(trade)

        # Persist completed trade
        self._record_completed_trade(trade, entry_spot, exit_spot, underlying_pts_pnl)
        self._record_virtual_order(executed_order)

        # Clear active position
        self.active_position_contract = None
        self._record_equity_snapshot()
        self.save_state()

    def replay_bars(self, bars: list[Bar]) -> None:
        """Deterministic offline replay of 5-minute bars for pipeline testing."""
        for bar in bars:
            # Simulate open tick
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

            # Close boundary tick (crosses into next 5-min window to close bar)
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
        return tick

    def run_paper_trade_cycle(self) -> SpotTick | None:
        """Execute a single polling and execution cycle in PAPER_TRADE mode."""
        tick = self.feed.fetch_spot_tick()
        if tick is not None:
            self.on_tick(tick)
        return tick

    def get_dashboard_summary(self) -> dict[str, Any]:
        """Compile complete real-time telemetry metrics for the Streamlit dashboard."""
        margins = self.broker.get_margins()
        self.broker.get_open_positions()

        # Update MTM on open position if we have a current option price
        current_opt_price = 0.0
        bid_ask_str = "N/A"
        if self.active_position_contract is not None:
            depth = self.feed.fetch_option_depth(
                scrip_code=self.active_position_contract["scrip_code"],
                symbol=self.active_position_contract["symbol"],
            )
            if depth:
                current_opt_price = depth.mid_price
                bid_ask_str = f"{depth.best_bid:.2f} / {depth.best_ask:.2f}"
                self.broker.update_market_price(
                    self.active_position_contract["symbol"], current_opt_price
                )
                margins = self.broker.get_margins()

        # Drawdown calculation
        equity = margins["total_equity"]
        peak_equity = max(self.config.initial_capital, equity)
        drawdown_pct = (
            ((peak_equity - equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0
        )

        return {
            "run_id": self.run_id,
            "mode": self.config.mode,
            "config_hash": self.config_hash,
            "is_connected": self.feed.is_connected,
            "connection_errors": self.feed.connection_errors_count,
            "nifty_spot": self.last_spot_price,
            "last_spot_time": self.last_spot_time.isoformat()
            if self.last_spot_time
            else None,
            "kill_switch_active": self.kill_switch_active,
            "consecutive_losses": self.consecutive_losses,
            "available_cash": margins["available_cash"],
            "total_equity": margins["total_equity"],
            "unrealized_pnl": margins["unrealized_pnl"],
            "realized_pnl": margins["realized_pnl"],
            "daily_drawdown_pct": round(drawdown_pct, 2),
            "total_brokerage_paid": margins["total_brokerage_paid"],
            "total_statutory_paid": margins["total_statutory_paid"],
            "active_position": self.active_position_contract,
            "active_bid_ask": bid_ask_str,
            "completed_trades_count": len(self.completed_trades),
            "signals_count": len(self.signals_log),
            "closed_candles_count": len(self.closed_bars),
        }

    # Persistence Helpers
    def _record_market_event(self, tick: SpotTick) -> None:
        try:
            con = duckdb.connect(str(self.db_path))
            event_id = f"EVT_{uuid.uuid4().hex[:8]}"
            con.execute(
                """
                INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    event_id,
                    self.run_id,
                    tick.provider_timestamp,
                    tick.scrip_code,
                    tick.symbol,
                    tick.last_rate,
                    tick.high,
                    tick.low,
                    tick.is_stale,
                ],
            )
            con.close()
        except Exception:
            pass

    def _record_candle(self, bar: Bar) -> None:
        try:
            con = duckdb.connect(str(self.db_path))
            bar_id = f"BAR_{uuid.uuid4().hex[:8]}"
            con.execute(
                """
                INSERT INTO candles_5m VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    bar_id,
                    self.run_id,
                    bar.exchange_time,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                ],
            )
            con.close()
        except Exception:
            pass

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
            con = duckdb.connect(str(self.db_path))
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
            con = duckdb.connect(str(self.db_path))
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
            con = duckdb.connect(str(self.db_path))
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
            # Record Fill
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
            con = duckdb.connect(str(self.db_path))
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
        margins = self.broker.get_margins()
        snapshot_id = f"EQ_{uuid.uuid4().hex[:8]}"
        now = datetime.now(TZ_IST)
        equity = margins["total_equity"]
        peak = max(self.config.initial_capital, equity)
        dd = ((peak - equity) / peak) * 100.0 if peak > 0 else 0.0
        try:
            con = duckdb.connect(str(self.db_path))
            con.execute(
                """
                INSERT INTO equity_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    snapshot_id,
                    self.run_id,
                    now,
                    margins["available_cash"],
                    margins["invested_capital"],
                    margins["unrealized_pnl"],
                    margins["realized_pnl"],
                    margins["total_equity"],
                    round(dd, 2),
                ],
            )
            con.close()
        except Exception:
            pass

    def save_state(self) -> None:
        """Persist engine state to JSON to prevent duplicate positions on restart."""
        state_path = Path(self.config.state_file)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        # Also persist broker state
        self.broker.save_state(state_path.with_name("paper_broker_state.json"))

        data = {
            "run_id": self.run_id,
            "config_hash": self.config_hash,
            "mode": self.config.mode,
            "active_position": self.active_position_contract,
            "consecutive_losses": self.consecutive_losses,
            "kill_switch_active": self.kill_switch_active,
            "last_spot_price": self.last_spot_price,
            "saved_at": datetime.now(TZ_IST).isoformat(),
        }
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def load_state(self) -> bool:
        """Recover engine state on startup."""
        state_path = Path(self.config.state_file)
        if not state_path.exists():
            return False

        try:
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)

            self.broker.load_state(state_path.with_name("paper_broker_state.json"))
            self.active_position_contract = data.get("active_position")
            if self.active_position_contract is not None:
                sym = self.active_position_contract.get("symbol")
                if sym and sym not in self.risk_manager._open_contracts:
                    self.risk_manager.register_position_opened(sym)
            self.consecutive_losses = int(data.get("consecutive_losses", 0))
            self.kill_switch_active = bool(data.get("kill_switch_active", False))
            self.last_spot_price = float(data.get("last_spot_price", 0.0))
            return True
        except Exception:
            return False

    def export_daily_parquet(self, output_dir: str | None = None) -> dict[str, Path]:
        """Export all DuckDB tables to Parquet files for daily archive."""
        out_path = Path(output_dir or self.config.reports_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        con = duckdb.connect(str(self.db_path))
        tables = [
            "candles_5m",
            "signals",
            "contract_selections",
            "virtual_orders",
            "virtual_fills",
            "completed_trades",
            "equity_snapshots",
        ]
        exported: dict[str, Path] = {}
        for tbl in tables:
            df = con.execute(f"SELECT * FROM {tbl} WHERE run_id = '{self.run_id}'").df()
            p_file = out_path / f"{tbl}_{self.run_id}.parquet"
            df.to_parquet(p_file, compression="zstd")
            exported[tbl] = p_file
        con.close()
        return exported


if __name__ == "__main__":
    import argparse
    import sys

    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="NIFTY Options Paper Trading Engine")
    parser.add_argument(
        "--mode",
        choices=["REPLAY_TEST", "LIVE_READ_ONLY", "PAPER_TRADE"],
        default="REPLAY_TEST",
        help="Operating mode (default: REPLAY_TEST)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100000.0,
        help="Starting virtual capital in INR (default: 100,000)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=10,
        help="Number of monitoring cycles for live modes (default: 10)",
    )
    parser.add_argument(
        "--kill-switch",
        action="store_true",
        help="Engage kill switch and exit open positions",
    )
    parser.add_argument(
        "--export-reports",
        action="store_true",
        help="Export DuckDB event tables to Parquet",
    )

    args = parser.parse_args()

    cfg = PaperTradingConfig(mode=args.mode, initial_capital=args.capital)
    engine = PaperTradingEngine(cfg)

    print("=" * 70)
    print("🚨 NIFTY OPTIONS PAPER TRADING ENGINE 🚨")
    print(f"Run ID:         {engine.run_id}")
    print(f"Mode:           {engine.config.mode}")
    print(f"Config Hash:    {engine.config_hash}")
    print(f"Virtual Cash:   ₹{engine.broker.current_cash:,.2f}")
    print(f"Strategies:     {list(engine.strategies.keys())}")
    print("=" * 70)

    if args.kill_switch:
        engine.trigger_kill_switch("CLI_KILL_SWITCH_REQUEST")
        print("Kill switch executed. Exiting.")
        sys.exit(0)

    if args.mode == "REPLAY_TEST":
        print("\nExecuting deterministic REPLAY_TEST with historical baseline bars...")
        # Load sample historical bars
        bars_path = Path("data/processed/nifty/5m/nifty50_5m.parquet")
        if bars_path.exists():
            df = pd.read_parquet(bars_path).tail(20)
            from trading_platform.brokers.fivepaisa import FivePaisaBroker

            bars = FivePaisaBroker().parse_nifty_bars(df)
            engine.replay_bars(bars)
            print(f"Replayed {len(bars)} 5-minute bars.")
        else:
            print("No historical bars found. Simulating synthetic bar sequence.")
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("Asia/Kolkata")
            sample_bar = Bar(
                timestamp=datetime(2026, 10, 21, 9, 15, tzinfo=ZoneInfo("UTC")),
                exchange_time=datetime(2026, 10, 21, 9, 15, tzinfo=tz),
                symbol="NIFTY 50",
                instrument_id="999920000",
                interval=Timeframe.M5,
                open=22800.0,
                high=22850.0,
                low=22790.0,
                close=22840.0,
            )
            engine.on_candle_close(sample_bar)
            print("Processed sample synthetic bar.")

    elif args.mode in ("LIVE_READ_ONLY", "PAPER_TRADE"):
        print(f"\nStarting {args.mode} monitoring loop ({args.cycles} cycles)...")
        for i in range(1, args.cycles + 1):
            if args.mode == "LIVE_READ_ONLY":
                tick = engine.run_live_read_only_cycle()
            else:
                tick = engine.run_paper_trade_cycle()

            if tick:
                print(f"[{i}/{args.cycles}] Spot: {tick.last_rate:,.2f} | Time: {tick.provider_timestamp.strftime('%H:%M:%S')} | Stale: {tick.is_stale}")
            else:
                print(f"[{i}/{args.cycles}] Market poll returned no tick (market offline or disconnected).")

            import time
            time.sleep(1.0)

    # Summary
    summary = engine.get_dashboard_summary()
    print("\n" + "=" * 70)
    print("SESSION SUMMARY:")
    print(f"Total Equity:       ₹{summary['total_equity']:,.2f}")
    print(f"Realized P&L:       ₹{summary['realized_pnl']:,.2f}")
    print(f"Unrealized P&L:     ₹{summary['unrealized_pnl']:,.2f}")
    print(f"Closed Trades:      {summary['completed_trades_count']}")
    print(f"Signals Generated:  {summary['signals_count']}")
    print(f"Candles Processed:  {summary['closed_candles_count']}")
    print("=" * 70)

    if args.export_reports:
        exported = engine.export_daily_parquet()
        print(f"Exported {len(exported)} Parquet tables to reports/paper_trading/")

