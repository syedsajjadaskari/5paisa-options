"""Launcher for the NIFTY options VIRTUAL paper-trading app (read-only data, no real orders).

    ./.venv/bin/python scripts/run_paper_trading.py            # LIVE, read-only 5paisa data
    ./.venv/bin/python scripts/run_paper_trading.py --replay auto --speed 0   # SIMULATED replay
    ./.venv/bin/python scripts/run_paper_trading.py --doctor   # startup checks only

All logic lives in ``trading_platform.live.app``; see README_PAPER_TRADING.md.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from trading_platform.live.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
