"""The py5paisa loguru sink must never print RequestTokens / JWTs (stdout, stderr, files)."""

from __future__ import annotations

import stat
import sys

import pytest
from prov_fakes import FAKE_JWT, FakeSdkClient, scrub_env

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core import redaction
from trading_platform.core.redaction import (
    REDACTED,
    redact_payload,
    redact_sensitive_text,
    register_secret,
    silence_sdk_logging,
)

REQUEST_TOKEN = "REQTOKEN-abcdef0123456789"


@pytest.fixture(autouse=True)
def _env_and_sink(monkeypatch):
    scrub_env(monkeypatch)
    monkeypatch.setattr(redaction, "_sdk_log_path", None)
    silence_sdk_logging()  # (re)install the redacting sink with no file
    yield
    monkeypatch.setattr(redaction, "_sdk_log_path", None)
    silence_sdk_logging()


def test_sdk_log_lines_with_tokens_are_never_printed(capfd):
    sdk_logging = pytest.importorskip("py5paisa.logging")
    sdk_logging.log_response(f"RequestToken: {REQUEST_TOKEN}")
    sdk_logging.log_response({"Status": 1, "RequestToken": REQUEST_TOKEN, "Jwt": FAKE_JWT})
    sdk_logging.log_response(f"Logged in {FAKE_JWT}")
    from loguru import logger

    logger.info(f"raw loguru call {FAKE_JWT} Authorization: Bearer {FAKE_JWT}")
    logger.error(f"error path RequestToken={REQUEST_TOKEN}")
    out, err = capfd.readouterr()
    for stream in (out, err):
        assert REQUEST_TOKEN not in stream and FAKE_JWT not in stream
        assert "RequestToken" not in stream  # nothing at all is printed by the SDK sink


def test_redacted_lines_go_only_to_the_app_error_log(tmp_path, capfd):
    sdk_logging = pytest.importorskip("py5paisa.logging")
    log = tmp_path / "logs" / "errors.log"
    silence_sdk_logging(log)
    sdk_logging.log_response(f"RequestToken: {REQUEST_TOKEN}")
    sdk_logging.log_response({"Status": 1, "RequestToken": REQUEST_TOKEN, "Jwt": FAKE_JWT})
    sdk_logging.log_response(f"Logged in!! token {FAKE_JWT}")
    out, err = capfd.readouterr()
    assert out == "" and err == ""
    text = log.read_text()
    assert "SDK" in text and REDACTED in text
    assert REQUEST_TOKEN not in text and FAKE_JWT not in text
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_login_through_the_broker_prints_nothing_even_when_the_sdk_logs_the_token(tmp_path, capfd):
    sdk_logging = pytest.importorskip("py5paisa.logging")

    class ChattyClient(FakeSdkClient):
        def get_totp_session(self, client_code, totp, pin):
            sdk_logging.log_response("RequestToken: " + REQUEST_TOKEN)  # what py5paisa really does
            print("stray stdout", REQUEST_TOKEN)
            print("stray stderr", FAKE_JWT, file=sys.stderr)
            super().get_totp_session(client_code, totp, pin)

    fake = ChattyClient()
    broker = FivePaisaBroker(
        client_code="TESTCLIENT",
        user_key="test-user-key-1234",
        encryption_key="test-enc-key-1234",
        cache_dir=tmp_path,
        token_cache_path=tmp_path / "tok.json",
        client_factory=lambda cred: fake,
    )
    assert broker.connect() is True
    out, err = capfd.readouterr()
    assert REQUEST_TOKEN not in out + err and FAKE_JWT not in out + err
    assert "fresh-access-token-123456" not in out + err
    assert "fresh-access-token-123456" not in broker.connection_detail


def test_importing_the_sdk_leaves_no_stdout_sink():
    pytest.importorskip("py5paisa")
    from loguru import logger

    handlers = logger._core.handlers  # noqa: SLF001 - introspect the installed sinks
    assert len(handlers) == 1
    assert all(getattr(h._sink, "_stream", None) is not sys.stdout for h in handlers.values())


def test_text_redaction_covers_dict_reprs_bearer_headers_and_registered_secrets():
    text = redact_sensitive_text(
        "{'Status': 1, 'RequestToken': 'tok-123456789', 'PIN': '1234'} "
        f"Authorization: Bearer {FAKE_JWT} spin=4 totp=123456"
    )
    assert "tok-123456789" not in text and FAKE_JWT not in text and "1234'" not in text
    assert "spin=4" in text  # whole-word key matching: no over-redaction
    register_secret("opaque-session-secret-xyz")
    assert "opaque-session-secret-xyz" not in redact_sensitive_text("x opaque-session-secret-xyz y")
    register_secret("short")  # too short to register safely
    assert redact_sensitive_text("short") == "short"


def test_payload_redaction_keeps_the_public_scrip_token_but_hides_credentials():
    cleaned = redact_payload(
        {"Token": 999920000, "LastRate": 1.0, "accessToken": "a", "Jwt_Token": "b", "PIN": "1", "spinner": 2}
    )
    assert cleaned["Token"] == 999920000 and cleaned["spinner"] == 2
    assert cleaned["accessToken"] == cleaned["Jwt_Token"] == cleaned["PIN"] == REDACTED
