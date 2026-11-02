"""Provider protocol and clock abstraction for the live application.

A provider only *reads* market data.  It never exposes anything that can place,
modify or cancel an order.  The engine is driven by whatever a provider returns,
so replay/mocked providers can exercise the full lifecycle deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from trading_platform.live.models import (
    CompletedCandle,
    ContractSpec,
    OptionQuote,
    RunMode,
    SpotTick,
    WarmupData,
    ist_now,
)


class Clock(Protocol):
    def now(self) -> datetime:  # tz-aware IST
        ...


class SystemClock:
    def now(self) -> datetime:
        return ist_now()


class ManualClock:
    """Deterministic clock for replay/tests."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = value


@dataclass(frozen=True)
class CheckResult:
    """One startup check shown to the user (name, ok, detail).  Never contains secrets."""

    name: str
    ok: bool
    detail: str
    blocking: bool = True


@dataclass(frozen=True)
class PollResult:
    """One data-poll cycle: spot tick + quotes for the requested option scrips."""

    spot: SpotTick | None
    quotes: dict[int, OptionQuote] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)  # redacted, human-readable
    rate_limited: bool = False


@runtime_checkable
class MarketDataProvider(Protocol):
    mode: RunMode

    def startup_checks(self, trading_date: date) -> list[CheckResult]:
        """Login + calendar + connectivity checks (idempotent, no secrets in output)."""

    def previous_session_and_warmup(self, trading_date: date) -> WarmupData:
        """Previous trading-day OHLC + daily/5m history for reference levels."""

    def load_contracts(self, trading_date: date) -> list[ContractSpec]:
        """Active NIFTY option contracts (expiry >= trading_date) with real lot sizes."""

    def poll(self, watch_scrips: list[int]) -> PollResult:
        """Fetch latest spot tick and bid/ask for ``watch_scrips`` (serialized SDK calls)."""

    def completed_candles(self, trading_date: date, as_of: datetime) -> list[CompletedCandle]:
        """Genuine completed 5m candles for today whose close_time <= as_of.

        Must return only candles proven complete.  If the provider cannot supply
        trustworthy completed candles it returns [] (fail closed); it must never
        promote sampled ticks to PROVIDER_COMPLETED.
        """

    def sampled_forming_candle(self) -> CompletedCandle | None:
        """Best-effort *display-only* forming candle built from ticks (source SAMPLED_TICKS)."""

    def close(self) -> None:
        """Release resources (idempotent)."""
