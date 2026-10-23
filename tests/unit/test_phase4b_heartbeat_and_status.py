"""Unit and regression tests for Phase 4B Persistent Heartbeat, Lock, and CLI Status Reporting."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.enums import OrderSide, OrderStatus, OrderType
from trading_platform.core.exceptions import LiveTradingPermissionError
from trading_platform.core.models import Order
from trading_platform.data.live_market_feed import LiveMarketFeed, parse_fivepaisa_timestamp
from trading_platform.paper.engine import PaperEngineConfig, PaperTradingEngine

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

TZ_IST = ZoneInfo("Asia/Kolkata")


def test_known_fivepaisa_epoch_is_2024_not_2026():
    """The raw epoch cited in prior reports resolves to 22-Oct-2024 IST."""
    parsed = parse_fivepaisa_timestamp("/Date(1729571156000+0530)/")
    assert parsed == datetime(2024, 10, 22, 9, 55, 56, tzinfo=TZ_IST)


@pytest.mark.parametrize(
    ("age_seconds", "expected_stale"),
    [(4.9, False), (5.0, False), (5.1, True)],
)
def test_quote_freshness_boundary_is_configured_limit(age_seconds, expected_stale):
    """Quote acceptance is inclusive at 5.0s and rejects immediately above it."""

    fixed_now = datetime(2026, 10, 21, 10, 0, 0, tzinfo=TZ_IST)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    broker = FivePaisaBroker()
    broker.client = MagicMock()
    broker.client.fetch_market_feed_scrip.return_value = {
        "Status": 0,
        "Message": "Success",
        "Data": [
            {
                "LastRate": 22650.0,
                "High": 22700.0,
                "Low": 22600.0,
                "PClose": 22500.0,
                "TickDt": (fixed_now - timedelta(seconds=age_seconds)).isoformat(),
            }
        ],
    }
    feed = LiveMarketFeed(broker=broker, max_staleness_seconds=5.0)
    feed.is_connected = True

    with patch("trading_platform.data.live_market_feed.datetime", FrozenDateTime):
        tick = feed.fetch_spot_tick()

    assert tick is not None
    assert tick.is_stale is expected_stale


def test_malformed_provider_timestamp_is_rejected():
    """Malformed provider timestamps must never be replaced with local 'now'."""
    with pytest.raises(ValueError, match="Invalid 5paisa timestamp"):
        parse_fivepaisa_timestamp("not-a-provider-timestamp")


def test_missing_paper_config_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A missing configuration must never implicitly enable paper execution."""
    monkeypatch.chdir(tmp_path)
    config = PaperEngineConfig.from_yaml("missing.yaml")
    assert config.mode == "LIVE_READ_ONLY"
    assert config.paper_execution_enabled is False


def test_malformed_paper_config_fails_closed(tmp_path: Path):
    """A malformed configuration must force the engine into read-only mode."""
    config_path = tmp_path / "malformed.yaml"
    config_path.write_text("portfolio:\n  entry_cutoff_ist: not-a-time\n", encoding="utf-8")
    config = PaperEngineConfig.from_yaml(config_path)
    assert config.mode == "LIVE_READ_ONLY"
    assert config.paper_execution_enabled is False


@pytest.fixture
def mock_engine_config(tmp_path: Path) -> PaperEngineConfig:
    """Create isolated test config in a temporary directory."""
    db_file = tmp_path / "paper_test.duckdb"
    state_file = tmp_path / "cache" / "paper_engine_state.json"
    reports_dir = tmp_path / "reports"
    return PaperEngineConfig(
        mode="PAPER_TRADE",
        paper_execution_enabled=True,
        starting_capital=250000.0,
        maximum_lots_per_trade=2,
        maximum_concurrent_positions=1,
        max_allocation_percent=20.0,
        daily_loss_limit_percent=2.0,
        slippage_points=0.50,
        brokerage_per_order=20.0,
        db_path=db_file,
        state_file=state_file,
        reports_dir=reports_dir,
    )


# -------------------------------------------------------------------------
# 1. API Success with Fresh Quote -> RUNNING_CONNECTED
# -------------------------------------------------------------------------
def test_api_success_with_fresh_quote(mock_engine_config: PaperEngineConfig):
    """API returns status 0 with fresh exchange timestamp; engine evaluates RUNNING_CONNECTED."""
    engine = PaperTradingEngine(mock_engine_config)
    now_ist = datetime.now(TZ_IST)
    tick_str = f"/Date({int(now_ist.timestamp() * 1000)}+0530)/"

    mock_client = MagicMock()
    mock_client.fetch_market_feed_scrip.return_value = {
        "Status": 0,
        "Message": "Success",
        "Data": [
            {
                "LastRate": 22650.0,
                "High": 22700.0,
                "Low": 22600.0,
                "PClose": 22500.0,
                "TickDt": tick_str,
            }
        ],
    }
    engine.feed.broker.client = mock_client
    engine.feed.is_connected = True

    tick = engine.feed.fetch_spot_tick()
    assert tick is not None
    assert tick.last_rate == 22650.0
    assert not tick.is_stale

    engine.on_tick(tick)
    engine.save_state()

    with patch(
        "trading_platform.data.calendar.NSECalendar.is_regular_trading_minute",
        return_value=True,
    ), patch(
        "trading_platform.paper.engine.get_process_cmd",
        return_value="python -m trading_platform.paper.cli start",
    ):
        status_info = PaperTradingEngine.inspect_saved_state(mock_engine_config.state_file)
        assert status_info["status"] == "RUNNING_CONNECTED"
        assert status_info["connection_status"] == "CONNECTED"
        assert status_info["process_running"] is True
        assert status_info["process_running_str"] == "YES"
        assert "22,650.00" in status_info["nifty_spot_str"]


# -------------------------------------------------------------------------
# 2. API Success with Stale Quote -> RUNNING_DATA_STALE
# -------------------------------------------------------------------------
def test_api_success_with_stale_quote(mock_engine_config: PaperEngineConfig):
    """API returns status 0, but timestamp is 45s old; evaluated as RUNNING_DATA_STALE."""
    engine = PaperTradingEngine(mock_engine_config)
    old_time = datetime.now(TZ_IST) - timedelta(seconds=45)
    tick_str = f"/Date({int(old_time.timestamp() * 1000)}+0530)/"

    mock_client = MagicMock()
    mock_client.fetch_market_feed_scrip.return_value = {
        "Status": 0,
        "Message": "Success",
        "Data": [
            {
                "LastRate": 22650.0,
                "High": 22700.0,
                "Low": 22600.0,
                "PClose": 22500.0,
                "TickDt": tick_str,
            }
        ],
    }
    engine.feed.broker.client = mock_client
    engine.feed.is_connected = True

    tick = engine.feed.fetch_spot_tick()
    assert tick is not None
    assert tick.is_stale is True

    engine.on_tick(tick)
    engine.save_state()

    with patch(
        "trading_platform.data.calendar.NSECalendar.is_regular_trading_minute",
        return_value=True,
    ), patch(
        "trading_platform.paper.engine.get_process_cmd",
        return_value="python -m trading_platform.paper.cli start",
    ):
        status_info = PaperTradingEngine.inspect_saved_state(mock_engine_config.state_file)
        assert status_info["status"] == "RUNNING_DATA_STALE"
        assert status_info["connection_status"] == "DATA_STALE"


# -------------------------------------------------------------------------
# 3. API Success with Empty Data -> RUNNING_DISCONNECTED
# -------------------------------------------------------------------------
def test_api_success_with_empty_data(mock_engine_config: PaperEngineConfig):
    """API returns status 0, but Data array is empty; feed catches it and marks error."""
    engine = PaperTradingEngine(mock_engine_config)
    mock_client = MagicMock()
    mock_client.fetch_market_feed_scrip.return_value = {
        "Status": 0,
        "Message": "Success",
        "Data": [],
    }
    engine.feed.broker.client = mock_client
    engine.feed.is_connected = True

    tick = engine.feed.fetch_spot_tick()
    assert tick is None
    assert engine.feed.last_error == "API returned empty Data list"

    engine.feed.is_connected = False
    engine.save_state()

    with patch(
        "trading_platform.data.calendar.NSECalendar.is_regular_trading_minute",
        return_value=True,
    ), patch(
        "trading_platform.paper.engine.get_process_cmd",
        return_value="python -m trading_platform.paper.cli start",
    ):
        status_info = PaperTradingEngine.inspect_saved_state(mock_engine_config.state_file)
        assert status_info["status"] == "RUNNING_DISCONNECTED"
        assert status_info["connection_status"] == "DISCONNECTED"
        assert "empty Data list" in status_info["last_error"]


# -------------------------------------------------------------------------
# 4. Disconnected Process with Saved PAPER_TRADE Mode -> STOPPED
# -------------------------------------------------------------------------
def test_disconnected_process_with_saved_paper_trade(mock_engine_config: PaperEngineConfig):
    """Saved state has PAPER_TRADE mode, but PID is dead; evaluated as STOPPED."""
    state_file = Path(mock_engine_config.state_file)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    dead_pid = 99999999  # Process that does not exist
    now_iso = datetime.now(TZ_IST).isoformat()
    data = {
        "run_id": "RUN_DEAD_PROC",
        "pid": dead_pid,
        "mode": "PAPER_TRADE",
        "status": "RUNNING_CONNECTED",
        "heartbeat_time": now_iso,
        "is_connected": True,
        "last_spot_price": 22600.0,
        "saved_at": now_iso,
    }
    with open(state_file, "w") as f:
        json.dump(data, f)

    with patch(
        "trading_platform.data.calendar.NSECalendar.is_regular_trading_minute",
        return_value=True,
    ):
        status_info = PaperTradingEngine.inspect_saved_state(state_file)
        assert status_info["process_running"] is False
        assert status_info["process_running_str"] == "NO"
        assert status_info["status"] == "STOPPED"
        assert status_info["engine_liveness"] == "STOPPED"


# -------------------------------------------------------------------------
# 5. Active Engine Heartbeat
# -------------------------------------------------------------------------
def test_active_engine_heartbeat(mock_engine_config: PaperEngineConfig):
    """Saved state has active PID and fresh heartbeat; displays YES and valid age."""
    engine = PaperTradingEngine(mock_engine_config)
    engine.feed.is_connected = True
    now_ist = datetime.now(TZ_IST)
    engine.feed.last_exchange_time = now_ist
    engine.feed.last_data_receipt_time = now_ist
    engine.last_spot_price = 22610.50
    engine.save_state()

    with patch(
        "trading_platform.data.calendar.NSECalendar.is_regular_trading_minute",
        return_value=True,
    ), patch("trading_platform.paper.engine.get_process_cmd", return_value="python -m trading_platform.paper.cli start"):
        status_info = PaperTradingEngine.inspect_saved_state(mock_engine_config.state_file)
        assert status_info["process_running"] is True
        assert status_info["process_running_str"] == "YES"
        assert status_info["heartbeat_age_seconds"] is not None
        assert status_info["heartbeat_age_seconds"] < 5.0
        assert status_info["heartbeat_age_str"] != "N/A"
        assert "None" not in status_info["quote_age_str"]


# -------------------------------------------------------------------------
# 6. Heartbeat Timeout (> 15s) -> STOPPED / STALE_HEARTBEAT
# -------------------------------------------------------------------------
def test_heartbeat_timeout(mock_engine_config: PaperEngineConfig):
    """Engine process is alive, but heartbeat is >15s old; evaluated as STOPPED / STALE_HEARTBEAT."""
    engine = PaperTradingEngine(mock_engine_config)
    engine.save_state()

    # Manually backdate heartbeat
    state_file = Path(mock_engine_config.state_file)
    with open(state_file) as f:
        data = json.load(f)
    backdated = (datetime.now(TZ_IST) - timedelta(seconds=35)).isoformat()
    data["heartbeat_time"] = backdated
    data["saved_at"] = backdated
    with open(state_file, "w") as f:
        json.dump(data, f)

    with patch(
        "trading_platform.data.calendar.NSECalendar.is_regular_trading_minute",
        return_value=True,
    ), patch("trading_platform.paper.engine.get_process_cmd", return_value="python -m trading_platform.paper.cli start"):
        status_info = PaperTradingEngine.inspect_saved_state(state_file)
        assert status_info["process_running"] is False
        assert status_info["status"] == "STOPPED"
        assert "STALE_HEARTBEAT" in status_info["engine_liveness"]


# -------------------------------------------------------------------------
# 7. Restart Recovery
# -------------------------------------------------------------------------
def test_restart_recovery(mock_engine_config: PaperEngineConfig):
    """Engine state is faithfully restored upon crash recovery."""
    engine1 = PaperTradingEngine(mock_engine_config)
    engine1.last_spot_price = 22750.25
    engine1.risk_manager.daily_realized_loss = 1250.0
    engine1.risk_manager.consecutive_losses = 2
    engine1.pending_exit_reason = "STOP_LOSS"
    engine1.save_state()

    engine2 = PaperTradingEngine(mock_engine_config)
    restored = engine2.load_state()
    assert restored is True
    assert engine2.run_id == engine1.run_id
    assert engine2.last_spot_price == 22750.25
    assert engine2.risk_manager.daily_realized_loss == 1250.0
    assert engine2.risk_manager.consecutive_losses == 2
    assert engine2.pending_exit_reason == "STOP_LOSS"


def test_restart_rejects_mixed_state_generations(mock_engine_config: PaperEngineConfig):
    """A crash-window mix of engine/broker/portfolio snapshots must fail closed."""
    engine1 = PaperTradingEngine(mock_engine_config)
    engine1.save_state()
    portfolio_state = Path(mock_engine_config.state_file).with_name(
        "paper_portfolio_state.json"
    )
    with open(portfolio_state, encoding="utf-8") as f:
        data = json.load(f)
    data["state_generation"] = "MISMATCHED_GENERATION"
    with open(portfolio_state, "w", encoding="utf-8") as f:
        json.dump(data, f)

    engine2 = PaperTradingEngine(mock_engine_config)
    assert engine2.config.paper_execution_enabled is False
    assert engine2.risk_manager.kill_switch_active is True
    assert engine2.load_state() is False


# -------------------------------------------------------------------------
# 8. Duplicate Process Rejection
# -------------------------------------------------------------------------
def test_duplicate_process_rejection(mock_engine_config: PaperEngineConfig):
    """Duplicate engine process launch is rejected when active lock exists with beating process."""
    lock_file = Path(mock_engine_config.state_file).parent / "paper_engine.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "w") as f:
        json.dump(
            {
                "pid": 98765,
                "run_id": "RUN_OTHER_ACTIVE",
                "mode": "PAPER_TRADE",
                "acquired_at": datetime.now(TZ_IST).isoformat(),
            },
            f,
        )

    engine2 = PaperTradingEngine(mock_engine_config)
    with patch("trading_platform.paper.engine.is_pid_alive", return_value=True), patch(
        "trading_platform.paper.engine.get_process_cmd",
        return_value="python -m trading_platform.paper.cli start",
    ), patch.object(
        PaperTradingEngine,
        "inspect_saved_state",
        return_value={"process_running": True, "pid": 98765, "run_id": "RUN_OTHER_ACTIVE"},
    ):
        with pytest.raises(RuntimeError, match="Active paper trading engine already running"):
            engine2.acquire_process_lock()

    lock_file.unlink(missing_ok=True)


def test_same_process_cannot_acquire_lock_twice(mock_engine_config: PaperEngineConfig):
    """Two engine instances in one process must not both own the singleton lock."""
    engine1 = PaperTradingEngine(mock_engine_config)
    engine2 = PaperTradingEngine(mock_engine_config)

    assert engine1.acquire_process_lock() is True
    try:
        with pytest.raises(RuntimeError, match="already running"):
            engine2.acquire_process_lock()
    finally:
        engine1.release_process_lock()


# -------------------------------------------------------------------------
# 9. CLI / Dashboard Agreement
# -------------------------------------------------------------------------
def test_cli_dashboard_agreement(mock_engine_config: PaperEngineConfig):
    """Dashboard load_engine_state reads identical values as CLI inspect_saved_state."""
    from scripts.paper_trading_dashboard import load_engine_state

    engine = PaperTradingEngine(mock_engine_config)
    engine.feed.is_connected = True
    now_ist = datetime.now(TZ_IST)
    engine.feed.last_exchange_time = now_ist
    engine.last_spot_price = 22680.0
    engine.save_state()

    with patch(
        "scripts.paper_trading_dashboard.STATE_FILE",
        Path(mock_engine_config.state_file),
    ):
        dashboard_state = load_engine_state()
        cli_state = PaperTradingEngine.inspect_saved_state(mock_engine_config.state_file)

        assert dashboard_state["run_id"] == cli_state["run_id"]
        assert dashboard_state["nifty_spot"] == cli_state["nifty_spot"]
        assert dashboard_state["status"] == cli_state["status"]
        assert dashboard_state["process_running"] == cli_state["process_running"]


# -------------------------------------------------------------------------
# 10. Market Closure Status
# -------------------------------------------------------------------------
def test_market_closure(mock_engine_config: PaperEngineConfig):
    """When market session is closed, status reports MARKET_CLOSED."""
    engine = PaperTradingEngine(mock_engine_config)
    engine.save_state()

    with patch(
        "trading_platform.data.calendar.NSECalendar.is_regular_trading_minute",
        return_value=False,
    ):
        status_info = PaperTradingEngine.inspect_saved_state(mock_engine_config.state_file)
        assert status_info["status"] == "MARKET_CLOSED"
        assert status_info["market_session"] == "CLOSED"
        assert status_info["market_session_open"] is False


# -------------------------------------------------------------------------
# 11. Broker Order Prohibition Safeguard
# -------------------------------------------------------------------------
def test_broker_order_prohibition():
    """Any attempt to execute live broker orders in PAPER_TRADE mode raises LiveTradingPermissionError."""
    from zoneinfo import ZoneInfo
    tz_utc = ZoneInfo("UTC")
    now_utc = datetime.now(tz_utc)

    broker = FivePaisaBroker(mode="PAPER_TRADE", live_approved=False)
    order = Order(
        order_id="TEST_REAL_ORDER",
        client_order_id="CL_TEST_REAL_ORDER",
        symbol="NIFTY26OCT22600CE",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=50,
        price=150.0,
        status=OrderStatus.SUBMITTED,
        created_at=now_utc,
        updated_at=now_utc,
    )

    with pytest.raises(LiveTradingPermissionError, match="strictly prohibited"):
        broker.place_order(order)


@pytest.mark.parametrize(
    "endpoint",
    [
        "place_order",
        "modify_order",
        "cancel_order",
        "bo_order",
        "cover_order",
        "execute_basket",
        "cancel_bulk_order",
        "squareoff_all",
        "place_order_bulk",
        "vtt_order",
        "order_request",
    ],
)
def test_raw_sdk_transaction_endpoints_are_blocked(endpoint):
    """The exposed SDK client boundary must not permit a transport-level order call."""
    raw_client = MagicMock()
    broker = FivePaisaBroker()
    broker.client = raw_client

    with pytest.raises(LiveTradingPermissionError, match="read-only"):
        getattr(broker.client, endpoint)()

    getattr(raw_client, endpoint).assert_not_called()
