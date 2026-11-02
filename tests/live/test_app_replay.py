"""``--replay``: the SIMULATED historical replay through the REAL engine, levels and strategies.

The underlying candles are real historical NIFTY bars (read-only parquet); ticks and option
quotes are SIMULATED by the replay provider.  Output must stay inside ``<runtime>/replay`` and be
labelled REPLAY_SIMULATED everywhere.  No network, no broker, no secrets.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import pytest
from app_fakes import FakeTime, make_console, spy_sdk_logging  # noqa: F401  (autouse fixture)

pytest.importorskip("pandas")

from trading_platform.live import app, costs  # noqa: E402
from trading_platform.live.checkpoint import Checkpoint, load_checkpoint  # noqa: E402
from trading_platform.live.lock import SingleInstanceLock  # noqa: E402
from trading_platform.live.providers import replay as replay_mod  # noqa: E402

BASE = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    not replay_mod.DEFAULT_5M_PATH.exists() or not replay_mod.DEFAULT_1D_PATH.exists(),
    reason="verified historical dataset absent",
)

BANNER = "SIMULATED — NOT MARKET DATA"
MARKET_DATA_KEYS = {"ticks", "quotes", "candles", "bars", "depth", "book", "ohlc"}
LIVE_SENTINELS = {
    "checkpoint.json": b'{"sentinel": "LIVE checkpoint, replay must never touch me"}\n',
    "live_state.json": b'{"sentinel": "LIVE snapshot"}\n',
    "journal/trades.csv": b"LIVE-LEDGER-SENTINEL\n",
    "journal/events_20990101.jsonl": b'{"sentinel": "LIVE events"}\n',
}


def rows_of(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def run_replay(runtime: Path, extra: list[str] | None = None, hooks=None):
    console, buf = make_console()
    argv = ["--replay", "auto", "--speed", "0", "--no-ui", "--runtime-dir", str(runtime)]
    rc = app.main(argv + (extra or []), console=console, hooks=hooks)
    return rc, buf.getvalue()


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    runtime = tmp_path_factory.mktemp("rt")
    for rel, data in LIVE_SENTINELS.items():
        (runtime / rel).parent.mkdir(parents=True, exist_ok=True)
        (runtime / rel).write_bytes(data)
    live_lock = SingleInstanceLock(runtime / "app.lock", mode="LIVE")
    assert live_lock.acquire()  # a LIVE instance is "running": replay has its own lock
    try:
        first = replay_mod.first_demo_day()
        rc, out = run_replay(runtime)
    finally:
        live_lock.release()
    return {"runtime": runtime, "rc": rc, "out": out, "demo": first, "root": runtime / "replay"}


def flat(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", re.sub(r"[│╭╮╰╯─┃┏┓┗┛━]", " ", text))


def test_replay_exits_zero_and_shows_the_simulated_banner_everywhere(replay):
    assert replay["rc"] == 0
    out = flat(replay["out"])
    assert out.count(BANNER) >= 3
    assert "REPLAY_SIMULATED" in out
    assert str(replay["demo"].session_date) in out
    assert "MODE: REPLAY_SIMULATED" in replay["out"]
    assert "VIRTUAL TRADE CLOSED" in replay["out"]


def test_replay_closed_trades_are_labelled_and_reconcile(replay):
    root: Path = replay["root"]
    rows = rows_of(root / "journal" / "trades.csv")
    assert len(rows) >= 1
    start = 250000.0
    running = start
    for r in rows:
        assert r["label"] == "REPLAY_SIMULATED"
        assert r["session_id"].startswith("SIM-")
        assert r["exit_reason"] in {"TARGET", "STOP", "EOD", "RISK"}
        assert datetime.fromisoformat(r["entry_time"]).time() < datetime(2000, 1, 1, 14, 30).time()
        qty = int(r["quantity"])
        d = datetime.fromisoformat(r["entry_time"]).date()
        # charges exactly once per leg
        e = costs.charges("BUY", d, float(r["entry_premium"]), qty).total
        x = costs.charges("SELL", d, float(r["exit_premium"]), qty).total
        assert float(r["entry_charges"]) == pytest.approx(e, abs=0.011)
        assert float(r["exit_charges"]) == pytest.approx(x, abs=0.011)
        assert float(r["total_charges"]) == pytest.approx(
            float(r["entry_charges"]) + float(r["exit_charges"]), abs=0.011
        )
        assert float(r["gross_pnl"]) - float(r["total_charges"]) == pytest.approx(
            float(r["net_pnl"]), abs=0.011
        )
        # fills: ask + slippage in, bid - slippage out
        assert float(r["entry_premium"]) == pytest.approx(float(r["entry_ask"]) + 0.5, abs=0.006)
        assert float(r["exit_premium"]) == pytest.approx(float(r["exit_bid"]) - 0.5, abs=0.006)
        running += float(r["net_pnl"])
        assert float(r["balance_after"]) == pytest.approx(running, abs=0.02)
    snap = json.loads((root / "live_state.json").read_text())
    cp = load_checkpoint(root / "checkpoint.json")
    assert isinstance(cp, Checkpoint)
    assert snap["account"]["equity"] == pytest.approx(
        start + sum(float(r["net_pnl"]) for r in rows), abs=0.02
    )
    assert cp.account["cash"] == pytest.approx(running, abs=0.02)
    assert cp.position is None and cp.run_mode == "REPLAY" and cp.label == "REPLAY_SIMULATED"


def test_replay_snapshot_is_present_and_labelled(replay):
    snap = json.loads((replay["root"] / "live_state.json").read_text())
    assert snap["schema_version"] == 1
    assert snap["session"]["mode"] == "REPLAY"
    assert snap["session"]["label"] == "REPLAY_SIMULATED"
    assert snap["session"]["date"] == str(replay["demo"].session_date)
    assert snap["session"]["provider"]["banner"] == BANNER
    assert snap["session"]["engine_state"] == "STOPPED"
    assert any(BANNER in n for n in snap["connection"]["notes"])
    assert snap["position"] is None and snap["candles"]


def test_replay_event_journal_is_compact_and_labelled(replay):
    files = sorted((replay["root"] / "journal").glob("events_*.jsonl"))
    assert len(files) == 1 and files[0].name == f"events_{replay['demo'].session_date:%Y%m%d}.jsonl"
    seen = set()

    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                assert k.lower() not in MARKET_DATA_KEYS, k
                walk(x)
        elif isinstance(v, list):
            assert len(v) <= 40
            for x in v:
                walk(x)

    for line in files[0].read_text(encoding="utf-8").splitlines():
        assert len(line.encode()) <= 8192
        ev = json.loads(line)
        seen.add(ev["kind"])
        walk(ev["payload"])
        assert ev["payload"].get("label") == "REPLAY_SIMULATED", ev["kind"]
    assert {"SIGNAL", "TRADE_OPENED", "TRADE_CLOSED", "SHUTDOWN"} <= seen


def test_replay_writes_nothing_outside_its_root_and_never_touches_live_files(replay):
    runtime: Path = replay["runtime"]
    outside = {p for p in tree(runtime) if not p.startswith("replay/")}
    assert outside == set(LIVE_SENTINELS) | {"app.lock"}
    for rel, data in LIVE_SENTINELS.items():
        assert (runtime / rel).read_bytes() == data
    inside = tree(replay["root"])
    assert {"app.lock", "checkpoint.json", "live_state.json", "journal/trades.csv"} <= inside
    assert all(p == "app.lock" or p.startswith(("journal/", "logs/")) or p in
               {"checkpoint.json", "live_state.json"} for p in inside), inside  # fmt: skip


def test_replay_starts_from_a_fresh_state_every_run(replay, tmp_path):
    root: Path = replay["root"]
    before = rows_of(root / "journal" / "trades.csv")
    stale = root / "journal" / "events_20200101.jsonl"
    stale.write_text("{}\n")
    rc, out = run_replay(replay["runtime"])
    assert rc == 0
    after = rows_of(root / "journal" / "trades.csv")
    assert [r["trade_id"] for r in after] == [r["trade_id"] for r in before]  # not appended
    assert sum(float(r["net_pnl"]) for r in after) == pytest.approx(
        sum(float(r["net_pnl"]) for r in before)
    )
    assert not stale.exists()
    assert "old replay file(s) removed" in flat(out)
    for rel, data in LIVE_SENTINELS.items():
        assert (replay["runtime"] / rel).read_bytes() == data


def test_replay_pacing_speed_n_sleeps_n_simulated_minutes_per_real_second(tmp_path):
    ft = FakeTime()
    hooks = app.AppHooks(sleep_fn=ft.sleep, monotonic_fn=ft.monotonic, install_signals=False)
    rc, out = run_replay(tmp_path / "rt", ["--speed", "100"], hooks)
    assert rc == 0
    assert 3.4 <= ft.now <= 4.1  # 375 simulated minutes / 100 per second = 3.75 s


def test_replay_stop_request_writes_a_checkpoint_and_exits_zero(tmp_path):
    import threading

    ev = threading.Event()
    ft = FakeTime()
    ticks = {"n": 0}

    def sleeping(s):
        ft.sleep(s)
        ticks["n"] += 1
        if ticks["n"] > 40:
            ev.set()

    hooks = app.AppHooks(
        sleep_fn=sleeping, monotonic_fn=ft.monotonic, stop_event=ev, install_signals=False
    )
    rc, out = run_replay(tmp_path / "rt", ["--speed", "30"], hooks)
    assert rc == 0 and "stop requested" in flat(out)
    cp = load_checkpoint(tmp_path / "rt" / "replay" / "checkpoint.json")
    assert isinstance(cp, Checkpoint) and cp.run_mode == "REPLAY"


def test_replay_doctor_reports_without_trading_and_without_clearing_state(tmp_path):
    rt = tmp_path / "rt"
    (rt / "replay" / "journal").mkdir(parents=True)
    keep = rt / "replay" / "journal" / "trades.csv"
    keep.write_text("old\n")
    console, buf = make_console()
    rc = app.main(
        ["--replay", "2025-08-18", "--doctor", "--no-ui", "--runtime-dir", str(rt)], console=console
    )
    assert rc == 0, buf.getvalue()
    assert keep.read_text() == "old\n"  # --doctor never resets or writes the ledger
    assert not (rt / "replay" / "checkpoint.json").exists()
    assert "DOCTOR: all checks passed" in flat(buf.getvalue())


def test_replay_invalid_date_and_non_session_date_are_rejected(tmp_path):
    console, buf = make_console()
    assert (
        app.main(
            ["--replay", "not-a-date", "--no-ui", "--runtime-dir", str(tmp_path / "a")],
            console=console,
        )
        == 2
    )
    console2, buf2 = make_console()
    rc = app.main(
        ["--replay", "2025-08-17", "--no-ui", "--runtime-dir", str(tmp_path / "b")],
        console=console2,
    )  # Sunday
    assert rc == 1
    assert "not a supported NSE trading session" in flat(buf2.getvalue()) or "trading" in flat(
        buf2.getvalue()
    )
    assert not (tmp_path / "b" / "replay" / "journal" / "trades.csv").exists()
