"""Pure performance maths for the dashboard.

Every function takes plain numbers / pandas objects and returns plain results.  The unit of the
numbers (rupees for LIVE PAPER, index points for HISTORICAL BACKTEST) is the caller's business -
these helpers never convert between the two and the dashboard never mixes them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd


def _clean(pnl: pd.Series | list[float]) -> pd.Series:
    return pd.to_numeric(pd.Series(pnl, dtype="float64"), errors="coerce").dropna()


def win_rate(pnl: pd.Series | list[float]) -> float | None:
    """Percent of trades with pnl > 0 (None when there are no trades)."""
    s = _clean(pnl)
    return None if s.empty else float((s > 0).sum()) / len(s) * 100.0


def profit_factor(pnl: pd.Series | list[float]) -> float | None:
    """Gross profit / gross loss.  None with no trades; ``inf`` with profit and no loss."""
    s = _clean(pnl)
    if s.empty:
        return None
    gain = float(s[s > 0].sum())
    loss = float(-s[s < 0].sum())
    if loss == 0:
        return math.inf if gain > 0 else None
    return gain / loss


@dataclass(frozen=True)
class Drawdown:
    absolute: float  # >= 0, same unit as the equity series
    percent: float  # >= 0, of the running peak
    peak_index: Any = None
    trough_index: Any = None


def max_drawdown(equity: pd.Series | list[float]) -> Drawdown:
    """Largest peak-to-trough decline of an equity series (index labels are returned)."""
    s = _clean(equity) if not isinstance(equity, pd.Series) else pd.to_numeric(equity, errors="coerce").dropna()
    if s.empty:
        return Drawdown(0.0, 0.0)
    peak = s.cummax()
    dd = peak - s
    trough_pos = int(dd.to_numpy().argmax())
    worst = float(dd.iloc[trough_pos])
    if worst <= 0:
        return Drawdown(0.0, 0.0)
    trough_label = s.index[trough_pos]
    peak_val = float(peak.iloc[trough_pos])
    peak_label = s.iloc[: trough_pos + 1][s.iloc[: trough_pos + 1] == peak_val].index[0]
    pct = worst / peak_val * 100.0 if peak_val > 0 else 0.0
    return Drawdown(worst, pct, peak_label, trough_label)


def equity_curve(
    trades: pd.DataFrame,
    starting_capital: float,
    *,
    pnl_col: str = "net_pnl",
    time_col: str = "exit_time",
) -> pd.DataFrame:
    """Equity after each closed trade (ordered by ``time_col``), preceded by a START row.

    Columns: time, equity, pnl, trade_id.
    """
    start_time = None
    if not trades.empty:
        first = trades.get("entry_time")
        start_time = first.min() if first is not None and first.notna().any() else trades[time_col].min()
    rows = [{"time": start_time, "equity": float(starting_capital), "pnl": 0.0, "trade_id": "START"}]
    if not trades.empty:
        ordered = trades.sort_values(time_col, kind="stable")
        equity = float(starting_capital)
        for _, row in ordered.iterrows():
            pnl = pd.to_numeric(row.get(pnl_col), errors="coerce")
            if pd.isna(pnl):
                continue
            equity += float(pnl)
            rows.append(
                {"time": row.get(time_col), "equity": equity, "pnl": float(pnl),
                 "trade_id": row.get("trade_id")}
            )  # fmt: skip
    return pd.DataFrame(rows, columns=["time", "equity", "pnl", "trade_id"])


def strategy_pnl(
    trades: pd.DataFrame, *, pnl_col: str = "net_pnl", strategy_col: str = "strategy"
) -> pd.DataFrame:
    """Per-strategy trades / wins / losses / win-rate / total P&L (sorted by P&L desc)."""
    cols = ["strategy", "trades", "wins", "losses", "win_rate", "pnl"]
    if trades.empty:
        return pd.DataFrame(columns=cols)
    d = trades[[strategy_col, pnl_col]].copy()
    d[pnl_col] = pd.to_numeric(d[pnl_col], errors="coerce")
    d = d.dropna(subset=[pnl_col])
    rows = []
    for name, grp in d.groupby(strategy_col, sort=True):
        p = grp[pnl_col]
        rows.append(
            {"strategy": name, "trades": len(p), "wins": int((p > 0).sum()),
             "losses": int((p < 0).sum()), "win_rate": win_rate(p), "pnl": float(p.sum())}
        )  # fmt: skip
    return pd.DataFrame(rows, columns=cols).sort_values("pnl", ascending=False).reset_index(drop=True)


def daily_pnl(
    trades: pd.DataFrame, *, pnl_col: str = "net_pnl", date_col: str = "session_date"
) -> pd.DataFrame:
    """Per-day trades / P&L with a cumulative column (ascending dates)."""
    cols = ["date", "trades", "pnl", "cumulative"]
    if trades.empty:
        return pd.DataFrame(columns=cols)
    d = trades[[date_col, pnl_col]].copy()
    d[pnl_col] = pd.to_numeric(d[pnl_col], errors="coerce")
    d = d.dropna()
    if d.empty:
        return pd.DataFrame(columns=cols)
    g = d.groupby(date_col).agg(trades=(pnl_col, "size"), pnl=(pnl_col, "sum")).reset_index()
    g = g.rename(columns={date_col: "date"}).sort_values("date").reset_index(drop=True)
    g["cumulative"] = g["pnl"].cumsum()
    return g[cols]


def summarize_paper(
    trades: pd.DataFrame,
    *,
    starting_capital: float | None,
    unrealized: float = 0.0,
    current_equity: float | None = None,
) -> dict[str, Any]:
    """Headline numbers for LIVE PAPER (rupees).

    ``current_equity`` (e.g. from the snapshot) wins over ``starting + realized + unrealized``.
    """
    pnl = pd.to_numeric(trades["net_pnl"], errors="coerce").dropna() if not trades.empty else pd.Series(dtype=float)
    realized = float(pnl.sum()) if not pnl.empty else 0.0
    equity = current_equity
    if equity is None and starting_capital is not None:
        equity = starting_capital + realized + unrealized
    curve = equity_curve(trades, starting_capital) if starting_capital is not None else None
    dd = max_drawdown(curve["equity"]) if curve is not None else Drawdown(0.0, 0.0)
    return {
        "starting_capital": starting_capital,
        "current_equity": equity,
        "realized_pnl": realized,
        "unrealized_pnl": float(unrealized),
        "total_trades": int(len(pnl)),
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl < 0).sum()),
        "win_rate": win_rate(pnl),
        "profit_factor": profit_factor(pnl),
        "max_drawdown": dd.absolute,
        "max_drawdown_pct": dd.percent,
        "total_charges": float(pd.to_numeric(trades["total_charges"], errors="coerce").sum())
        if not trades.empty and "total_charges" in trades
        else 0.0,
    }


def summarize_points(trades: pd.DataFrame) -> dict[str, Any]:
    """Headline numbers for HISTORICAL BACKTEST (index POINTS, no capital, no charges)."""
    pnl = pd.to_numeric(trades["points_pnl"], errors="coerce").dropna() if not trades.empty else pd.Series(dtype=float)
    curve = equity_curve(trades, 0.0, pnl_col="points_pnl", time_col="exit_time") if not trades.empty else None
    dd = max_drawdown(curve["equity"]) if curve is not None else Drawdown(0.0, 0.0)
    return {
        "total_trades": int(len(pnl)),
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl < 0).sum()),
        "win_rate": win_rate(pnl),
        "profit_factor": profit_factor(pnl),
        "total_points": float(pnl.sum()) if not pnl.empty else 0.0,
        "avg_points": float(pnl.mean()) if not pnl.empty else None,
        "max_drawdown_points": dd.absolute,
    }
