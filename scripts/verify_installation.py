"""Offline installation check for the NIFTY options virtual paper-trading app.

Makes NO network call, never logs in, never reads ``.env`` or token caches, and writes nothing.
It verifies that the environment can import the live app, that the shipped config loads, that every
strategy is SHADOW_ONLY by default, and that the read-only broker boundary blocks order endpoints.

(The Phase-1 version of this script, which exercised the retired DuckDB/PaperBroker stack, is kept at
``archive/legacy_2026-10-28/scripts/verify_installation.py``.)

Usage:  python scripts/verify_installation.py        (exit code 0 = all required checks passed)
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

REQUIRED_DEPS = [
    "numpy", "pandas", "yaml", "pydantic", "dotenv", "exchange_calendars", "rich",
    "streamlit", "plotly", "py5paisa", "pyotp", "openpyxl",
]
# Historical research / reporting only; the live app does not need these to run.
OPTIONAL_DEPS = ["duckdb", "pyarrow", "scipy", "sklearn", "optuna", "matplotlib", "reportlab", "pytest", "ruff"]

REQUIRED_MODULES = [
    "trading_platform",
    "trading_platform.live.config",
    "trading_platform.live.authorization",
    "trading_platform.live.engine",
    "trading_platform.live.ledger",
    "trading_platform.live.providers.fivepaisa",
    "trading_platform.live.providers.replay",
    "trading_platform.live.terminal",
    "trading_platform.dashboard.data",
    "trading_platform.strategies.registry",
    "trading_platform.backtesting.portfolio_runner",
    "trading_platform.brokers.fivepaisa",
]
RETIRED_MODULES = [
    "trading_platform.paper",
    "trading_platform.execution.paper_trading_engine",
    "trading_platform.brokers.paper_broker",
    "trading_platform.data.read_only_collector",
    "trading_platform.data.offline_replay",
]


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def ok(self, msg: str) -> None:
        print(f"  [ OK ] {msg}")

    def fail(self, msg: str) -> None:
        self.failures += 1
        print(f"  [FAIL] {msg}")

    def warn(self, msg: str) -> None:
        self.warnings += 1
        print(f"  [WARN] {msg}")


def check_python(rep: Report) -> None:
    print("1. Python")
    v = sys.version_info
    if v >= (3, 11):
        rep.ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        rep.fail(f"Python >= 3.11 required, found {v.major}.{v.minor}.{v.micro}")


def check_dependencies(rep: Report) -> None:
    print("2. Dependencies")
    for dep in REQUIRED_DEPS:
        try:
            importlib.import_module(dep)
            rep.ok(dep)
        except Exception as exc:  # noqa: BLE001 - report any import problem
            rep.fail(f"{dep}: {type(exc).__name__}: {exc}")
    for dep in OPTIONAL_DEPS:
        if importlib.util.find_spec(dep) is None:
            rep.warn(f"{dep}: not installed (historical research/reporting only)")
        else:
            rep.ok(f"{dep} (optional)")


def check_modules(rep: Report) -> None:
    print("3. Project modules")
    for mod in REQUIRED_MODULES:
        try:
            importlib.import_module(mod)
            rep.ok(mod)
        except Exception as exc:  # noqa: BLE001
            rep.fail(f"{mod}: {type(exc).__name__}: {exc}")
    for mod in RETIRED_MODULES:
        try:
            present = importlib.util.find_spec(mod) is not None
        except ModuleNotFoundError:
            present = False
        if present:
            rep.fail(f"{mod} is retired but still importable (see docs/CLEANUP_MANIFEST.md)")
        else:
            rep.ok(f"{mod} correctly absent (retired)")


def check_config_and_authorization(rep: Report) -> None:
    print("4. Config and authorization defaults")
    try:
        from trading_platform.live.authorization import authorize, authorize_all
        from trading_platform.live.config import load_live_config
        from trading_platform.live.models import ExecutionTier

        cfg = load_live_config(ROOT / "configs" / "live_paper.yaml")
        rep.ok("configs/live_paper.yaml loads and validates")
        decisions = authorize_all(cfg, cli_research_flag=False)
        for name in ("PDS", "ORB", "IRF", "PDR"):
            d = decisions.get(name)
            if d is not None and d.tier == ExecutionTier.SHADOW_ONLY and not d.can_fill:
                rep.ok(f"{name}: SHADOW_ONLY by default (cannot fill)")
            else:
                rep.fail(f"{name}: expected SHADOW_ONLY without opt-in, got {d}")
        rf = authorize("RF", cfg, cli_research_flag=True)
        if rf.tier == ExecutionTier.DISABLED and not rf.can_fill:
            rep.ok("RF: DISABLED even with the research flag")
        else:
            rep.fail(f"RF must be DISABLED, got {rf.tier}")
    except Exception as exc:  # noqa: BLE001
        rep.fail(f"{type(exc).__name__}: {exc}")


def check_read_only_boundary(rep: Report) -> None:
    print("5. Read-only broker boundary (no network, no credentials)")
    try:
        from trading_platform.brokers.fivepaisa import (
            _BLOCKED_METHOD_NAMES,
            _ReadOnlyFivePaisaClientProxy,
        )

        proxy = _ReadOnlyFivePaisaClientProxy(object())
        blocked = 0
        for name in sorted(_BLOCKED_METHOD_NAMES):
            try:
                getattr(proxy, name)
            except PermissionError:
                blocked += 1
        if blocked == len(_BLOCKED_METHOD_NAMES) and blocked > 0:
            rep.ok(f"all {blocked} transactional SDK members are blocked by the proxy")
        else:
            rep.fail(f"only {blocked}/{len(_BLOCKED_METHOD_NAMES)} transactional members blocked")
    except Exception as exc:  # noqa: BLE001
        rep.fail(f"{type(exc).__name__}: {exc}")


def check_reference_files(rep: Report) -> None:
    print("6. Reference files")
    for rel, required in (
        ("configs/live_paper.yaml", True),
        ("configs/strategy_provisional", True),
        ("data/reference/scrip_master.parquet", False),
        ("data/processed/nifty/5m/nifty50_5m.parquet", False),
        ("data/processed/nifty/1d/nifty50_1d.parquet", False),
    ):
        exists = (ROOT / rel).exists()
        if exists:
            rep.ok(rel)
        elif required:
            rep.fail(f"{rel} is missing")
        else:
            rep.warn(f"{rel} is missing (needed for replay / warm-up fallbacks and backtests)")


def run_verification() -> bool:
    print("=" * 70)
    print("NIFTY OPTIONS VIRTUAL PAPER TRADING - OFFLINE INSTALLATION CHECK")
    print("=" * 70)
    rep = Report()
    for check in (
        check_python,
        check_dependencies,
        check_modules,
        check_config_and_authorization,
        check_read_only_boundary,
        check_reference_files,
    ):
        check(rep)
    print("=" * 70)
    print(f"failures={rep.failures} warnings={rep.warnings}")
    print("RESULT:", "PASS" if rep.failures == 0 else "FAIL")
    return rep.failures == 0


if __name__ == "__main__":
    sys.exit(0 if run_verification() else 1)
