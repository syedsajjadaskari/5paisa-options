"""Live Market Data Feed, WebSocket/Polling Client, and 5-Minute Candle Aggregator."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.enums import DataSource, Exchange, InstrumentType, Timeframe
from trading_platform.core.models import Bar

logger = logging.getLogger(__name__)

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")
MAX_PROVIDER_FUTURE_SKEW_SECONDS = 2.0


def parse_fivepaisa_timestamp(ts_str: str | None) -> datetime:
    """Parse 5paisa '/Date(1792577624000+0530)/' or ISO string to timezone-aware IST datetime."""
    if not ts_str:
        raise ValueError("Invalid 5paisa timestamp: value is missing")

    m = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", ts_str.strip())
    if m:
        try:
            epoch_seconds = int(m.group(1)) / 1000.0
            return datetime.fromtimestamp(epoch_seconds, TZ_IST)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError(f"Invalid 5paisa timestamp: {ts_str!r}") from exc

    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ_IST)
        return dt.astimezone(TZ_IST)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid 5paisa timestamp: {ts_str!r}") from exc


@dataclass(frozen=True)
class Level2Quote:
    """Level 2 Best Bid/Ask quote snapshot for option contracts."""

    scrip_code: int
    symbol: str
    best_bid: float
    best_ask: float
    bid_qty: int
    ask_qty: int
    last_price: float
    provider_timestamp: datetime  # Official exchange matching engine timestamp
    ingestion_timestamp: datetime  # Local system receipt timestamp
    is_stale: bool = False
    raw_provider_timestamp: str | None = None
    provenance_verified: bool = True
    raw_response_json: str | None = None

    @property
    def spread(self) -> float:
        """Absolute bid/ask spread."""
        return round(self.best_ask - self.best_bid, 2)

    @property
    def spread_pct(self) -> float:
        """Spread as a percentage of ask price."""
        if self.best_ask > 0:
            return round((self.spread / self.best_ask) * 100.0, 2)
        return 0.0

    @property
    def mid_price(self) -> float:
        """Midpoint of best bid and ask."""
        return round((self.best_ask + self.best_bid) / 2.0, 2)

    @property
    def quote_age_seconds(self) -> float:
        """Age of quote measured from exchange match time to local receipt time."""
        return max(0.0, (self.ingestion_timestamp - self.provider_timestamp).total_seconds())


@dataclass
class SpotTick:
    """Underlying NIFTY 50 spot price tick."""

    symbol: str
    scrip_code: int
    last_rate: float
    high: float
    low: float
    prev_close: float
    provider_timestamp: datetime  # Official exchange tick timestamp
    ingestion_timestamp: datetime  # Local system receipt timestamp
    is_stale: bool = False
    raw_provider_timestamp: str | None = None
    provenance_verified: bool = True
    raw_response_json: str | None = None

    @property
    def tick_age_seconds(self) -> float:
        """Age of spot tick from exchange broadcast to local receipt."""
        return max(0.0, (self.ingestion_timestamp - self.provider_timestamp).total_seconds())


class FiveMinuteCandleAggregator:
    """Aggregates continuous spot ticks into standard 5-minute NSE candles.

    NSE Trading Hours:
    09:15 to 15:30 IST.
    Candle boundaries: 09:15-09:20, 09:20-09:25, ..., 15:25-15:30.
    A bar is officially closed ONLY when a tick arrives at or after the boundary time.
    """

    def __init__(self) -> None:
        self.current_bar_open_time: datetime | None = None
        self.current_bar_close_time: datetime | None = None
        self.open: float | None = None
        self.high: float | None = None
        self.low: float | None = None
        self.close: float | None = None
        self.tick_count: int = 0
        self.closed_bars: list[Bar] = []

    def get_candle_boundary(self, tick_dt: datetime) -> tuple[datetime, datetime]:
        """Compute the containing 5-minute bar boundaries (open and close time) in IST."""
        dt_ist = tick_dt.astimezone(TZ_IST)
        minute = dt_ist.minute
        bucket_minute = (minute // 5) * 5
        bar_open = dt_ist.replace(minute=bucket_minute, second=0, microsecond=0)
        bar_close = bar_open + timedelta(minutes=5)
        return bar_open, bar_close

    def process_tick(self, tick: SpotTick) -> Bar | None:
        """Process incoming tick, update running OHLC, and return finalized Bar on boundary cross."""
        price = tick.last_rate
        tick_time = tick.provider_timestamp.astimezone(TZ_IST)

        bar_open, bar_close = self.get_candle_boundary(tick_time)

        # Initial bar initialization
        if self.current_bar_open_time is None:
            self.current_bar_open_time = bar_open
            self.current_bar_close_time = bar_close
            self.open = price
            self.high = price
            self.low = price
            self.close = price
            self.tick_count = 1
            return None

        # Check if tick belongs to a new 5-minute candle
        if tick_time >= self.current_bar_close_time or bar_open > self.current_bar_open_time:
            # Finalize previous bar
            completed_bar = Bar(
                timestamp=self.current_bar_open_time.astimezone(TZ_UTC),
                exchange_time=self.current_bar_open_time,
                symbol=tick.symbol,
                instrument_id=str(tick.scrip_code),
                exchange=Exchange.NSE,
                instrument_type=InstrumentType.INDEX,
                interval=Timeframe.M5,
                open=self.open if self.open is not None else price,
                high=self.high if self.high is not None else price,
                low=self.low if self.low is not None else price,
                close=self.close if self.close is not None else price,
                volume=self.tick_count,
                # This is an OHLC reconstructed from sampled live spot ticks,
                # not an authoritative provider candle.
                source=DataSource.FIVEPAISA_WS,
            )
            self.closed_bars.append(completed_bar)

            # Start new candle
            self.current_bar_open_time = bar_open
            self.current_bar_close_time = bar_close
            self.open = price
            self.high = price
            self.low = price
            self.close = price
            self.tick_count = 1
            return completed_bar

        # Same candle update
        self.high = max(self.high if self.high is not None else price, price)
        self.low = min(self.low if self.low is not None else price, price)
        self.close = price
        self.tick_count += 1
        return None

    def force_close_current_bar(self) -> Bar | None:
        """Force finalize current candle at EOD or session shutdown."""
        if self.current_bar_open_time is None or self.open is None:
            return None

        bar = Bar(
            timestamp=self.current_bar_open_time.astimezone(TZ_UTC),
            exchange_time=self.current_bar_open_time,
            symbol="NIFTY 50",
            instrument_id="999920000",
            exchange=Exchange.NSE,
            instrument_type=InstrumentType.INDEX,
            interval=Timeframe.M5,
            open=self.open,
            high=self.high or self.open,
            low=self.low or self.open,
            close=self.close or self.open,
            volume=self.tick_count,
            # Force-close is still sampled-tick OHLC and is not breakout-safe.
            source=DataSource.FIVEPAISA_WS,
        )
        self.closed_bars.append(bar)
        self.current_bar_open_time = None
        self.open = None
        return bar


class LiveMarketFeed:
    """Manages authenticated live polling from 5paisa with reconnection & staleness protection."""

    def __init__(
        self,
        broker: FivePaisaBroker | None = None,
        poll_interval: float = 1.0,
        max_staleness_seconds: float = 5.0,
    ) -> None:
        self.broker = broker or FivePaisaBroker()
        self.poll_interval = poll_interval
        self.max_staleness_seconds = max_staleness_seconds
        self.aggregator = FiveMinuteCandleAggregator()

        self.is_running: bool = False
        self.last_spot_tick: SpotTick | None = None
        self.last_poll_time: datetime | None = None
        self.last_data_receipt_time: datetime | None = None
        self.last_exchange_time: datetime | None = None
        self.last_error: str | None = None
        self.connection_errors_count: int = 0
        self.feed_interruptions_count: int = 0
        self.is_connected: bool = False
        self.last_error_category: str | None = None
        self.last_spot_raw_response: dict | None = None
        self.last_option_raw_responses: dict[int, dict] = {}
        self.last_option_quotes: dict[int, Level2Quote] = {}

    def connect(self) -> bool:
        """Connect to broker market data stream."""
        try:
            self.is_connected = self.broker.connect()
            if self.is_connected:
                self.connection_errors_count = 0
                self.last_error = None
                self.last_error_category = None
            else:
                self.feed_interruptions_count += 1
                self.last_error_category = getattr(
                    self.broker, "connection_category", "AUTH_FAILED"
                )
                self.last_error = getattr(
                    self.broker,
                    "connection_detail",
                    "Broker connection refused or credentials missing",
                )
            return self.is_connected
        except Exception as e:
            self.last_error_category = "NETWORK_FAILED"
            self.last_error = f"Market feed connection failed: {type(e).__name__}"
            logger.error(self.last_error)
            self.is_connected = False
            self.feed_interruptions_count += 1
            return False

    def fetch_spot_tick(self) -> SpotTick | None:
        """Poll latest NIFTY 50 index spot quote with strict exchange timestamp validation."""
        if not self.is_connected and not self.connect():
            self.feed_interruptions_count += 1
            return None

        ingestion_ts = datetime.now(TZ_IST)
        scrip_code = self.broker.get_nifty_spot_scrip_code()
        logger.info(f"[TRANSITION] MARKET_DATA_REQUEST: Polling NIFTY 50 spot (scrip={scrip_code})")
        try:
            req = [{"Exch": "N", "ExchType": "C", "ScripCode": scrip_code}]
            resp = self.broker.client.fetch_market_feed_scrip(req)
            self.last_spot_raw_response = resp if isinstance(resp, dict) else None
            logger.info(f"[TRANSITION] API_RESPONSE: Status={resp.get('Status') if resp else 'None'}")

            if not resp or resp.get("Status") != 0:
                self.connection_errors_count += 1
                self.feed_interruptions_count += 1
                err_msg = resp.get("Message") if resp else "Empty response"
                self.last_error_category = "API_REJECTED" if resp else "INVALID_PAYLOAD"
                self.last_error = f"API error: {err_msg}"
                return None

            data = resp.get("Data", [])
            if not data:
                self.last_error_category = "INVALID_PAYLOAD"
                self.last_error = "API returned empty Data list"
                return None

            item = data[0]
            response_scrip = item.get("ScripCode") or item.get("Scripcode")
            if response_scrip is not None and int(response_scrip) != scrip_code:
                self.last_error_category = "INVALID_PAYLOAD"
                self.last_error = f"Unexpected spot scrip code: {response_scrip}"
                self.feed_interruptions_count += 1
                return None
            if item.get("Exch") not in (None, "N") or item.get("ExchType") not in (None, "C"):
                self.last_error_category = "INVALID_PAYLOAD"
                self.last_error = "Unexpected exchange segment for NIFTY spot"
                self.feed_interruptions_count += 1
                return None
            if item.get("Symbol") not in (None, "NIFTY", "NIFTY 50"):
                self.last_error_category = "INVALID_PAYLOAD"
                self.last_error = f"Unexpected spot symbol: {item.get('Symbol')}"
                self.feed_interruptions_count += 1
                return None
            raw_ts = item.get("TickDt") or item.get("TimeStamp")
            last_rate = float(item.get("LastRate", 0.0))
            logger.info(f"[TRANSITION] QUOTE_PARSED: Rate={last_rate:,.2f}, RawTS={raw_ts}")

            if not raw_ts:
                self.last_error_category = "UNSUPPORTED_TIMESTAMP"
                self.last_error = "Provider timestamp missing; quote rejected"
                self.feed_interruptions_count += 1
                return None

            provider_ts = parse_fivepaisa_timestamp(str(raw_ts))
            age_seconds = (ingestion_ts - provider_ts).total_seconds()
            if age_seconds < -MAX_PROVIDER_FUTURE_SKEW_SECONDS:
                self.last_error_category = "FUTURE_QUOTE"
                self.last_error = "Provider timestamp is in the future; quote rejected"
                self.feed_interruptions_count += 1
                return None
            is_stale = age_seconds > self.max_staleness_seconds

            logger.info(
                f"[TRANSITION] TIMESTAMP_VALIDATED: ProviderTS={provider_ts.isoformat()}, "
                f"Age={(ingestion_ts - provider_ts).total_seconds():.2f}s, Stale={is_stale}"
            )

            tick = SpotTick(
                symbol="NIFTY 50",
                scrip_code=scrip_code,
                last_rate=last_rate,
                high=float(item.get("High", 0.0)),
                low=float(item.get("Low", 0.0)),
                prev_close=float(item.get("PClose", 0.0)),
                provider_timestamp=provider_ts,
                ingestion_timestamp=ingestion_ts,
                is_stale=is_stale,
                raw_provider_timestamp=str(raw_ts),
                provenance_verified=True,
                raw_response_json=json.dumps(resp, sort_keys=True, default=str),
            )
            self.last_spot_tick = tick
            self.last_poll_time = ingestion_ts
            self.last_data_receipt_time = ingestion_ts
            self.last_exchange_time = provider_ts
            self.connection_errors_count = 0
            self.is_connected = True
            self.last_error = None
            self.last_error_category = None if not is_stale else "STALE_QUOTE"
            logger.info(
                f"[TRANSITION] MARKET_DATA_ACCEPTED: Spot={last_rate:,.2f} at {provider_ts.strftime('%H:%M:%S')} ready for engine"
            )
            return tick

        except Exception as e:
            self.last_error_category = "INVALID_PAYLOAD"
            self.last_error = f"Error fetching spot tick: {type(e).__name__}"
            logger.error(self.last_error)
            self.connection_errors_count += 1
            self.feed_interruptions_count += 1
            return None

    def fetch_option_depth(self, scrip_code: int, symbol: str = "") -> Level2Quote | None:
        """Fetch 5-level market depth with independent exchange timestamp freshness checks."""
        if not self.is_connected and not self.connect():
            self.feed_interruptions_count += 1
            return None

        ingestion_ts = datetime.now(TZ_IST)
        try:
            req = [{"Exchange": "N", "ExchangeType": "D", "ScripCode": scrip_code}]
            resp = self.broker.client.fetch_market_depth(req)
            if isinstance(resp, dict):
                self.last_option_raw_responses[scrip_code] = resp

            if not resp or resp.get("Status") != 0:
                self.last_error_category = "API_REJECTED" if resp else "INVALID_PAYLOAD"
                return None

            data = resp.get("MarketDepthData", [])
            if not data:
                self.last_error_category = "INVALID_PAYLOAD"
                return None

            depth = data[0]
            response_scrip = depth.get("ScripCode") or depth.get("Scripcode")
            if response_scrip is not None and int(response_scrip) != scrip_code:
                logger.warning(
                    "Option depth rejected: response scrip %s != requested %s",
                    response_scrip,
                    scrip_code,
                )
                self.last_error_category = "INVALID_PAYLOAD"
                return None
            if depth.get("Exch") not in (None, "N") or depth.get("ExchType") not in (None, "D"):
                logger.warning("Option depth rejected: unexpected exchange segment for %s", scrip_code)
                self.last_error_category = "INVALID_PAYLOAD"
                return None
            raw_ts = depth.get("TimeStamp")
            if not raw_ts:
                logger.warning("Option depth rejected: provider timestamp missing for %s", scrip_code)
                self.last_error_category = "UNSUPPORTED_TIMESTAMP"
                return None
            provider_ts = parse_fivepaisa_timestamp(str(raw_ts))
            age_seconds = (ingestion_ts - provider_ts).total_seconds()
            if age_seconds < -MAX_PROVIDER_FUTURE_SKEW_SECONDS:
                logger.warning("Option depth rejected: provider timestamp in future for %s", scrip_code)
                self.last_error_category = "FUTURE_QUOTE"
                return None
            is_stale = age_seconds > self.max_staleness_seconds

            asks = depth.get("Asks", [])
            bids = depth.get("Bids", [])

            best_ask = float(asks[0]["Price"]) if asks and len(asks) > 0 else 0.0
            ask_qty = int(asks[0]["Quantity"]) if asks and len(asks) > 0 else 0
            best_bid = float(bids[0]["Price"]) if bids and len(bids) > 0 else 0.0
            bid_qty = int(bids[0]["Quantity"]) if bids and len(bids) > 0 else 0

            last_price = best_ask if best_ask > 0 else best_bid

            quote = Level2Quote(
                scrip_code=scrip_code,
                symbol=symbol or f"NIFTY_OPT_{scrip_code}",
                best_bid=best_bid,
                best_ask=best_ask,
                bid_qty=bid_qty,
                ask_qty=ask_qty,
                last_price=last_price,
                provider_timestamp=provider_ts,
                ingestion_timestamp=ingestion_ts,
                is_stale=is_stale,
                raw_provider_timestamp=str(raw_ts),
                provenance_verified=True,
                raw_response_json=json.dumps(resp, sort_keys=True, default=str),
            )
            self.last_option_quotes[scrip_code] = quote
            return quote

        except Exception as e:
            self.last_error_category = "INVALID_PAYLOAD"
            logger.error("Error fetching option depth for scrip %s: %s", scrip_code, type(e).__name__)
            self.feed_interruptions_count += 1
            return None

    def validate_spot_and_option_freshness(
        self,
        spot_tick: SpotTick,
        option_quote: Level2Quote,
    ) -> tuple[bool, str]:
        """Validate NIFTY underlying spot and option quote freshness independently."""
        if spot_tick.is_stale:
            return (
                False,
                f"STALE_SPOT_TICK: Underlying spot age {spot_tick.tick_age_seconds:.1f}s > {self.max_staleness_seconds}s",
            )

        if option_quote.is_stale:
            return (
                False,
                f"STALE_OPTION_QUOTE: Option quote age {option_quote.quote_age_seconds:.1f}s > {self.max_staleness_seconds}s",
            )

        time_skew = abs((spot_tick.provider_timestamp - option_quote.provider_timestamp).total_seconds())
        if time_skew > self.max_staleness_seconds:
            return (
                False,
                f"TIMESTAMP_DESYNC: Spot ({spot_tick.provider_timestamp.strftime('%H:%M:%S')}) and Option ({option_quote.provider_timestamp.strftime('%H:%M:%S')}) skew {time_skew:.1f}s > limit",
            )

        return True, "FRESH"

    def is_market_session_open(self, check_time: datetime | None = None) -> bool:
        """Validate if current time is within official NSE trading hours and trading days."""
        dt = (check_time or datetime.now(TZ_IST)).astimezone(TZ_IST)
        from trading_platform.data.calendar import NSECalendar

        return NSECalendar().is_regular_trading_minute(dt)
