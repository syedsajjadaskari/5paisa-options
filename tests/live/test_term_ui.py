"""TerminalUI behaviour: Live vs non-TTY fallback, scrollback panels, no spam, redaction."""

from __future__ import annotations

from datetime import datetime

from rich.console import Console

from trading_platform.live.terminal import (
    IST,
    TerminalUI,
    demo_main,
    illustrative_snapshot,
    illustrative_trade,
)

NOW = datetime(2026, 10, 28, 10, 30, 0, tzinfo=IST)
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV"
)


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _plain_ui(clock: FakeClock, **kw) -> tuple[TerminalUI, Console]:
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    return TerminalUI(console=console, now_fn=lambda: NOW, monotonic=clock, **kw), console


def test_non_tty_does_not_spam() -> None:
    clock = FakeClock()
    ui, console = _plain_ui(clock, plain_heartbeat_seconds=60.0)
    assert not ui.interactive
    with ui:
        snap = illustrative_snapshot("flat", NOW)
        for i in range(200):
            snap["spot"]["price"] = 20000.0 + i  # price moves every poll
            clock.t += 0.5  # 100 s total
            ui.update(snap, [])
    lines = [line for line in console.export_text().splitlines() if line.strip()]
    # first print + at most 1-2 heartbeats over 100 s; never one per update
    assert 1 <= len(lines) <= 3, lines
    assert "LIVE" in lines[0] and "engine=RUNNING" in lines[0]


def test_non_tty_prints_on_state_change_and_events_once() -> None:
    clock = FakeClock()
    ui, console = _plain_ui(clock)
    snap = illustrative_snapshot("flat", NOW)
    event = {"t": NOW.isoformat(), "kind": "SIGNAL", "text": "fake signal"}
    ui.update(snap, [event])
    ui.update(snap, [event])  # same event again: not re-printed
    blocked = illustrative_snapshot("blocked", NOW)
    ui.update(blocked, [])
    out = console.export_text()
    assert out.count("fake signal") == 1
    assert "engine=BLOCKED" in out
    assert "no trustworthy completed candle" in out
    assert len([ln for ln in out.splitlines() if ln.strip()]) == 3


def test_non_tty_redacts_events_and_messages() -> None:
    clock = FakeClock()
    ui, console = _plain_ui(clock)
    ui.update(
        illustrative_snapshot("flat", NOW),
        [{"t": NOW.isoformat(), "kind": "ERROR", "text": f"boom RequestToken=abcd1234efgh {JWT}"}],
    )
    ui.show_message(f"Authorization: Bearer {JWT}", style="red")
    out = console.export_text()
    assert "abcd1234efgh" not in out and JWT not in out
    assert "[REDACTED]" in out


def test_trade_panels_printed_in_scrollback_non_tty() -> None:
    clock = FakeClock()
    ui, console = _plain_ui(clock)
    ui.show_trade_opened(illustrative_snapshot("open_profit", NOW)["position"])
    ui.show_trade_closed(illustrative_trade(NOW))
    ui.flush()  # panels are queued so they print after the related event lines
    out = console.export_text()
    assert "VIRTUAL TRADE OPENED" in out and "VIRTUAL TRADE CLOSED" in out


def test_startup_banner_via_ui() -> None:
    clock = FakeClock()
    ui, console = _plain_ui(clock)
    ui.startup_banner([{"name": "config", "ok": True, "detail": "fine"}])
    assert "1. config" in console.export_text()


def test_live_mode_on_tty_throttles_and_keeps_scrollback() -> None:
    clock = FakeClock()
    console = Console(record=True, width=120, force_terminal=True, color_system="standard")
    ui = TerminalUI(console=console, refresh_hz=2.0, now_fn=lambda: NOW, monotonic=clock)
    assert ui.interactive
    with ui:
        assert ui._live is not None
        calls = []
        real_update = ui._live.update
        ui._live.update = lambda r, refresh=False: (calls.append(1), real_update(r, refresh=refresh))[1]
        snap = illustrative_snapshot("open_profit", NOW)
        for _ in range(10):  # 10 updates within 0.1 s of fake time
            clock.t += 0.01
            ui.update(snap, [])
        assert len(calls) == 1  # throttled to refresh_hz
        clock.t += 1.0
        ui.update(snap, [])
        assert len(calls) == 2
        ui.show_trade_closed(illustrative_trade(NOW))
    assert ui._live is None
    out = console.export_text()
    assert "VIRTUAL TRADE CLOSED" in out
    assert "NIFTY OPTIONS" in out


def test_update_survives_garbage_input() -> None:
    clock = FakeClock()
    ui, console = _plain_ui(clock)
    ui.update(None, None)  # type: ignore[arg-type]
    ui.update({"position": "weird"}, [None, 5, {"kind": "X"}])  # type: ignore[list-item]
    assert console.export_text()


def test_demo_main_runs(capsys) -> None:
    demo_main()
    out = capsys.readouterr().out
    assert "ILLUSTRATIVE SYNTHETIC" in out
    assert "VIRTUAL TRADE OPENED" in out
