"""Phase 4E.2 point-in-time context and authoritative-candle regressions."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.core.enums import DataSource, Exchange, InstrumentType, Timeframe
from trading_platform.core.models import Bar
from trading_platform.data.candle_quality import validate_5m_frame
from trading_platform.data.point_in_time_context import PointInTimeReferenceProvider
from trading_platform.paper.shadow import ShadowSignalCollector

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


def _bar(day: date, hour: int, minute: int, open_: float, high: float, low: float, close: float, interval: Timeframe = Timeframe.M5) -> Bar:
    ts = datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ_IST)
    return Bar(
        timestamp=ts.astimezone(TZ_UTC),
        exchange_time=ts,
        symbol="NIFTY 50",
        instrument_id="999920000",
        exchange=Exchange.NSE,
        instrument_type=InstrumentType.INDEX,
        interval=interval,
        open=open_,
        high=high,
        low=low,
        close=close,
        source=DataSource.FIVEPAISA_HISTORICAL,
    )


def test_previous_trading_day_and_official_close_skip_weekend() -> None:
    friday = date(2025, 8, 8)
    monday = date(2025, 8, 11)
    intraday = [
        _bar(friday, 9, 15, 100, 110, 90, 102),
        _bar(monday, 9, 15, 106, 108, 104, 107),
    ]
    daily = [_bar(friday, 0, 0, 99, 112, 88, 105, Timeframe.D1)]
    provider = PointInTimeReferenceProvider(intraday, daily, ema_period=1, dma_period=1)
    context = provider.build(intraday[1])
    values = context.as_dict()
    assert values["pdh"] == 112
    assert values["pdl"] == 88
    assert values["pdc"] == 105
    assert values["pdo"] == 99
    assert values["gap_points"] == 1
    assert context.provenance_by_field()["pdc"].source_date == friday


def test_ema_does_not_use_later_bar() -> None:
    day = date(2025, 8, 6)
    current = _bar(day, 9, 15, 100, 101, 99, 100)
    later = _bar(day, 9, 20, 100, 201, 99, 200)
    daily = [_bar(date(2025, 8, 5), 0, 0, 90, 95, 85, 92, Timeframe.D1)]
    provider = PointInTimeReferenceProvider([current, later], daily, ema_period=1, dma_period=1)
    assert provider.build(current).as_dict()["ema_200"] == 100


def test_missing_warmup_is_explicit() -> None:
    day = date(2025, 8, 6)
    current = _bar(day, 9, 15, 100, 101, 99, 100)
    provider = PointInTimeReferenceProvider([current], [], ema_period=200, dma_period=200)
    context = provider.build(current)
    assert context.as_dict()["ema_200"] is None
    assert "ema_200" in context.missing_fields
    assert context.validation_status == "MISSING_DATA"


def test_late_received_candle_is_not_shadow_eligible() -> None:
    day = date(2025, 8, 6)
    current = _bar(day, 9, 15, 100, 101, 99, 100)
    prior = _bar(date(2025, 8, 5), 9, 15, 90, 95, 85, 92)
    provider = PointInTimeReferenceProvider([prior, current], [prior], ema_period=1, dma_period=1)
    context = provider.build(current, observation_time=current.exchange_time + timedelta(minutes=4))
    shadow = ShadowSignalCollector()
    row = shadow.evaluate_strategy_bar("PDS", "METHOD_1", current, context)
    assert row["reason_code"] == "INCOMPLETE_CANDLE"
    assert row["execution_created"] is False


def test_shadow_uses_same_frozen_context_values() -> None:
    day = date(2025, 8, 6)
    prior = _bar(date(2025, 8, 5), 9, 15, 90, 95, 85, 92)
    current = _bar(day, 9, 15, 100, 101, 99, 100)
    provider = PointInTimeReferenceProvider([prior, current], [prior], ema_period=1, dma_period=1)
    context = provider.build(current)
    shadow = ShadowSignalCollector()
    row = shadow.evaluate_strategy_bar("PDS", "METHOD_1", current, context)
    assert '"pdc": 92' in row["previous_day_levels"]
    assert row["decision_available_time"] == context.candle_available_timestamp.isoformat()


def test_authoritative_candle_validation_rejects_duplicates_and_future_rows() -> None:
    ts = datetime(2025, 8, 6, 9, 15, tzinfo=TZ_IST)
    frame = pd.DataFrame(
        [
            {"timestamp": ts, "open": 100, "high": 101, "low": 99, "close": 100},
            {"timestamp": ts, "open": 100, "high": 101, "low": 99, "close": 100},
            {"timestamp": ts + timedelta(minutes=5), "open": 100, "high": 102, "low": 98, "close": 101},
        ]
    )
    metrics = validate_5m_frame(frame, observation_time=ts + timedelta(minutes=4))
    assert metrics["duplicate_rows"] == 2
    assert metrics["future_rows"] == 1
    assert metrics["complete"] is False
