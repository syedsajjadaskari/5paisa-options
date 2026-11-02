"""SYNTHETIC fixtures for the dashboard tests.

Everything built here is made up in-test (labelled SYNTHETIC) - none of it is market data or a
real paper-trading result.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from trading_platform.dashboard.data import TRADE_COLUMNS

IST_SUFFIX = "+05:30"
DAY = "2026-10-26"


def iso(hhmm: str, day: str = DAY, seconds: str = "00") -> str:
    return f"{day}T{hhmm}:{seconds}{IST_SUFFIX}"


def synthetic_candles(day: str = DAY, n: int = 12, start: float = 25000.0) -> list[dict[str, Any]]:
    """SYNTHETIC 5m candles starting 09:15 (deterministic zig-zag, not market data)."""
    out = []
    t = datetime.fromisoformat(f"{day}T09:15:00")
    px = start
    for i in range(n):
        o = px
        c = o + (6.0 if i % 2 == 0 else -4.0)
        out.append(
            {"t": f"{t:%Y-%m-%dT%H:%M:%S}{IST_SUFFIX}", "o": o, "h": max(o, c) + 3.0,
             "l": min(o, c) - 3.0, "c": c, "source": "PROVIDER_COMPLETED"}
        )  # fmt: skip
        px = c
        t += timedelta(minutes=5)
    return out


def synthetic_snapshot(
    written_at: datetime | None = None,
    *,
    mode: str = "LIVE",
    engine_state: str = "RUNNING",
    position: bool = False,
    day: str = DAY,
    tier: str = "SHADOW_ONLY",
) -> dict[str, Any]:
    """SYNTHETIC live_state.json content following the documented schema."""
    written = (written_at or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S") + IST_SUFFIX
    candles = synthetic_candles(day)
    snap: dict[str, Any] = {
        "schema_version": 1,
        "written_at": written,
        "session": {"session_id": "SYNTH", "date": day, "mode": mode, "label": "SYNTHETIC",
                    "pid": 1, "started_at": iso("09:10", day), "engine_state": engine_state,
                    "state_reason": "synthetic test state", "market_status": "OPEN"},
        "connection": {"broker": "5paisa", "status": "CONNECTED",
                       "last_valid_quote_at": iso("10:00", day), "data_age_seconds": 0.7,
                       "stale": False, "candle_source": "PROVIDER_COMPLETED",
                       "candle_trusted": True, "notes": []},
        "spot": {"price": 25012.5, "exchange_time": iso("10:00", day), "day_high": 25030.0,
                 "day_low": 24980.0, "prev_close": 24990.0},
        "current_candle": {"open_time": iso("10:00", day), "o": 25010.0, "h": 25014.0, "l": 25008.0,
                           "c": 25012.5, "source": "SAMPLED_TICKS", "authoritative": False},
        "quotes": {"CE": {"symbol": "NIFTY CE SYNTH", "strike": 25000, "bid": 110.0, "ask": 111.0,
                          "server_time": iso("10:00", day)},
                   "PE": {"symbol": "NIFTY PE SYNTH", "strike": 25000, "bid": 90.0, "ask": 91.0,
                          "server_time": iso("10:00", day)}},
        "levels": {"PDH": 25040.0, "PDL": 24950.0, "PDC": 24990.0,
                   "support_zone": [24955.0, 24965.0], "resistance_zone": [25035.0, 25045.0],
                   "IRH": 25020.0, "IRL": 24985.0, "EMA200": None, "notes": "SYNTHETIC"},
        "candles": candles,
        "markers": [
            {"kind": "SIGNAL", "t": iso("09:30", day), "price": 25005.0, "strategy": "ORB",
             "trade_id": None, "text": "synthetic signal"},
        ],
        "account": {"starting_capital": 250000.0, "cash": 250000.0, "equity": 250000.0,
                    "realized_pnl": 0.0, "session_realized_pnl": 0.0, "unrealized_pnl": 0.0,
                    "session_open_equity": 250000.0, "trades_today": 0, "wins_today": 0,
                    "losses_today": 0, "daily_loss_limit": 25000.0, "daily_loss_used": 0.0},
        "risk": {"state": "OK", "max_daily_loss_inr": 25000.0, "entry_cutoff": "14:30",
                 "square_off": "15:15", "cutoff_passed": False},
        "strategies": [
            {"name": n, "label": f"{n} M1", "tier": tier, "state": "MONITORING",
             "last_evaluated_candle": iso("09:55", day), "levels": {"PDH": 25040.0},
             "conditions_satisfied": ["synthetic ok"], "conditions_failed": ["synthetic fail"],
             "last_signal": None, "last_rejection": "SHADOW_ONLY", "trades_today": 0,
             "pnl_today": 0.0, "note": "synthetic"}
            for n in ("PDS", "ORB", "IRF", "PDR")
        ],
        "position": None,
        "last_event": {"kind": "STATE", "t": iso("10:00", day), "text": "synthetic"},
        "recent_events": [],
        "last_closed_trade": None,
    }
    if position:
        snap["position"] = {
            "trade_id": "T-SYNTH-OPEN", "strategy": "ORB", "method": "METHOD_1",
            "direction": "BULLISH", "option_type": "CE", "symbol": "NIFTY CE SYNTH",
            "strike": 25000, "expiry": "2026-10-29", "lots": 1, "quantity": 75,
            "entry_time": iso("09:50", day), "entry_spot": 25005.0, "entry_bid": 109.0,
            "entry_ask": 110.0, "entry_premium": 110.5, "current_bid": 112.0,
            "stop_spot": 24975.0, "target_spot": 25065.0, "dist_to_stop": 37.5,
            "dist_to_target": 52.5, "gross_pnl": 112.5, "net_pnl_estimate": 60.0, "pnl_pct": 1.4,
            "holding_seconds": 600, "status": "OPEN", "entry_reason": "synthetic breakout",
            "label": "RESEARCH_PAPER_PROVISIONAL",
        }
        snap["markers"].append(
            {"kind": "ENTRY", "t": iso("09:50", day, "10"), "price": 25005.0, "strategy": "ORB",
             "trade_id": "T-SYNTH-OPEN", "text": "synthetic"}
        )  # fmt: skip
        snap["account"]["unrealized_pnl"] = 112.5
        snap["account"]["equity"] = 250112.5
    return snap


def synthetic_trade(
    n: int, *, strategy: str = "ORB", net: float = 500.0, day: str = DAY, label: str = "",
    exit_reason: str = "TARGET", balance_after: float | None = None, hhmm: str = "09:50",
) -> dict[str, Any]:
    """One SYNTHETIC trades.csv row (all TRADE_COLUMNS present)."""
    entry = datetime.fromisoformat(f"{day}T{hhmm}:10")
    exit_ = entry + timedelta(minutes=20)
    row: dict[str, Any] = {c: "" for c in TRADE_COLUMNS}
    row.update(
        trade_id=f"T-SYNTH-{n:03d}", session_date=day, session_id="SYNTH", label=label,
        strategy=strategy, method="METHOD_1", direction="BULLISH", option_type="CE",
        symbol="NIFTY CE SYNTH", expiry="2026-10-29", strike=25000, scrip_code=1, lot_size=75,
        lots=1, quantity=75,
        signal_time=f"{entry - timedelta(seconds=10):%Y-%m-%dT%H:%M:%S}{IST_SUFFIX}",
        entry_time=f"{entry:%Y-%m-%dT%H:%M:%S}{IST_SUFFIX}",
        exit_time=f"{exit_:%Y-%m-%dT%H:%M:%S}{IST_SUFFIX}",
        entry_spot=25005.0, exit_spot=25065.0 if net > 0 else 24975.0, stop_spot=24975.0,
        target_spot=25065.0, entry_bid=109.0, entry_ask=110.0, entry_premium=110.5,
        exit_bid=150.0, exit_premium=149.5, entry_slippage=0.5, exit_slippage=0.5,
        gross_pnl=net + 40.0, entry_charges=20.0, exit_charges=20.0, total_charges=40.0,
        net_pnl=net, exit_reason=exit_reason, entry_reason="synthetic breakout above PDH",
        entry_conditions_json=json.dumps({"close_above_PDH": True, "body_ratio_ok": True,
                                          "volume_filter": False}),
        levels_json=json.dumps({"PDH": 25000.0, "PDL": 24900.0, "support_zone": [24950.0, 24960.0]}),
        trigger_candle_json=json.dumps({"t": "09:45", "o": 24998.0, "h": 25008.0, "l": 24996.0,
                                        "c": 25005.0}),
        exit_detail_json=json.dumps({"condition": "underlying reached target 25065.0"}),
        balance_after=balance_after if balance_after is not None else 250000.0 + net,
    )  # fmt: skip
    return row


def write_trades_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_snapshot(path: Path, snap: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap), encoding="utf-8")


def test_synthetic_trade_has_every_column() -> None:
    assert set(synthetic_trade(1)) == set(TRADE_COLUMNS)
