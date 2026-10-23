#!/usr/bin/env python3
"""Phase 2D End-to-End Execution Script: Historical Backtest & Manual Trade Reconciliation.

Orchestrates the entire research workflow:
1. Loads verified 1D and 5M NIFTY historical bars.
2. Executes baseline underlying backtests for all 5 canonical strategies and individual methods.
3. Performs 1-to-1 matching against the 190 manual journal trades.
4. Generates institutional quantitative metrics (Gross/Net points, win rates, PF, max DD, precision, recall).
5. Exports all required Phase 2D reports (CSV, Parquet, HTML).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "src"))

from trading_platform.backtesting.reconciliation_audit import (  # noqa: E402
    HistoricalReconciliationAuditor,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("phase2d_runner")


def main() -> None:
    logger.info("=================================================================")
    logger.info("PHASE 2D: HISTORICAL BACKTEST AND MANUAL TRADE RECONCILIATION")
    logger.info("=================================================================")

    auditor = HistoricalReconciliationAuditor(base_dir=BASE_DIR, slippage_points=0.50)

    logger.info("[Step 1/4] Loading verified historical market data and manual trade journal...")
    auditor.load_data()

    logger.info("[Step 2/4] Executing underlying backtests across all 5 strategies and methods...")
    auditor.run_backtests()

    logger.info("[Step 3/4] Performing 1-to-1 trade reconciliation and outcome evaluation...")
    auditor.reconcile_manual_trades()

    logger.info(
        "[Step 4/4] Exporting all deliverables and generating interactive Plotly dashboards..."
    )
    auditor.export_all_deliverables()

    logger.info("=================================================================")
    logger.info("PHASE 2D RECONCILIATION RUN COMPLETED SUCCESSFULLY")
    logger.info("Deliverables exported to: %s", auditor.reports_dir)
    logger.info("=================================================================")


if __name__ == "__main__":
    main()
