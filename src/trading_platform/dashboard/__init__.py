"""Streamlit dashboard support code for the NIFTY options VIRTUAL paper-trading app.

The dashboard is a pure READER of the files written by the trading process
(``runtime/live_state.json``, ``runtime/journal/*``) and of the verified historical datasets.
It never starts a trading process and never imports broker or order code.

Modules
-------
* :mod:`~trading_platform.dashboard.data`    - tolerant file loaders (missing/stale/corrupt safe)
* :mod:`~trading_platform.dashboard.metrics` - pure performance maths (rupees OR points, never mixed)
* :mod:`~trading_platform.dashboard.explain` - "why did the trade open / close" from recorded fields
* :mod:`~trading_platform.dashboard.charts`  - pure plotly figure builders
* :mod:`~trading_platform.dashboard.views`   - Streamlit rendering of each dashboard section
"""
