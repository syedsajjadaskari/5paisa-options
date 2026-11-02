"""Credential redaction for logs and persisted provider payloads."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

REDACTED = "[REDACTED]"

# Key-name matching works on the key with separators removed, so ``Access_Token``,
# ``accessToken`` and ``access-token`` are all caught.  The short, ambiguous words
# (``pin``, ``totp``) must match a whole word to avoid over-redacting ``spinner`` etc.
# A bare ``token`` is deliberately NOT sensitive: 5paisa uses ``Token`` for the public
# scrip identity of a quote.
_SENSITIVE_SUBSTRINGS = (
    "accesstoken",
    "requesttoken",
    "jwt",
    "authorization",
    "bearer",
    "password",
    "secret",
    "userkey",
    "encryptionkey",
    "apikey",
    "totp",
)
_SENSITIVE_WORDS = frozenset({"pin"})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_ASSIGNMENT_KEYS = (
    r"request[_ -]?token|access[_ -]?token|jwt[_ -]?token|jwt|authorization|bearer|password|"
    r"totp|pin|user[_ -]?key|encryption[_ -]?key|api[_ -]?key|secret"
)
# ``key: value``, ``key=value``, ``'key': 'value'`` and ``"key": "value"`` forms.
_VALUE = r"(?:\[REDACTED\]|[^\s,;\"'}\])]+)"
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9])([\"']?)(" + _ASSIGNMENT_KEYS + r")([\"']?)(\s*[:=]\s*)([\"']?)"
    r"(" + _VALUE + r")"
)
# ``Authorization: <scheme> <credentials>`` - the scheme word must not shield the credential.
_AUTHORIZATION = re.compile(
    r"(?i)(?<![A-Za-z0-9])([\"']?authorization[\"']?\s*[:=]\s*[\"']?)"
    r"(?:(?:bearer|basic|digest|token|negotiate|hmac|oauth|apikey)\s+)?" + _VALUE
)
# ``Cookie: a=b; c=d`` / ``Set-Cookie: ...`` - the whole header value up to end of line.
_COOKIE = re.compile(r"(?i)(?<![A-Za-z0-9])((?:set-)?cookie[\"']?\s*:\s*)[^\r\n]+")
# Whitespace-delimited forms (``request token abc123def``, ``Password hunter2``).  Prose such
# as "password reset failed" must survive, so the value has to contain a digit or be long.
_WS_KEYS = (
    r"request[_ -]?token|access[_ -]?token|jwt[_ -]?token|password|passwd|secret|"
    r"user[_ -]?key|encryption[_ -]?key|api[_ -]?key"
)
_SENSITIVE_WHITESPACE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(" + _WS_KEYS + r")(\s+)"
    r"((?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{4,}|[A-Za-z0-9._~+/=-]{16,})"
)
# ``pin 123456`` / ``TOTP 123456`` / ``otp 123456`` (whole word only, so ``spin 1234`` survives).
_SHORT_SECRET_WORDS = re.compile(r"(?i)(?<![A-Za-z0-9])(pin|totp|otp)(\s+)\d{4,8}(?![A-Za-z0-9])")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}(?![A-Za-z0-9_-])"
)

_KNOWN_SECRETS: set[str] = set()
_KNOWN_SECRETS_LOCK = threading.Lock()
_MIN_SECRET_LENGTH = 8


def register_secret(value: object) -> None:
    """Remember an exact secret value so it is scrubbed wherever it appears in text.

    Values shorter than 8 characters are ignored (too likely to collide with ordinary
    words); short PIN/TOTP values are still caught by the key-based patterns.
    """
    text = str(value or "").strip()
    if len(text) >= _MIN_SECRET_LENGTH:
        with _KNOWN_SECRETS_LOCK:
            _KNOWN_SECRETS.add(text)


def _is_sensitive_key(key: object) -> bool:
    spaced = _CAMEL_BOUNDARY.sub("_", str(key)).lower().replace("-", "_").replace(" ", "_")
    compact = spaced.replace("_", "")
    if any(part in compact for part in _SENSITIVE_SUBSTRINGS):
        return True
    return any(word in _SENSITIVE_WORDS for word in spaced.split("_"))


def redact_payload(value: Any) -> Any:
    """Return a JSON-safe copy with credential-bearing fields removed."""
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive_key(key) else redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_payload(item) for item in value)
    return value


def redact_sensitive_text(value: object) -> str:
    """Redact credential-shaped fields, bearer tokens, JWTs and registered secrets."""
    text = str(value)
    with _KNOWN_SECRETS_LOCK:
        secrets = sorted(_KNOWN_SECRETS, key=len, reverse=True)
    for secret in secrets:
        if secret in text:
            text = text.replace(secret, REDACTED)
    text = _COOKIE.sub(lambda m: m.group(1) + REDACTED, text)
    text = _AUTHORIZATION.sub(lambda m: m.group(1) + REDACTED, text)
    text = _BEARER.sub("Bearer " + REDACTED, text)
    text = _SENSITIVE_ASSIGNMENT.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}{REDACTED}", text
    )
    text = _SENSITIVE_WHITESPACE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _SHORT_SECRET_WORDS.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return _JWT.sub(REDACTED, text)


def redact_deep(value: Any) -> Any:
    """Single-pass deep scrub: sensitive KEYS are replaced and every string VALUE (including
    free text such as event messages and notes) goes through :func:`redact_sensitive_text`.
    Dict keys are coerced to ``str``; tuples/sets become lists (JSON-safe)."""
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _is_sensitive_key(key) else redact_deep(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact_deep(item) for item in value]
    return value


def redacted_json(value: Any) -> str:
    """Serialize a provider payload only after recursive credential redaction."""
    return json.dumps(redact_payload(value), sort_keys=True, default=str)


class SensitiveDataFilter(logging.Filter):
    """Prevent credentials from escaping through a Python logging record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_sensitive_text(record.getMessage())
        record.args = ()
        return True


# --------------------------------------------------------------------------- loguru (py5paisa)

_SDK_LOG_LOCK = threading.Lock()
_sdk_log_path: Path | None = None
_sdk_handler_id: int | None = None
_sdk_stdlib_logger = logging.getLogger("trading_platform.sdk")
_sdk_stdlib_logger.addHandler(logging.NullHandler())
_sdk_stdlib_logger.addFilter(SensitiveDataFilter())
_MAX_SDK_LINE = 400
# Identical consecutive SDK lines (e.g. a failing poll loop) are collapsed within this window.
_SDK_REPEAT_WINDOW_SECONDS = 30.0
_sdk_last_line: str | None = None
_sdk_last_at: float = 0.0
_sdk_suppressed: int = 0


def _sdk_file_logger(path: Path) -> logging.Logger:
    """The app's single bounded, 0600, rotating error logger for ``path`` (shared with the
    engine's error logger so only ONE handler ever appends to / rotates the file)."""
    from trading_platform.live.journal import get_error_logger  # lazy: avoids an import cycle

    return get_error_logger(path.parent, filename=path.name)


def _sdk_sink(message: Any, *, _now: float | None = None) -> None:
    """loguru sink: redact, rate-limit repeats, then write through the rotating error log."""
    global _sdk_last_line, _sdk_last_at, _sdk_suppressed
    try:
        line = redact_sensitive_text(str(message).rstrip())[:_MAX_SDK_LINE]
        path = _sdk_log_path
        if path is None:
            _sdk_stdlib_logger.debug("%s", line)
            return
        now = time.monotonic() if _now is None else _now
        with _SDK_LOG_LOCK:
            if line == _sdk_last_line and now - _sdk_last_at < _SDK_REPEAT_WINDOW_SECONDS:
                _sdk_suppressed += 1
                return
            logger = _sdk_file_logger(path)
            if _sdk_suppressed:
                logger.warning("SDK (previous line repeated %d more times)", _sdk_suppressed)
            _sdk_suppressed = 0
            _sdk_last_line, _sdk_last_at = line, now
            logger.warning("SDK %s", line)
    except Exception:  # a logging sink must never raise into the SDK call
        return


def silence_sdk_logging(error_log_path: str | Path | None = None) -> bool:
    """Remove every loguru handler (py5paisa prints RequestTokens to stdout) and install
    a redacting sink that writes only to ``error_log_path`` (or the stdlib debug logger).

    Idempotent; safe to call again to re-point the log file.  Returns False when loguru
    is not installed (nothing to silence).
    """
    global _sdk_log_path, _sdk_handler_id, _sdk_last_line, _sdk_suppressed
    try:
        from loguru import logger as loguru_logger
    except ImportError:
        return False
    if error_log_path is not None:
        _sdk_log_path = Path(error_log_path)
        _sdk_last_line, _sdk_suppressed = None, 0
    loguru_logger.remove()  # drops py5paisa's sys.stdout sink and any earlier sink of ours
    _sdk_handler_id = loguru_logger.add(_sdk_sink, level="INFO", format="{message}", colorize=False)
    return True
