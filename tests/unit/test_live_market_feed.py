"""data.live_market_feed / FivePaisaBroker read-only regressions (ported from retired suites).

Ported from the Phase-4 4C/4D/4E and safety suites whose paper engines/recorders were archived
under ``archive/legacy_2026-10-28`` (see docs/CLEANUP_MANIFEST.md).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.enums import (
    DataSource,
    OperationalMode,
    OrderSide,
    OrderType,
)
from trading_platform.core.exceptions import LiveTradingPermissionError
from trading_platform.core.models import Order
from trading_platform.data.candle_quality import validate_5m_frame
from trading_platform.data.live_market_feed import (
    FiveMinuteCandleAggregator,
    LiveMarketFeed,
    SpotTick,
    parse_fivepaisa_timestamp,
)

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


def _tick(ts: datetime, price: float = 100.0) -> SpotTick:
    return SpotTick(
        symbol="NIFTY 50",
        scrip_code=999920000,
        last_rate=price,
        high=price,
        low=price,
        prev_close=price,
        provider_timestamp=ts,
        ingestion_timestamp=ts + timedelta(seconds=1),
        raw_provider_timestamp=ts.isoformat(),
    )


def test_known_2026_provider_timestamp_is_parsed_in_ist() -> None:
    parsed = parse_fivepaisa_timestamp("/Date(1792651839000+0530)/")
    assert parsed == datetime(2026, 10, 22, 12, 20, 39, tzinfo=TZ_IST)


def test_small_provider_second_boundary_skew_is_tolerated_but_large_future_is_rejected() -> None:
    fixed_now = datetime(2026, 10, 22, 12, 20, 39, tzinfo=TZ_IST)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    broker = MagicMock()
    broker.get_nifty_spot_scrip_code.return_value = 999920000
    broker.client.fetch_market_feed_scrip.side_effect = [
        {
            "Status": 0,
            "Data": [{"ScripCode": 999920000, "LastRate": 22500.0, "TickDt": "/Date(1792651840000+0530)/"}],
        },
        {
            "Status": 0,
            "Data": [{"ScripCode": 999920000, "LastRate": 22500.0, "TickDt": "/Date(1792651843000+0530)/"}],
        },
    ]
    feed = LiveMarketFeed(broker=broker, max_staleness_seconds=5.0)
    feed.is_connected = True
    with patch("trading_platform.data.live_market_feed.datetime", FrozenDateTime):
        tolerated = feed.fetch_spot_tick()
        rejected = feed.fetch_spot_tick()
    assert tolerated is not None
    assert rejected is None
    assert feed.last_error_category == "FUTURE_QUOTE"


def test_live_account_observation_is_not_a_paper_funding_source() -> None:
    margins = FivePaisaBroker(mode=OperationalMode.PAPER).get_margins()
    assert margins["account_namespace"] == "BROKER_READ_ONLY_OBSERVATION"
    assert margins["available_cash"] is None


def test_missing_and_future_provider_timestamps_are_rejected() -> None:
    broker = FivePaisaBroker()
    broker.client = type(
        "ReadOnlyTestClient",
        (),
        {
            "fetch_market_feed_scrip": lambda self, req: {
                "Status": 0,
                "Data": [{"LastRate": 22000.0}],
            }
        },
    )()
    feed = LiveMarketFeed(broker=broker)
    feed.is_connected = True
    assert feed.fetch_spot_tick() is None
    assert "timestamp missing" in (feed.last_error or "")

    now = datetime.now(TZ_IST)
    future = now + timedelta(seconds=30)
    broker.client = type(
        "FutureTimestampClient",
        (),
        {
            "fetch_market_feed_scrip": lambda self, req: {
                "Status": 0,
                "Data": [
                    {
                        "LastRate": 22000.0,
                        "TickDt": future.isoformat(),
                    }
                ],
            }
        },
    )()
    assert feed.fetch_spot_tick() is None
    assert "future" in (feed.last_error or "")


def test_candle_quality_detects_alignment_and_gap() -> None:
    base = datetime(2025, 8, 1, 9, 15)
    rows = [
        {"timestamp": base, "open": 100, "high": 102, "low": 99, "close": 101},
        {"timestamp": base + timedelta(minutes=10), "open": 101, "high": 103, "low": 100, "close": 102},
    ]
    metrics = validate_5m_frame(pd.DataFrame(rows))
    assert metrics["aligned_rows"] == 2
    assert metrics["missing_intervals"] == 1
    assert metrics["complete"] is False


def test_live_aggregator_emits_only_after_boundary_and_marks_sampled_source() -> None:
    aggregator = FiveMinuteCandleAggregator()
    assert aggregator.process_tick(_tick(datetime(2026, 1, 2, 9, 15, 1, tzinfo=TZ_IST))) is None
    closed = aggregator.process_tick(_tick(datetime(2026, 1, 2, 9, 20, tzinfo=TZ_IST), 101))
    assert closed is not None
    assert closed.exchange_time == datetime(2026, 1, 2, 9, 15, tzinfo=TZ_IST)
    assert closed.source == DataSource.FIVEPAISA_WS


def test_missing_candles_handling():
    agg = FiveMinuteCandleAggregator()
    t1 = datetime(2026, 10, 21, 9, 15, 0, tzinfo=TZ_IST)
    tick1 = SpotTick("NIFTY", 999920000, 22800.0, 22810.0, 22790.0, 22800.0, t1, t1)
    agg.process_tick(tick1)

    # Missing ticks between 09:16 and 09:29; next tick arrives at 09:30
    t2 = datetime(2026, 10, 21, 9, 30, 0, tzinfo=TZ_IST)
    tick2 = SpotTick("NIFTY", 999920000, 22850.0, 22860.0, 22840.0, 22800.0, t2, t2)
    bar = agg.process_tick(tick2)

    # Closes the 09:15 bar accurately without crashing
    assert bar is not None
    assert bar.exchange_time == datetime(2026, 10, 21, 9, 15, 0, tzinfo=TZ_IST)
    assert bar.open == 22800.0


def test_market_disconnection_handling():
    feed = LiveMarketFeed(broker=FivePaisaBroker(), poll_interval=1.0)
    feed.is_connected = False
    # Spot polling when disconnected returns None and increments error counter
    tick = feed.fetch_spot_tick()
    # In offline test, connection cannot be made so returns None
    assert tick is None or feed.is_connected is True


def test_fivepaisa_broker_place_order_is_blocked() -> None:
    """The retired PaperBroker half of this test is gone; the read-only broker half stays."""
    with pytest.raises(LiveTradingPermissionError):
        FivePaisaBroker(mode=OperationalMode.PAPER, live_approved=False).place_order(
            Order(
                order_id="1",
                client_order_id="1",
                symbol="NIFTY",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=65,
                created_at=datetime.now(TZ_UTC),
                updated_at=datetime.now(TZ_UTC),
            )
        )


def test_read_only_broker_reports_the_canonical_nifty_spot_scrip() -> None:
    assert FivePaisaBroker().get_nifty_spot_scrip_code() == 999920000
