"""SYNTHETIC tests for the safety fixes: opaque proxy methods, snapshot value scrub, event-journal
bulk-data guard, whitespace/cookie/pin redaction and the bounded SDK error-log sink."""

from __future__ import annotations

import json
import logging
import stat
from datetime import datetime

import pytest

from trading_platform.brokers.fivepaisa import (
    SUBSCRIPTION_KEY,
    ReadOnlyBoundaryError,
    _ReadOnlyFivePaisaClientProxy,
)
from trading_platform.core import redaction
from trading_platform.core.redaction import (
    REDACTED,
    redact_deep,
    redact_sensitive_text,
    register_secret,
    silence_sdk_logging,
)
from trading_platform.live.journal import EventJournal, EventRejectedError
from trading_platform.live.models import IST
from trading_platform.live.snapshot import SnapshotWriter

JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
NOW = datetime(2026, 10, 27, 10, 0, 0, tzinfo=IST)


# ------------------------------------------------------------------ 1. proxy opacity


class _FakeSdk:
    def __init__(self) -> None:
        self.access_token = "visible"
        self.client_code = "C1"
        self.placed: list[str] = []

    def fetch_market_feed(self, req):
        return {"echo": req}

    def get_expiry(self, exch, sym):
        return [exch, sym]

    def place_order(self, *a, **k):  # pragma: no cover - must never run
        self.placed.append("PLACED")


def test_allowed_methods_work_but_do_not_expose_the_raw_client():
    fake = _FakeSdk()
    proxy = _ReadOnlyFivePaisaClientProxy(fake)
    assert proxy.fetch_market_feed([1]) == {"echo": [1]}
    assert proxy.get_expiry("N", "NIFTY") == ["N", "NIFTY"]
    assert proxy.access_token == "visible"
    for name in ("fetch_market_feed", "get_expiry"):
        method = getattr(proxy, name)
        assert method.__name__ == name
        for attr in ("__self__", "__func__", "__wrapped__"):
            with pytest.raises(AttributeError):
                getattr(method, attr)
    # the bound-method route to place_order is gone and the name route is blocked
    with pytest.raises(AttributeError):
        _ = proxy.fetch_market_feed.__self__.place_order
    for blocked in ("place_order", "modify_order", "cancel_order", "order_request", "session",
                    "_client", "__dict__"):
        with pytest.raises(PermissionError):
            getattr(proxy, blocked)
    assert fake.placed == []


def test_proxy_docstring_is_honest_about_cpython_limits():
    doc = _ReadOnlyFivePaisaClientProxy.__doc__ or ""
    assert "gc" in doc and "accidental" in doc
    assert issubclass(ReadOnlyBoundaryError, PermissionError)


def test_vendor_subscription_key_is_a_registered_secret():
    assert redact_sensitive_text(f"url?key={SUBSCRIPTION_KEY}") == f"url?key={REDACTED}"


# ------------------------------------------------------------------ 2. snapshot value scrub


def test_snapshot_scrubs_free_text_and_registered_secrets(tmp_path):
    register_secret("registered-secret-value-9")
    writer = SnapshotWriter(tmp_path / "live_state.json")
    doc = {
        "connection": {
            "status": "OK",
            "notes": [f"login ok token {JWT}", "key registered-secret-value-9 leaked"],
        },
        "recent_events": [
            {"t": "x", "kind": "ERROR", "text": f"Authorization: Bearer {JWT}"},
            {"t": "y", "kind": "ERROR", "text": "oops registered-secret-value-9 here"},
        ],
        "strategies": [{"name": "S", "note": "password hunter2hunter2 used"}],
    }
    res = writer.write(doc, now=NOW)
    assert res.ok
    raw = (tmp_path / "live_state.json").read_text()
    assert JWT not in raw and "registered-secret-value-9" not in raw and "hunter2hunter2" not in raw
    loaded = json.loads(raw)
    assert REDACTED in loaded["recent_events"][0]["text"]
    assert loaded["recent_events"][0]["kind"] == "ERROR"  # non-secret text untouched
    assert doc["connection"]["notes"][0].endswith(JWT)  # caller's dict not mutated


# ------------------------------------------------------------------ 3. journal bulk guard


def _t():
    return datetime(2026, 10, 27, 10, 0, tzinfo=IST)


@pytest.mark.parametrize(
    "payload",
    [
        {"ticks_5m": [1, 2]},
        {"ctx": {"quote_snapshots": [{"b": 1}]}},
        {"candles5m": [1]},
        {"a": {"b": {"candles5m": [{"o": 1}]}}},
        {"px": {f"k{i}": i for i in range(250)}},
        {"px": {f"k{i}": i for i in range(41)}},
        {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}},  # depth > 5
        {"series": [{"o": 1, "h": 2, "l": 0, "c": 1} for _ in range(12)]},
        {"rows": [[1, 2, 3, 4] for _ in range(12)]},
        {"order_book": [1]},
        {"market_depth": [{"p": 1}]},
        {"quote_history": {f"t{i}": i for i in range(15)}},
    ],
)
def test_bulk_market_data_shapes_are_refused(tmp_path, payload):
    ej = EventJournal(tmp_path)
    with pytest.raises(EventRejectedError):
        ej.append_event("X", payload, _t())
    assert not ej.path_for(_t().date()).exists()


def test_real_event_shapes_are_still_accepted(tmp_path):
    ej = EventJournal(tmp_path)
    payload = {
        "text": "signal",
        "label": "LIVE",
        "trigger_candle": {"open": 1, "high": 2, "low": 0, "close": 1, "volume": 10, "t": "x"},
        "quote_age_seconds": 1.2,
        "bid_received_at": "2026-10-27T10:00:00+05:30",
        "entry_quote": {"bid": 1, "ask": 2, "ltp": 1.5},
        "candle_open_time": "2026-10-27T10:00:00+05:30",
        "reasons": ["a", "b", "c"],
        "last_evaluated_candle": None,
        "levels": {"pdh": 1, "pdl": 2},
        "ctx": {"a": {"b": {"c": [1, 2, 3]}}},
        "wide": {f"k{i}": i for i in range(30)},
    }
    assert ej.append_event("SIGNAL", payload, _t())["seq"] == 1


# ------------------------------------------------------------------ 4a. text redaction


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("request token abcdef123456 failed", "abcdef123456"),
        ("Password hunter2hunter2", "hunter2hunter2"),
        ("access token ey12345abcde", "ey12345abcde"),
        ("Cookie: session=abc123; other=zzz999", "abc123"),
        ("Set-Cookie: sid=topsecret77; Path=/", "topsecret77"),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
        ("Authorization: Bearer abcdefghijklmnop", "abcdefghijklmnop"),
        ('{"authorization": "Token tok12345678"}', "tok12345678"),
        ("login with pin 123456 now", "123456"),
        ("TOTP 654321 rejected", "654321"),
        ("PIN: 1234", "1234"),
    ],
)
def test_more_credential_text_forms_are_redacted(text, secret):
    out = redact_sensitive_text(text)
    assert secret not in out and REDACTED in out


@pytest.mark.parametrize(
    "text",
    [
        "spin 123456 times",
        "the spinner pin",
        "password reset failed",
        "request token expired",
        "pin 5 of 9",
        "access token refresh needed",
        "mapping 12 cookies",
    ],
)
def test_ordinary_prose_is_not_over_redacted(text):
    assert redact_sensitive_text(text) == text


def test_redact_deep_scrubs_nested_text_and_keys():
    out = redact_deep({"a": [{"text": f"x {JWT}"}], "Password": "p", "n": 3, ("k",): 1})
    assert JWT not in json.dumps(out) and out["Password"] == REDACTED and out["n"] == 3


# ------------------------------------------------------------------ 4b. bounded SDK sink


@pytest.fixture
def sdk_sink(monkeypatch):
    pytest.importorskip("loguru")
    monkeypatch.setattr(redaction, "_sdk_log_path", None)
    yield
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("trading_platform.live.errors."):
            lg = logging.getLogger(name)
            for h in list(lg.handlers):
                h.close()
                lg.removeHandler(h)
    monkeypatch.setattr(redaction, "_sdk_log_path", None)
    silence_sdk_logging()


def test_sdk_sink_uses_the_rotating_error_logger_and_stays_private(tmp_path, sdk_sink):
    from logging.handlers import RotatingFileHandler

    log = tmp_path / "logs" / "errors.log"
    silence_sdk_logging(log)
    redaction._sdk_sink(f"RequestToken: {JWT}")  # noqa: SLF001
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert JWT not in log.read_text() and "SDK" in log.read_text()
    from trading_platform.live.journal import get_error_logger

    handlers = get_error_logger(log.parent, filename=log.name).handlers
    assert len([h for h in handlers if isinstance(h, RotatingFileHandler)]) == 1


def test_sdk_sink_rotates_instead_of_growing_forever(tmp_path, sdk_sink):
    from trading_platform.live.journal import get_error_logger

    log = tmp_path / "errors.log"
    get_error_logger(tmp_path, filename="errors.log", max_bytes=2000, backup_count=2)
    silence_sdk_logging(log)
    for i in range(200):
        redaction._sdk_sink(f"distinct sdk line number {i} " + "x" * 60)  # noqa: SLF001
    files = sorted(p.name for p in tmp_path.iterdir())
    assert "errors.log.1" in files
    assert all(p.stat().st_size < 4000 for p in tmp_path.iterdir())
    assert len(files) <= 3


def test_sdk_sink_rate_limits_identical_consecutive_lines(tmp_path, sdk_sink):
    log = tmp_path / "errors.log"
    silence_sdk_logging(log)
    for i in range(50):
        redaction._sdk_sink("same failing line", _now=100.0 + i * 0.1)  # noqa: SLF001
    assert log.read_text().count("same failing line") == 1
    redaction._sdk_sink("different line", _now=110.0)  # noqa: SLF001
    text = log.read_text()
    assert "repeated 49 more times" in text and "different line" in text
    redaction._sdk_sink("different line", _now=200.0)  # noqa: SLF001  (window elapsed)
    assert log.read_text().count("different line") == 2
