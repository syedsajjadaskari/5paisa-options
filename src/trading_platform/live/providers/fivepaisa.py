"""Live, READ-ONLY 5paisa market-data provider (mode LIVE).

Endpoints used (all read-only; the SDK client is reached only through the allowlist proxy
in ``brokers/fivepaisa.py``):

* ``POST V1/MarketFeed`` via ``fetch_market_feed_scrip`` - NIFTY spot (Exch N / ExchType C /
  ScripCode 999920000).  ``TickDt`` is the provider tick time (1 s resolution).
* ``POST V3/MarketDepth`` via ``fetch_market_depth`` - top-of-book for option scrips (Exch N /
  ExchType D).  The SDK hard-codes ``Count=1`` so it is one HTTP call per contract.  The
  response ``TimeStamp`` is API-server time, NOT a proven exchange time.
* ``GET V2/historical/{Exch}/{Seg}/{Scrip}/{tf}?from=&end=`` via
  ``FivePaisaBroker.fetch_historical_candles`` - warm-up (1d, 5m) and same-day 5m candles.
* ``GET VendorsAPI/.../ScripMaster/segment/All`` (public CSV) via
  ``FivePaisaBroker.get_scrip_master`` - contracts and lot sizes, refreshed once per IST day.
* Login: ``get_totp_session`` (TOTP -> RequestToken -> AccessToken); health probe: the same
  spot quote call.  Nothing else.  No order, account, margin or websocket endpoint is reachable.

Rate limit: 5paisa documents roughly 5 market-data requests/second (docs/broker_capabilities.md;
UNVERIFIED server-side).  The provider keeps at least ``min_call_interval_s`` (0.25 s => <= 4/s)
between SDK calls, re-uses a contract's depth for ``option_depth_interval_s`` and backs off
exponentially when throttling is detected.

Concurrency: the SDK keeps request bodies in module-level shared mutable dicts, so every SDK
call is serialized by one lock (``_sdk_lock``).

Same-day historical candle behaviour is UNVERIFIED (whether V2/historical returns the
current forming bar, its lag, whether the last bar is partial).  Hence ``completed_candles``
is fail-closed: a vendor bar is only returned when it is proven closed AND agrees with the
spot ticks this provider sampled.  Sampled ticks are NEVER promoted to PROVIDER_COMPLETED.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from datetime import time as dtime
from io import StringIO
from typing import Any

import pandas as pd

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.exceptions import BrokerError, TradingPlatformError
from trading_platform.core.models import Bar
from trading_platform.core.redaction import redact_sensitive_text
from trading_platform.data.calendar import NSECalendar
from trading_platform.data.live_market_feed import (
    FeedParseError,
    best_levels,
    extract_depth_item,
    extract_spot_item,
    parse_fivepaisa_timestamp,
)
from trading_platform.live.models import (
    CANDLE_MINUTES,
    IST,
    CandleSource,
    CompletedCandle,
    ContractSpec,
    DailyOhlc,
    OptionQuote,
    RunMode,
    SpotTick,
    WarmupData,
)
from trading_platform.live.providers.base import CheckResult, Clock, PollResult, SystemClock

NIFTY_SPOT_SCRIP = 999920000
SPOT_SOURCE_LABEL = "FIVEPAISA_REST_MARKETFEED_TICKDT"
DEPTH_SOURCE_LABEL = "FIVEPAISA_REST_DEPTH_SERVER_TIME"  # TimeStamp is server time, not exchange time

_SESSION_OPEN = dtime(9, 15)
_LAST_CANDLE_OPEN = dtime(15, 25)  # the 15:25-15:30 candle is the last valid one
_RATE_LIMIT_RE = re.compile(r"(?i)too many|rate.?limit|throttl|\b429\b|exceed")
_AUTH_RE = re.compile(r"(?i)token|unauthori[sz]ed|\b401\b|session|expired|login|jwt|forbidden")
_NIFTY_OPTION_ROOT = "NIFTY"


class ProviderError(TradingPlatformError):
    """The live provider cannot supply data it is required to supply (fail closed)."""


def _strict_bool(value: Any, name: str) -> bool:
    """Only a real bool: ``"false"`` must never silently become True (or the reverse)."""
    if not isinstance(value, bool):
        raise ValueError(f"{name}: expected true/false, got {value!r}")
    return value


def _positive_seconds(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name}: expected a number of seconds, got {value!r}")
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"{name}: must be a finite number > 0, got {value!r}")
    return seconds


@dataclass(frozen=True)
class ProviderConfig:
    """Tunables of the live provider (defaults mirror ``configs/live_paper.yaml``)."""

    min_call_interval_s: float = 0.25
    option_depth_interval_s: float = 1.0
    max_depth_scrips_per_poll: int = 8
    backoff_initial_s: float = 2.0
    backoff_max_s: float = 30.0
    max_future_skew_s: float = 2.0
    spot_fresh_max_age_s: float = 15.0
    candle_safety_delay_s: float = 3.0
    spot_crosscheck_tolerance: float = 15.0
    crosscheck_window_s: float = 20.0
    require_sample_crosscheck: bool = True
    uncrosschecked_min_age_s: float = 60.0
    candle_fetch_min_interval_s: float = 5.0
    historical_end_offset_days: int = 1  # end = today + 1 works whether `end` is inclusive or not
    tick_buffer_size: int = 4000
    min_daily_bars: int = 250
    min_intraday_bars: int = 600
    min_prev_session_bars: int = 70
    daily_lookback_days: int = 430
    intraday_lookback_days: int = 45
    prev_daily_vs_5m_close_tolerance_pct: float = 1.0

    @classmethod
    def from_live_config(
        cls, candles: Mapping[str, Any] | None = None, polling: Mapping[str, Any] | None = None
    ) -> ProviderConfig:
        """Build from the ``candles`` / ``polling`` sections of ``configs/live_paper.yaml``."""
        candles = candles or {}
        polling = polling or {}
        base = cls()
        return replace(
            base,
            candle_safety_delay_s=float(
                candles.get("provider_poll_delay_seconds", base.candle_safety_delay_s)
            ),
            spot_crosscheck_tolerance=float(
                candles.get("spot_crosscheck_tolerance_points", base.spot_crosscheck_tolerance)
            ),
            option_depth_interval_s=float(
                polling.get("option_depth_interval_seconds", base.option_depth_interval_s)
            ),
            require_sample_crosscheck=_strict_bool(
                candles.get("require_sample_crosscheck", base.require_sample_crosscheck),
                "candles.require_sample_crosscheck",
            ),
            uncrosschecked_min_age_s=_positive_seconds(
                candles.get("uncrosschecked_min_age_seconds", base.uncrosschecked_min_age_s),
                "candles.uncrosschecked_min_age_seconds",
            ),
        )


def _floor_5m(value: datetime) -> datetime:
    return value.replace(minute=value.minute - value.minute % CANDLE_MINUTES, second=0, microsecond=0)


def _finite(*values: float) -> bool:
    return all(isinstance(v, int | float) and math.isfinite(v) for v in values)


def _parse_expiry(value: Any) -> date | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    text = str(value).strip()
    m = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", text)
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000.0, IST).date()
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    parsed = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


class FivePaisaLiveProvider:
    """``MarketDataProvider`` backed by live 5paisa REST polling (read-only)."""

    mode = RunMode.LIVE

    def __init__(
        self,
        broker: FivePaisaBroker | None = None,
        config: ProviderConfig | None = None,
        clock: Clock | None = None,
        calendar: NSECalendar | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        self._owns_broker = broker is None
        self._broker = broker or FivePaisaBroker()
        # Refresh the scrip master at most once per IST day (see FivePaisaBroker).
        if hasattr(self._broker, "scrip_master_daily_refresh"):
            self._broker.scrip_master_daily_refresh = True
        self._cfg = config or ProviderConfig()
        self._clock: Clock = clock or SystemClock()
        self._calendar = calendar or NSECalendar()
        self._sleep = sleep_fn or time.sleep
        self._monotonic = monotonic_fn or time.monotonic

        self._sdk_lock = threading.RLock()  # serializes ALL SDK calls (shared mutable payloads)
        self._hist_lock = threading.Lock()  # serializes historical REST calls
        self._last_call_end = -math.inf
        self._backoff_until = 0.0
        self._backoff_s = 0.0
        self._closed = False

        self._last_spot: SpotTick | None = None
        self._ticks: deque[tuple[datetime, float]] = deque(maxlen=self._cfg.tick_buffer_size)
        # ``_ticks`` is appended by the poll (main) thread and iterated by the candle worker
        # thread: every access goes through this lock / ``_ticks_snapshot``.
        self._ticks_lock = threading.Lock()
        self._depth_cache: dict[int, OptionQuote] = {}
        self._depth_fetched_mono: dict[int, float] = {}
        self._symbol_by_scrip: dict[int, str] = {}

        self._accepted: dict[datetime, CompletedCandle] = {}
        self._accepted_date: date | None = None
        self._last_candle_fetch_mono = -math.inf
        self.candle_diagnostics: dict[str, Any] = {"summary": "no candle evaluation yet"}

    # ------------------------------------------------------------------ properties

    @property
    def connection_status(self) -> str:
        """CONNECTED | DEGRADED | DISCONNECTED (from the broker's session health)."""
        return str(getattr(self._broker, "connection_status", "DISCONNECTED"))

    @property
    def last_spot(self) -> SpotTick | None:
        return self._last_spot

    @property
    def candle_diagnostics_text(self) -> str:
        return str(self.candle_diagnostics.get("summary", ""))

    # ------------------------------------------------------------------ SDK plumbing

    def _throttle(self) -> None:
        wait = self._cfg.min_call_interval_s - (self._monotonic() - self._last_call_end)
        if wait > 0:
            self._sleep(wait)

    def _sdk_call(self, fn: Callable[[list[dict[str, Any]]], Any], request: list[dict[str, Any]]):
        """One serialized, throttled SDK call.  Returns ``(response, exception)``."""
        with self._sdk_lock:
            self._throttle()
            try:
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    return fn(request), None
            except Exception as exc:  # the SDK normally swallows; fakes/transport may raise
                return None, exc
            finally:
                self._last_call_end = self._monotonic()

    def _note_rate_limit(self) -> None:
        self._backoff_s = (
            self._cfg.backoff_initial_s
            if self._backoff_s <= 0
            else min(self._backoff_s * 2, self._cfg.backoff_max_s)
        )
        self._backoff_until = self._monotonic() + self._backoff_s

    def _note_success(self) -> None:
        self._backoff_s = 0.0

    def _record(self, ok: bool, auth_suspected: bool = False) -> None:
        recorder = getattr(self._broker, "record_request_result", None)
        if recorder is not None:
            recorder(ok, auth_suspected=auth_suspected)

    @staticmethod
    def _classify(error: FeedParseError | Exception) -> tuple[str, bool, bool]:
        """Return ``(redacted message, rate_limited, auth_suspected)``."""
        if isinstance(error, FeedParseError):
            text = f"{error.category}: {error}"
            auth = error.category == "EMPTY_RESPONSE" or (
                error.category == "API_REJECTED" and bool(_AUTH_RE.search(str(error)))
            )
            limited = error.category == "API_REJECTED" and bool(_RATE_LIMIT_RE.search(str(error)))
        else:
            text = f"SDK_EXCEPTION: {type(error).__name__}: {error}"
            limited = bool(_RATE_LIMIT_RE.search(text))
            auth = bool(_AUTH_RE.search(text))
        return redact_sensitive_text(text)[:240], limited, auth and not limited

    # ------------------------------------------------------------------ polling

    def poll(self, watch_scrips: list[int]) -> PollResult:
        """ONE poll cycle: NIFTY spot + top-of-book for ``watch_scrips`` (requested only)."""
        errors: list[str] = []
        with self._sdk_lock:
            if self._closed:
                return PollResult(None, {}, ["PROVIDER_CLOSED"], False)
            if not self._broker.ensure_session():
                detail = redact_sensitive_text(getattr(self._broker, "connection_detail", ""))
                return PollResult(None, {}, [f"DISCONNECTED: {detail}".strip()], False)
            remaining = self._backoff_until - self._monotonic()
            if remaining > 0:
                return PollResult(None, {}, [f"RATE_LIMITED: backing off {remaining:.1f}s"], True)

            spot, spot_error, limited = self._fetch_spot()
            if spot_error:
                errors.append(spot_error)
            quotes: dict[int, OptionQuote] = {}
            if limited:
                return PollResult(spot, quotes, errors, True)

            wanted = list(dict.fromkeys(int(s) for s in watch_scrips))
            cap = self._cfg.max_depth_scrips_per_poll
            if len(wanted) > cap:
                errors.append(f"WATCHLIST_TRUNCATED: {len(wanted)} requested, first {cap} polled")
                wanted = wanted[:cap]
            for scrip in wanted:
                if not self._broker.is_connected:
                    errors.append("DISCONNECTED: session lost during poll")
                    break
                quote, err, limited = self._fetch_depth(scrip)
                if quote is not None:
                    quotes[scrip] = quote
                if err:
                    errors.append(err)
                if limited:
                    return PollResult(spot, quotes, errors, True)
            return PollResult(spot, quotes, errors, False)

    def _fetch_spot(self) -> tuple[SpotTick | None, str | None, bool]:
        req = [{"Exch": "N", "ExchType": "C", "ScripCode": NIFTY_SPOT_SCRIP}]
        resp, exc = self._sdk_call(self._broker.client.fetch_market_feed_scrip, req)
        received = self._clock.now()
        try:
            if exc is not None:
                raise exc
            item = extract_spot_item(resp, NIFTY_SPOT_SCRIP)
            last_rate = float(item.get("LastRate") or 0.0)
            if not _finite(last_rate) or last_rate <= 0:
                raise FeedParseError("INVALID_PAYLOAD", "spot LastRate missing or non-positive")
            raw_ts = item.get("TickDt")
            if not raw_ts:
                raise FeedParseError("UNSUPPORTED_TIMESTAMP", "spot TickDt missing; tick rejected")
            try:
                exchange_time = parse_fivepaisa_timestamp(str(raw_ts))
            except ValueError as err:
                raise FeedParseError("UNSUPPORTED_TIMESTAMP", "spot TickDt unparseable") from err
            skew = (exchange_time - received).total_seconds()
            if skew > self._cfg.max_future_skew_s:
                raise FeedParseError("FUTURE_QUOTE", f"spot TickDt is {skew:.1f}s in the future")
        except Exception as err:
            message, limited, auth = self._classify(err)
            self._record(False, auth_suspected=auth)
            if limited:
                self._note_rate_limit()
            return None, f"SPOT_{message}", limited

        self._record(True)
        self._note_success()

        def positive(key: str) -> float | None:
            try:
                value = float(item.get(key) or 0.0)
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) and value > 0 else None

        tick = SpotTick(
            price=last_rate,
            exchange_time=exchange_time,
            received_at=received,
            day_high=positive("High"),
            day_low=positive("Low"),
            prev_close=positive("PClose"),
            source=SPOT_SOURCE_LABEL,
        )
        self._last_spot = tick
        with self._ticks_lock:
            if not self._ticks or self._ticks[-1] != (exchange_time, last_rate):
                self._ticks.append((exchange_time, last_rate))
        return tick, None, False

    def _ticks_snapshot(self) -> list[tuple[datetime, float]]:
        """A private copy of the sampled ticks, safe to iterate from any thread."""
        with self._ticks_lock:
            return list(self._ticks)

    def _fetch_depth(self, scrip: int) -> tuple[OptionQuote | None, str | None, bool]:
        cached = self._depth_cache.get(scrip)
        fetched = self._depth_fetched_mono.get(scrip)
        if (
            cached is not None
            and fetched is not None
            and self._monotonic() - fetched < self._cfg.option_depth_interval_s
        ):
            return cached, None, False
        req = [{"Exchange": "N", "ExchangeType": "D", "ScripCode": scrip}]
        resp, exc = self._sdk_call(self._broker.client.fetch_market_depth, req)
        received = self._clock.now()
        try:
            if exc is not None:
                raise exc
            depth = extract_depth_item(resp, scrip)
            raw_ts = depth.get("TimeStamp")
            if not raw_ts:
                raise FeedParseError("UNSUPPORTED_TIMESTAMP", "depth TimeStamp missing")
            try:
                server_time = parse_fivepaisa_timestamp(str(raw_ts))
            except ValueError as err:
                raise FeedParseError("UNSUPPORTED_TIMESTAMP", "depth TimeStamp unparseable") from err
            bid, bid_qty, ask, ask_qty = best_levels(depth)
            if not _finite(bid, ask) or bid < 0 or ask < 0:
                raise FeedParseError("INVALID_PAYLOAD", "depth prices not finite/non-negative")
        except Exception as err:
            message, limited, _auth = self._classify(err)
            if limited:
                self._note_rate_limit()
            return None, f"DEPTH[{scrip}]_{message}", limited

        self._note_success()
        quote = OptionQuote(
            scrip_code=scrip,
            symbol=self._symbol_by_scrip.get(scrip, f"NIFTY_OPT_{scrip}"),
            bid=bid,
            ask=ask,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            server_time=server_time,
            received_at=received,
            source=DEPTH_SOURCE_LABEL,
        )
        self._depth_cache[scrip] = quote
        self._depth_fetched_mono[scrip] = self._monotonic()
        return quote, None, False

    # ------------------------------------------------------------------ startup

    def _previous_trading_day(self, trading_date: date) -> date | None:
        day = trading_date - timedelta(days=1)
        for _ in range(14):
            if self._calendar.is_trading_day(day):
                return day
            day -= timedelta(days=1)
        return None

    def startup_checks(self, trading_date: date) -> list[CheckResult]:
        """Login, token validity, calendar, previous day, scrip master, spot freshness."""
        results: list[CheckResult] = []

        def add(name: str, ok: bool, detail: str, blocking: bool = True) -> None:
            results.append(CheckResult(name, ok, redact_sensitive_text(detail)[:300], blocking))

        logged_in = False
        with self._sdk_lock:
            try:
                if self._broker.is_connected and self._broker.client is not None:
                    logged_in = True
                else:
                    logged_in = bool(self._broker.connect())
            except BrokerError as exc:
                add("login", False, f"login failed: {exc}")
            else:
                category = getattr(self._broker, "connection_category", "")
                detail = getattr(self._broker, "connection_detail", "")
                add("login", logged_in, f"{category}: {detail}")
            if logged_in:
                ok, detail = self._broker.validate_session()
                add("token_valid", ok, detail)
                logged_in = ok
            else:
                add("token_valid", False, "skipped: login failed")

        is_day = self._calendar.is_trading_day(trading_date)
        supported = self._calendar.is_entry_session_supported(trading_date)
        add(
            "calendar",
            is_day and supported,
            f"{trading_date.isoformat()} trading_day={is_day} entry_session_supported={supported}",
        )
        prev = self._previous_trading_day(trading_date)
        add("previous_trading_day", prev is not None, str(prev) if prev else "not found in 14 days")

        try:
            contracts = self.load_contracts(trading_date)
            stale = getattr(self._broker, "scrip_master_is_stale", False)
            expiries = sorted({c.expiry for c in contracts})
            add(
                "scrip_master",
                not stale,
                f"{len(contracts)} NIFTY option contracts, nearest expiry "
                f"{expiries[0] if expiries else 'n/a'}, lot sizes "
                f"{sorted({c.lot_size for c in contracts})}"
                + ("; STALE cache in use" if stale else ""),
            )
        except ProviderError as exc:
            add("scrip_master", False, str(exc))

        if logged_in:
            with self._sdk_lock:
                spot, err, _limited = self._fetch_spot()
            now = self._clock.now()
            in_session = self._calendar.is_regular_trading_minute(now)
            if spot is None:
                add("spot_freshness", False, err or "no spot tick", blocking=in_session)
            else:
                age = (now - spot.exchange_time).total_seconds()
                if in_session:
                    add(
                        "spot_freshness",
                        age <= self._cfg.spot_fresh_max_age_s,
                        f"spot {spot.price:.2f} tick age {age:.1f}s (limit "
                        f"{self._cfg.spot_fresh_max_age_s:.0f}s)",
                    )
                else:
                    add(
                        "spot_freshness",
                        True,
                        f"outside session; last tick {spot.exchange_time.isoformat()} "
                        f"spot {spot.price:.2f}",
                        blocking=False,
                    )
        else:
            add("spot_freshness", False, "skipped: not logged in")
        return results

    # ------------------------------------------------------------------ history

    def _fetch_history(self, interval: str, start: date, end: date) -> pd.DataFrame:
        with self._hist_lock:
            try:
                df = self._broker.fetch_historical_candles(
                    interval=interval, from_date=start.isoformat(), to_date=end.isoformat()
                )
            except Exception as exc:
                raise ProviderError(
                    f"historical {interval} request failed: {type(exc).__name__}: "
                    f"{redact_sensitive_text(exc)[:160]}"
                ) from exc
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    @staticmethod
    def _clean_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Drop rows that could not be a real bar; returns (clean, dropped_count)."""
        if df.empty:
            return df, 0
        work = df.copy()
        for col in ("open", "high", "low", "close"):
            work[col] = pd.to_numeric(work[col], errors="coerce")
        work["timestamp"] = pd.to_datetime(work["timestamp"], errors="coerce")
        mask = (
            work[["open", "high", "low", "close"]].gt(0).all(axis=1)
            & work["timestamp"].notna()
            & (work["high"] >= work[["open", "close", "low"]].max(axis=1))
            & (work["low"] <= work[["open", "close", "high"]].min(axis=1))
        )
        mask &= work[["open", "high", "low", "close"]].map(math.isfinite).all(axis=1)
        clean = work[mask].drop_duplicates(subset="timestamp", keep="last")
        clean = clean.sort_values("timestamp").reset_index(drop=True)
        return clean, len(work) - len(clean)

    def previous_session_and_warmup(self, trading_date: date) -> WarmupData:
        """Previous-session OHLC + >=250 daily bars + >=600 prior-session 5m bars (fail closed)."""
        cfg = self._cfg
        prev_day = self._previous_trading_day(trading_date)
        if prev_day is None:
            raise ProviderError("no previous trading day found within 14 days")
        notes: list[str] = []
        yesterday = trading_date - timedelta(days=1)

        daily_raw = self._fetch_history("1d", trading_date - timedelta(days=cfg.daily_lookback_days), yesterday)
        daily, dropped = self._clean_rows(daily_raw)
        if dropped:
            notes.append(f"dropped {dropped} invalid daily rows")
        if not daily.empty:
            daily = daily[daily["timestamp"].dt.date < trading_date]
        if len(daily) < cfg.min_daily_bars:
            raise ProviderError(
                f"insufficient daily history: {len(daily)} bars < {cfg.min_daily_bars} required"
            )

        intraday_raw = self._fetch_history(
            "5m", trading_date - timedelta(days=cfg.intraday_lookback_days), yesterday
        )
        intraday, dropped = self._clean_rows(intraday_raw)
        if not intraday.empty:
            times = intraday["timestamp"].dt.time
            keep = (
                (intraday["timestamp"].dt.date < trading_date)
                & (times >= _SESSION_OPEN)
                & (times <= _LAST_CANDLE_OPEN)
                & (intraday["timestamp"].dt.minute % CANDLE_MINUTES == 0)
            )
            notes.append(
                f"dropped {int((~keep).sum()) + dropped} 5m rows (15:30 stub, after 15:25, "
                "pre-open, misaligned, invalid)"
            )
            intraday = intraday[keep].reset_index(drop=True)
        if len(intraday) < cfg.min_intraday_bars:
            raise ProviderError(
                f"insufficient prior-session 5m history: {len(intraday)} bars < "
                f"{cfg.min_intraday_bars} required"
            )
        prev_5m = intraday[intraday["timestamp"].dt.date == prev_day]
        if len(prev_5m) < cfg.min_prev_session_bars:
            raise ProviderError(
                f"previous session {prev_day} has only {len(prev_5m)} clean 5m bars "
                f"(< {cfg.min_prev_session_bars}); reference levels untrustworthy"
            )
        agg = DailyOhlc(
            session_date=prev_day,
            open=float(prev_5m.iloc[0]["open"]),
            high=float(prev_5m["high"].max()),
            low=float(prev_5m["low"].min()),
            close=float(prev_5m.iloc[-1]["close"]),
        )
        prev_daily = daily[daily["timestamp"].dt.date == prev_day]
        if prev_daily.empty:
            previous = agg
            notes.append("previous session OHLC derived from 5m bars (no daily bar for that date)")
        else:
            row = prev_daily.iloc[-1]
            previous = DailyOhlc(prev_day, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
            diff_pct = abs(previous.close - agg.close) / agg.close * 100.0
            if diff_pct > cfg.prev_daily_vs_5m_close_tolerance_pct:
                raise ProviderError(
                    f"previous-session daily close {previous.close} disagrees with 5m close "
                    f"{agg.close} by {diff_pct:.2f}%"
                )
            notes.append(f"previous session OHLC from daily bar; close vs 5m differs {diff_pct:.3f}%")

        daily_bars = self._broker.parse_nifty_bars(daily, interval="1d")
        intraday_bars: list[Bar] = self._broker.parse_nifty_bars(intraday, interval="5m")
        notes.append(f"{len(daily_bars)} daily bars, {len(intraday_bars)} prior-session 5m bars")
        return WarmupData(
            previous_session=previous,
            daily_bars=daily_bars,
            intraday_bars=intraday_bars,
            source_notes=notes,
        )

    # ------------------------------------------------------------------ contracts

    def load_contracts(self, trading_date: date) -> list[ContractSpec]:
        """NIFTY index options (CE/PE) with expiry >= date and the master's real lot size."""
        try:
            df = self._broker.get_scrip_master()
        except Exception as exc:
            raise ProviderError(
                f"scrip master unavailable: {type(exc).__name__}: {redact_sensitive_text(exc)[:160]}"
            ) from exc
        if df is None or df.empty:
            raise ProviderError("scrip master is empty")
        mask = (
            (df["Exch"].astype(str).str.strip().str.upper() == "N")
            & (df["ExchType"].astype(str).str.strip().str.upper() == "D")
            & (df["SymbolRoot"].astype(str).str.strip().str.upper() == _NIFTY_OPTION_ROOT)
            & df["ScripType"].astype(str).str.strip().str.upper().isin(["CE", "PE"])
        )
        contracts: dict[int, ContractSpec] = {}
        skipped = 0
        for row in df[mask].to_dict("records"):
            name = str(row.get("FullName") or "").strip() or str(row.get("Name") or "").strip()
            short = str(row.get("Name") or name).strip()
            # SymbolRoot is exactly NIFTY, but guard against BANKNIFTY/FINNIFTY/... in the name.
            first = (short.split() or [""])[0].upper()
            expiry = _parse_expiry(row.get("Expiry"))
            try:
                scrip = int(row["ScripCode"])
                lot = int(float(row.get("LotSize")))
                strike = float(row.get("StrikeRate"))
            except (TypeError, ValueError, KeyError):
                skipped += 1
                continue
            if first != _NIFTY_OPTION_ROOT or expiry is None or expiry < trading_date:
                continue
            if not math.isfinite(strike) or strike <= 0:
                m = re.search(r"(\d+(?:\.\d+)?)\s*$", short)
                strike = float(m.group(1)) if m else 0.0
            if lot <= 0 or strike <= 0 or scrip <= 0:
                skipped += 1
                continue
            contracts[scrip] = ContractSpec(
                scrip_code=scrip,
                symbol=name,
                expiry=expiry,
                strike=strike,
                option_type=str(row["ScripType"]).strip().upper(),
                lot_size=lot,
            )
        if not contracts:
            raise ProviderError(f"no NIFTY option contracts with expiry >= {trading_date} in master")
        ordered = sorted(contracts.values(), key=lambda c: (c.expiry, c.strike, c.option_type))
        self._symbol_by_scrip = {c.scrip_code: c.symbol for c in ordered}
        return ordered

    # ------------------------------------------------------------------ candles

    def _sample_near(
        self, when: datetime, ticks: list[tuple[datetime, float]] | None = None
    ) -> tuple[datetime, float] | None:
        window = timedelta(seconds=self._cfg.crosscheck_window_s)
        best: tuple[datetime, float] | None = None
        for ts, price in self._ticks_snapshot() if ticks is None else ticks:
            if abs(ts - when) <= window and (best is None or abs(ts - when) < abs(best[0] - when)):
                best = (ts, price)
        return best

    def _validate_candle(
        self, row: Mapping[str, Any], trading_date: date, as_of: datetime, spot_time: datetime
    ) -> tuple[CompletedCandle | None, str | None]:
        cfg = self._cfg
        open_time = pd.Timestamp(row["timestamp"]).to_pydatetime()
        open_time = open_time.replace(tzinfo=IST) if open_time.tzinfo is None else open_time.astimezone(IST)
        if open_time.date() != trading_date:
            return None, "NOT_TODAY"
        if open_time.second or open_time.microsecond or open_time.minute % CANDLE_MINUTES:
            return None, "MISALIGNED"
        if open_time.time() >= dtime(15, 30):
            return None, "STUB_1530"
        if open_time.time() < _SESSION_OPEN or open_time.time() > _LAST_CANDLE_OPEN:
            return None, "OUTSIDE_SESSION"
        o, h, low, c = (float(row[k]) for k in ("open", "high", "low", "close"))
        if not _finite(o, h, low, c) or min(o, h, low, c) <= 0 or h < max(o, c, low) or low > min(o, c, h):
            return None, "INVALID_OHLC"
        close_time = open_time + timedelta(minutes=CANDLE_MINUTES)
        if close_time > as_of - timedelta(seconds=cfg.candle_safety_delay_s):
            return None, "NOT_CLOSED_AS_OF"
        if spot_time < close_time:
            return None, "NOT_PROVEN_CLOSED_BY_SPOT"
        tol = cfg.spot_crosscheck_tolerance
        ticks = self._ticks_snapshot()
        near = self._sample_near(close_time, ticks)
        in_bar = [p for ts, p in ticks if open_time <= ts < close_time]
        if near is not None:
            if abs(c - near[1]) > tol:
                return None, "CROSSCHECK_MISMATCH_CLOSE"
            if in_bar and (min(in_bar) < low - tol or max(in_bar) > h + tol):
                return None, "CROSSCHECK_MISMATCH_RANGE"
        else:
            if cfg.require_sample_crosscheck:
                return None, "NO_SAMPLE_UNTRUSTED"
            if spot_time - close_time < timedelta(seconds=cfg.uncrosschecked_min_age_s):
                return None, "NO_SAMPLE_TOO_RECENT"
        try:
            volume = int(float(row.get("volume") or 0))
        except (TypeError, ValueError):
            volume = 0
        return (
            CompletedCandle(open_time, o, h, low, c, max(volume, 0), CandleSource.PROVIDER_COMPLETED),
            None,
        )

    def completed_candles(self, trading_date: date, as_of: datetime) -> list[CompletedCandle]:
        """Vendor 5m candles proven closed; ``[]`` (fail closed) when anything is doubtful.

        See the module docstring: same-day vendor behaviour is UNVERIFIED, so a bar is only
        returned when (i) close_time <= as_of - safety delay and the latest valid spot
        exchange time >= close_time, (ii) it is not the 15:30 stub, and (iii) its close and
        range agree with the sampled spot ticks (or, with ``require_sample_crosscheck`` off,
        it is old enough).  Sampled ticks are never turned into provider candles.
        """
        as_of = as_of.astimezone(IST) if as_of.tzinfo else as_of.replace(tzinfo=IST)
        diag: dict[str, Any] = {"as_of": as_of.isoformat(), "trading_date": trading_date.isoformat()}
        self.candle_diagnostics = diag
        if self._accepted_date != trading_date:
            self._accepted.clear()
            self._accepted_date = trading_date

        def finish(summary: str) -> list[CompletedCandle]:
            diag["accepted_total"] = len(self._accepted)
            diag["summary"] = summary
            return sorted(
                (c for c in self._accepted.values() if c.close_time <= as_of),
                key=lambda c: c.open_time,
            )

        spot = self._last_spot
        if spot is None:
            diag["endpoint_status"] = "NOT_CALLED"
            return finish("NO_SPOT_PROOF: no valid spot tick yet; cannot prove any candle closed")

        latest_closed_open = _floor_5m(as_of - timedelta(seconds=self._cfg.candle_safety_delay_s)) - timedelta(
            minutes=CANDLE_MINUTES
        )
        session_open = datetime.combine(trading_date, _SESSION_OPEN, IST)
        last_open = datetime.combine(trading_date, _LAST_CANDLE_OPEN, IST)
        latest_closed_open = min(latest_closed_open, last_open)
        if latest_closed_open < session_open:
            diag["endpoint_status"] = "NOT_CALLED"
            return finish("BEFORE_FIRST_CLOSE: no 5m candle can have closed yet")
        expected = []
        cursor = session_open
        while cursor <= latest_closed_open:
            expected.append(cursor)
            cursor += timedelta(minutes=CANDLE_MINUTES)
        missing = [t for t in expected if t not in self._accepted]
        diag["expected"] = len(expected)
        if not missing:
            diag["endpoint_status"] = "NOT_CALLED"
            return finish(f"UP_TO_DATE: {len(expected)} candles accepted")
        if self._monotonic() - self._last_candle_fetch_mono < self._cfg.candle_fetch_min_interval_s:
            diag["endpoint_status"] = "THROTTLED"
            return finish(f"THROTTLED: {len(missing)} candle(s) outstanding, fetch rate-limited")

        self._last_candle_fetch_mono = self._monotonic()
        try:
            df = self._fetch_history(
                "5m", trading_date, trading_date + timedelta(days=self._cfg.historical_end_offset_days)
            )
        except ProviderError as exc:
            diag["endpoint_status"] = f"ERROR: {exc}"
            return finish(f"ENDPOINT_ERROR: {exc}")
        if df.empty:
            diag["endpoint_status"] = "EMPTY"
            return finish(
                "ENDPOINT_EMPTY: vendor returned no same-day 5m candles; failing closed "
                "(sampled ticks are NOT promoted)"
            )
        clean, dropped = self._clean_rows(df)
        diag["endpoint_status"] = "OK"
        diag["fetched_rows"] = len(df)
        diag["invalid_rows"] = dropped
        rejected: dict[str, list[str]] = {}
        newly = 0
        for row in clean.to_dict("records"):
            candle, reason = self._validate_candle(row, trading_date, as_of, spot.exchange_time)
            if candle is None:
                label = pd.Timestamp(row["timestamp"]).strftime("%H:%M")
                rejected.setdefault(str(reason), []).append(label)
            elif candle.open_time not in self._accepted:
                self._accepted[candle.open_time] = candle
                newly += 1
        diag["rejected"] = rejected
        diag["newly_accepted"] = newly
        still_missing = [t.strftime("%H:%M") for t in expected if t not in self._accepted]
        diag["missing"] = still_missing
        reasons = ", ".join(f"{k}={len(v)}" for k, v in sorted(rejected.items())) or "none"
        return finish(
            f"fetched {len(df)} rows, accepted {newly} new; rejected: {reasons}; "
            f"missing: {still_missing[:6]}"
        )

    def sampled_forming_candle(self) -> CompletedCandle | None:
        """DISPLAY-ONLY candle from sampled spot ticks (never authoritative)."""
        ticks = self._ticks_snapshot()
        if not ticks:
            return None
        last_time = ticks[-1][0]
        bucket = _floor_5m(last_time)
        if bucket.time() < _SESSION_OPEN or bucket.time() > _LAST_CANDLE_OPEN:
            return None
        close_time = bucket + timedelta(minutes=CANDLE_MINUTES)
        if self._clock.now() >= close_time:
            return None  # feed stalled past the bucket: not a forming candle any more
        prices = [p for ts, p in ticks if bucket <= ts < close_time]
        if not prices:
            return None
        return CompletedCandle(
            open_time=bucket,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=0,  # the index feed carries no volume; do not invent one
            source=CandleSource.SAMPLED_TICKS,
        )

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        """Release resources (idempotent)."""
        with self._sdk_lock:
            if self._closed:
                return
            self._closed = True
            with self._ticks_lock:
                self._ticks.clear()
            self._depth_cache.clear()
            self._depth_fetched_mono.clear()
            if self._owns_broker:
                self._broker.disconnect()


__all__ = [
    "DEPTH_SOURCE_LABEL",
    "SPOT_SOURCE_LABEL",
    "FivePaisaLiveProvider",
    "ProviderConfig",
    "ProviderError",
]
