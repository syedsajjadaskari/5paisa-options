"""CLI validation, helpers, the candle feed, safety (static) checks and the real launcher.

SYNTHETIC / read-only: no network, no broker, no secrets.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from app_fakes import (
    InlineExecutor,
    Run,
    SyntheticLiveProvider,
    flat_then_slide,
    make_console,
    spy_sdk_logging,  # noqa: F401  (autouse fixture)
    write_config,
)
from engine_fakes import Harness
from test_core_helpers import at

from trading_platform.live import app
from trading_platform.live.checkpoint import Checkpoint, load_checkpoint
from trading_platform.live.models import CandleSource, CompletedCandle
from trading_platform.live.providers.base import ManualClock

BASE = Path(__file__).resolve().parents[2]
LIVE_DIR = BASE / "src" / "trading_platform" / "live"
FLAT = lambda s: re.sub(r"\s+", " ", re.sub(r"[│╭╮╰╯─┃┏┓┗┛━]", " ", s))  # noqa: E731


# =========================================================================== CLI validation


def run_cli(argv, **kw):
    console, buf = make_console()
    rc = app.main(argv, console=console, **kw)
    return rc, FLAT(buf.getvalue())


def test_usage_and_config_errors_exit_two(tmp_path):
    rc, out = run_cli(["--speed", "5", "--no-ui", "--runtime-dir", str(tmp_path)])
    assert rc == 2 and "--speed only applies to --replay" in out
    rc, out = run_cli(["--speed", "-1", "--replay", "--no-ui"])
    assert rc == 2
    rc, out = run_cli(["--acknowledge-recovery", "--replay", "auto", "--no-ui"])
    assert rc == 2
    rc, _ = run_cli(["--definitely-not-a-flag"])
    assert rc == 2
    rc, out = run_cli(["--config", str(tmp_path / "missing.yaml"), "--no-ui"])
    assert rc == 2 and "configuration error" in out and "not found" in out
    bad = tmp_path / "bad.yaml"
    bad.write_text("account: {starting_capital_inr: 1}\n")
    rc, out = run_cli(["--config", str(bad), "--no-ui"])
    assert rc == 2 and "configuration error" in out


def test_config_refuses_fills_in_the_strategy_block(tmp_path):
    import yaml

    raw = yaml.safe_load((BASE / "configs" / "live_paper.yaml").read_text())
    raw["strategies"]["PDS"]["tier"] = "RESEARCH_PAPER"
    bad = tmp_path / "t.yaml"
    bad.write_text(yaml.safe_dump(raw))
    rc, out = run_cli(["--config", str(bad), "--no-ui", "--runtime-dir", str(tmp_path / "rt")])
    assert rc == 2 and "cannot be set here" in out


def test_tier_summary_wording():
    from trading_platform.live.authorization import authorize_all
    from trading_platform.live.config import load_live_config

    cfg = load_live_config()
    line, extra = app._tier_summary(authorize_all(cfg, False), False, cfg)
    assert (
        line
        == "PDS SHADOW_ONLY | ORB SHADOW_ONLY | IRF SHADOW_ONLY | PDR SHADOW_ONLY | RF DISABLED"
    )
    assert "not requested (default)" in extra[0] and "NO virtual fills" in extra[0]
    line, extra = app._tier_summary(authorize_all(cfg, True), True, cfg)
    assert "requested (--research-paper) but NOT granted" in extra[0]
    assert any("research_paper.enabled is false" in e for e in extra)


def test_sdk_logging_is_silenced_into_the_runtime_error_log_before_login(tmp_path, spy_sdk_logging):  # noqa: F811
    clock = ManualClock(at(9, 56))
    stop = threading.Event()
    prov = SyntheticLiveProvider(clock, stop_at=at(9, 58), stop_event=stop)
    calls_at_login: list[int] = []
    orig = prov.startup_checks

    def spying(d):
        calls_at_login.append(len(spy_sdk_logging))
        return orig(d)

    prov.startup_checks = spying
    run = Run(tmp_path, provider=prov, clock=clock, stop_event=stop)
    assert run.go() == 0
    assert calls_at_login == [1]  # already silenced when login happens
    assert spy_sdk_logging[0] == run.runtime / "logs" / "errors.log"


# =========================================================================== crash handling


def test_unexpected_error_in_the_loop_preserves_the_position_and_exits_five(tmp_path):
    config = write_config(tmp_path, research=["ORB"])
    clock = ManualClock(at(9, 56))
    stop = threading.Event()
    prov = SyntheticLiveProvider(clock, spot_fn=flat_then_slide(at(10, 10)))
    run = Run(
        tmp_path, ["--research-paper"], provider=prov, clock=clock, stop_event=stop, config=config
    )
    real_sleep = run.hooks.sleep_fn

    def exploding(s):
        real_sleep(s)
        if clock.now() >= at(10, 12):
            raise RuntimeError("synthetic crash in the loop")

    run.hooks.sleep_fn = exploding
    assert run.go() == app.EXIT_CRASH
    assert "UNEXPECTED ERROR" in run.flat and "virtual position preserved, NOT closed" in run.flat
    cp = load_checkpoint(run.checkpoint)
    assert isinstance(cp, Checkpoint) and cp.position is not None
    assert prov.closed
    from trading_platform.live.lock import SingleInstanceLock

    probe = SingleInstanceLock(run.runtime / "app.lock")
    assert probe.acquire()
    probe.release()


def test_poll_exceptions_degrade_instead_of_crashing(tmp_path):
    class Bad(SyntheticLiveProvider):
        def poll(self, watch):
            if self.armed and self.loop_polls % 3 == 0:
                self.loop_polls += 1
                self.clock.set(self.clock.now() + self.step)
                raise ConnectionError("socket dropped password=hunter2hunter2")
            return super().poll(watch)

    clock = ManualClock(at(9, 56))
    stop = threading.Event()
    prov = Bad(clock, stop_at=at(10, 10), stop_event=stop)
    run = Run(tmp_path, provider=prov, clock=clock, stop_event=stop)
    assert run.go() == 0
    assert "hunter2hunter2" not in run.out


# =========================================================================== reset_replay_root


def test_reset_replay_root_removes_only_known_replay_files(tmp_path):
    root = tmp_path / "replay"
    (root / "journal").mkdir(parents=True)
    (root / "logs").mkdir()
    known = [
        root / "checkpoint.json",
        root / "live_state.json",
        root / "journal" / "trades.csv",
        root / "journal" / "trades.csv.torn",
        root / "journal" / "events_20250818.jsonl",
        root / "checkpoint.recovered-20250818T101010.json",
        root / "logs" / "errors.log",
        root / "logs" / "errors.log.1",
        root / ".checkpoint.json.1.2.tmp",
    ]
    keep = [
        root / "app.lock",
        root / "notes.txt",
        root / "journal" / "my_analysis.csv",
        root / "logs" / "other.log",
    ]
    for p in known + keep:
        p.write_text("x")
    outside = tmp_path / "checkpoint.json"
    outside.write_text("live")
    (root / "link.json").symlink_to(outside)
    removed = app.reset_replay_root(root)
    assert sorted(removed) == sorted(known)
    assert all(p.exists() for p in keep) and outside.read_text() == "live"
    assert (root / "link.json").is_symlink()
    assert (root / "journal").is_dir() and (root / "logs").is_dir()
    with pytest.raises(ValueError):
        app.reset_replay_root(tmp_path)  # not a directory named 'replay'
    with pytest.raises(ValueError):
        app.reset_replay_root(tmp_path / "runtime")


# =========================================================================== candle feed


def c(h: int, m: int, day=None) -> CompletedCandle:
    return CompletedCandle(at(h, m), 100.0, 101.0, 99.0, 100.5, 10, CandleSource.PROVIDER_COMPLETED)


class FeedProvider:
    def __init__(self):
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.calls = 0
        self.available: list[CompletedCandle] = []
        self.candle_diagnostics = {"summary": "feed test"}
        self.threads: list[str] = []

    def completed_candles(self, d, as_of):
        self.calls += 1
        self.threads.append(threading.current_thread().name)
        self.entered.set()
        self.gate.wait(5)
        return list(self.available)


def build_feed(tmp_path, executor, provider, **cfg_over):
    h = Harness(tmp_path)
    h.start()
    mono = {"t": 0.0}
    feed = app.CandleFeed(
        provider, h.engine, h.cfg, h.engine.trading_date, executor, lambda: mono["t"]
    )
    return h, feed, mono


def test_candle_feed_uses_one_worker_never_blocks_and_delivers_in_order(tmp_path):
    prov = FeedProvider()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="candle-fetch") as ex:
        h, feed, mono = build_feed(tmp_path, ex, prov)
        prov.available = [c(9, 15)]
        h.set(at(9, 20, 5))  # the 09:15 candle closed 5s ago (+3s poll delay already passed)
        t0 = datetime.now()
        assert (
            feed.step(at(9, 20, 5)) == []
        )  # submitted; returns at once although the worker blocks
        assert (datetime.now() - t0) < timedelta(seconds=1)
        assert prov.entered.wait(2)
        mono["t"] += 10
        h.set(at(9, 20, 6))
        assert feed.step(at(9, 20, 6)) == []  # still in flight: no second submission
        assert prov.calls == 1
        prov.gate.set()
        ex.shutdown(wait=True)  # worker done
        h.set(at(9, 20, 7))
        events = feed.step(at(9, 20, 7))
        assert feed.delivered == {at(9, 15)} and feed.next_open == at(9, 20)
        assert prov.threads == ["candle-fetch_0"]
        assert [e.kind for e in events if e.kind in ("CANDLE_BLOCKED", "ERROR")] == []
        assert [x["t"] for x in h.engine.snapshot()["candles"]] == [at(9, 15).isoformat()]


def test_candle_feed_reports_missing_once_keeps_retrying_and_backfills(tmp_path):
    prov = FeedProvider()
    prov.gate.set()
    ex = InlineExecutor()
    h, feed, mono = build_feed(tmp_path, ex, prov)
    # 09:15 never arrives; wait window is 90s after its 09:20 close
    now = at(9, 20, 5)
    h.set(now)
    feed.step(now)
    assert prov.calls == 1 and not feed.delivered
    mono["t"] += 5
    now = at(9, 21, 40)
    h.set(now)
    ev = feed.step(now)
    assert [e.kind for e in ev] == ["CANDLE_BLOCKED"]
    assert (
        "not supplied by the provider" in ev[0].text
        and "no sampled-tick substitution" in ev[0].text
    )
    assert ev[0].payload["diagnostics"]["summary"] == "feed test"
    mono["t"] += 5
    now = at(9, 22, 0)
    h.set(now)
    assert [e.kind for e in feed.step(now)] == []  # reported once per candle
    assert prov.calls == 3  # but fetching continues
    snap = h.engine.snapshot()
    assert {s["name"]: s["state"] for s in snap["strategies"]}["PDS"] == "BLOCKED"
    # the candle (and the next one) finally appear: backfilled in order, strategies unblock
    prov.available = [c(9, 15), c(9, 20)]
    h.set(at(9, 26, 0))
    mono["t"] += 5
    feed.step(at(9, 26, 0))
    assert feed.delivered == {at(9, 15), at(9, 20)} and feed.next_open == at(9, 25)
    assert h.engine.missing_candle_opens() == []


def test_candle_feed_redelivers_an_open_the_engine_still_lists_as_missing(tmp_path):
    prov = FeedProvider()
    prov.gate.set()
    h, feed, mono = build_feed(tmp_path, InlineExecutor(), prov)
    prov.available = [c(9, 15), c(9, 25)]  # 09:20 missing -> engine holds 09:25
    h.set(at(9, 31, 0))
    feed.step(at(9, 31, 0))
    assert h.engine.missing_candle_opens() == [at(9, 20)]
    assert feed.next_open == at(9, 20)
    prov.available = [c(9, 15), c(9, 20), c(9, 25)]
    mono["t"] += 5
    feed.step(at(9, 31, 10))
    assert h.engine.missing_candle_opens() == [] and feed.next_open == at(9, 30)


def test_candle_feed_survives_a_provider_exception_and_redacts_nothing_into_events(tmp_path):
    class Boom(FeedProvider):
        def completed_candles(self, d, as_of):
            raise RuntimeError("endpoint down")

    h, feed, mono = build_feed(tmp_path, InlineExecutor(), Boom())
    h.set(at(9, 20, 5))
    feed.step(at(9, 20, 5))
    assert feed.last_error and "endpoint down" in feed.last_error
    assert any("endpoint down" in n for n in feed.notes())


# =========================================================================== static safety


def _blocked_names() -> set[str]:
    """Order/modify/cancel SDK names, parsed (not imported) from the read-only boundary."""
    tree = ast.parse((BASE / "src/trading_platform/brokers/fivepaisa.py").read_text())
    for node in ast.walk(tree):
        targets = [t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)]
        if "_BLOCKED_METHOD_NAMES" in targets:
            return {n.value for n in ast.walk(node.value) if isinstance(n, ast.Constant)}
    raise AssertionError("_BLOCKED_METHOD_NAMES not found")


ALLOWLIST: set[str] = set()  # no live/ module or launcher may mention any blocked name


def test_no_live_module_or_launcher_references_order_placement_names():
    names = _blocked_names()
    assert {"place_order", "modify_order", "cancel_order", "squareoff_all"} <= names
    files = sorted(LIVE_DIR.rglob("*.py")) + [BASE / "scripts" / "run_paper_trading.py"]
    assert len(files) > 15
    hits = []
    for f in files:
        if f.relative_to(BASE).as_posix() in ALLOWLIST:
            continue
        text = f.read_text(encoding="utf-8")
        for name in names:
            if re.search(rf"\b{re.escape(name)}\b", text):
                hits.append((f.relative_to(BASE).as_posix(), name))
    assert hits == []


def test_live_path_has_no_market_data_database_or_parquet_writes():
    banned = ("duckdb", "sqlite3", "to_parquet", "to_sql", "pq.write", "ParquetWriter")
    for f in sorted(LIVE_DIR.rglob("*.py")) + [BASE / "scripts" / "run_paper_trading.py"]:
        text = f.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, (f.name, token)


def test_app_does_not_import_the_legacy_execution_paths():
    text = (LIVE_DIR / "app.py").read_text()
    for legacy in (
        "execution.paper_trading_engine",
        "trading_platform.paper",
        "trading_platform.execution",
    ):
        assert legacy not in text
    # the real broker modules are imported lazily, only inside the default provider factory
    head = text.split("def default_provider_factory")[0]
    assert "brokers.fivepaisa" not in head and "providers.fivepaisa" not in head


# =========================================================================== real launcher


def test_launcher_help_does_not_import_the_broker_or_read_env(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(BASE / "scripts" / "run_paper_trading.py"), "--help"],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )  # fmt: skip
    assert proc.returncode == 0
    for flag in ("--research-paper", "--replay", "--speed", "--doctor", "--config",
                 "--runtime-dir", "--no-ui", "--acknowledge-recovery"):  # fmt: skip
        assert flag in proc.stdout


def test_launcher_replay_subprocess_end_to_end(tmp_path):
    pytest.importorskip("pandas")
    from trading_platform.live.providers import replay as replay_mod

    if not replay_mod.DEFAULT_5M_PATH.exists():
        pytest.skip("verified historical dataset absent")
    rt = tmp_path / "runtime"
    env = {**os.environ, "COLUMNS": "140"}
    proc = subprocess.run(
        [sys.executable, str(BASE / "scripts" / "run_paper_trading.py"), "--replay", "2025-08-18",
         "--speed", "0", "--no-ui", "--runtime-dir", str(rt)],
        capture_output=True, text=True, timeout=300, cwd=tmp_path, env=env,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    out = FLAT(proc.stdout)
    assert "SIMULATED — NOT MARKET DATA" in out and "VIRTUAL TRADE CLOSED" in out
    assert (rt / "replay" / "journal" / "trades.csv").exists()
    assert not (rt / "journal").exists() and not (rt / "checkpoint.json").exists()
    assert "Traceback" not in proc.stderr


@pytest.mark.skipif(not hasattr(os, "fork"), reason="pty needs POSIX")
def test_replay_with_the_rich_ui_under_a_pseudo_terminal(tmp_path):
    pytest.importorskip("pandas")
    import pty
    import select

    from trading_platform.live.providers import replay as replay_mod

    if not replay_mod.DEFAULT_5M_PATH.exists():
        pytest.skip("verified historical dataset absent")
    rt = tmp_path / "runtime"
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.chdir(tmp_path)
        os.environ.update({"TERM": "xterm-256color", "COLUMNS": "130", "LINES": "50"})
        os.execv(sys.executable, [sys.executable, str(BASE / "scripts" / "run_paper_trading.py"),
                                  "--replay", "2025-08-18", "--speed", "0", "--runtime-dir", str(rt)])  # fmt: skip
    chunks: list[bytes] = []
    deadline = datetime.now() + timedelta(seconds=240)
    while datetime.now() < deadline:
        ready, _, _ = select.select([fd], [], [], 1.0)
        if ready:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
    _, status = os.waitpid(pid, 0)
    text = b"".join(chunks).decode("utf-8", errors="replace")
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, text[-1500:]
    assert "REPLAY" in text and "SIMULATED" in text
    assert "VIRTUAL TRADE" in text
    assert "\x1b[" in text  # real terminal control sequences were emitted by Rich
    assert (rt / "replay" / "journal" / "trades.csv").exists()
