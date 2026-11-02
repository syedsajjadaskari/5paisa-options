"""SIMULATED replay provider: real historical NIFTY bars, SIMULATED ticks and option quotes.

!!! EVERYTHING THIS MODULE EMITS AFTER THE WARM-UP IS SIMULATED TEST DATA !!!

* The NIFTY index path comes from REAL historical 5-minute bars (``data/processed/nifty``).
* The intra-bar ticks are a deterministic polyline through the bar's O/H/L/C (no randomness).
* The option quotes are produced by :class:`SimulatedOptionModel` (a documented, deterministic
  normal-model curve).  THEY ARE NOT MARKET QUOTES: no bid/ask in this module was ever observed
  on an exchange, no history of option prices exists in the repo, and a P&L computed from them
  says nothing about real option profitability.
* The contract list is SIMULATED (fake scrip codes >= ``SIM_SCRIP_BASE``).  Only the lot size is
  read (read-only) from the real scrip master file, and a missing/unreadable master raises.
* Replay results must NEVER be mixed with the live ledger: replay uses its own runtime root
  (``runtime/replay/``) and every record carries the label ``REPLAY_SIMULATED``.

Labelling: ``CandleSource.SIMULATED_REPLAY`` on completed candles, ``source`` strings that start
with ``SIMULATED_REPLAY`` on ticks/quotes, contract symbols that start with ``SIMULATED``, and
``CheckResult`` details that start with ``SIMULATED_REPLAY``.

Read-only boundary: this module reads local parquet files only.  It performs no network call,
imports no broker module and exposes nothing that could place, modify or cancel an order.

Time model.  A :class:`~trading_platform.live.providers.base.ManualClock` drives everything:
``poll()`` answers for ``clock.now()`` and ``completed_candles(as_of)`` is purely a function of
``as_of``, so a replay loop that sets the clock before each call exercises the same code path as
live.  :meth:`ReplayProvider.run_events` is the ready-made driver.

Intra-bar tick path (``ticks_per_bar`` ticks, default 12, tick ``k`` at ``bar_open + k*300/n`` s).
Every real bar's high AND low are visited, the first tick is the bar open and the last tick (at
``bar_open + 300*(n-1)/n`` s) is the bar close, so a candle completed at ``bar_open + 300`` s agrees
with the last tick.  The visiting ORDER is a modelling assumption - real intra-bar order is
unknowable from OHLC:

* ``HEURISTIC`` (default): green/flat bar ``O -> L -> H -> C``, red bar ``O -> H -> L -> C``.
  This is conservative for a stop only when the position is on the side that the first extreme
  hurts (longs on green bars, shorts on red bars); it is NOT conservative for the opposite side.
* ``LOW_FIRST``: ``O -> L -> H -> C`` always - the adverse extreme first for BULLISH (long CE)
  positions, so a stop is hit before a target inside the same bar.
* ``HIGH_FIRST``: ``O -> H -> L -> C`` always - adverse first for BEARISH (long PE) positions.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.core.enums import (
    DataSource,
    Exchange,
    InstrumentType,
    Timeframe,
)
from trading_platform.core.exceptions import TradingPlatformError
from trading_platform.core.models import Bar
from trading_platform.data.calendar import NSECalendar
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
from trading_platform.live.providers.base import (
    CheckResult,
    ManualClock,
    PollResult,
)

#: Source-string prefix on everything simulated (asserted by the tests).
SIM_PREFIX = "SIMULATED_REPLAY"
#: Label the engine stamps on every record produced from this provider.
REPLAY_LABEL = "REPLAY_SIMULATED"
SPOT_SOURCE = f"{SIM_PREFIX}_INDEX_TICK_PATH"
QUOTE_SOURCE = f"{SIM_PREFIX}_OPTION_MODEL"
#: Fake scrip codes start here (real NIFTY option scrip codes are far below).
SIM_SCRIP_BASE = 9_000_000
#: Label of the display-only forming candle (CandleSource has no simulated-sampled value).
FORMING_CANDLE_LABEL = f"{SIM_PREFIX}_SAMPLED_TICKS"

DEFAULT_5M_PATH = Path("data/processed/nifty/5m/nifty50_5m.parquet")
DEFAULT_1D_PATH = Path("data/processed/nifty/1d/nifty50_1d.parquet")
DEFAULT_SCRIP_MASTER_PATH = Path("data/reference/scrip_master.parquet")

_SESSION_OPEN = time(9, 15)
_LAST_CANDLE_OPEN = time(15, 25)  # the 15:25-15:30 candle is the last valid one
_EXPIRY_CLOSE = time(15, 30)
_INDEX_TICK = 0.05
_OPTION_TICK = 0.05
_BAR_SECONDS = CANDLE_MINUTES * 60
_NIFTY_SPOT_SCRIP = "999920000"
_UTC = ZoneInfo("UTC")

# Warm-up requirements: identical to the live provider (live/providers/fivepaisa.py).
MIN_DAILY_BARS = 250
MIN_INTRADAY_BARS = 600
MIN_SESSION_BARS = 70  # also the minimum for the previous session and for the replay day
DAILY_LOOKBACK_DAYS = 430
INTRADAY_LOOKBACK_DAYS = 45
PREV_DAILY_VS_5M_CLOSE_TOLERANCE_PCT = 1.0

IntrabarOrder = Literal["HEURISTIC", "LOW_FIRST", "HIGH_FIRST"]


class ReplayProviderError(TradingPlatformError):
    """The replay provider cannot supply what it must supply (fail closed, nothing assumed)."""


# --------------------------------------------------------------------------- fault injection

Window = tuple[datetime, datetime]  # [start, end): half-open, tz-aware


@dataclass(frozen=True)
class FaultInjection:
    """Deterministic data faults, to exercise stale-quote / EXIT_PENDING / outage paths.

    All windows are half-open ``[start, end)`` in tz-aware IST clock time.

    * ``drop_quotes_between``  - ``poll`` returns the spot but NO option quotes (+ an error).
    * ``stale_between``        - quotes are served FROZEN at the window start: values, ``server_time``
      and ``received_at`` are those of the window start, so their age grows (STALE_QUOTE).
    * ``stale_after``          - same as an open-ended ``stale_between`` from that instant.
    * ``zero_bid_between``     - quotes have ``bid == 0`` (no valid bid => cannot exit).
    * ``spot_outage_between``  - no spot and no quotes; the error starts with ``DISCONNECTED``.
    """

    drop_quotes_between: tuple[Window, ...] = ()
    stale_between: tuple[Window, ...] = ()
    stale_after: datetime | None = None
    zero_bid_between: tuple[Window, ...] = ()
    spot_outage_between: tuple[Window, ...] = ()

    @staticmethod
    def _in(windows: Sequence[Window], now: datetime) -> Window | None:
        for start, end in windows:
            if start <= now < end:
                return (start, end)
        return None

    def dropped(self, now: datetime) -> bool:
        return self._in(self.drop_quotes_between, now) is not None

    def zero_bid(self, now: datetime) -> bool:
        return self._in(self.zero_bid_between, now) is not None

    def spot_outage(self, now: datetime) -> bool:
        return self._in(self.spot_outage_between, now) is not None

    def stale_since(self, now: datetime) -> datetime | None:
        """Instant the quotes are frozen at, or None when quotes are live."""
        win = self._in(self.stale_between, now)
        if win is not None:
            return win[0]
        if self.stale_after is not None and now >= self.stale_after:
            return self.stale_after
        return None


# --------------------------------------------------------------------------- option model


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _round_tick(value: float, tick: float = _OPTION_TICK) -> float:
    return round(round(value / tick) * tick, 2)


@dataclass(frozen=True)
class OptionModelParams:
    """Constants of the SIMULATED option curve (all documented, none calibrated to a market).

    * ``iv_proxy`` 0.13: the annualised index vol used to size the expected move.  It is NOT an
      implied volatility observed anywhere; it only sets plausible magnitudes.
    * ``min_sd_points`` 5: floor of the expected-move width so the curve stays finite at expiry.
    * ``half_spread_pct`` 0.75: half-spread as % of the model premium, bounded to
      [``min_half_spread``, ``max_half_spread``] = [0.05, 0.5] index points.
    * ``depth_lots``: displayed quantity on each side, in lots (so it is never below one lot).
    """

    iv_proxy: float = 0.13
    min_sd_points: float = 5.0
    min_premium: float = 0.05
    half_spread_pct: float = 0.75
    min_half_spread: float = 0.05
    max_half_spread: float = 0.5
    depth_lots: int = 20
    minutes_per_year: float = 365.0 * 24.0 * 60.0


class SimulatedOptionModel:
    """Deterministic, documented, NOT-a-market-model option premium curve.

    Premium = ``max(0.05, Bachelier(spot, strike, sd))`` where ``sd = iv_proxy * spot *
    sqrt(minutes_to_expiry / minutes_per_year)`` (floored) is the expected one-sigma index move
    until expiry (expiry = 15:30 IST on the expiry date).  For a call::

        C = (S - K) * N(d) + sd * n(d),   d = (S - K) / sd        delta = N(d)
        P = (K - S) * N(-d) + sd * n(d)                           delta = -N(-d)

    Consequences (all asserted by tests): ATM premium ~ 0.4 * sd, ATM delta 0.5, delta rises
    smoothly to ~0.9 at 1.28 sd in the money and falls to ~0.1 at 1.28 sd out of the money
    (a smooth version of "0.5 ATM / 0.9 deep ITM / 0.1 OTM"), premium is non-decreasing in spot
    for a CE and non-increasing for a PE, premium decays as expiry approaches at a fixed spot,
    and put-call parity holds.  Because delta is the exact slope of the premium curve, a stop or
    target on the UNDERLYING produces a plausible option P&L.

    Quotes are ``bid = premium - half``, ``ask = premium + half`` snapped to the 0.05 tick with
    ``bid >= 0.05`` and ``ask > bid`` always.
    """

    def __init__(self, params: OptionModelParams | None = None) -> None:
        self.params = params or OptionModelParams()

    # ------------------------------------------------------------------ internals

    def minutes_to_expiry(self, expiry: date, now: datetime) -> float:
        end = datetime.combine(expiry, _EXPIRY_CLOSE, tzinfo=IST)
        return max(0.0, (end - now.astimezone(IST)).total_seconds() / 60.0)

    def sd_points(self, spot: float, expiry: date, now: datetime) -> float:
        p = self.params
        frac = self.minutes_to_expiry(expiry, now) / p.minutes_per_year
        return max(p.min_sd_points, p.iv_proxy * spot * math.sqrt(frac))

    # ------------------------------------------------------------------ public

    def premium(self, contract: ContractSpec, spot: float, now: datetime) -> float:
        """Model premium in index points (>= ``min_premium``)."""
        if not spot > 0:
            raise ValueError(f"invalid spot {spot!r}")
        sd = self.sd_points(spot, contract.expiry, now)
        d = (spot - contract.strike) / sd
        if contract.option_type == "CE":
            value = (spot - contract.strike) * _norm_cdf(d) + sd * _norm_pdf(d)
        elif contract.option_type == "PE":
            value = (contract.strike - spot) * _norm_cdf(-d) + sd * _norm_pdf(d)
        else:
            raise ValueError(f"unknown option type {contract.option_type!r}")
        return max(self.params.min_premium, value)

    def delta(self, contract: ContractSpec, spot: float, now: datetime) -> float:
        """Signed delta of the (unfloored) curve: CE in (0, 1), PE in (-1, 0)."""
        sd = self.sd_points(spot, contract.expiry, now)
        d = (spot - contract.strike) / sd
        return _norm_cdf(d) if contract.option_type == "CE" else -_norm_cdf(-d)

    def book(self, contract: ContractSpec, spot: float, now: datetime) -> tuple[float, float]:
        """``(bid, ask)`` with ``0 < bid < ask`` always."""
        p = self.params
        prem = self.premium(contract, spot, now)
        half = min(p.max_half_spread, max(p.min_half_spread, prem * p.half_spread_pct / 100.0))
        bid = max(p.min_premium, _round_tick(prem - half))
        ask = max(_round_tick(prem + half), round(bid + _OPTION_TICK, 2))
        return bid, ask


# --------------------------------------------------------------------------- history helpers


def read_master_lot_size(path: Path) -> tuple[int, str]:
    """NIFTY option lot size from the scrip-master file (read-only; raises, never assumes).

    Uses the rows of the master's nearest expiry; if the file lists more than one lot size the
    nearest-expiry value is returned and the note says so.
    """
    path = Path(path)
    if not path.exists():
        raise ReplayProviderError(f"scrip master not found at {path}; lot size cannot be assumed")
    try:
        df = pd.read_parquet(
            path, columns=["Exch", "ExchType", "SymbolRoot", "ScripType", "LotSize", "Expiry"]
        )
    except Exception as exc:
        raise ReplayProviderError(
            f"scrip master unreadable ({type(exc).__name__}: {str(exc)[:120]}); "
            "lot size cannot be assumed"
        ) from exc
    mask = (
        (df["Exch"].astype(str).str.strip().str.upper() == "N")
        & (df["ExchType"].astype(str).str.strip().str.upper() == "D")
        & (df["SymbolRoot"].astype(str).str.strip().str.upper() == "NIFTY")
        & df["ScripType"].astype(str).str.strip().str.upper().isin(["CE", "PE"])
    )
    rows = df[mask].copy()
    if rows.empty:
        raise ReplayProviderError("scrip master has no NIFTY index option rows")
    rows["_expiry"] = pd.to_datetime(rows["Expiry"], errors="coerce")
    rows["_lot"] = pd.to_numeric(rows["LotSize"], errors="coerce")
    rows = rows.dropna(subset=["_expiry", "_lot"])
    rows = rows[rows["_lot"] > 0]
    if rows.empty:
        raise ReplayProviderError("scrip master NIFTY option rows have no valid LotSize")
    nearest = rows["_expiry"].min()
    lots = {int(v) for v in rows.loc[rows["_expiry"] == nearest, "_lot"]}
    if len(lots) != 1:
        raise ReplayProviderError(f"ambiguous NIFTY lot size at nearest expiry: {sorted(lots)}")
    lot = lots.pop()
    all_lots = sorted({int(v) for v in rows["_lot"]})
    note = f"lot size {lot} from scrip master (nearest expiry {nearest.date()})"
    if len(all_lots) > 1:
        note += f"; master lists several lot sizes {all_lots}"
    return lot, note


def _to_ist(value: pd.Timestamp) -> datetime:
    return value.to_pydatetime().replace(tzinfo=IST)


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows that cannot be a real bar (same rules as the live provider)."""
    if df.empty:
        return df
    work = df.copy()
    for col in ("open", "high", "low", "close"):
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work["timestamp"] = pd.to_datetime(work["timestamp"], errors="coerce")
    ohlc = work[["open", "high", "low", "close"]]
    mask = (
        ohlc.gt(0).all(axis=1)
        & work["timestamp"].notna()
        & (work["high"] >= work[["open", "close", "low"]].max(axis=1))
        & (work["low"] <= work[["open", "close", "high"]].min(axis=1))
    )
    mask &= ohlc.map(math.isfinite).all(axis=1)
    clean = work[mask].drop_duplicates(subset="timestamp", keep="last")
    return clean.sort_values("timestamp").reset_index(drop=True)


def _session_filter(df: pd.DataFrame) -> pd.DataFrame:
    """5m rows inside 09:15..15:25 (bar-open stamps), aligned; drops the 15:30 stub."""
    if df.empty:
        return df
    times = df["timestamp"].dt.time
    keep = (
        (times >= _SESSION_OPEN)
        & (times <= _LAST_CANDLE_OPEN)
        & (df["timestamp"].dt.minute % CANDLE_MINUTES == 0)
    )
    return df[keep].reset_index(drop=True)


def _frame_to_bars(df: pd.DataFrame, interval: Timeframe) -> list[Bar]:
    """REAL historical rows -> ``core.models.Bar`` (tz-aware IST bar-open stamps)."""
    bars: list[Bar] = []
    for row in df.itertuples(index=False):
        local = _to_ist(row.timestamp)
        bars.append(
            Bar(
                timestamp=local.astimezone(_UTC),
                exchange_time=local,
                symbol="NIFTY 50",
                instrument_id=_NIFTY_SPOT_SCRIP,
                exchange=Exchange.NSE,
                instrument_type=InstrumentType.INDEX,
                interval=interval,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=int(getattr(row, "volume", 0) or 0),
                source=DataSource.FIVEPAISA_HISTORICAL,
            )
        )
    return bars


@dataclass(frozen=True)
class _Tick:
    time: datetime
    price: float
    bar_index: int
    k: int


@dataclass(frozen=True)
class ReplayStep:
    """One step of :meth:`ReplayProvider.run_events` (clock already set to ``now``)."""

    now: datetime
    poll_result: PollResult
    completed_candles_due: list[CompletedCandle]  # newly completed since the previous step
    step_index: int = 0
    label: str = REPLAY_LABEL


# --------------------------------------------------------------------------- provider


class ReplayProvider:
    """SIMULATED replay of one historical NIFTY session (see module docstring)."""

    mode = RunMode.REPLAY

    def __init__(
        self,
        trading_date: date,
        nifty_5m_path: Path | str = DEFAULT_5M_PATH,
        nifty_1d_path: Path | str = DEFAULT_1D_PATH,
        clock: ManualClock | None = None,
        option_model: SimulatedOptionModel | None = None,
        scrip_master_path: Path | str = DEFAULT_SCRIP_MASTER_PATH,
        ticks_per_bar: int = 12,
        intrabar_order: IntrabarOrder = "HEURISTIC",
        faults: FaultInjection | None = None,
        candle_delay_s: float = 0.0,
        expiry_weekday: int = 1,
        strike_step: int = 50,
    ) -> None:
        if ticks_per_bar < 4:
            raise ValueError("ticks_per_bar must be >= 4 so that open, high, low and close are visited")
        if intrabar_order not in ("HEURISTIC", "LOW_FIRST", "HIGH_FIRST"):
            raise ValueError(f"unknown intrabar_order {intrabar_order!r}")
        if candle_delay_s < 0:
            raise ValueError("candle_delay_s must be >= 0")
        self.trading_date = trading_date
        self.ticks_per_bar = ticks_per_bar
        self.intrabar_order = intrabar_order
        self.candle_delay = timedelta(seconds=candle_delay_s)
        self.faults = faults or FaultInjection()
        self.option_model = option_model or SimulatedOptionModel()
        self.expiry_weekday = expiry_weekday
        self.strike_step = strike_step
        self._path_5m = Path(nifty_5m_path)
        self._path_1d = Path(nifty_1d_path)
        self._master_path = Path(scrip_master_path)
        self._clock = clock or ManualClock(
            datetime.combine(trading_date, time(9, 0), tzinfo=IST)
        )
        self._calendar = NSECalendar()
        self._closed = False
        self._warmup: WarmupData | None = None
        self._lot: tuple[int, str] | None = None
        self._contracts: list[ContractSpec] | None = None
        self._by_scrip: dict[int, ContractSpec] = {}

        for p in (self._path_5m, self._path_1d):
            if not p.exists():
                raise ReplayProviderError(f"historical dataset not found: {p}")
        self._clean_5m, self._clean_1d = clean_history(
            pd.read_parquet(self._path_5m), pd.read_parquet(self._path_1d)
        )

        self._day_rows = self._load_day_rows()
        self._candles: list[CompletedCandle] = [
            CompletedCandle(
                open_time=_to_ist(r.timestamp),
                open=float(r.open),
                high=float(r.high),
                low=float(r.low),
                close=float(r.close),
                volume=int(r.volume),
                source=CandleSource.SIMULATED_REPLAY,
            )
            for r in self._day_rows.itertuples(index=False)
        ]
        self._ticks = self._build_ticks()
        self._tick_times = [t.time for t in self._ticks]
        hi = lo = None
        self._run_high: list[float] = []
        self._run_low: list[float] = []
        for t in self._ticks:
            hi = t.price if hi is None else max(hi, t.price)
            lo = t.price if lo is None else min(lo, t.price)
            self._run_high.append(hi)
            self._run_low.append(lo)

    # ------------------------------------------------------------------ session data

    def _load_day_rows(self) -> pd.DataFrame:
        if not self._calendar.is_entry_session_supported(self.trading_date):
            raise ReplayProviderError(
                f"{self.trading_date} is not a supported NSE trading session (holiday, weekend or "
                "unsupported special session)"
            )
        day = self._clean_5m[self._clean_5m["timestamp"].dt.date == self.trading_date]
        day = day.reset_index(drop=True)
        if len(day) < MIN_SESSION_BARS:
            raise ReplayProviderError(
                f"historical 5m data has only {len(day)} clean bars for {self.trading_date} "
                f"(< {MIN_SESSION_BARS}); cannot replay it"
            )
        return day

    def _waypoints(self, c: CompletedCandle) -> list[float]:
        green = c.close >= c.open
        low_first = self.intrabar_order == "LOW_FIRST" or (
            self.intrabar_order == "HEURISTIC" and green
        )
        mid = [c.low, c.high] if low_first else [c.high, c.low]
        return [c.open, *mid, c.close]

    def _build_ticks(self) -> list[_Tick]:
        n = self.ticks_per_bar
        idx = [0, (n - 1) // 3, (2 * (n - 1)) // 3, n - 1]
        step = _BAR_SECONDS / n
        ticks: list[_Tick] = []
        for bi, candle in enumerate(self._candles):
            way = self._waypoints(candle)
            for k in range(n):
                # locate the polyline segment containing tick k
                seg = max(i for i in range(3) if idx[i] <= k)
                a, b = idx[seg], idx[seg + 1]
                if k == a:
                    price = way[seg]
                elif k == b:
                    price = way[seg + 1]
                else:
                    frac = (k - a) / (b - a)
                    raw = way[seg] + (way[seg + 1] - way[seg]) * frac
                    price = min(candle.high, max(candle.low, round(_round_tick(raw, _INDEX_TICK), 2)))
                ticks.append(
                    _Tick(
                        time=candle.open_time + timedelta(seconds=k * step),
                        price=price,
                        bar_index=bi,
                        k=k,
                    )
                )
        return ticks

    # ------------------------------------------------------------------ introspection

    @property
    def candles(self) -> list[CompletedCandle]:
        """All of the day's candles (for tests/launcher only - the engine uses completed_candles)."""
        return list(self._candles)

    @property
    def ticks(self) -> list[tuple[datetime, float]]:
        return [(t.time, t.price) for t in self._ticks]

    @property
    def clock(self) -> ManualClock:
        return self._clock

    @property
    def session_start(self) -> datetime:
        return self._candles[0].open_time

    @property
    def session_end(self) -> datetime:
        return self._candles[-1].close_time

    @property
    def forming_candle_label(self) -> str:
        """Label for ``sampled_forming_candle()`` output (its CandleSource is SAMPLED_TICKS)."""
        return FORMING_CANDLE_LABEL

    @property
    def run_label(self) -> str:
        return REPLAY_LABEL

    def step_times(self) -> list[datetime]:
        """Every instant at which something can change (ticks + candle availability)."""
        times = set(self._tick_times)
        times.update(c.close_time + self.candle_delay for c in self._candles)
        return sorted(times)

    # ------------------------------------------------------------------ startup / warm-up

    def _lot_size(self) -> tuple[int, str]:
        if self._lot is None:
            self._lot = read_master_lot_size(self._master_path)
        return self._lot

    def previous_trading_day(self, trading_date: date) -> date | None:
        d = trading_date
        for _ in range(14):
            d -= timedelta(days=1)
            if self._calendar.is_trading_day(d):
                return d
        return None

    def startup_checks(self, trading_date: date) -> list[CheckResult]:
        out = [
            CheckResult(
                "mode",
                True,
                f"{SIM_PREFIX}: SIMULATED replay of {self.trading_date} - real historical NIFTY "
                "bars, SIMULATED ticks and option quotes; results are NOT market results and "
                "never enter the live ledger",
            ),
            CheckResult(
                "trading_date",
                trading_date == self.trading_date,
                f"{SIM_PREFIX}: provider bound to {self.trading_date}"
                + ("" if trading_date == self.trading_date else f" (asked for {trading_date})"),
            ),
            CheckResult(
                "historical_session",
                True,
                f"{SIM_PREFIX}: {len(self._candles)} real 5m bars on {self.trading_date} "
                f"({self.ticks_per_bar} SIMULATED ticks/bar, order {self.intrabar_order})",
            ),
        ]
        try:
            w = self.previous_session_and_warmup(self.trading_date)
            out.append(
                CheckResult(
                    "warmup",
                    True,
                    f"{SIM_PREFIX}: {len(w.daily_bars)} daily + {len(w.intraday_bars)} prior 5m "
                    f"bars, previous session {w.previous_session.session_date}",
                )
            )
        except ReplayProviderError as exc:
            out.append(CheckResult("warmup", False, f"{SIM_PREFIX}: {exc}"))
        try:
            lot, note = self._lot_size()
            out.append(CheckResult("lot_size", True, f"{SIM_PREFIX}: {note}"))
        except ReplayProviderError as exc:
            out.append(CheckResult("lot_size", False, f"{SIM_PREFIX}: {exc}"))
        if self.faults != FaultInjection():
            out.append(
                CheckResult(
                    "fault_injection",
                    True,
                    f"{SIM_PREFIX}: artificial data faults are ACTIVE (test mode)",
                    blocking=False,
                )
            )
        out.append(
            CheckResult(
                "network",
                True,
                f"{SIM_PREFIX}: no broker login and no network access in replay mode",
                blocking=False,
            )
        )
        return out

    def _require_date(self, trading_date: date) -> None:
        if trading_date != self.trading_date:
            raise ReplayProviderError(
                f"replay provider is bound to {self.trading_date}, not {trading_date}"
            )

    def previous_session_and_warmup(self, trading_date: date) -> WarmupData:
        """Previous-session OHLC + >=250 daily + >=600 prior 5m bars, strictly before the date."""
        self._require_date(trading_date)
        if self._warmup is None:
            self._warmup = build_warmup(
                self._clean_5m,
                self._clean_1d,
                trading_date,
                self.previous_trading_day(trading_date),
            )
        return self._warmup

    # ------------------------------------------------------------------ contracts

    def _expiry_dates(self, trading_date: date) -> list[date]:
        """Weekly (nearest 5) + monthly (next 3) expiries on ``expiry_weekday`` (default Tuesday).

        Tuesday is the NSE NIFTY weekly expiry convention from 2025-09; ``options/expiry_calendar``
        carries no weekday convention of its own.  A holiday expiry moves to the previous trading
        day.  For the earliest (Aug 2025) dates the real convention was Thursday - irrelevant
        here because the whole chain is SIMULATED.
        """

        def adjust(d: date) -> date:
            for _ in range(7):
                if self._calendar.is_trading_day(d):
                    return d
                d -= timedelta(days=1)
            return d

        expiries: set[date] = set()
        first = trading_date + timedelta(days=(self.expiry_weekday - trading_date.weekday()) % 7)
        for i in range(5):
            expiries.add(adjust(first + timedelta(days=7 * i)))
        y, m = trading_date.year, trading_date.month
        for _ in range(4):
            nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
            last = nxt - timedelta(days=1)
            last -= timedelta(days=(last.weekday() - self.expiry_weekday) % 7)
            expiries.add(adjust(last))
            y, m = nxt.year, nxt.month
        return sorted(e for e in expiries if e >= trading_date)

    def load_contracts(self, trading_date: date) -> list[ContractSpec]:
        """SIMULATED weekly + monthly NIFTY CE/PE chain (fake scrip codes), real master lot size."""
        self._require_date(trading_date)
        if self._contracts is not None:
            return list(self._contracts)
        lot, _ = self._lot_size()
        warm = self.previous_session_and_warmup(trading_date)
        lo = min(warm.previous_session.close, min(c.low for c in self._candles))
        hi = max(warm.previous_session.close, max(c.high for c in self._candles))
        step = self.strike_step
        first = int(math.floor((lo - 600) / step) * step)
        last = int(math.ceil((hi + 600) / step) * step)
        strikes = list(range(first, last + step, step))
        out: list[ContractSpec] = []
        code = SIM_SCRIP_BASE
        for expiry in self._expiry_dates(trading_date):
            label = expiry.strftime("%d %b %Y").upper()
            for strike in strikes:
                for opt in ("CE", "PE"):
                    out.append(
                        ContractSpec(
                            scrip_code=code,
                            symbol=f"SIMULATED NIFTY {label} {opt} {float(strike):.2f}",
                            expiry=expiry,
                            strike=float(strike),
                            option_type=opt,
                            lot_size=lot,
                        )
                    )
                    code += 1
        self._contracts = out
        self._by_scrip = {c.scrip_code: c for c in out}
        return list(out)

    # ------------------------------------------------------------------ market data

    def _norm_time(self, value: datetime) -> datetime:
        return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)

    def _tick_index_at(self, when: datetime) -> int:
        """Index of the latest tick with time <= ``when`` (-1 when none yet)."""
        return bisect.bisect_right(self._tick_times, when) - 1

    def _spot_tick(self, i: int, now: datetime) -> SpotTick:
        t = self._ticks[i]
        prev_close = self.previous_session_and_warmup(self.trading_date).previous_session.close
        return SpotTick(
            price=t.price,
            exchange_time=t.time,
            received_at=now,
            day_high=self._run_high[i],
            day_low=self._run_low[i],
            prev_close=prev_close,
            source=SPOT_SOURCE,
        )

    def _quote(self, contract: ContractSpec, spot: float, at: datetime) -> OptionQuote:
        bid, ask = self.option_model.book(contract, spot, at)
        return OptionQuote(
            scrip_code=contract.scrip_code,
            symbol=contract.symbol,
            bid=bid,
            ask=ask,
            bid_qty=contract.lot_size * self.option_model.params.depth_lots,
            ask_qty=contract.lot_size * self.option_model.params.depth_lots,
            server_time=at,
            received_at=at,
            source=QUOTE_SOURCE,
        )

    def poll(self, watch_scrips: list[int]) -> PollResult:
        """Latest SIMULATED spot tick and option quotes for ``clock.now()``."""
        now = self._norm_time(self._clock.now())
        errors: list[str] = []
        i = self._tick_index_at(now)
        if i < 0:
            return PollResult(
                spot=None, errors=[f"{SIM_PREFIX}: no tick yet (before {self.session_start:%H:%M:%S})"]
            )
        if self.faults.spot_outage(now):
            return PollResult(
                spot=None,
                errors=[f"DISCONNECTED: {SIM_PREFIX} injected spot outage"],
            )
        spot = self._spot_tick(i, now)
        quotes: dict[int, OptionQuote] = {}
        if self._contracts is None:
            self.load_contracts(self.trading_date)
        if self.faults.dropped(now):
            if watch_scrips:
                errors.append(f"{SIM_PREFIX}: injected option-quote outage")
            return PollResult(spot=spot, quotes={}, errors=errors)
        frozen_at = self.faults.stale_since(now)
        zero_bid = self.faults.zero_bid(now)
        for scrip in dict.fromkeys(watch_scrips):
            contract = self._by_scrip.get(scrip)
            if contract is None:
                errors.append(f"{SIM_PREFIX}: unknown scrip {scrip}")
                continue
            qt, qspot = now, spot.price
            if frozen_at is not None:
                j = self._tick_index_at(frozen_at)
                if j < 0:
                    errors.append(f"{SIM_PREFIX}: no frozen quote available for {scrip}")
                    continue
                qt, qspot = frozen_at, self._ticks[j].price
            quote = self._quote(contract, qspot, qt)
            if zero_bid:
                quote = OptionQuote(
                    scrip_code=quote.scrip_code,
                    symbol=quote.symbol,
                    bid=0.0,
                    ask=quote.ask,
                    bid_qty=0,
                    ask_qty=quote.ask_qty,
                    server_time=quote.server_time,
                    received_at=quote.received_at,
                    source=quote.source,
                )
            quotes[scrip] = quote
        return PollResult(spot=spot, quotes=quotes, errors=errors)

    def completed_candles(self, trading_date: date, as_of: datetime) -> list[CompletedCandle]:
        """SIMULATED_REPLAY candles (real historical bars) with ``close_time <= as_of``."""
        if trading_date != self.trading_date:
            return []
        as_of = self._norm_time(as_of)
        return [c for c in self._candles if c.close_time + self.candle_delay <= as_of]

    def sampled_forming_candle(self) -> CompletedCandle | None:
        """Display-only forming candle from the SIMULATED ticks up to ``clock.now()``.

        ``source`` is SAMPLED_TICKS (non-authoritative); everything here is simulated, which
        :attr:`forming_candle_label` states for UIs.  None before the open / after the close.
        """
        now = self._norm_time(self._clock.now())
        i = self._tick_index_at(now)
        if i < 0:
            return None
        bi = self._ticks[i].bar_index
        bar = self._candles[bi]
        if now >= bar.close_time:
            return None
        mine = [t for t in self._ticks[: i + 1] if t.bar_index == bi]
        prices = [t.price for t in mine]
        return CompletedCandle(
            open_time=bar.open_time,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=0,
            source=CandleSource.SAMPLED_TICKS,
        )

    # ------------------------------------------------------------------ driver

    def run_events(
        self, watch_scrips: Sequence[int] | Callable[[], Sequence[int]] | None = None
    ) -> Iterator[ReplayStep]:
        """Yield one :class:`ReplayStep` per instant of the session, advancing the clock.

        ``watch_scrips`` is re-read at every step (pass a list the engine mutates, or a
        callable) so the poll always covers the engine's current watch list.  The clock is
        set to the step time BEFORE the step is built, and stays there while the consumer
        runs, so any extra ``poll()`` / ``completed_candles()`` call is consistent with it.
        """
        emitted = 0
        for n, now in enumerate(self.step_times()):
            if self._closed:
                return
            self._clock.set(now)
            watch = list(watch_scrips() if callable(watch_scrips) else (watch_scrips or []))
            result = self.poll(watch)
            done = self.completed_candles(self.trading_date, now)
            due = done[emitted:]
            emitted = len(done)
            yield ReplayStep(now=now, poll_result=result, completed_candles_due=due, step_index=n)

    def close(self) -> None:
        self._closed = True


# --------------------------------------------------------------------------- warm-up builder


def clean_history(raw_5m: pd.DataFrame, raw_1d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cleaned ``(5m_in_session, daily)`` frames; compute once and reuse across dates."""
    return _session_filter(_clean_frame(raw_5m)), _clean_frame(raw_1d)


def warmup_frames(
    clean_5m: pd.DataFrame,
    clean_1d: pd.DataFrame,
    trading_date: date,
    prev_day: date | None,
) -> tuple[DailyOhlc, pd.DataFrame, pd.DataFrame, list[str]]:
    """Validated warm-up frames from CLEANED data strictly before ``trading_date``.

    Same thresholds as the live provider.  Returns ``(previous_session, daily, intraday, notes)``.
    """
    if prev_day is None:
        raise ReplayProviderError("no previous trading day found within 14 days")
    notes = [f"{SIM_PREFIX}: warm-up uses REAL historical NIFTY bars strictly before {trading_date}"]

    daily = clean_1d
    if not daily.empty:
        daily = daily[
            (daily["timestamp"].dt.date < trading_date)
            & (daily["timestamp"].dt.date >= trading_date - timedelta(days=DAILY_LOOKBACK_DAYS))
        ].reset_index(drop=True)
    if len(daily) < MIN_DAILY_BARS:
        raise ReplayProviderError(
            f"insufficient daily history: {len(daily)} bars < {MIN_DAILY_BARS} required"
        )

    intraday = clean_5m
    if not intraday.empty:
        intraday = intraday[
            (intraday["timestamp"].dt.date < trading_date)
            & (intraday["timestamp"].dt.date >= trading_date - timedelta(days=INTRADAY_LOOKBACK_DAYS))
        ].reset_index(drop=True)
    if len(intraday) < MIN_INTRADAY_BARS:
        raise ReplayProviderError(
            f"insufficient prior-session 5m history: {len(intraday)} bars < "
            f"{MIN_INTRADAY_BARS} required"
        )
    prev_5m = intraday[intraday["timestamp"].dt.date == prev_day]
    if len(prev_5m) < MIN_SESSION_BARS:
        raise ReplayProviderError(
            f"previous session {prev_day} has only {len(prev_5m)} clean 5m bars "
            f"(< {MIN_SESSION_BARS}); reference levels untrustworthy"
        )
    agg_close = float(prev_5m.iloc[-1]["close"])
    prev_daily = daily[daily["timestamp"].dt.date == prev_day]
    if prev_daily.empty:
        previous = DailyOhlc(
            prev_day,
            float(prev_5m.iloc[0]["open"]),
            float(prev_5m["high"].max()),
            float(prev_5m["low"].min()),
            agg_close,
        )
        notes.append("previous session OHLC derived from 5m bars (no daily bar for that date)")
    else:
        row = prev_daily.iloc[-1]
        previous = DailyOhlc(
            prev_day, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        )
        diff = abs(previous.close - agg_close) / agg_close * 100.0
        if diff > PREV_DAILY_VS_5M_CLOSE_TOLERANCE_PCT:
            raise ReplayProviderError(
                f"previous-session daily close {previous.close} disagrees with 5m close "
                f"{agg_close} by {diff:.2f}%"
            )
        notes.append(f"previous session OHLC from daily bar; close vs 5m differs {diff:.3f}%")
    return previous, daily, intraday, notes


def build_warmup(
    clean_5m: pd.DataFrame, clean_1d: pd.DataFrame, trading_date: date, prev_day: date | None
) -> WarmupData:
    """:class:`WarmupData` (real ``core.models.Bar`` lists) strictly before ``trading_date``."""
    previous, daily, intraday, notes = warmup_frames(clean_5m, clean_1d, trading_date, prev_day)
    daily_bars = _frame_to_bars(daily, Timeframe.D1)
    intraday_bars = _frame_to_bars(intraday, Timeframe.M5)
    notes.append(f"{len(daily_bars)} daily bars, {len(intraday_bars)} prior-session 5m bars")
    return WarmupData(
        previous_session=previous,
        daily_bars=daily_bars,
        intraday_bars=intraday_bars,
        source_notes=notes,
    )


# --------------------------------------------------------------------------- demo days


@dataclass(frozen=True)
class DemoDay:
    """A historical date on which the corrected portfolio produced accepted trade(s)."""

    session_date: date
    strategies: tuple[str, ...]  # strategy of each accepted trade, chronological
    trades: int
    first_signal_candle_open: datetime  # OPEN stamp of the first signal candle
    first_entry_time: datetime
    directions: tuple[str, ...]  # underlying side: BUY (bullish) / SELL (bearish)
    exit_reasons: tuple[str, ...]
    points_pnl: float  # NIFTY INDEX points (never option rupees)
    raw_signals: int


@dataclass(frozen=True)
class DemoScan:
    days: list[DemoDay]
    skipped: dict[date, str] = field(default_factory=dict)  # dates with trades but unreplayable


def find_demo_days(
    nifty_5m_path: Path | str = DEFAULT_5M_PATH,
    nifty_1d_path: Path | str = DEFAULT_1D_PATH,
    *,
    start: date | None = None,
    end: date | None = None,
    strategy_factory: Callable[[], Any] | None = None,
    require_replayable: bool = True,
    return_scan: bool = False,
) -> list[DemoDay] | DemoScan:
    """Dates on which the four CORRECTED strategies' portfolio produced ACCEPTED trades.

    One chronological joint simulation of ``build_authorized_strategies()`` through the shared
    ``UnderlyingBacktestEngine.simulate`` core (same engine parameters as the official backtest:
    0.5 point slippage, 14:30 cutoff, 15:15 square-off, PDS>ORB>IRF>PDR tie-break, one
    position).  Nothing is cached or written; strategies/backtest code are only called.

    A date is kept when the live-style warm-up is available for it (``require_replayable``:
    >=250 daily, >=600 prior 5m bars, a clean previous session, a supported full session) so
    ``--replay auto`` never picks a date the provider would refuse.  Points are INDEX points:
    the day's real option outcome is unknown.  The live engine rebuilds its own reference
    levels, so edge cases may differ from this historical scan; it only nominates dates.
    """
    from trading_platform.backtesting.constants import (
        DEFAULT_SLIPPAGE_POINTS,
        ENTRY_CUTOFF_TIME,
        PORTFOLIO_PRIORITY,
        SQUARE_OFF_TIME,
    )
    from trading_platform.backtesting.engine import UnderlyingBacktestEngine
    from trading_platform.backtesting.portfolio_runner import build_authorized_strategies
    from trading_platform.data.reference_levels import ReferenceLevelCalculator

    all_5m = _clean_frame(pd.read_parquet(Path(nifty_5m_path)))
    clean_1d = _clean_frame(pd.read_parquet(Path(nifty_1d_path)))
    clean_5m = _session_filter(all_5m)
    # The backtest consumes RAW bars (15:30 stub included), exactly like the official runner.
    bars_5m = _frame_to_bars(all_5m, Timeframe.M5)
    bars_1d = _frame_to_bars(clean_1d, Timeframe.D1)
    bars_5m.sort(key=lambda b: b.exchange_time)

    enriched = ReferenceLevelCalculator().enrich_bars_with_context(
        bars_5m, daily_history_bars=bars_1d
    )
    contexts = {ctx.bar.exchange_time: ctx for ctx in enriched}
    engine = UnderlyingBacktestEngine(
        slippage_points=DEFAULT_SLIPPAGE_POINTS,
        eod_exit_time=SQUARE_OFF_TIME,
        entry_cutoff_time=ENTRY_CUTOFF_TIME,
        bar_minutes=CANDLE_MINUTES,
    )
    strategies = dict((strategy_factory or build_authorized_strategies)())
    names = [n for n in PORTFOLIO_PRIORITY if n in strategies] or list(strategies)
    sim = engine.simulate(
        [strategies[n] for n in names], bars_5m, enriched_contexts=contexts, priority=names
    )

    signals_by_day: dict[date, int] = {}
    for rec in sim.signals:
        signals_by_day[rec.date] = signals_by_day.get(rec.date, 0) + 1
    by_day: dict[date, list[Any]] = {}
    for trade in sim.trades:
        by_day.setdefault(trade.date, []).append(trade)

    calendar = NSECalendar()
    days: list[DemoDay] = []
    skipped: dict[date, str] = {}
    for d in sorted(by_day):
        if (start and d < start) or (end and d > end):
            continue
        if require_replayable:
            problem = _replayable_problem(clean_5m, clean_1d, d, calendar)
            if problem:
                skipped[d] = problem
                continue
        trades = sorted(by_day[d], key=lambda t: (t.entry_time, t.trade_id))
        days.append(
            DemoDay(
                session_date=d,
                strategies=tuple(t.strategy_name for t in trades),
                trades=len(trades),
                first_signal_candle_open=min(t.signal_time for t in trades if t.signal_time),
                first_entry_time=trades[0].entry_time,
                directions=tuple(
                    t.direction.value if hasattr(t.direction, "value") else str(t.direction)
                    for t in trades
                ),
                exit_reasons=tuple(t.exit_reason for t in trades),
                points_pnl=round(sum(t.points_pnl for t in trades), 2),
                raw_signals=signals_by_day.get(d, 0),
            )
        )
    return DemoScan(days=days, skipped=skipped) if return_scan else days


def first_demo_day(*args: Any, **kwargs: Any) -> DemoDay | None:
    """The earliest replayable date with an accepted trade (``--replay auto``), or None."""
    kwargs["return_scan"] = False
    days = find_demo_days(*args, **kwargs)
    return days[0] if days else None  # type: ignore[index]


def _replayable_problem(
    clean_5m: pd.DataFrame, clean_1d: pd.DataFrame, d: date, calendar: NSECalendar
) -> str | None:
    """Why ``d`` cannot be replayed (None when it can)."""
    if not calendar.is_entry_session_supported(d):
        return "not a supported NSE session"
    if int((clean_5m["timestamp"].dt.date == d).sum()) < MIN_SESSION_BARS:
        return f"fewer than {MIN_SESSION_BARS} clean 5m bars on the date"
    prev = d
    for _ in range(14):
        prev -= timedelta(days=1)
        if calendar.is_trading_day(prev):
            break
    else:
        return "no previous trading day"
    try:
        warmup_frames(clean_5m, clean_1d, d, prev)
    except ReplayProviderError as exc:
        return str(exc)
    return None


__all__ = [
    "DemoDay",
    "DemoScan",
    "FaultInjection",
    "OptionModelParams",
    "QUOTE_SOURCE",
    "REPLAY_LABEL",
    "ReplayProvider",
    "ReplayProviderError",
    "ReplayStep",
    "SIM_PREFIX",
    "SIM_SCRIP_BASE",
    "SPOT_SOURCE",
    "SimulatedOptionModel",
    "build_warmup",
    "clean_history",
    "find_demo_days",
    "first_demo_day",
    "read_master_lot_size",
    "warmup_frames",
]
