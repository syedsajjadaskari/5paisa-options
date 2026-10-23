"""Continuous, strictly read-only NIFTY market-data collector."""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading_platform.data.calendar import NSECalendar
from trading_platform.data.live_market_feed import LiveMarketFeed
from trading_platform.paper.recorder import MarketDataRecorder

TZ_IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class ReadOnlyCollectorConfig:
    """Operational limits for one market-data-only session."""

    poll_interval_seconds: float = 1.0
    option_strike_radius: int = 2
    max_cycles: int | None = None
    state_file: str | Path = "data/cache/read_only_collector_state.json"
    lock_file: str | Path = "data/cache/read_only_collector.lock"
    db_path: str | Path = "data/processed/paper_trading.duckdb"
    archive_dir: str | Path = "data/processed/options_archive_verified"
    max_quote_age_seconds: float = 5.0
    option_request_interval_seconds: float = 0.2


class ReadOnlyCollector:
    """Own one lock-protected, heartbeat-persisted read-only collection session."""

    def __init__(self, config: ReadOnlyCollectorConfig | None = None) -> None:
        self.config = config or ReadOnlyCollectorConfig()
        self.run_id = f"READONLY_{datetime.now(TZ_IST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.calendar = NSECalendar()
        self.feed = LiveMarketFeed(max_staleness_seconds=self.config.max_quote_age_seconds)
        self.recorder = MarketDataRecorder(
            db_path=self.config.db_path,
            archive_dir=self.config.archive_dir,
            run_id=self.run_id,
            max_quote_age_seconds=self.config.max_quote_age_seconds,
            option_request_interval_seconds=self.config.option_request_interval_seconds,
        )
        self._lock_handle: Any | None = None
        self.attempts = 0
        self.cycles = 0
        self.last_error: str | None = None
        self.quality = {
            "spot_valid": 0,
            "spot_rejected": 0,
            "spot_deduplicated": 0,
            "option_valid": 0,
            "option_rejected": 0,
            "option_deduplicated": 0,
            "completed_sampled_candles": 0,
            "candle_rejected": 0,
            "candle_missing_intervals": 0,
            "reconnection_events": 0,
            "feed_interruptions": 0,
            "max_gap_seconds": 0.0,
        }
        self._last_observation_time: datetime | None = None
        self._last_closed_candle_time: datetime | None = None
        self._seen_spot_provider_times: set[datetime] = set()
        self._ever_connected = False

    @property
    def state_file(self) -> Path:
        return Path(self.config.state_file)

    def _persist_heartbeat(self, status: str) -> None:
        """Atomically persist only collector telemetry, never account state."""
        path = self.state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(TZ_IST)
        payload = {
            "run_id": self.run_id,
            "mode": "READ_ONLY",
            "status": status,
            "pid": os.getpid() if status == "RUNNING" else None,
            "heartbeat_time": now.isoformat(),
            "cycles": self.cycles,
            "last_error": self.last_error,
            "last_error_category": self.feed.last_error_category,
            "quality": self.quality,
            "feed_connected": self.feed.is_connected,
            "feed_connection_errors": self.feed.connection_errors_count,
            "feed_interruptions": self.feed.feed_interruptions_count,
            "last_provider_time": (
                self.feed.last_exchange_time.isoformat()
                if self.feed.last_exchange_time
                else None
            ),
        }
        temp = path.with_name(f"{path.name}.tmp.{os.getpid()}_{uuid.uuid4().hex[:6]}")
        temp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(temp, path)

    def acquire_lock(self) -> None:
        """Acquire an exclusive non-blocking process lock."""
        path = Path(self.config.lock_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError("Another read-only market-data collector is already running") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._lock_handle = handle

    def release_lock(self) -> None:
        if self._lock_handle is not None:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None

    def collect_once(self) -> bool:
        """Collect one bounded spot plus ATM-neighbour option observation cycle."""
        self.attempts += 1
        if not self.calendar.is_regular_trading_minute(datetime.now(TZ_IST)):
            self.last_error = "MARKET_CLOSED_OR_UNSUPPORTED_SESSION"
            self._persist_heartbeat("MARKET_CLOSED")
            return False

        was_connected = self.feed.is_connected
        tick = self.feed.fetch_spot_tick()
        if tick is None or tick.is_stale:
            self.quality["spot_rejected"] += 1
            self.quality["feed_interruptions"] += 1
            self.last_error = self.feed.last_error or "STALE_OR_INVALID_SPOT"
            self._persist_heartbeat("RUNNING_DATA_REJECTED")
            return False

        if self._ever_connected and not was_connected and self.feed.is_connected:
            self.quality["reconnection_events"] += 1
        self._ever_connected = True

        if tick.provider_timestamp in self._seen_spot_provider_times:
            self.quality["spot_deduplicated"] += 1
        self._seen_spot_provider_times.add(tick.provider_timestamp)

        self.recorder.record_spot_tick(tick)
        self.quality["spot_valid"] += 1
        if self._last_observation_time is not None:
            gap = (tick.ingestion_timestamp - self._last_observation_time).total_seconds()
            self.quality["max_gap_seconds"] = max(self.quality["max_gap_seconds"], gap)
        self._last_observation_time = tick.ingestion_timestamp

        # A candle is emitted only when a provider-timestamped tick crosses
        # the next 5-minute boundary.  It remains sampled-tick OHLC and is
        # therefore never used as breakout-authoritative strategy input.
        closed_bar = self.feed.aggregator.process_tick(tick)
        if closed_bar is not None:
            # The aggregator exposes the new bucket after the boundary-crossing
            # tick.  Comparing it with the just-closed bucket catches an entire
            # missing interval even when no intermediate candle was emitted.
            next_bucket = self.feed.aggregator.current_bar_open_time
            missing = 0
            if next_bucket is not None:
                expected_next = closed_bar.exchange_time + timedelta(minutes=5)
                gap_minutes = (next_bucket - expected_next).total_seconds() / 60.0
                missing = max(0, int(gap_minutes / 5.0))
            if self._last_closed_candle_time is not None:
                interval_minutes = (
                    closed_bar.exchange_time - self._last_closed_candle_time
                ).total_seconds() / 60.0
                missing = max(missing, max(0, int(interval_minutes / 5.0) - 1))
            if missing:
                self.quality["candle_missing_intervals"] += missing
                self.quality["candle_rejected"] += 1
            else:
                self.quality["completed_sampled_candles"] += 1
                self.recorder.record_candle(closed_bar, interval="5m", is_complete=True)
            self._last_closed_candle_time = closed_bar.exchange_time

        self.recorder.archive_atm_option_chain(
            broker=self.feed.broker,
            spot_price=tick.last_rate,
            strike_radius=self.config.option_strike_radius,
        )
        stats = self.recorder.last_archive_stats
        self.quality["option_valid"] += stats["valid"]
        self.quality["option_rejected"] += stats["rejected"]
        self.quality["option_deduplicated"] += stats["deduplicated"]
        self.last_error = None
        self.cycles += 1
        self._persist_heartbeat("RUNNING")
        return True

    def run(self, max_cycles: int | None = None) -> dict[str, Any]:
        """Run until the session closes, a cycle limit is reached, or interrupted."""
        self.acquire_lock()
        self._persist_heartbeat("RUNNING")
        limit = max_cycles if max_cycles is not None else self.config.max_cycles
        try:
            while limit is None or self.attempts < limit:
                if not self.collect_once():
                    if self.last_error == "MARKET_CLOSED_OR_UNSUPPORTED_SESSION":
                        break
                if limit is None or self.attempts < limit:
                    time.sleep(max(0.0, self.config.poll_interval_seconds))
        except KeyboardInterrupt:
            self.last_error = "GRACEFUL_SHUTDOWN_REQUESTED"
        finally:
            self._persist_heartbeat("STOPPED")
            self.release_lock()
        return self.summary()

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "mode": "READ_ONLY",
            "status": "STOPPED" if self._lock_handle is None else "RUNNING",
            "attempts": self.attempts,
            "cycles": self.cycles,
            "quality": dict(self.quality),
            "last_error": self.last_error,
        }

    @classmethod
    def inspect_state(cls, state_file: str | Path = "data/cache/read_only_collector_state.json") -> dict[str, Any]:
        path = Path(state_file)
        if not path.exists():
            return {"status": "NOT_STARTED"}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "CORRUPT_STATE"}
