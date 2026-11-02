"""Single-instance lock for the paper-trading app (``runtime/app.lock``).

Uses ``fcntl.flock(LOCK_EX | LOCK_NB)`` on an open file descriptor.  The kernel drops the lock
when the holder exits for any reason (including SIGKILL / crash), so a stale lock file left by a
dead process is recovered automatically by the next acquirer; the file content (pid, start
time, mode) is purely informational for the error message.
"""

from __future__ import annotations

import atexit
import fcntl
import json
import os
import signal
from datetime import datetime
from pathlib import Path
from types import FrameType, TracebackType
from typing import Any

from trading_platform.live.models import ist_now


class LockHeldError(RuntimeError):
    """Another live instance holds the lock.  ``holder`` has pid/started_at/mode when known."""

    def __init__(self, path: Path, holder: dict[str, Any] | None) -> None:
        self.path = path
        self.holder = holder or {}
        if holder and holder.get("pid") is not None:
            alive = holder.get("pid_alive")
            state = "running" if alive else ("not running?" if alive is False else "unknown state")
            msg = (
                f"another instance is already running (pid {holder.get('pid')} [{state}], "
                f"mode {holder.get('mode', '?')}, started {holder.get('started_at', '?')}); "
                f"lock file: {path}"
            )
        else:
            msg = f"another instance holds the lock ({path}); holder details unavailable"
        super().__init__(msg)


def _pid_alive(pid: Any) -> bool | None:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (TypeError, ValueError, OverflowError):
        return None
    return True


class SingleInstanceLock:
    """``with SingleInstanceLock(path, mode="LIVE"):`` or ``acquire()`` / ``release()``."""

    def __init__(self, path: str | Path, *, mode: str = "LIVE") -> None:
        self.path = Path(path)
        self.mode = mode
        self.holder: dict[str, Any] | None = None  # filled when acquire() loses
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def read_holder(self) -> dict[str, Any] | None:
        """Best-effort read of the holder info written by the lock owner."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        data["pid_alive"] = _pid_alive(data.get("pid"))
        return data

    def acquire(self, *, raise_on_held: bool = False) -> bool:
        """Try to take the lock.  ``False`` (or :class:`LockHeldError`) if another process has it."""
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # No O_TRUNC: the previous holder's info must stay readable until we own the lock.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            self.holder = self.read_holder()
            if raise_on_held:
                raise LockHeldError(self.path, self.holder) from None
            return False
        info = {
            "pid": os.getpid(),
            "started_at": ist_now().isoformat(),
            "mode": self.mode,
        }
        os.ftruncate(fd, 0)
        os.pwrite(fd, (json.dumps(info) + "\n").encode("utf-8"), 0)
        os.fsync(fd)
        self._fd = fd
        self.holder = None
        atexit.register(self.release)
        return True

    def release(self) -> None:
        """Release the lock (idempotent).  The file is left in place (no unlink race)."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            os.ftruncate(fd, 0)
        except OSError:
            pass
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        atexit.unregister(self.release)

    def __enter__(self) -> SingleInstanceLock:
        self.acquire(raise_on_held=True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    def install_sigterm_release(self) -> None:
        """On SIGTERM release the lock, then re-raise the default termination.

        (The kernel would release it on exit anyway; this makes shutdown explicit and lets
        ``finally`` blocks in the caller run via ``SystemExit``.)
        """

        def _handler(signum: int, _frame: FrameType | None) -> None:
            self.release()
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, _handler)


def describe_started(holder: dict[str, Any]) -> str:
    """Human string for a holder dict (used by CLI status output)."""
    started = holder.get("started_at")
    try:
        started = datetime.fromisoformat(str(started)).strftime("%Y-%m-%d %H:%M:%S %Z")
    except (TypeError, ValueError):
        started = str(started)
    return f"pid {holder.get('pid')} mode {holder.get('mode')} since {started}"
