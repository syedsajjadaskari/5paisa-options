"""5paisa Broker Read-Only Adapter and Market Data Client with safety safeguards."""

import os
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

from trading_platform.brokers.base import BaseBroker
from trading_platform.core.enums import (
    DataSource,
    Exchange,
    InstrumentType,
    OperationalMode,
    Timeframe,
)
from trading_platform.core.exceptions import BrokerError, LiveTradingPermissionError
from trading_platform.core.models import Bar, Order, Position
from trading_platform.data.option_chain import OptionBar

try:
    from py5paisa import FivePaisaClient

    _HAS_PY5PAISA = True
except ImportError:
    _HAS_PY5PAISA = False

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")

# Library and dashboard callers may instantiate the broker directly instead of
# going through the CLI, so credentials must be loaded consistently. Values
# are never logged or exposed by this module.
load_dotenv()

SCRIP_MASTER_URL = "https://Openapi.5paisa.com/VendorsAPI/Service1.svc/ScripMaster/segment/All"
HISTORICAL_V2_URL = "https://openapi.5paisa.com/V2/historical/"
SUBSCRIPTION_KEY = "c89fab8d895a426d9e00db380b433027"


class _ReadOnlyFivePaisaClientProxy:
    """Expose market-data SDK methods while blocking every transactional transport."""

    _BLOCKED_METHODS = {
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

    def __init__(self, wrapped: Any) -> None:
        object.__setattr__(self, "_wrapped", wrapped)

    @staticmethod
    def _blocked(*args: Any, **kwargs: Any) -> None:
        raise LiveTradingPermissionError(
            "5paisa SDK transactional endpoint is blocked by the read-only client boundary."
        )

    def __getattr__(self, name: str) -> Any:
        if name in self._BLOCKED_METHODS:
            return self._blocked
        return getattr(object.__getattribute__(self, "_wrapped"), name)


class FivePaisaBroker(BaseBroker):
    """5paisa Market Data & Read-Only Broker Adapter."""

    def __init__(
        self,
        mode: OperationalMode = OperationalMode.PAPER,
        live_approved: bool = False,
        client_code: str | None = None,
        user_key: str | None = None,
        encryption_key: str | None = None,
        cache_dir: str | Path = "data/reference",
    ) -> None:
        super().__init__(mode=mode, live_approved=live_approved)
        self.client_code = client_code or os.getenv("FIVEPAISA_USER_ID", "")
        self.user_key = user_key or os.getenv("FIVEPAISA_USER_KEY", "")
        self.encryption_key = encryption_key or os.getenv("FIVEPAISA_ENCRYPTION_KEY", "")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client: Any = None
        self._scrip_df: pd.DataFrame | None = None
        self.access_token: str | None = None
        self.connection_category: str = "NOT_ATTEMPTED"
        self.connection_detail: str = ""

    @property
    def client(self) -> Any:
        return self._client

    @client.setter
    def client(self, value: Any) -> None:
        self._client = None if value is None else _ReadOnlyFivePaisaClientProxy(value)

    def connect(self) -> bool:
        """Initialize 5paisa client session."""
        if not self.client_code or not self.user_key:
            self.is_connected = False
            self.connection_category = "AUTH_FAILED"
            self.connection_detail = "Required credentials are unavailable"
            return False

        if not _HAS_PY5PAISA:
            self.connection_category = "SDK_UNAVAILABLE"
            self.connection_detail = "py5paisa is not installed"
            raise BrokerError("py5paisa library is not installed in the current environment.")

        try:
            user_id = os.getenv("FIVEPAISA_USER_ID", self.client_code)
            cred = {
                "APP_NAME": os.getenv("FIVEPAISA_APP_NAME", "APP"),
                "APP_SOURCE": os.getenv("FIVEPAISA_APP_SOURCE", "SOURCE"),
                "USER_ID": user_id,
                "PASSWORD": os.getenv("FIVEPAISA_PASSWORD", ""),
                "USER_KEY": self.user_key,
                "ENCRYPTION_KEY": self.encryption_key,
            }
            self.client = FivePaisaClient(cred=cred)

            # Check cached session token
            token_cache = Path("data/cache/.token_cache.json")
            if token_cache.exists():
                try:
                    import json
                    import time

                    with open(token_cache) as f:
                        data = json.load(f)
                    if time.time() - data.get("created_at", 0) < 20 * 3600 and data.get(
                        "access_token"
                    ):
                        self.access_token = data["access_token"]
                        self.client.set_access_token(
                            self.access_token, data.get("client_code", user_id)
                        )
                        self.is_connected = True
                        self.connection_category = "AUTHENTICATED_CACHED_TOKEN"
                        self.connection_detail = "Cached token loaded; read-only API validation pending"
                        return True
                except Exception as exc:
                    self.connection_category = "AUTH_FAILED"
                    self.connection_detail = f"Cached token rejected: {type(exc).__name__}"

            totp = os.getenv("FIVEPAISA_TOTP_SECRET", "").strip()
            if totp and hasattr(self.client, "get_totp_session"):
                pin = os.getenv("FIVEPAISA_PIN", "").strip()
                account_client_code = os.getenv("FIVEPAISA_CLIENT_CODE", self.client_code).strip()
                totp_code = totp
                try:
                    import pyotp

                    if len(totp) > 6:
                        totp_code = pyotp.TOTP(totp).now()
                except Exception:
                    totp_code = totp

                try:
                    self.client.get_totp_session(account_client_code, totp_code, pin)
                    self.access_token = getattr(self.client, "access_token", None)
                    if self.access_token:
                        import json
                        import time

                        token_cache.parent.mkdir(parents=True, exist_ok=True)
                        with open(token_cache, "w") as f:
                            json.dump(
                                {
                                    "access_token": self.access_token,
                                    "client_code": account_client_code,
                                    "created_at": time.time(),
                                },
                                f,
                            )
                        self.connection_category = "AUTHENTICATED_TOTP"
                        self.connection_detail = "Fresh TOTP session established"
                except Exception as exc:
                    self.connection_category = "AUTH_FAILED"
                    self.connection_detail = f"TOTP authentication failed: {type(exc).__name__}"
            self.is_connected = bool(self.access_token)
            if not self.is_connected:
                self.connection_category = "AUTH_FAILED"
                if not self.connection_detail:
                    self.connection_detail = "No usable access token"
            return self.is_connected
        except Exception as e:
            self.is_connected = False
            self.connection_category = "SDK_OR_NETWORK_FAILED"
            self.connection_detail = f"{type(e).__name__}: {str(e)[:160]}"
            raise BrokerError(f"Failed to connect to 5paisa: {e}") from e

    def disconnect(self) -> None:
        self.is_connected = False
        self.client = None
        self.access_token = None
        self.connection_category = "DISCONNECTED"
        self.connection_detail = "Client disconnected"

    def get_scrip_master(self, force_refresh: bool = False) -> pd.DataFrame:
        """Download or read cached active Scrip Master (~30MB CSV) from 5paisa."""
        if self._scrip_df is not None and not force_refresh:
            return self._scrip_df

        cache_parquet = self.cache_dir / "scrip_master.parquet"
        if cache_parquet.exists() and not force_refresh:
            self._scrip_df = pd.read_parquet(cache_parquet)
            return self._scrip_df

        # Fetch live from official 5paisa URL
        headers = {"Content-Type": "application/json"}
        try:
            resp = requests.get(SCRIP_MASTER_URL, headers=headers, timeout=30)
            if resp.status_code != 200:
                raise BrokerError(f"Scrip Master fetch returned HTTP {resp.status_code}")
            import io

            df = pd.read_csv(io.StringIO(resp.text))
            df.to_parquet(cache_parquet, compression="zstd")
            self._scrip_df = df
            return df
        except Exception as e:
            raise BrokerError(f"Failed to download 5paisa Scrip Master: {e}") from e

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
        stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True
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
            raise BrokerError("Authentication required to fetch 5paisa historical candles.")

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
            raise BrokerError("5paisa API authorization expired or invalid token.")
        if resp.status_code != 200:
            raise BrokerError(
                f"Historical candles request failed HTTP {resp.status_code}: {resp.text}"
            )

        data = resp.json()
        candles = data.get("data", {}).get("candles", [])
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
        lot_size: int = 25,
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
