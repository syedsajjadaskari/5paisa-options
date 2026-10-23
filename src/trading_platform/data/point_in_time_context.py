"""Immutable, point-in-time strategy reference context.

This module is deliberately separate from execution.  It provides one context
builder for retrospective shadow analysis and future approved observation modes,
with source/provenance attached to every derived value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from trading_platform.core.models import Bar
from trading_platform.data.calendar import NSECalendar

TZ_IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)


@dataclass(frozen=True)
class ReferenceProvenance:
    """Source and availability audit record for one context value."""

    field_name: str
    value: Any
    source_dataset: str
    source_date: date | None
    source_timestamp: datetime | None
    availability_timestamp: datetime | None
    calculation_method: str
    validation_status: str


@dataclass(frozen=True)
class PointInTimeReferenceContext:
    """Frozen context snapshot valid for one candle decision."""

    session_date: date
    candle_timestamp: datetime
    candle_available_timestamp: datetime
    decision_timestamp: datetime
    values: tuple[tuple[str, Any], ...]
    missing_fields: tuple[str, ...]
    provenance: tuple[ReferenceProvenance, ...]
    validation_status: str

    def as_dict(self) -> dict[str, Any]:
        """Return a copy suitable for a strategy context state."""
        return dict(self.values)

    def provenance_by_field(self) -> dict[str, ReferenceProvenance]:
        return {item.field_name: item for item in self.provenance}


class PointInTimeReferenceProvider:
    """Build previous-session and indicator context without future leakage."""

    def __init__(
        self,
        intraday_bars: list[Bar],
        official_daily_bars: list[Bar],
        *,
        source_intraday: str = "data/processed/nifty/5m/nifty50_5m.parquet",
        source_daily: str = "data/processed/nifty/1d/nifty50_1d.parquet",
        ema_period: int = 200,
        dma_period: int = 200,
        calendar: NSECalendar | None = None,
    ) -> None:
        self.intraday_bars = tuple(sorted(intraday_bars, key=lambda bar: bar.exchange_time))
        self.official_daily_bars = tuple(
            sorted(official_daily_bars, key=lambda bar: bar.exchange_time)
        )
        self.source_intraday = source_intraday
        self.source_daily = source_daily
        self.ema_period = ema_period
        self.dma_period = dma_period
        self.calendar = calendar or NSECalendar()
        self._intraday_by_date: dict[date, tuple[Bar, ...]] = {}
        for bar in self.intraday_bars:
            if SESSION_OPEN <= self._ist(bar.exchange_time).time() < SESSION_CLOSE:
                local_date = self._ist(bar.exchange_time).date()
                self._intraday_by_date.setdefault(local_date, tuple())
                self._intraday_by_date[local_date] += (bar,)
        self._daily_by_date: dict[date, Bar] = {
            self._ist(bar.exchange_time).date(): bar for bar in self.official_daily_bars
        }
        self._ema_cache: dict[datetime, tuple[float | None, int, datetime | None]] = {}
        k = 2.0 / (self.ema_period + 1.0)
        ema: float | None = None
        self._ema_source_bars = tuple(
            bar
            for bar in self.intraday_bars
            if SESSION_OPEN <= self._ist(bar.exchange_time).time() < SESSION_CLOSE
        )
        for index, historical_bar in enumerate(self._ema_source_bars, start=1):
            ema = historical_bar.close if ema is None else (historical_bar.close * k) + (ema * (1.0 - k))
            self._ema_cache[historical_bar.exchange_time] = (
                round(ema, 2) if index >= self.ema_period else None,
                index,
                self._bar_close_time(historical_bar) if index >= self.ema_period else None,
            )

    @staticmethod
    def _ist(value: datetime) -> datetime:
        return value.replace(tzinfo=TZ_IST) if value.tzinfo is None else value.astimezone(TZ_IST)

    @staticmethod
    def _available_at(date_value: date) -> datetime:
        """Conservative retrospective assumption: available at next open."""
        return datetime.combine(date_value, SESSION_OPEN, tzinfo=TZ_IST)

    @staticmethod
    def _bar_close_time(bar: Bar) -> datetime:
        value = bar.exchange_time.replace(tzinfo=TZ_IST) if bar.exchange_time.tzinfo is None else bar.exchange_time.astimezone(TZ_IST)
        return value + timedelta(minutes=5)

    def _provenance(
        self,
        field_name: str,
        value: Any,
        *,
        source_dataset: str,
        source_date: date | None,
        source_timestamp: datetime | None,
        availability_timestamp: datetime | None,
        calculation_method: str,
        validation_status: str = "VALIDATED",
    ) -> ReferenceProvenance:
        return ReferenceProvenance(
            field_name=field_name,
            value=value,
            source_dataset=source_dataset,
            source_date=source_date,
            source_timestamp=source_timestamp,
            availability_timestamp=availability_timestamp,
            calculation_method=calculation_method,
            validation_status=validation_status,
        )

    def _prior_daily(self, session_date: date) -> tuple[date, Bar] | None:
        candidates = [
            (day, bar)
            for day, bar in self._daily_by_date.items()
            if day < session_date and self.calendar.is_trading_day(day)
        ]
        return max(candidates, key=lambda item: item[0]) if candidates else None

    def _zone_bodies(self, prior_date: date) -> tuple[float | None, float | None, Bar | None, Bar | None]:
        bars = self._intraday_by_date.get(prior_date, tuple())
        if not bars:
            return None, None, None, None
        high_bar = max(bars, key=lambda item: item.high)
        low_bar = min(bars, key=lambda item: item.low)
        return (
            max(high_bar.open, high_bar.close),
            min(low_bar.open, low_bar.close),
            high_bar,
            low_bar,
        )

    def _ema(self, current_bar: Bar) -> tuple[float | None, int, datetime | None]:
        cached = self._ema_cache.get(current_bar.exchange_time)
        if cached is not None:
            return cached
        eligible_count = sum(item.exchange_time <= current_bar.exchange_time for item in self._ema_source_bars)
        return None, eligible_count, None

    def _dma(self, session_date: date) -> tuple[float | None, int, date | None]:
        eligible = [bar for day, bar in self._daily_by_date.items() if day < session_date]
        eligible.sort(key=lambda bar: bar.exchange_time)
        if len(eligible) < self.dma_period:
            return None, len(eligible), None
        selected = eligible[-self.dma_period :]
        return round(sum(bar.close for bar in selected) / self.dma_period, 2), len(eligible), selected[-1].exchange_time.date()

    def build(self, bar: Bar, *, observation_time: datetime | None = None) -> PointInTimeReferenceContext:
        """Build context for a bar only after its five-minute close is available."""
        candle_time = self._ist(bar.exchange_time)
        candle_available = self._bar_close_time(bar)
        effective_observation = observation_time.astimezone(TZ_IST) if observation_time else candle_available
        values: dict[str, Any] = {
            "candle_timestamp": candle_time,
            "candle_available_timestamp": candle_available,
            "session_supported": self.calendar.is_entry_session_supported(candle_time.date()),
            "prior_completed_bars": tuple(
                prior
                for prior in self._ema_source_bars
                if self._ist(prior.exchange_time) < candle_time
            ),
        }
        provenance: list[ReferenceProvenance] = []
        missing: list[str] = []

        def add_value(
            field_name: str,
            value: Any,
            *,
            source_dataset: str,
            source_date: date | None,
            source_timestamp: datetime | None,
            availability_timestamp: datetime | None,
            calculation_method: str,
            required: bool = True,
            validation_status: str = "VALIDATED",
        ) -> None:
            values[field_name] = value
            provenance.append(
                self._provenance(
                    field_name,
                    value,
                    source_dataset=source_dataset,
                    source_date=source_date,
                    source_timestamp=source_timestamp,
                    availability_timestamp=availability_timestamp,
                    calculation_method=calculation_method,
                    validation_status=validation_status,
                )
            )
            if required and value is None:
                missing.append(field_name)

        prior = self._prior_daily(candle_time.date())
        if prior is None:
            prior_date = None
            prior_bar = None
        else:
            prior_date, prior_bar = prior
        availability = self._available_at(candle_time.date())
        for field_name, field_value in (
            ("pdh", prior_bar.high if prior_bar else None),
            ("pdl", prior_bar.low if prior_bar else None),
            ("pdc", prior_bar.close if prior_bar else None),
            ("pdo", prior_bar.open if prior_bar else None),
        ):
            add_value(
                field_name,
                field_value,
                source_dataset=self.source_daily,
                source_date=prior_date,
                source_timestamp=prior_bar.exchange_time if prior_bar else None,
                availability_timestamp=availability if prior_bar else None,
                calculation_method="official completed daily OHLC; prior date strictly less than decision date",
                validation_status="HISTORICAL_AVAILABILITY_ASSUMED" if prior_bar else "MISSING",
            )
        pd_res_body, pd_supp_body, high_bar, low_bar = (
            self._zone_bodies(prior_date) if prior_date else (None, None, None, None)
        )
        add_value(
            "pd_res_body",
            pd_res_body,
            source_dataset=self.source_intraday,
            source_date=prior_date,
            source_timestamp=high_bar.exchange_time if high_bar else None,
            availability_timestamp=availability if high_bar else None,
            calculation_method="body of intraday candle containing prior official high",
            validation_status="HISTORICAL_AVAILABILITY_ASSUMED" if high_bar else "MISSING",
        )
        add_value(
            "pd_supp_body",
            pd_supp_body,
            source_dataset=self.source_intraday,
            source_date=prior_date,
            source_timestamp=low_bar.exchange_time if low_bar else None,
            availability_timestamp=availability if low_bar else None,
            calculation_method="body of intraday candle containing prior official low",
            validation_status="HISTORICAL_AVAILABILITY_ASSUMED" if low_bar else "MISSING",
        )

        ema, ema_history_count, ema_available = self._ema(bar)
        dma, dma_history_count, dma_source_date = self._dma(candle_time.date())
        add_value(
            "ema_200",
            ema,
            source_dataset=self.source_intraday,
            source_date=candle_time.date(),
            source_timestamp=ema_available,
            availability_timestamp=ema_available,
            calculation_method=f"recursive EMA over {self.ema_period} completed 5-minute closes through this candle",
            validation_status="VALIDATED" if ema is not None else "INSUFFICIENT_HISTORY",
        )
        add_value(
            "dma_200",
            dma,
            source_dataset=self.source_daily,
            source_date=dma_source_date,
            source_timestamp=self._daily_by_date[dma_source_date].exchange_time if dma_source_date else None,
            availability_timestamp=availability if dma is not None else None,
            calculation_method=f"simple mean of the previous {self.dma_period} official daily closes",
            validation_status="HISTORICAL_AVAILABILITY_ASSUMED" if dma is not None else "INSUFFICIENT_HISTORY",
        )
        session_bars = self._intraday_by_date.get(candle_time.date(), tuple())
        first_bar = session_bars[0] if session_bars else None
        add_value(
            "session_open",
            first_bar.open if first_bar else None,
            source_dataset=self.source_intraday,
            source_date=candle_time.date(),
            source_timestamp=first_bar.exchange_time if first_bar else None,
            availability_timestamp=self._bar_close_time(first_bar) if first_bar else None,
            calculation_method="first completed 09:15 session candle open",
            validation_status="VALIDATED" if first_bar else "MISSING",
        )
        add_value(
            "irh",
            first_bar.high if first_bar and candle_time >= self._ist(first_bar.exchange_time) else None,
            source_dataset=self.source_intraday,
            source_date=candle_time.date(),
            source_timestamp=first_bar.exchange_time if first_bar else None,
            availability_timestamp=self._bar_close_time(first_bar) if first_bar else None,
            calculation_method="high of the completed first 09:15 five-minute candle",
            validation_status="VALIDATED" if first_bar and candle_time >= self._ist(first_bar.exchange_time) else "NOT_YET_AVAILABLE",
        )
        add_value(
            "irl",
            first_bar.low if first_bar and candle_time >= self._ist(first_bar.exchange_time) else None,
            source_dataset=self.source_intraday,
            source_date=candle_time.date(),
            source_timestamp=first_bar.exchange_time if first_bar else None,
            availability_timestamp=self._bar_close_time(first_bar) if first_bar else None,
            calculation_method="low of the completed first 09:15 five-minute candle",
            validation_status="VALIDATED" if first_bar and candle_time >= self._ist(first_bar.exchange_time) else "NOT_YET_AVAILABLE",
        )
        add_value(
            "gap_points",
            round(first_bar.open - prior_bar.close, 2) if first_bar and prior_bar else None,
            source_dataset=f"{self.source_intraday}+{self.source_daily}",
            source_date=candle_time.date(),
            source_timestamp=first_bar.exchange_time if first_bar else None,
            availability_timestamp=self._bar_close_time(first_bar) if first_bar else None,
            calculation_method="current session open minus official prior daily close",
        )
        values["is_gap_up"] = values["gap_points"] is not None and values["gap_points"] > 0
        values["is_gap_down"] = values["gap_points"] is not None and values["gap_points"] < 0
        values["is_gap_within_previous_range"] = (
            values["is_gap_up"] and prior_bar is not None and first_bar is not None and first_bar.open <= prior_bar.high
        )
        values["historical_availability_assumption"] = (
            "official daily and intraday historical bars assumed available by next session open; original publication timestamps unavailable"
        )
        if effective_observation < candle_available:
            missing.append("candle_available_timestamp")
        if not values["session_supported"]:
            missing.append("session_supported")
        status = "VALID" if not missing else "MISSING_DATA"
        return PointInTimeReferenceContext(
            session_date=candle_time.date(),
            candle_timestamp=candle_time,
            candle_available_timestamp=candle_available,
            decision_timestamp=effective_observation,
            values=tuple(sorted(values.items(), key=lambda item: item[0])),
            missing_fields=tuple(sorted(set(missing))),
            provenance=tuple(provenance),
            validation_status=status,
        )

    def build_session(self, bars: list[Bar]) -> tuple[PointInTimeReferenceContext, ...]:
        """Build contexts in chronological order for a session."""
        return tuple(self.build(bar) for bar in sorted(bars, key=lambda item: item.exchange_time))
