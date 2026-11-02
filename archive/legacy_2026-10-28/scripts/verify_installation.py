"""Verification script to test all Phase 1 components, dependencies, and interfaces."""

import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def run_verification() -> bool:
    print("=" * 70)
    print("PHASE 1 ENVIRONMENT & SUBSYSTEM VERIFICATION")
    print("=" * 70)
    all_passed = True

    # 1. Python Version Check
    py_ver = sys.version_info
    print(f"[1/7] Python Version: {py_ver.major}.{py_ver.minor}.{py_ver.micro} ... ", end="")
    if py_ver >= (3, 11):
        print("OK")
    else:
        print("FAIL (Python >= 3.11 required)")
        all_passed = False

    # 2. Dependency Imports
    print("[2/7] Checking Core Dependencies ...")
    deps = [
        "numpy",
        "pandas",
        "scipy",
        "pydantic",
        "yaml",
        "dotenv",
        "duckdb",
        "pyarrow",
        "httpx",
        "tenacity",
        "exchange_calendars",
        "optuna",
        "sklearn",
        "matplotlib",
        "plotly",
        "streamlit",
        "loguru",
        "py5paisa",
        "pypdf",
        "fitz",
        "pytest",
        "ruff",
    ]
    for dep in deps:
        try:
            __import__(dep)
            print(f"  - {dep:20s}: OK")
        except ImportError as e:
            print(f"  - {dep:20s}: FAIL ({e})")
            all_passed = False

    # 3. Import Project Package
    print("[3/7] Importing trading_platform ... ", end="")
    try:
        import trading_platform

        print(f"OK (v{trading_platform.__version__})")
    except Exception as e:
        print(f"FAIL ({e})")
        all_passed = False

    # 4. Configuration Loading
    print("[4/7] Testing Configuration Loading ... ", end="")
    try:
        from trading_platform.core.config import ConfigManager

        cfg = ConfigManager()
        app_cfg = cfg.get_app_config()
        risk_cfg = cfg.get_risk_config()
        assert app_cfg["environment"] in ("RESEARCH", "PAPER", "LIVE")
        assert "limits" in risk_cfg
        print("OK")
    except Exception as e:
        print(f"FAIL ({e})")
        all_passed = False

    # 5. Data Ingestion, Parquet Storage & DuckDB Querying
    print("[5/7] Testing Data Pipeline (CSV -> Parquet -> DuckDB) ... ", end="")
    try:
        from trading_platform.data.providers.csv_provider import CSVDataProvider
        from trading_platform.data.storage import DataStorage

        provider = CSVDataProvider()
        sample_path = Path("data/samples/nifty50_5min_sample.csv")
        bars = provider.load_from_file(sample_path)
        assert len(bars) == 75, f"Expected 75 bars, got {len(bars)}"

        storage = DataStorage()
        parquet_path = storage.save_bars(bars)
        assert parquet_path.exists()

        # Query back via DuckDB
        queried_bars = storage.load_bars_from_parquet(symbol="NIFTY 50", interval="5m")
        assert len(queried_bars) == 75
        storage.close()
        print("OK")
    except Exception as e:
        print(f"FAIL ({e})")
        all_passed = False

    # 6. Strategy Registry & BaseStrategy Interface
    print("[6/7] Testing Strategy Registry & Signal Interface ... ", end="")
    try:
        from trading_platform.core.enums import SignalDirection
        from trading_platform.core.models import Signal
        from trading_platform.strategies.base import BaseStrategy, StrategyContext
        from trading_platform.strategies.registry import registry

        class DummyStrategy(BaseStrategy):
            def prepare(self, data, config):
                self.prepared = True

            def on_bar(self, context, bar):
                return self.generate_signal(context)

            def generate_signal(self, context):
                bar = context.latest_bar
                if not bar:
                    return None
                return Signal(
                    strategy_name="DUMMY",
                    method_id="DUMMY_M1",
                    symbol=bar.symbol,
                    direction=SignalDirection.BUY,
                    timestamp=bar.exchange_time,
                    reference_price=bar.close,
                    stop_loss=bar.close - 30.0,
                    target=bar.close + 60.0,
                    reason="Test Signal",
                )

        # Register and test
        registry.register("DUMMY", DummyStrategy)
        strategy_cls = registry.get("DUMMY")
        strat = strategy_cls(name="DUMMY")
        strat.prepare(None, {})
        ctx = StrategyContext()
        ctx.add_bar(bars[0])
        sig = strat.on_bar(ctx, bars[0])
        assert sig is not None
        assert strat.validate_signal(sig, ctx) is True
        registry.unregister("DUMMY")
        print("OK")
    except Exception as e:
        print(f"FAIL ({e})")
        all_passed = False

    # 7. Broker Safeguards Test
    print("[7/7] Testing Broker Safe Defaults & Safeguards ... ", end="")
    try:
        from trading_platform.brokers.base import BaseBroker
        from trading_platform.brokers.paper_broker import PaperBroker
        from trading_platform.core.enums import OperationalMode, OrderSide, OrderType
        from trading_platform.core.exceptions import LiveTradingPermissionError
        from trading_platform.core.models import Order

        # Paper broker executes safely
        paper = PaperBroker()
        paper.connect()
        test_order = Order(
            order_id="TEST_001",
            client_order_id="CLI_001",
            symbol="NIFTY 50",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=25,
            price=24800.0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        filled_order = paper.place_order(test_order)
        assert filled_order.filled_quantity == 25

        # Live safety gatekeeper check
        class DummyLiveBroker(BaseBroker):
            def connect(self):
                return True

            def disconnect(self):
                pass

            def place_order(self, o):
                self.assert_live_allowed()
                return o

            def cancel_order(self, oid):
                return True

            def get_positions(self):
                return []

            def get_orders(self):
                return []

            def get_margins(self):
                return {}

        live_broker = DummyLiveBroker(mode=OperationalMode.RESEARCH, live_approved=False)
        try:
            live_broker.place_order(test_order)
            print("FAIL (Live permission was not blocked!)")
            all_passed = False
        except LiveTradingPermissionError:
            # Expected behavior
            print("OK (Paper simulated correctly; Live order placement blocked)")
    except Exception as e:
        print(f"FAIL ({e})")
        all_passed = False

    print("=" * 70)
    if all_passed:
        print("ALL PHASE 1 VERIFICATION CHECKS PASSED SUCCESSFULLY.")
    else:
        print("SOME VERIFICATION CHECKS FAILED.")
    print("=" * 70)
    return all_passed


if __name__ == "__main__":
    success = run_verification()
    sys.exit(0 if success else 1)
