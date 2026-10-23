"""Phase 4C remediation tests: namespaces, provenance, accounting, and sessions."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.enums import OperationalMode
from trading_platform.data.calendar import NSECalendar
from trading_platform.data.live_market_feed import Level2Quote, LiveMarketFeed
from trading_platform.paper.accounting import ReferenceLedger
from trading_platform.paper.broker import PaperBroker
from trading_platform.paper.portfolio import VirtualPortfolio
from trading_platform.paper.recorder import MarketDataRecorder

TZ_IST = ZoneInfo("Asia/Kolkata")


def test_reference_ledger_matches_paise_level_round_trip() -> None:
    ledger = ReferenceLedger.open(250000.0, 101.0, 65, 26.53)
    assert ledger.cash == Decimal("243408.47")
    assert ledger.mark(109.5) == Decimal("250525.97")
    assert ledger.close(109.5, 65, 37.23) == Decimal("488.74")
    ledger.assert_flat_invariant()
    assert ledger.cash == Decimal("250488.74")


def test_paper_namespace_cannot_load_broker_namespace(tmp_path: Path) -> None:
    path = tmp_path / "paper_broker.json"
    broker = PaperBroker(initial_balance=250000.0)
    broker.save_state(path, state_generation="GEN")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["account_namespace"] = "BROKER_READ_ONLY_OBSERVATION"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert PaperBroker(initial_balance=250000.0).load_state(path) is False


def test_virtual_portfolio_restart_preserves_only_virtual_cash(tmp_path: Path) -> None:
    path = tmp_path / "portfolio.json"
    portfolio = VirtualPortfolio(starting_capital=250000.0)
    portfolio.available_cash = 249999.99
    portfolio.save_state(path, state_generation="GEN")
    restored = VirtualPortfolio(starting_capital=1.0)
    assert restored.load_state(path) is True
    assert restored.available_cash == 249999.99
    assert restored.ACCOUNT_NAMESPACE == "PAPER_VIRTUAL"


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


def test_unsupported_special_sessions_block_entry_calendar() -> None:
    calendar = NSECalendar()
    assert calendar.is_unsupported_special_session(datetime(2025, 10, 21).date()) is True
    assert calendar.is_entry_session_supported(datetime(2025, 10, 21).date()) is False
    assert calendar.is_entry_session_supported(datetime(2026, 2, 1).date()) is False
    assert calendar.is_regular_trading_minute(
        datetime(2025, 10, 21, 14, 0, tzinfo=TZ_IST)
    ) is False


def test_verified_archive_persists_provenance_and_raw_depth(tmp_path: Path) -> None:
    now = datetime.now(TZ_IST)
    recorder = MarketDataRecorder(
        db_path=tmp_path / "paper.duckdb",
        archive_dir=tmp_path / "verified_archive",
    )
    quote = Level2Quote(
        scrip_code=51344,
        symbol="NIFTY 27 OCT 2026 CE 22800.00",
        best_bid=100.0,
        best_ask=100.5,
        bid_qty=65,
        ask_qty=65,
        last_price=100.5,
        provider_timestamp=now - timedelta(milliseconds=100),
        ingestion_timestamp=now,
        raw_provider_timestamp="/Date(1792577624000+0530)/",
        provenance_verified=True,
    )
    recorder.record_option_snapshot(
        quote,
        expiry=date(2026, 10, 27),
        strike=22800.0,
        option_type="CE",
        lot_size=65,
        raw_response={"TimeStamp": quote.raw_provider_timestamp, "Asks": [{"Price": 100.5}]},
    )
    parquet = recorder.flush_options_archive_parquet()
    assert parquet is not None
    import pandas as pd

    archived = pd.read_parquet(parquet)
    assert archived.loc[0, "provenance_status"] == "VALID_EXCHANGE_TIMESTAMPED"
    assert "TimeStamp" in archived.loc[0, "raw_response_json"]
