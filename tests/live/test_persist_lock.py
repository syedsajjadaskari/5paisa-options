"""SYNTHETIC tests: single-instance flock."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from trading_platform.live.lock import LockHeldError, SingleInstanceLock

SRC = str(Path(__file__).resolve().parents[2] / "src")

HOLDER = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, {src!r})
    from trading_platform.live.lock import SingleInstanceLock
    lock = SingleInstanceLock({path!r}, mode="LIVE")
    assert lock.acquire()
    print("ACQUIRED", flush=True)
    time.sleep(60)
    """
)


def _spawn(path: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER.format(src=SRC, path=str(path))],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout.readline().strip() == "ACQUIRED"
    return proc


def test_two_objects_same_process_contend(tmp_path):
    p = tmp_path / "runtime" / "app.lock"
    a, b = SingleInstanceLock(p, mode="LIVE"), SingleInstanceLock(p, mode="REPLAY")
    assert a.acquire() is True and a.held
    assert b.acquire() is False and not b.held
    assert b.holder["pid"] == os.getpid() and b.holder["mode"] == "LIVE"
    with pytest.raises(LockHeldError) as ei:
        b.acquire(raise_on_held=True)
    msg = str(ei.value)
    assert str(os.getpid()) in msg and "started" in msg and "LIVE" in msg
    a.release()
    a.release()  # idempotent
    assert b.acquire() is True
    b.release()


def test_context_manager_releases_even_on_error(tmp_path):
    p = tmp_path / "app.lock"
    with pytest.raises(RuntimeError):
        with SingleInstanceLock(p):
            raise RuntimeError("engine crashed")
    other = SingleInstanceLock(p)
    assert other.acquire()
    other.release()


def test_context_manager_raises_when_held(tmp_path):
    p = tmp_path / "app.lock"
    with SingleInstanceLock(p):
        with pytest.raises(LockHeldError):
            with SingleInstanceLock(p):
                pass  # pragma: no cover


def test_lock_file_content_has_pid_started_mode(tmp_path):
    p = tmp_path / "app.lock"
    with SingleInstanceLock(p, mode="LIVE"):
        info = json.loads(p.read_text())
        assert info["pid"] == os.getpid() and info["mode"] == "LIVE" and info["started_at"]
    assert p.exists() and p.read_text() == ""  # released: file kept, info cleared


def test_real_second_process_is_blocked_and_sees_holder_info(tmp_path):
    p = tmp_path / "app.lock"
    holder = _spawn(p)
    try:
        mine = SingleInstanceLock(p)
        assert mine.acquire() is False
        assert mine.holder["pid"] == holder.pid and mine.holder["pid_alive"] is True
        with pytest.raises(LockHeldError, match=str(holder.pid)):
            mine.acquire(raise_on_held=True)
    finally:
        holder.kill()
        holder.wait()


def test_stale_lock_after_sigkill_is_recovered(tmp_path):
    p = tmp_path / "app.lock"
    holder = _spawn(p)
    holder.kill()  # SIGKILL: no cleanup runs; lock file keeps the dead pid's info
    holder.wait()
    assert json.loads(p.read_text())["pid"] == holder.pid
    mine = SingleInstanceLock(p)
    deadline = time.time() + 5
    while not mine.acquire() and time.time() < deadline:
        time.sleep(0.05)
    assert mine.held
    assert json.loads(p.read_text())["pid"] == os.getpid()
    mine.release()


def test_sigterm_releases_lock_in_holder(tmp_path):
    p = tmp_path / "app.lock"
    code = HOLDER.replace("time.sleep(60)", "lock.install_sigterm_release(); print('READY', flush=True); time.sleep(60)")
    proc = subprocess.Popen(
        [sys.executable, "-c", code.format(src=SRC, path=str(p))], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout.readline().strip() == "ACQUIRED"
        assert proc.stdout.readline().strip() == "READY"
        proc.terminate()
        assert proc.wait(timeout=10) == 128 + 15
        mine = SingleInstanceLock(p)
        assert mine.acquire()
        mine.release()
    finally:
        if proc.poll() is None:
            proc.kill()
