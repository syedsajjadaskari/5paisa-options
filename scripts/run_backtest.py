#!/usr/bin/env python3
"""Clean historical NIFTY-index-point backtest (engine version V3_PDF_CORRECTED).

    ./.venv/bin/python scripts/run_backtest.py [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--out DIR]

* Loads the verified historical datasets (data/processed/nifty/5m and 1d).
* Verifies their SHA-256 against docs/baseline_v1_manifest.md where listed (WARNS on a
  mismatch, never fails).
* Runs each of the four authorized strategies alone, then ONE joint chronological portfolio
  (max one position, FIFO with explicit PDS > ORB > IRF > PDR tie-break).
* Writes reports/backtest/<run_id>/{signals,standalone_trades,portfolio_trades,
  rejected_signals}.csv, summary.json, summary.md, manifest.json.  A run folder is never
  overwritten and the frozen V1/V2 artifacts are never touched.

This is NIFTY INDEX-POINT backtesting of the original configuration WITHOUT optimization.
There are no historical option prices, so no option-rupee P&L is computed or implied.
Network and broker access: none (reads local parquet files only).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time as _time
from datetime import UTC, date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR / "src") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "src"))

from trading_platform.backtesting.constants import ENGINE_VERSION_LABEL  # noqa: E402
from trading_platform.backtesting.metrics import (  # noqa: E402
    METRICS_TABLE_HEADER,
    metrics_table_row,
)
from trading_platform.backtesting.portfolio_runner import (  # noqa: E402
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    SCOPE_NOTE,
    BacktestParams,
    build_authorized_strategies,
    canonical_params_hash,
    load_nifty_bars,
    metric_rows,
    run_portfolio_backtest,
    verify_dataset_hashes,
    write_run_outputs,
)

logger = logging.getLogger("run_backtest")


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--start", type=_parse_date, default=DEFAULT_WINDOW_START,
        help="first reported date (all earlier bars still warm the indicators)",
    )  # fmt: skip
    parser.add_argument(
        "--end", type=_parse_date, default=DEFAULT_WINDOW_END, help="last reported date"
    )
    parser.add_argument(
        "--out", type=Path, default=Path("reports/backtest"),
        help="parent directory; results go to <out>/<run_id>/ (never overwritten)",
    )  # fmt: skip
    parser.add_argument("--run-id", default=None, help="override the generated run id")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.start > args.end:
        parser.error("--start must not be after --end")

    params = BacktestParams(window_start=args.start, window_end=args.end)

    checks = verify_dataset_hashes(BASE_DIR)
    for check in checks:
        if check["status"] == "MATCH":
            logger.info("dataset hash OK: %s", check["path"])
        else:
            logger.warning(
                "dataset hash %s: %s (expected %s, actual %s)",
                check["status"], check["path"], check["expected_sha256"], check["actual_sha256"],
            )  # fmt: skip

    started = _time.time()
    logger.info("Loading verified NIFTY bars...")
    bars_1d, bars_5m = load_nifty_bars(BASE_DIR)
    logger.info("Loaded %d daily and %d five-minute bars", len(bars_1d), len(bars_5m))

    result = run_portfolio_backtest(bars_5m, bars_1d, params)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    phash = canonical_params_hash(params, result.strategy_parameters)[:8]
    run_id = args.run_id or f"{ENGINE_VERSION_LABEL}_{stamp}_{phash}"
    out_root = args.out if args.out.is_absolute() else BASE_DIR / args.out
    files = write_run_outputs(result, out_root / run_id, BASE_DIR, run_id, checks)

    print()
    print(f"NIFTY INDEX-POINT BACKTEST - engine {ENGINE_VERSION_LABEL} - run {run_id}")
    print(SCOPE_NOTE)
    print(
        f"Strategies: {', '.join(build_authorized_strategies())} (RF excluded)."
    )
    for title, key in (
        (f"EVALUATION WINDOW {args.start} .. {args.end}", "window"),
        ("WHOLE SIMULATED DATASET (incl. warm-up months)", "full_dataset"),
    ):
        print()
        print(title)
        print(METRICS_TABLE_HEADER)
        print("-" * len(METRICS_TABLE_HEADER))
        for label, metrics in metric_rows(result.summary[key], params.priority):
            print(metrics_table_row(label, metrics))
    win = result.summary["window"]
    print()
    print(f"Raw signals in window: {win['raw_signals']}")
    print(f"Portfolio exit mix:    {win['portfolio_exit_mix']}")
    print(f"Rejected by reason:    {win['portfolio_rejections_by_reason']}")
    print(f"Output folder:         {files['manifest'].parent}")
    print(f"Elapsed: {_time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
