"""Credential redaction regressions for the read-only market-data path."""

from __future__ import annotations

from trading_platform.core.redaction import (
    REDACTED,
    redact_payload,
    redact_sensitive_text,
    redacted_json,
)


def test_sensitive_text_redacts_request_token_and_jwt() -> None:
    request_token = "request-token-value-123456789"
    jwt = "eyJ" + "a" * 30 + "." + "b" * 30 + "." + "c" * 15
    result = redact_sensitive_text(f"RequestToken={request_token} jwt={jwt}")
    assert request_token not in result
    assert jwt not in result
    assert result.count(REDACTED) == 2


def test_provider_payload_redacts_credentials_before_persistence() -> None:
    payload = {
        "Status": 0,
        "Data": [{"LastRate": 100.0}],
        "access_token": "secret-access-token",
        "nested": {"Authorization": "Bearer secret"},
    }
    result = redact_payload(payload)
    assert result["access_token"] == REDACTED
    assert result["nested"]["Authorization"] == REDACTED
    serialized = redacted_json(payload)
    assert "secret-access-token" not in serialized
    assert "Bearer secret" not in serialized
