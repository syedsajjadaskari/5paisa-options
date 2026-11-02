"""Phase 4D connectivity, timestamp tolerance, and persistence regressions."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import duckdb

from trading_platform.data.live_market_feed import (
    Level2Quote,
    LiveMarketFeed,
    SpotTick,
    parse_fivepaisa_timestamp,
)
from trading_platform.paper.recorder import MarketDataRecorder

TZ_IST = ZoneInfo("Asia/Kolkata")


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


def test_recorder_migrates_ingestion_timestamp_and_deduplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "phase4d.duckdb"
    recorder = MarketDataRecorder(db_path=db_path, archive_dir=tmp_path / "archive", run_id="RUN")
    now = datetime.now(TZ_IST)
    tick = SpotTick(
        symbol="NIFTY 50",
        scrip_code=999920000,
        last_rate=22500.0,
        high=22600.0,
        low=22400.0,
        prev_close=22550.0,
        provider_timestamp=now,
        ingestion_timestamp=now,
        raw_provider_timestamp="/Date(1792651839000+0530)/",
        raw_response_json='{"Status":0}',
    )
    recorder.record_spot_tick(tick)
    recorder.record_spot_tick(tick)

    quote = Level2Quote(
        scrip_code=51334,
        symbol="NIFTY 27 OCT 2026 CE 22600.00",
        best_bid=171.0,
        best_ask=171.2,
        bid_qty=65,
        ask_qty=65,
        last_price=171.2,
        provider_timestamp=now - timedelta(milliseconds=100),
        ingestion_timestamp=now,
        raw_provider_timestamp="/Date(1792651839000+0530)/",
        raw_response_json='{"Status":0}',
    )
    recorder.record_option_snapshot(quote, datetime(2026, 10, 27).date(), 22600.0, "CE", 65)
    recorder.record_option_snapshot(quote, datetime(2026, 10, 27).date(), 22600.0, "CE", 65)

    con = duckdb.connect(str(db_path), read_only=True)
    assert con.execute("SELECT COUNT(*) FROM market_events").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM options_archive").fetchone()[0] == 1
    assert "ingestion_timestamp" in {
        row[0] for row in con.execute("DESCRIBE market_events").fetchall()
    }
    con.close()
