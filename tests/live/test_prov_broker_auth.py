"""Broker login, token cache, session health and scrip-master policy (fake SDK, no network)."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from prov_fakes import IST, TEST_DAY, FakeSdkClient, at, scrub_env

from trading_platform.brokers import fivepaisa as broker_mod
from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.exceptions import BrokerAuthenticationError, BrokerError


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    scrub_env(monkeypatch)


class Env:
    def __init__(self, tmp_path: Path, fake: FakeSdkClient, now: datetime):
        self.now = now
        self.mono = 1000.0
        self.wall = now.timestamp()
        self.sleeps: list[float] = []
        self.fake = fake
        self.cache = tmp_path / "tok.json"
        self.tmp_path = tmp_path

    def broker(self) -> FivePaisaBroker:
        return FivePaisaBroker(
            client_code="TESTCLIENT",
            user_key="test-user-key-1234",
            encryption_key="test-enc-key-1234",
            cache_dir=self.tmp_path,
            token_cache_path=self.cache,
            client_factory=lambda cred: self.fake,
            now_fn=lambda: self.now,
            wall_time_fn=lambda: self.wall,
            monotonic_fn=lambda: self.mono,
            sleep_fn=self.sleeps.append,
        )


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path, FakeSdkClient(), at(10, 0))


def write_cache(env: Env, token="cached-token-abcdef", ist_date=None, created=None, mode=0o600):
    env.cache.write_text(
        json.dumps(
            {
                "access_token": token,
                "client_code": "TESTCLIENT",
                "created_at": env.wall if created is None else created,
                "ist_date": ist_date or env.now.date().isoformat(),
            }
        )
    )
    os.chmod(env.cache, mode)


def test_totp_login_success_writes_owner_only_cache_with_ist_date(env):
    broker = env.broker()
    assert broker.connect() is True
    assert broker.connection_category == "AUTHENTICATED_TOTP"
    assert env.fake.totp_calls and env.fake.totp_calls[0][0] == "TESTCLIENT"
    assert len(env.fake.totp_calls[0][1]) == 6 and env.fake.totp_calls[0][1].isdigit()
    data = json.loads(env.cache.read_text())
    assert data["ist_date"] == "2026-10-28" and data["access_token"] == "fresh-access-token-123456"
    assert stat.S_IMODE(env.cache.stat().st_mode) == 0o600
    assert not list(env.tmp_path.glob("*.tmp"))
    assert broker.connection_status == "CONNECTED"


def test_valid_cached_token_is_probed_once_and_totp_is_skipped(env):
    write_cache(env)
    env.fake.spot = lambda req: {"Status": 0, "Data": [{"LastRate": 22500.0}]}
    broker = env.broker()
    assert broker.connect() is True
    assert broker.connection_category == "AUTHENTICATED_CACHED_TOKEN"
    assert env.fake.spot_calls() == 1  # exactly one cheap read-only validation call
    assert env.fake.totp_calls == []
    assert env.fake.access_token == "cached-token-abcdef"


def test_cached_token_rejected_by_probe_falls_back_to_one_totp_login(env):
    write_cache(env, token="dead-token-abcdef")
    env.fake.accept_tokens = {"fresh-access-token-123456"}
    broker = env.broker()
    assert broker.connect() is True
    assert broker.connection_category == "AUTHENTICATED_TOTP"
    assert len(env.fake.totp_calls) == 1
    assert json.loads(env.cache.read_text())["access_token"] == "fresh-access-token-123456"


def test_cache_from_previous_ist_date_is_discarded_even_if_young(env):
    write_cache(env, ist_date="2026-10-27", created=env.wall - 3600)
    env.fake.accept_tokens = {"fresh-access-token-123456"}
    broker = env.broker()
    assert broker.connect() is True
    assert broker.connection_category == "AUTHENTICATED_TOTP"
    assert env.fake.spot_calls() == 0  # stale cache never even probed


def test_loose_cache_permissions_are_tightened_to_0600(env):
    write_cache(env, mode=0o644)
    env.fake.spot = lambda req: {"Status": 0, "Data": [{"LastRate": 1.0}]}
    env.broker().connect()
    assert stat.S_IMODE(env.cache.stat().st_mode) == 0o600


def test_corrupt_cache_is_deleted_and_login_proceeds(env):
    env.cache.write_text("{not json")
    assert env.broker().connect() is True
    assert json.loads(env.cache.read_text())["access_token"]


def test_invalid_totp_secret_fails_fast_without_using_the_secret_as_code(env, monkeypatch):
    monkeypatch.setenv("FIVEPAISA_TOTP_SECRET", "not-base32-secret!!")
    broker = env.broker()
    assert broker.connect() is False
    assert env.fake.totp_calls == []  # nothing was sent to the broker
    assert env.sleeps == []  # no retry
    assert broker.connection_category == "AUTH_FAILED"
    assert "not-base32-secret" not in broker.connection_detail


def test_failed_totp_retries_once_at_the_next_window(env):
    env.fake.totp_results = [None, "fresh-access-token-123456"]
    env.wall = 1_000_000_000.0  # 10 s into a 30 s window (1e9 % 30 == 10)
    broker = env.broker()
    assert broker.connect() is True
    assert len(env.fake.totp_calls) == 2
    assert env.sleeps == [pytest.approx(21.0)]  # waits for the next window (+1 s margin)


def test_two_failed_totp_attempts_give_up(env):
    env.fake.totp_results = [None, None, "never-used-token-123456"]
    broker = env.broker()
    assert broker.connect() is False
    assert len(env.fake.totp_calls) == 2
    assert broker.connection_status == "DISCONNECTED"
    assert broker.access_token is None


def test_missing_credentials_never_touch_the_sdk(env):
    broker = FivePaisaBroker(
        client_code="", user_key="", cache_dir=env.tmp_path, client_factory=lambda c: env.fake
    )
    broker.client_code = ""
    broker.user_key = ""
    assert broker.connect() is False
    assert env.fake.calls == [] and env.fake.totp_calls == []


def test_consecutive_auth_failures_flip_to_disconnected_and_relogin_with_backoff(env):
    broker = env.broker()
    assert broker.connect() is True
    broker.record_request_result(False, auth_suspected=True)
    assert broker.connection_status == "DEGRADED"
    broker.record_request_result(False, auth_suspected=True)
    broker.record_request_result(False, auth_suspected=True)
    assert broker.connection_status == "DISCONNECTED"
    assert not env.cache.exists()  # cached token invalidated
    # first re-login attempt is immediate and succeeds with a fresh TOTP session
    env.fake.totp_results = ["second-access-token-123456"]
    assert broker.ensure_session() is True
    assert broker.access_token == "second-access-token-123456"
    assert broker.connection_status == "CONNECTED"


def test_relogin_is_backed_off_then_retried_every_cooldown_forever(env):
    """The old permanent cap (5 attempts, then 'restart required') was a defect: after the fast
    attempts the broker keeps retrying every RELOGIN_COOLDOWN_SECONDS, indefinitely."""
    broker = env.broker()
    broker.connect()
    for _ in range(3):
        broker.record_request_result(False, auth_suspected=True)
    env.fake.totp_results = [None] * 500
    attempt_times: list[float] = []
    for _ in range(4000):  # fake clock: 1 s per step, ~66 minutes
        before = len(env.fake.totp_calls)
        broker.ensure_session()
        if len(env.fake.totp_calls) > before:
            attempt_times.append(env.mono)
        env.mono += 1.0
    assert len(attempt_times) > broker.MAX_RELOGIN_ATTEMPTS  # NOT capped
    gaps = [b - a for a, b in zip(attempt_times, attempt_times[1:], strict=False)]
    # fast, bounded back-off first (5, 15, 45, 120 s) ...
    assert gaps[0] >= 5 and gaps[1] >= 15 and gaps[2] >= 45 and gaps[3] >= 120
    # ... then a steady cool-down: never faster than 300 s, never slower than ~301 s
    assert all(300 <= g <= 302 for g in gaps[broker.MAX_RELOGIN_ATTEMPTS - 1 :])
    assert "exhausted" not in broker.connection_detail
    assert "next in 300s" in broker.connection_detail
    for secret in ("test-key", "test-enc"):
        assert secret not in broker.connection_detail


def test_relogin_recovers_after_the_cooldown_when_the_network_returns(env):
    broker = env.broker()
    broker.connect()
    for _ in range(3):
        broker.record_request_result(False, auth_suspected=True)
    env.fake.totp_results = [None] * 40
    for _ in range(broker.MAX_RELOGIN_ATTEMPTS + 2):  # burn through every fast attempt
        broker.ensure_session()
        env.mono += 400.0
    assert broker.ensure_session() is False  # a due attempt: fails, cool-down starts
    assert broker.connection_status == "DISCONNECTED"
    calls = len(env.fake.totp_calls)
    env.mono += 1.0
    assert broker.ensure_session() is False  # still inside the cool-down: no new attempt
    assert len(env.fake.totp_calls) == calls
    env.mono += 400.0
    env.fake.totp_results = ["network-is-back-token-1"]
    assert broker.ensure_session() is True  # the "network" returned: it logs in again
    assert broker.access_token == "network-is-back-token-1"
    assert broker.relogin_attempts == 0 and broker.connection_status == "CONNECTED"


def test_backoff_blocks_early_retry(env):
    broker = env.broker()
    broker.connect()
    for _ in range(3):
        broker.record_request_result(False, auth_suspected=True)
    env.fake.totp_results = [None, None, "x-token-abcdefgh"]
    assert broker.ensure_session() is False  # attempt 1 fails (two TOTP tries inside)
    calls = len(env.fake.totp_calls)
    env.mono += 1.0
    assert broker.ensure_session() is False
    assert len(env.fake.totp_calls) == calls  # still inside the 5 s backoff: no new login


def test_success_resets_failure_counters(env):
    broker = env.broker()
    broker.connect()
    broker.record_request_result(False, auth_suspected=True)
    broker.record_request_result(True)
    assert broker.consecutive_auth_failures == 0 and broker.connection_status == "CONNECTED"


def test_session_expires_when_the_ist_date_changes(env):
    broker = env.broker()
    broker.connect()
    env.now = env.now + timedelta(days=1)
    env.wall += 86400
    env.fake.totp_results = ["next-day-token-123456"]
    assert broker.ensure_session() is True
    assert broker.access_token == "next-day-token-123456"
    assert broker.session_ist_date == env.now.date()


# ---------------------------------------------------------------- scrip master / history


def _master_csv(rows: int = 1200) -> str:
    frame = pd.DataFrame(
        {
            "Exch": ["N"] * rows,
            "ExchType": ["D"] * rows,
            "ScripCode": range(1, rows + 1),
            "Name": ["NIFTY 03 NOV 2026 CE 25900.00"] * rows,
            "Expiry": ["2026-11-03 14:30:00"] * rows,
            "ScripType": ["CE"] * rows,
            "StrikeRate": [25900.0] * rows,
            "LotSize": [65] * rows,
            "SymbolRoot": ["NIFTY"] * rows,
        }
    )
    return frame.to_csv(index=False)


class FakeResp:
    def __init__(self, status=200, text="", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


def test_scrip_master_downloads_once_per_ist_day_atomically(env, monkeypatch):
    gets = []
    monkeypatch.setattr(
        broker_mod.requests, "get", lambda *a, **k: gets.append(a) or FakeResp(text=_master_csv())
    )
    broker = env.broker()
    broker.scrip_master_daily_refresh = True
    stale = env.tmp_path / "scrip_master.parquet"
    pd.DataFrame({"Exch": ["N"]}).to_parquet(stale)
    old = datetime(2026, 10, 21, 10, 0, tzinfo=IST).timestamp()
    os.utime(stale, (old, old))

    df = broker.get_scrip_master()
    assert len(df) == 1200 and len(gets) == 1
    assert not list(env.tmp_path.glob("*.tmp"))
    assert broker.get_scrip_master() is df and len(gets) == 1  # memory
    # The written cache carries the REAL wall-clock mtime; pin it to the fake clock's day so the
    # test does not depend on the date it is run.
    os.utime(stale, (env.now.timestamp(), env.now.timestamp()))
    again = env.broker()
    again.scrip_master_daily_refresh = True
    again.get_scrip_master()
    assert len(gets) == 1  # same-day parquet reused

    env.now = env.now + timedelta(days=1)  # next IST day -> one refresh
    broker.get_scrip_master()
    assert len(gets) == 2


def test_legacy_default_uses_existing_cache_without_network(env, monkeypatch):
    monkeypatch.setattr(
        broker_mod.requests, "get", lambda *a, **k: pytest.fail("network must not be touched")
    )
    path = env.tmp_path / "scrip_master.parquet"
    pd.DataFrame({"Exch": ["N"]}).to_parquet(path)
    old = datetime(2026, 10, 1, tzinfo=IST).timestamp()
    os.utime(path, (old, old))
    assert len(env.broker().get_scrip_master()) == 1


def test_failed_download_falls_back_to_recent_cache_and_flags_it(env, monkeypatch):
    monkeypatch.setattr(broker_mod.requests, "get", lambda *a, **k: FakeResp(status=503))
    path = env.tmp_path / "scrip_master.parquet"
    pd.DataFrame({"Exch": ["N"]}).to_parquet(path)
    old = datetime(2026, 10, 27, 9, 0, tzinfo=IST).timestamp()
    os.utime(path, (old, old))
    broker = env.broker()
    broker.scrip_master_daily_refresh = True
    assert len(broker.get_scrip_master()) == 1
    assert broker.scrip_master_is_stale is True


def test_failed_download_without_usable_cache_raises(env, monkeypatch):
    monkeypatch.setattr(broker_mod.requests, "get", lambda *a, **k: FakeResp(status=503))
    broker = env.broker()
    broker.scrip_master_daily_refresh = True
    with pytest.raises(BrokerError):
        broker.get_scrip_master()


def test_implausible_master_is_rejected(env, monkeypatch):
    monkeypatch.setattr(broker_mod.requests, "get", lambda *a, **k: FakeResp(text=_master_csv(5)))
    broker = env.broker()
    broker.scrip_master_daily_refresh = True
    with pytest.raises(BrokerError):
        broker.get_scrip_master()


def test_option_bar_parser_has_no_default_lot_size(env):
    broker = env.broker()
    with pytest.raises(TypeError):
        broker.parse_option_bars(pd.DataFrame(), TEST_DAY, 25000.0, "CE", 1)  # type: ignore[call-arg]


def test_historical_401_is_an_auth_failure_and_is_not_retried(env, monkeypatch):
    gets = []
    monkeypatch.setattr(
        broker_mod.requests, "get", lambda *a, **k: gets.append(1) or FakeResp(status=401)
    )
    broker = env.broker()
    broker.connect()
    with pytest.raises(BrokerAuthenticationError):
        broker.fetch_historical_candles(from_date="2026-10-28", to_date="2026-10-29")
    assert len(gets) == 1
    assert broker.consecutive_auth_failures == 1


def test_historical_parses_candles(env, monkeypatch):
    payload = {"data": {"candles": [["2026-10-28T09:15:00", 1, 2, 0.5, 1.5, 0]]}}
    monkeypatch.setattr(broker_mod.requests, "get", lambda *a, **k: FakeResp(payload=payload))
    broker = env.broker()
    broker.connect()
    df = broker.fetch_historical_candles(from_date="2026-10-28", to_date="2026-10-29")
    assert list(df.columns)[:2] == ["timestamp", "open"] and len(df) == 1
    assert broker.consecutive_request_failures == 0
