"""Single source of truth for the session/execution constants shared by backtest and paper app.

Import these (never re-type them) so the historical backtest and the live paper application
cannot drift apart.  This module is a dependency-free leaf: it imports only the standard
library, so the paper app can import it without pulling in the options-backtest machinery.

Conventions (all times are IST wall-clock, bars are BAR-OPEN stamped):

* ``ENTRY_CUTOFF_TIME`` (14:30): no new position may be opened at or after this time.  A
  5-minute candle whose CLOSE (open + 5 min) is at or after 14:30 therefore cannot be a
  signal candle (its entry fill would be at/after 14:30).  The last tradable signal candle
  is the one opening 14:20 (closing 14:25, filled at the 14:25 open).
* ``SQUARE_OFF_TIME`` (15:15): any open position is closed at the open of the first bar
  stamped at/after this time.
* ``DEFAULT_SLIPPAGE_POINTS`` (0.5): paid on EACH side (entry and exit) on every exit type
  (target, stop, gap, same-bar conflict, square-off, forced close), in NIFTY index points.
"""

from __future__ import annotations

from datetime import time

ENGINE_VERSION_LABEL = "V3_PDF_CORRECTED"

ENTRY_CUTOFF_TIME: time = time(14, 30)
SQUARE_OFF_TIME: time = time(15, 15)
DEFAULT_SLIPPAGE_POINTS: float = 0.5
BAR_MINUTES: int = 5

#: Explicit tie-break order when several strategies signal on the same candle.  First wins.
PORTFOLIO_PRIORITY: tuple[str, ...] = ("PDS", "ORB", "IRF", "PDR")

#: Max concurrent positions across the whole portfolio.
MAX_CONCURRENT_POSITIONS = 1

#: Everything produced by the historical backtest is NIFTY index points, never option rupees.
PNL_UNIT_LABEL = "NIFTY_INDEX_POINTS"
