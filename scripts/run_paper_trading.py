"""Top-level CLI launcher for NIFTY Options Paper Trading Platform."""

import sys
from pathlib import Path

# Ensure root directory is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir / "src"))
    sys.path.insert(0, str(root_dir))


if __name__ == "__main__":
    import runpy

    # Route the public launcher through the audited CLI and engine.  The legacy
    # execution.paper_trading_engine module is retained only for compatibility
    # and must not be the operational entry point.
    runpy.run_module("trading_platform.paper.cli", run_name="__main__")
