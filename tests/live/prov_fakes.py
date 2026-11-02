"""Shared fakes for the live-provider tests.  No network, no real SDK client, no secrets."""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.live.providers.base import ManualClock

IST = ZoneInfo("Asia/Kolkata")
TEST_DAY = date(2026, 10, 28)  # a Wednesday, regular NSE session
FAKE_JWT = "eyJ" + "a" * 30 + "." + "b" * 30 + "." + "c" * 20
TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # public RFC-style example secret, not a real account


def at(hh: int, mm: int, ss: int = 0, day: date = TEST_DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=IST)


def tickdt(when: datetime) -> str:
    return f"/Date({int(when.timestamp() * 1000)}+0530)/"


def scrub_env(monkeypatch: Any) -> None:
    """Make sure no real credential from a developer's .env can reach a test."""
    for name in (
        "FIVEPAISA_USER_ID",
        "FIVEPAISA_USER_KEY",
        "FIVEPAISA_ENCRYPTION_KEY",
        "FIVEPAISA_PASSWORD",
        "FIVEPAISA_APP_NAME",
        "FIVEPAISA_APP_SOURCE",
    ):
        monkeypatch.setenv(name, f"test-{name.lower()}")
    monkeypatch.setenv("FIVEPAISA_TOTP_SECRET", TEST_TOTP_SECRET)
    monkeypatch.setenv("FIVEPAISA_PIN", "000000")
    monkeypatch.setenv("FIVEPAISA_CLIENT_CODE", "TESTCLIENT")


def spot_response(price: float, when: datetime, **overrides: Any) -> dict[str, Any]:
    item = {
        "Exch": "N",
        "ExchType": "C",
        "Symbol": "NIFTY",
        "Token": 999920000,
        "LastRate": price,
        "High": price + 20,
        "Low": price - 20,
        "PClose": price - 5,
        "TickDt": tickdt(when),
        "TotalQty": 0,
    }
    item.update(overrides)
    return {"Status": 0, "Message": "", "Data": [item]}


def depth_response(
    scrip: int, bid: float, ask: float, when: datetime, **overrides: Any
) -> dict[str, Any]:
    item = {
        "ScripCode": scrip,
        "Exch": "N",
        "ExchType": "D",
        "TimeStamp": tickdt(when),
        "Bids": [{"Price": bid, "Quantity": 650, "NumberOfOrders": 3}] * 5,
        "Asks": [{"Price": ask, "Quantity": 130, "NumberOfOrders": 2}] * 5,
    }
    item.update(overrides)
    return {"Status": 0, "MarketDepthData": [item]}


class FakeSdkClient:
    """Stand-in for ``py5paisa.FivePaisaClient`` exposing only what the app may reach."""

    def __init__(self) -> None:
        self.access_token = ""
        self.client_code = ""
        self.session = object()  # must never be reachable through the proxy
        self.calls: list[tuple[str, Any]] = []
        self.spot: Any = None  # response, callable(req) -> response, or Exception
        self.depth: Any = None
        self.totp_results: list[str | None] = ["fresh-access-token-123456"]
        self.totp_calls: list[tuple[str, str, str]] = []
        self.accept_tokens: set[str] | None = None  # None: accept any token for the probe
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def _enter(self) -> None:
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)

    def _leave(self) -> None:
        with self._lock:
            self.in_flight -= 1

    def _answer(self, source: Any, req: Any) -> Any:
        value = source(req) if callable(source) else source
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_market_feed_scrip(self, req: list[dict[str, Any]]) -> Any:
        self._enter()
        try:
            self.calls.append(("spot", req))
            if self.spot is None and self.accept_tokens is not None:
                if self.access_token not in self.accept_tokens:
                    return {"Status": 1, "Message": "Invalid token / session expired"}
                return spot_response(22500.0, datetime.now(IST))
            return self._answer(self.spot, req)
        finally:
            self._leave()

    def fetch_market_depth(self, req: list[dict[str, Any]]) -> Any:
        self._enter()
        try:
            self.calls.append(("depth", req))
            return self._answer(self.depth, req)
        finally:
            self._leave()

    def set_access_token(self, token: str, client_code: str) -> None:
        self.access_token = token
        self.client_code = client_code

    def get_totp_session(self, client_code: str, totp: str, pin: str) -> None:
        self.totp_calls.append((client_code, totp, pin))
        result = self.totp_results.pop(0) if self.totp_results else None
        if result is not None:
            self.access_token = result
            self.client_code = client_code

    def spot_calls(self) -> int:
        return sum(1 for kind, _ in self.calls if kind == "spot")

    def depth_requests(self) -> list[int]:
        return [req[0]["ScripCode"] for kind, req in self.calls if kind == "depth"]


class StubBroker(FivePaisaBroker):
    """Real broker logic with canned history / scrip master and a fake SDK client."""

    def __init__(self, tmp_path: Path, client: FakeSdkClient | None = None) -> None:
        super().__init__(
            client_code="TESTCLIENT",
            user_key="test-key",
            encryption_key="test-enc",
            cache_dir=tmp_path,
            token_cache_path=tmp_path / "tok.json",
        )
        self.fake = client or FakeSdkClient()
        self.client = self.fake
        self.is_connected = True
        self.access_token = "stub-access-token-123456"
        self.history: dict[str, Any] = {}
        self.history_calls: list[tuple[str, str, str]] = []
        self.master: pd.DataFrame | None = None
        self.always_connected = True

    def ensure_session(self) -> bool:
        return True if self.always_connected else super().ensure_session()

    def fetch_historical_candles(  # type: ignore[override]
        self,
        exch: str = "N",
        exchange_segment: str = "C",
        scrip_code: int = 999920000,
        interval: str = "5m",
        from_date: str = "",
        to_date: str = "",
    ) -> pd.DataFrame:
        self.history_calls.append((interval, from_date, to_date))
        value = self.history.get(interval)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value(from_date, to_date)
        if value is None:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        return value.copy()

    def get_scrip_master(self, force_refresh: bool = False) -> pd.DataFrame:
        if self.master is None:
            raise RuntimeError("no master configured")
        return self.master


def make_clock(when: datetime) -> ManualClock:
    return ManualClock(when)


def bars_frame(rows: list[tuple[datetime, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": ts.replace(tzinfo=None),
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": 0,
            }
            for ts, o, h, low, c in rows
        ]
    )


def five_min_session(day: date, base: float, bars: int = 75, drift: float = 0.5):
    """Rows for one clean 5m session (09:15 .. 15:25 open labels)."""
    rows = []
    price = base
    start = datetime(day.year, day.month, day.day, 9, 15)
    for i in range(bars):
        ts = start + timedelta(minutes=5 * i)
        o = price
        c = price + drift
        rows.append((ts, o, max(o, c) + 3, min(o, c) - 3, c))
        price = c
    return rows
