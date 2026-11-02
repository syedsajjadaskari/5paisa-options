"""5paisa Broker Read-Only Adapter and Market Data Client with safety safeguards."""

import io
import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from trading_platform.brokers.base import BaseBroker
from trading_platform.core.enums import (
    DataSource,
    Exchange,
    InstrumentType,
    OperationalMode,
    Timeframe,
)
from trading_platform.core.exceptions import (
    BrokerAuthenticationError,
    BrokerError,
    LiveTradingPermissionError,
)
from trading_platform.core.models import Bar, Order, Position
from trading_platform.core.redaction import (
    SensitiveDataFilter,
    redact_sensitive_text,
    register_secret,
    silence_sdk_logging,
)
from trading_platform.data.option_chain import OptionBar

try:
    from py5paisa import FivePaisaClient

    _HAS_PY5PAISA = True
    # py5paisa registers a loguru sink on sys.stdout at import time and logs the
    # RequestToken / failure bodies through it.  Replace it with a redacting sink
    # that writes to the app error log only (never stdout/stderr).
    silence_sdk_logging()
except ImportError:
    _HAS_PY5PAISA = False

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")
logger = logging.getLogger(__name__)
logger.addFilter(SensitiveDataFilter())

# Library and dashboard callers may instantiate the broker directly instead of
# going through the CLI, so credentials must be loaded consistently. Values
# are never logged or exposed by this module.
load_dotenv()

SCRIP_MASTER_URL = "https://Openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/All"
HISTORICAL_V2_URL = "https://openapi.5paisa.com/V2/historical/"
SUBSCRIPTION_KEY = "c89fab8d895a426d9e00db380b433027"
register_secret(SUBSCRIPTION_KEY)  # never let the vendor key reach a log / snapshot / event
DEFAULT_TOKEN_CACHE = Path("data/cache/.token_cache.json")
_SCRIP_MASTER_REQUIRED_COLUMNS = frozenset(
    {"Exch", "ExchType", "ScripCode", "Name", "Expiry", "ScripType", "StrikeRate", "LotSize", "SymbolRoot"}
)
_TOTP_WINDOW_SECONDS = 30


class ReadOnlyBoundaryError(LiveTradingPermissionError, PermissionError):
    """Raised when code reaches for an SDK member outside the read-only allowlist."""


# Market-data / session helpers the application is allowed to reach.  Everything else
# in the SDK (orders, baskets, GTT/VTT, positions, holdings, margin, ledger, tradebook,
# order margin calculators, websocket plumbing, raw ``order_request`` ...) is unreachable.
_ALLOWED_METHODS = frozenset(
    {
        "fetch_market_feed_scrip",  # V1/MarketFeed  (spot + option LTP)
        "fetch_market_feed",
        "fetch_market_depth",  # V3/MarketDepth (top-5 book)
        "fetch_market_depth_by_symbol",
        "fetch_market_snapshot",
        "get_expiry",
        "get_option_chain",
        "historical_data",  # V2/historical candles
        "get_market_status",
        "set_access_token",
        "get_totp_session",  # TOTP login (RequestToken -> AccessToken)
    }
)
# Plain data attributes that may be read (never written) through the proxy.
_ALLOWED_ATTRIBUTES = frozenset({"access_token", "client_code"})
# Explicit deny layer kept on top of the allowlist (defence in depth + clearer message).
_BLOCKED_METHOD_NAMES = frozenset(
    {
        "order_request",
        "place_order",
        "modify_order",
        "cancel_order",
        "bo_order",
        "modify_bo_order",
        "cancel_bo_order",
        "cover_order",
        "modify_cover_order",
        "cancel_cover_order",
        "create_basket",
        "rename_basket",
        "delete_basket",
        "clone_basket",
        "execute_basket",
        "add_basket_order",
        "cancel_bulk_order",
        "squareoff_all",
        "position_convertion",
        "vtt_order",
        "place_order_bulk",
    }
)
assert not (_ALLOWED_METHODS | _ALLOWED_ATTRIBUTES) & _BLOCKED_METHOD_NAMES


def _opaque_callable(method: Callable[..., Any], name: str) -> Callable[..., Any]:
    """Wrap a bound SDK method in a plain function so ``__self__``/``__func__`` are absent."""

    def call(*args: Any, **kwargs: Any) -> Any:
        return method(*args, **kwargs)

    call.__name__ = name
    call.__qualname__ = f"ReadOnlyFivePaisaClient.{name}"
    call.__doc__ = getattr(method, "__doc__", None)
    return call


class _ReadOnlyFivePaisaClientProxy:
    """Allowlist proxy: only market-data/session members of the SDK client are reachable.

    The wrapped client, its ``session`` and its shared payload dicts are not exposed, and
    allowlisted methods are returned as plain wrapper closures, so ``proxy.fetch_market_feed
    .__self__`` / ``__func__`` do not lead back to the raw SDK client (and its ``place_order``).

    Honest limit: CPython cannot make an object perfectly opaque.  Deliberate introspection
    (``gc.get_referents``, closure-cell inspection, ``object.__getattribute__`` tricks) can
    still reach the wrapped client.  The guarantee is "no accidental or name-based access":
    nothing off the allowlist is reachable by attribute name, and the ordinary routes to the
    raw client (``_client``, ``__self__``, ``__wrapped__``, ``__dict__``) are closed.
    """

    __slots__ = ("_ro_target",)
    ALLOWED_METHODS = _ALLOWED_METHODS
    ALLOWED_ATTRIBUTES = _ALLOWED_ATTRIBUTES
    _BLOCKED_METHODS = _BLOCKED_METHOD_NAMES

    def __init__(self, wrapped: Any) -> None:
        object.__setattr__(self, "_ro_target", wrapped)

    def __getattribute__(self, name: str) -> Any:
        if name in _BLOCKED_METHOD_NAMES:
            raise ReadOnlyBoundaryError(
                f"5paisa SDK transactional member {name!r} is blocked by the read-only client boundary."
            )
        if name in _ALLOWED_METHODS:
            target = object.__getattribute__(self, "_ro_target")
            member = getattr(target, name)
            if not callable(member):
                return member
            return _opaque_callable(member, name)
        if name in _ALLOWED_ATTRIBUTES:
            return getattr(object.__getattribute__(self, "_ro_target"), name)
        if name == "__class__":
            return object.__getattribute__(self, "__class__")
        raise ReadOnlyBoundaryError(
            f"5paisa SDK member {name!r} is not on the read-only allowlist."
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise ReadOnlyBoundaryError("The read-only 5paisa client proxy is immutable.")

    def __delattr__(self, name: str) -> None:
        raise ReadOnlyBoundaryError("The read-only 5paisa client proxy is immutable.")

    def __dir__(self) -> list[str]:
        return sorted(_ALLOWED_METHODS | _ALLOWED_ATTRIBUTES)

    def __repr__(self) -> str:
        return "<ReadOnlyFivePaisaClientProxy>"


@contextmanager
def _quiet_sdk() -> Iterator[None]:
    """Capture stray SDK prints (the loguru sink is replaced separately)."""
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        yield


class FivePaisaBroker(BaseBroker):
    """5paisa Market Data & Read-Only Broker Adapter."""

    AUTH_FAILURE_THRESHOLD = 3  # consecutive auth-suspect failures before DISCONNECTED
    MAX_RELOGIN_ATTEMPTS = 5  # fast (backed-off) re-login attempts per outage
    RELOGIN_BACKOFF_SECONDS = (5.0, 15.0, 45.0, 120.0, 300.0)
    #: After the fast attempts are used up, retry this often, indefinitely.  A transient network
    #: blip must never leave the app permanently disconnected until a manual restart.
    RELOGIN_COOLDOWN_SECONDS = 300.0
    TOKEN_MAX_AGE_SECONDS = 20 * 3600
    SCRIP_MASTER_MAX_STALE_DAYS = 3

    def __init__(
        self,
        mode: OperationalMode = OperationalMode.PAPER,
        live_approved: bool = False,
        client_code: str | None = None,
        user_key: str | None = None,
        encryption_key: str | None = None,
        cache_dir: str | Path = "data/reference",
        token_cache_path: str | Path | None = None,
        client_factory: Callable[[dict[str, str]], Any] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        wall_time_fn: Callable[[], float] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(mode=mode, live_approved=live_approved)
        self.client_code = client_code or os.getenv("FIVEPAISA_USER_ID", "")
        self.user_key = user_key or os.getenv("FIVEPAISA_USER_KEY", "")
        self.encryption_key = encryption_key or os.getenv("FIVEPAISA_ENCRYPTION_KEY", "")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.token_cache_path = Path(token_cache_path) if token_cache_path else DEFAULT_TOKEN_CACHE
        self._client_factory = client_factory
        self._now_fn = now_fn or (lambda: datetime.now(TZ_IST))
        self._wall_time = wall_time_fn or time.time
        self._monotonic = monotonic_fn or time.monotonic
        self._sleep = sleep_fn or time.sleep
        self._client: Any = None
        self._scrip_df: pd.DataFrame | None = None
        self._scrip_loaded_date: date | None = None
        self.scrip_master_is_stale: bool = False
        # Off by default so legacy/offline callers keep using the on-disk cache as-is.
        # The live provider switches it on: refresh at most once per IST day.
        self.scrip_master_daily_refresh: bool = False
        self.access_token: str | None = None
        self.connection_category: str = "NOT_ATTEMPTED"
        self.connection_detail: str = ""
        self.session_ist_date: date | None = None
        self.consecutive_auth_failures: int = 0
        self.consecutive_request_failures: int = 0
        self.relogin_attempts: int = 0
        self._next_relogin_at: float = 0.0
        for secret in (self.user_key, self.encryption_key):
            register_secret(secret)

    # ------------------------------------------------------------------ client / proxy

    @property
    def client(self) -> Any:
        return self._client

    @client.setter
    def client(self, value: Any) -> None:
        if value is None or isinstance(value, _ReadOnlyFivePaisaClientProxy):
            self._client = value
        else:
            self._client = _ReadOnlyFivePaisaClientProxy(value)

    def _today_ist(self) -> date:
        return self._now_fn().astimezone(TZ_IST).date()

    # ------------------------------------------------------------------ session health

    @property
    def connection_status(self) -> str:
        """CONNECTED | DEGRADED | DISCONNECTED (shown by the terminal/dashboard)."""
        if not self.is_connected:
            return "DISCONNECTED"
        if self.consecutive_request_failures > 0:
            return "DEGRADED"
        return "CONNECTED"

    def record_request_result(self, ok: bool, *, auth_suspected: bool = False) -> None:
        """Feed the outcome of a market-data request into the session health tracker.

        ``auth_suspected`` marks failures that look like an expired/invalid session (HTTP
        401, auth messages, or an empty body: the SDK swallows every error and returns
        None).  ``AUTH_FAILURE_THRESHOLD`` consecutive ones flip the broker to
        DISCONNECTED so that ``ensure_session`` re-authenticates.
        """
        if ok:
            self.consecutive_auth_failures = 0
            self.consecutive_request_failures = 0
            return
        self.consecutive_request_failures += 1
        if auth_suspected:
            self.consecutive_auth_failures += 1
            if self.consecutive_auth_failures >= self.AUTH_FAILURE_THRESHOLD and self.is_connected:
                self._mark_disconnected(
                    f"{self.consecutive_auth_failures} consecutive authentication-suspect failures"
                )

    def _mark_disconnected(self, reason: str) -> None:
        self.is_connected = False
        self.access_token = None
        self.connection_category = "DISCONNECTED"
        self.connection_detail = redact_sensitive_text(reason)[:160]
        self.relogin_attempts = 0
        self._next_relogin_at = 0.0
        self._delete_token_cache()

    def ensure_session(self) -> bool:
        """Return True when authenticated; otherwise attempt a bounded, backed-off re-login."""
        if self.is_connected and self.session_ist_date and self.session_ist_date != self._today_ist():
            self._mark_disconnected("IST date changed; session token expires at midnight IST")
        if self.is_connected:
            return True
        if self._monotonic() < self._next_relogin_at:
            return False
        self.connection_detail = ""  # rebuilt by connect(); avoids stacking attempt suffixes
        try:
            ok = self.connect(force_fresh=True)
        except BrokerError:
            ok = False
        if ok:
            self.relogin_attempts = 0
            self._next_relogin_at = 0.0
            return True
        index = min(self.relogin_attempts, len(self.RELOGIN_BACKOFF_SECONDS) - 1)
        self.relogin_attempts = min(self.relogin_attempts + 1, 10_000)
        if self.relogin_attempts > self.MAX_RELOGIN_ATTEMPTS:
            delay = self.RELOGIN_COOLDOWN_SECONDS  # fast attempts used up: cool-down, not give-up
        else:
            delay = self.RELOGIN_BACKOFF_SECONDS[index]
        self._next_relogin_at = self._monotonic() + delay
        # connection_detail is already redacted by connect(); only a generic note is appended.
        self.connection_detail = (
            f"{self.connection_detail}; re-login attempt {self.relogin_attempts} failed, "
            f"next in {delay:.0f}s"
        )[:240]
        return False

    def validate_session(self) -> tuple[bool, str]:
        """One cheap read-only call (NIFTY spot quote) proving the token is accepted."""
        if self.client is None:
            return False, "no client"
        try:
            with _quiet_sdk():
                resp = self.client.fetch_market_feed_scrip(
                    [{"Exch": "N", "ExchType": "C", "ScripCode": self.get_nifty_spot_scrip_code()}]
                )
        except Exception as exc:
            return False, f"validation probe raised {type(exc).__name__}"
        if not isinstance(resp, dict):
            return False, "validation probe returned no usable response"
        if resp.get("Status") != 0:
            return False, redact_sensitive_text(f"validation probe rejected: {resp.get('Message')}")[:120]
        data = resp.get("Data")
        if not isinstance(data, list) or not data:
            return False, "validation probe returned no data"
        return True, "validation probe OK"

    # ------------------------------------------------------------------ token cache

    def _delete_token_cache(self) -> None:
        try:
            self.token_cache_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _read_token_cache(self) -> dict[str, Any] | None:
        """Return the cached session if it is well-formed, owner-only and from today (IST)."""
        path = self.token_cache_path
        if not path.exists():
            return None
        try:
            if (path.stat().st_mode & 0o077) != 0:
                os.chmod(path, 0o600)
            data = json.loads(path.read_text())
            token = data.get("access_token")
            created = float(data.get("created_at", 0))
            if not isinstance(token, str) or not token:
                raise ValueError("no token")
            cache_date = data.get("ist_date") or datetime.fromtimestamp(created, TZ_IST).date().isoformat()
            age = self._wall_time() - created
            if cache_date != self._today_ist().isoformat() or age >= self.TOKEN_MAX_AGE_SECONDS or age < -300:
                raise ValueError("expired")
        except Exception:
            self._delete_token_cache()
            return None
        register_secret(data["access_token"])
        return data

    def _write_token_cache(self, token: str, client_code: str) -> None:
        path = self.token_cache_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {
                    "access_token": token,
                    "client_code": client_code,
                    "created_at": self._wall_time(),
                    "ist_date": self._today_ist().isoformat(),
                }
            )
            tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Could not persist the session token cache: %s", type(exc).__name__)

    # ------------------------------------------------------------------ login

    @staticmethod
    def _totp_code(secret: str) -> str:
        """Current TOTP code.  Fails fast: never sends the secret itself as the code."""
        if re.fullmatch(r"\d{6}", secret):
            return secret  # explicit manual 6-digit code
        try:
            import pyotp

            return str(pyotp.TOTP(secret).now())
        except Exception:
            raise BrokerAuthenticationError(
                "TOTP secret cannot generate a code (invalid base32 secret or pyotp missing)"
            ) from None

    def _seconds_to_next_totp_window(self) -> float:
        return _TOTP_WINDOW_SECONDS - (self._wall_time() % _TOTP_WINDOW_SECONDS) + 1.0

    def _try_cached_token(self, account_client_code: str) -> bool:
        data = self._read_token_cache()
        if data is None:
            return False
        code = data.get("client_code") or account_client_code
        self.access_token = data["access_token"]
        with _quiet_sdk():
            self.client.set_access_token(self.access_token, code)
        ok, detail = self.validate_session()
        if ok:
            self._set_connected("AUTHENTICATED_CACHED_TOKEN", "Cached token accepted by read-only probe")
            return True
        self.access_token = None
        with _quiet_sdk():
            self.client.set_access_token("", code)
        self._delete_token_cache()
        self.connection_detail = f"Cached token rejected ({detail})"
        return False

    def _set_connected(self, category: str, detail: str) -> None:
        self.is_connected = True
        self.connection_category = category
        self.connection_detail = detail
        self.session_ist_date = self._today_ist()
        self.consecutive_auth_failures = 0
        self.consecutive_request_failures = 0

    def _login_totp(self, account_client_code: str) -> bool:
        secret = os.getenv("FIVEPAISA_TOTP_SECRET", "").strip()
        pin = os.getenv("FIVEPAISA_PIN", "").strip()
        register_secret(secret)
        register_secret(os.getenv("FIVEPAISA_PASSWORD", ""))
        if not secret:
            self.connection_category = "AUTH_FAILED"
            self.connection_detail = "TOTP secret is not configured"
            return False
        manual_code = bool(re.fullmatch(r"\d{6}", secret))
        with _quiet_sdk():  # never mistake a leftover token for the result of this login
            self.client.set_access_token("", account_client_code)
        for attempt in (1, 2):
            try:
                totp_code = self._totp_code(secret)
            except BrokerAuthenticationError as exc:
                self.connection_category = "AUTH_FAILED"
                self.connection_detail = str(exc)
                return False
            token: Any = None
            try:
                with _quiet_sdk():
                    self.client.get_totp_session(account_client_code, totp_code, pin)
                    token = self.client.access_token
            except Exception as exc:
                self.connection_detail = f"TOTP authentication raised {type(exc).__name__}"
            if isinstance(token, str) and token:
                self.access_token = token
                register_secret(token)
                self._write_token_cache(token, account_client_code)
                self._set_connected("AUTHENTICATED_TOTP", "Fresh TOTP session established")
                return True
            if attempt == 1 and not manual_code:
                self._sleep(self._seconds_to_next_totp_window())  # retry once, next TOTP window
        self.connection_category = "AUTH_FAILED"
        self.connection_detail = "TOTP authentication failed (no access token returned)"
        return False

    def connect(self, force_fresh: bool = False) -> bool:
        """Initialize the 5paisa client session.

        Order: (1) a cached token from today (IST) that passes one read-only probe;
        (2) TOTP login (one retry at the next TOTP window).  ``force_fresh`` skips (1).
        """
        if not self.client_code or not self.user_key:
            self.is_connected = False
            self.connection_category = "AUTH_FAILED"
            self.connection_detail = "Required credentials are unavailable"
            return False

        factory = self._client_factory or (FivePaisaClient if _HAS_PY5PAISA else None)
        if factory is None:
            self.connection_category = "SDK_UNAVAILABLE"
            self.connection_detail = "py5paisa is not installed"
            raise BrokerError("py5paisa library is not installed in the current environment.")

        try:
            silence_sdk_logging()
            user_id = os.getenv("FIVEPAISA_USER_ID", self.client_code)
            cred = {
                "APP_NAME": os.getenv("FIVEPAISA_APP_NAME", "APP"),
                "APP_SOURCE": os.getenv("FIVEPAISA_APP_SOURCE", "SOURCE"),
                "USER_ID": user_id,
                "PASSWORD": os.getenv("FIVEPAISA_PASSWORD", ""),
                "USER_KEY": self.user_key,
                "ENCRYPTION_KEY": self.encryption_key,
            }
            with _quiet_sdk():
                self.client = factory(cred)
            self.is_connected = False
            self.access_token = None
            account_client_code = os.getenv("FIVEPAISA_CLIENT_CODE", self.client_code).strip()

            if not force_fresh:
                try:
                    if self._try_cached_token(account_client_code):
                        return True
                except Exception as exc:
                    self.connection_detail = f"Cached token rejected: {type(exc).__name__}"
            if hasattr(self.client, "get_totp_session") and self._login_totp(account_client_code):
                return True
            self.is_connected = False
            self.connection_category = "AUTH_FAILED"
            if not self.connection_detail:
                self.connection_detail = "No usable access token"
            return False
        except Exception as e:
            self.is_connected = False
            self.connection_category = "SDK_OR_NETWORK_FAILED"
            safe_error = redact_sensitive_text(str(e))
            self.connection_detail = f"{type(e).__name__}: {safe_error[:160]}"
            raise BrokerError(f"Failed to connect to 5paisa: {safe_error}") from e

    def disconnect(self) -> None:
        self.is_connected = False
        self.client = None
        self.access_token = None
        self.connection_category = "DISCONNECTED"
        self.connection_detail = "Client disconnected"

    # ------------------------------------------------------------------ scrip master

    def get_scrip_master(self, force_refresh: bool = False) -> pd.DataFrame:
        """Active Scrip Master held in memory.

        With ``scrip_master_daily_refresh`` (the live provider sets it) the master is
        re-downloaded at most once per IST day; otherwise an existing cache is used as-is.
        A parquet cache (atomic write) survives restarts on the same IST day.  If the
        daily download fails, a cache younger than ``SCRIP_MASTER_MAX_STALE_DAYS`` is
        used and ``scrip_master_is_stale`` is set; otherwise ``BrokerError`` is raised.
        """
        today = self._today_ist()
        if self._scrip_df is not None and not force_refresh:
            if self._scrip_loaded_date is None or self._scrip_loaded_date == today:
                return self._scrip_df

        cache_parquet = self.cache_dir / "scrip_master.parquet"
        cache_date: date | None = None
        if cache_parquet.exists():
            cache_date = datetime.fromtimestamp(cache_parquet.stat().st_mtime, TZ_IST).date()
        use_cache = cache_date == today or (cache_date is not None and not self.scrip_master_daily_refresh)
        if use_cache and not force_refresh:
            self._scrip_df = pd.read_parquet(cache_parquet)
            self._scrip_loaded_date = today
            self.scrip_master_is_stale = False
            return self._scrip_df

        try:
            df = self._download_scrip_master()
        except BrokerError:
            stale_ok = (
                not force_refresh
                and cache_date is not None
                and (today - cache_date).days <= self.SCRIP_MASTER_MAX_STALE_DAYS
            )
            if not stale_ok:
                raise
            logger.warning("Scrip Master download failed; using cache from %s", cache_date)
            self._scrip_df = pd.read_parquet(cache_parquet)
            self._scrip_loaded_date = today  # one attempt per IST day
            self.scrip_master_is_stale = True
            return self._scrip_df

        tmp = cache_parquet.with_name(f"{cache_parquet.name}.{os.getpid()}.tmp")
        try:
            df.to_parquet(tmp, compression="zstd")
            os.replace(tmp, cache_parquet)
        except Exception as exc:  # cache is optional; memory copy is authoritative
            logger.warning("Could not write the Scrip Master cache: %s", type(exc).__name__)
            tmp.unlink(missing_ok=True)
        self._scrip_df = df
        self._scrip_loaded_date = today
        self.scrip_master_is_stale = False
        return df

    @staticmethod
    def _download_scrip_master() -> pd.DataFrame:
        headers = {"Content-Type": "application/json"}
        try:
            resp = requests.get(SCRIP_MASTER_URL, headers=headers, timeout=30)
            if resp.status_code != 200:
                raise BrokerError(f"Scrip Master fetch returned HTTP {resp.status_code}")
            df = pd.read_csv(io.StringIO(resp.text))
        except BrokerError:
            raise
        except Exception as e:
            raise BrokerError(f"Failed to download 5paisa Scrip Master: {type(e).__name__}") from e
        missing = _SCRIP_MASTER_REQUIRED_COLUMNS - set(df.columns)
        if missing or len(df) < 1000:
            raise BrokerError(
                f"Scrip Master looks invalid (rows={len(df)}, missing columns={sorted(missing)})"
            )
        return df

    def lookup_nifty_options(
        self,
        expiry: date | str | None = None,
        strike: float | None = None,
        option_type: str | None = None,
    ) -> pd.DataFrame:
        """Filter active NIFTY options from the Scrip Master."""
        df = self.get_scrip_master()
        mask = (df["Exch"] == "N") & (df["ExchType"] == "D") & (df["SymbolRoot"] == "NIFTY")
        if option_type:
            mask = mask & (df["ScripType"] == option_type.upper())
        if strike is not None:
            mask = mask & (df["StrikeRate"] == strike)
        if expiry:
            exp_str = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
            mask = mask & (df["Expiry"].str.startswith(exp_str))

        filtered = df[mask].copy()
        return filtered

    @staticmethod
    def get_nifty_spot_scrip_code() -> int:
        """Canonical 5paisa ScripCode for NIFTY 50 cash/spot index."""
        return 999920000

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type(BrokerAuthenticationError),  # never re-hammer a 401
        reraise=True,
    )
    def fetch_historical_candles(
        self,
        exch: str = "N",
        exchange_segment: str = "C",
        scrip_code: int = 999920000,
        interval: str = "5m",
        from_date: str = "2024-10-01",
        to_date: str = "2024-10-05",
    ) -> pd.DataFrame:
        """Call official 5paisa /V2/historical/ REST endpoint."""
        if not self.access_token:
            raise BrokerAuthenticationError(
                "Authentication required to fetch 5paisa historical candles."
            )

        d_from = pd.to_datetime(from_date)
        d_to = pd.to_datetime(to_date)
        if interval != "1d" and (d_to - d_from).days > 180:
            raise BrokerError(
                f"5paisa historical candle request window cannot exceed 180 days (6 months) for intraday data. "
                f"Requested {(d_to - d_from).days} days ({from_date} to {to_date})."
            )

        url = f"{HISTORICAL_V2_URL}{exch}/{exchange_segment}/{scrip_code}/{interval}?from={from_date}&end={to_date}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
            "Content-Type": "application/json",
        }
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 401:
            self.record_request_result(False, auth_suspected=True)
            raise BrokerAuthenticationError("5paisa API authorization expired or invalid token.")
        if resp.status_code != 200:
            self.record_request_result(False)
            raise BrokerError(
                f"Historical candles request failed HTTP {resp.status_code}: "
                f"{redact_sensitive_text(resp.text)[:200]}"
            )
        self.record_request_result(True)

        payload = resp.json()
        body = payload.get("data") if isinstance(payload, dict) else None
        candles = body.get("candles", []) if isinstance(body, dict) else []
        if not candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df

    def parse_nifty_bars(self, df: pd.DataFrame, interval: str = "5m") -> list[Bar]:
        """Convert raw 5paisa NIFTY DataFrame to normalized Bar models."""
        bars: list[Bar] = []
        for _, row in df.iterrows():
            ts = pd.to_datetime(row["timestamp"])
            ist_dt = ts.replace(tzinfo=TZ_IST) if ts.tzinfo is None else ts.astimezone(TZ_IST)
            utc_dt = ist_dt.astimezone(TZ_UTC)

            bar = Bar(
                timestamp=utc_dt,
                exchange_time=ist_dt,
                symbol="NIFTY 50",
                instrument_id=str(self.get_nifty_spot_scrip_code()),
                exchange=Exchange.NSE,
                instrument_type=InstrumentType.INDEX,
                interval=Timeframe(interval),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row.get("volume", 0)),
                source=DataSource.FIVEPAISA_HISTORICAL,
            )
            bars.append(bar)
        return bars

    def parse_option_bars(
        self,
        df: pd.DataFrame,
        expiry: date,
        strike: float,
        option_type: str,
        scrip_code: int,
        lot_size: int,
        interval: str = "1m",
    ) -> list[OptionBar]:
        """Convert raw 5paisa Option DataFrame to normalized OptionBar models."""
        bars: list[OptionBar] = []
        for _, row in df.iterrows():
            ts = pd.to_datetime(row["timestamp"])
            ist_dt = ts.replace(tzinfo=TZ_IST) if ts.tzinfo is None else ts.astimezone(TZ_IST)
            utc_dt = ist_dt.astimezone(TZ_UTC)

            bar = OptionBar(
                timestamp=utc_dt,
                exchange_time=ist_dt,
                trading_date=ist_dt.date(),
                underlying_symbol="NIFTY",
                expiry_date=expiry,
                strike_price=strike,
                option_type=option_type.upper(),  # type: ignore[arg-type]
                scrip_code=scrip_code,
                exchange_segment="D",
                lot_size=lot_size,
                tick_size=0.05,
                interval=Timeframe(interval),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row.get("volume", 0)),
                open_interest=int(row["open_interest"])
                if pd.notna(row.get("open_interest"))
                else None,
                data_source=DataSource.FIVEPAISA_HISTORICAL,
            )
            bars.append(bar)
        return bars

    def place_order(self, order: Order) -> Order:
        """Mandatory live block - Phase 2 is strictly read-only."""
        self.assert_live_allowed()
        raise NotImplementedError("Live order placement is strictly blocked in Phase 2.")

    def cancel_order(self, order_id: str) -> bool:
        self.assert_live_allowed()
        return False

    def get_positions(self) -> list[Position]:
        return []

    def get_orders(self) -> list[Order]:
        return []

    def get_margins(self) -> dict[str, Any]:
        # Live account observations are never a funding source for PaperBroker.
        return {
            "account_namespace": "BROKER_READ_ONLY_OBSERVATION",
            "available_cash": None,
            "status": "READ_ONLY_UNAVAILABLE",
        }
