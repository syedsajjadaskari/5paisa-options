"""Terminal rendering tests.  All snapshots are ILLUSTRATIVE SYNTHETIC (fake numbers)."""

from __future__ import annotations

from datetime import datetime

import pytest
from rich.console import Console

from trading_platform.live.providers.base import CheckResult
from trading_platform.live.terminal import (
    DEMO_KINDS,
    IST,
    fmt_inr,
    fmt_time,
    illustrative_snapshot,
    illustrative_trade,
    render_dashboard,
    render_startup_checks,
    render_trade_closed,
    render_trade_opened,
)

NOW = datetime(2026, 10, 28, 10, 30, 0, tzinfo=IST)
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV"
)


def _text(renderable: object, width: int = 120) -> str:
    console = Console(record=True, width=width, force_terminal=False, color_system=None)
    console.print(renderable)
    return console.export_text()


def _dash(kind: str, mutate=None) -> str:
    snap = illustrative_snapshot(kind, NOW)
    if mutate:
        mutate(snap)
    return _text(render_dashboard(snap, NOW))


def test_formatters() -> None:
    assert fmt_inr(250000, decimals=0) == "₹250,000"
    assert fmt_inr(-1234.5) == "-₹1,234.50"
    assert fmt_inr(12.5, signed=True) == "+₹12.50"
    assert fmt_inr(None) == "n/a"
    assert fmt_inr(float("nan")) == "n/a"
    assert fmt_time("2026-10-28T09:45:07+05:30") == "09:45:07"
    assert fmt_time("2026-10-28T04:15:07+00:00") == "09:45:07"
    assert fmt_time("garbage") == "n/a"


def test_flat_dashboard_contains_key_fields() -> None:
    out = _dash("flat")
    for needle in (
        "NIFTY OPTIONS — LIVE PAPER TRADING",
        "Broker: 5paisa",
        "CONNECTED",
        "PAPER ONLY",
        "OPEN",
        "RUNNING",
        "Candles: PROVIDER_COMPLETED ✓",
        "Virtual capital",
        "₹100,000",
        "Available cash",
        "Session P&L",
        "Daily loss used",
        "Active Position: NONE",
        "PDS M1",
        "SHADOW",
        "RESEARCH-PAPER",
        "DISABLED",
        "ILLUSTRATIVE SYNTHETIC",
        "20,000.00",
    ):
        assert needle in out, needle
    assert "REPLAY" not in out


def test_open_profit_and_loss_panels() -> None:
    profit = _dash("open_profit")
    assert "Active Position" in profit
    assert "BUY CE" in profit
    assert "+₹1,450.00" in profit
    assert "Option BID" in profit
    assert "To stop" in profit and "To target" in profit
    assert "0:12:34" in profit  # holding 754s
    assert "Data freshness" in profit
    assert "RESEARCH_PAPER_PROVISIONAL" in profit
    loss = _dash("open_loss")
    assert "-₹1,050.00" in loss


def test_exit_pending_banner() -> None:
    out = _dash("exit_pending")
    assert "EXIT PENDING — waiting for a valid bid" in out
    assert "STOP" in out


def test_blocked_and_recovery_reasons() -> None:
    out = _dash("blocked")
    assert "BLOCKED" in out
    assert "no trustworthy completed candle from provider" in out
    assert "NOT TRUSTED — strategy evaluation blocked" in out
    rec = _dash("recovery")
    assert "RECOVERY_REQUIRED" in rec
    assert "open position found in checkpoint" in rec


def test_halted_risk_reason() -> None:
    def mutate(s: dict) -> None:
        s["session"].update(engine_state="HALTED_RISK", state_reason="daily loss limit reached")

    out = _dash("flat", mutate)
    assert "HALTED_RISK" in out and "daily loss limit reached" in out


def test_replay_banner_and_title() -> None:
    out = _dash("replay")
    assert "NIFTY OPTIONS — REPLAY (SIMULATED)" in out
    assert "REPLAY — SIMULATED DATA — NOT LIVE MARKET" in out
    assert "LIVE PAPER TRADING" not in out


def test_stale_data_flagged() -> None:
    out = _dash("stale")
    assert "STALE" in out
    assert "DEGRADED" in out
    assert "42.0s" in out


def test_missing_fields_do_not_crash() -> None:
    for snap in (None, {}, {"session": None, "position": {}}, {"position": {"status": "OPEN"}}):
        out = _text(render_dashboard(snap, NOW))  # type: ignore[arg-type]
        assert "NIFTY OPTIONS" in out
    garbage = {
        "spot": {"price": "abc"},
        "account": {"cash": float("nan"), "equity": "x"},
        "strategies": ["bad", None, {"tier": "ODD"}],
        "quotes": {"CE": None, "PE": "x"},
        "recent_events": ["bad", {"t": "nope"}],
        "connection": {"notes": "not-a-list"},
        "position": {"status": "OPEN", "stop_spot": "?", "lots": None},
    }
    out = _text(render_dashboard(garbage, NOW))
    assert "n/a" in out


def test_recent_events_bounded_to_eight() -> None:
    def mutate(s: dict) -> None:
        s["recent_events"] = [
            {"t": NOW.isoformat(), "kind": "INFO", "text": f"event-number-{i}"} for i in range(40)
        ]

    out = _dash("flat", mutate)
    assert "event-number-39" in out and "event-number-32" in out
    assert "event-number-31" not in out


def test_secrets_redacted_in_dashboard() -> None:
    def mutate(s: dict) -> None:
        s["recent_events"] = [
            {"t": NOW.isoformat(), "kind": "ERROR", "text": f"login failed RequestToken=abcd1234efgh {JWT}"}
        ]
        s["session"]["state_reason"] = "access_token: SUPERSECRETVALUE99"
        s["connection"]["notes"] = [f"Authorization: Bearer {JWT}"]

    out = _dash("flat", mutate)
    assert "abcd1234efgh" not in out
    assert JWT not in out and "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV" not in out
    assert "SUPERSECRETVALUE99" not in out
    assert "[REDACTED]" in out


def test_trade_opened_panel_fields() -> None:
    position = illustrative_snapshot("open_profit", NOW)["position"]
    position.update(remaining_cash=89950.0, premium_invested=10050.0)
    out = _text(render_trade_opened(position))
    for needle in (
        "VIRTUAL TRADE OPENED",
        "ORB M1",
        "Reason:",
        "breakout confirmation above the previous-day high (PDH)",
        "BUY CE",
        "NIFTY",
        "20000 CE",
        "Entry NIFTY",
        "Entry Option ASK",
        "Virtual Entry Premium",
        "100.50",
        "2 lot(s) x 50 = 100",
        "Target NIFTY",
        "Stop NIFTY",
        "Premium Invested",
        "₹10,050.00",
        "Remaining Cash",
        "₹89,950.00",
        "RESEARCH_PAPER_PROVISIONAL",
    ):
        assert needle in out, needle


def test_trade_opened_replay_label_prominent_and_pe_direction() -> None:
    position = {
        "strategy": "PDS", "method": "METHOD_1", "direction": "BEARISH", "label": "REPLAY_SIMULATED",
        "strike": 20050, "expiry": "2026-11-03", "lots": 1, "quantity": 50, "entry_premium": 80.0,
    }
    out = _text(render_trade_opened(position))
    assert "BUY PE" in out and "NIFTY 03NOV2026 20050 PE" in out
    assert "REPLAY_SIMULATED" in out and "SIMULATED, NOT LIVE" in out
    assert "₹4,000.00" in out  # invested derived from premium * quantity


def test_trade_closed_panel_fields_and_colours() -> None:
    trade = illustrative_trade(NOW)
    out = _text(render_trade_closed(trade))
    for needle in (
        "VIRTUAL TRADE CLOSED", "Strategy:", "Entry Time", "Exit Time", "Exit Reason", "TARGET",
        "Entry Premium", "Exit Premium", "Quantity", "Gross P&L", "Charges", "NET P&L",
        "+₹2,814.75", "Account Balance", "₹102,814.75",
    ):
        assert needle in out, needle
    colour = Console(record=True, width=120, force_terminal=True, color_system="standard")
    colour.print(render_trade_closed(trade))
    assert "\x1b[32m" in colour.export_text(styles=True) or "32m" in colour.export_text(styles=True)
    loss = dict(trade, net_pnl=-500.0, gross_pnl=-450.0, exit_reason="STOP")
    assert "-₹500.00" in _text(render_trade_closed(loss))
    assert "NET P&L" in _text(render_trade_closed({}))


def test_startup_banner_ordered_with_marks_and_redaction() -> None:
    checks = [
        CheckResult("config", True, "ok"),
        CheckResult("login", True, f"token {JWT}"),
        {"name": "calendar", "ok": False, "detail": "RequestToken=zzzz9999yyyy", "blocking": True},
        CheckResult("warm-up", False, "slow", blocking=False),
    ]
    out = _text(render_startup_checks(checks))
    assert out.index("1. config") < out.index("2. login") < out.index("3. calendar")
    assert "✓" in out and "✗" in out
    assert "STARTUP BLOCKED" in out
    assert JWT not in out and "zzzz9999yyyy" not in out
    ok = _text(render_startup_checks([CheckResult("config", True, "ok")]))
    assert "Startup checks passed" in ok


@pytest.mark.parametrize("kind", DEMO_KINDS)
def test_all_demo_kinds_render(kind: str) -> None:
    assert "ILLUSTRATIVE SYNTHETIC" in _dash(kind) or kind == "replay"
