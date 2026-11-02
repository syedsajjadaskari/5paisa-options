"""SYNTHETIC fakes + harness for the test_engine_* tests.  No network, no broker, no secrets.

Everything produced here is SYNTHETIC test data (prices, quotes, candles, signals); it is never
market data.  The harness drives a real ``SessionEngine`` with a ``ManualClock``.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from test_core_helpers import TRADE_DATE, at, cfg, standard_contracts

from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar, Signal
from trading_platform.live.authorization import AuthDecision
from trading_platform.live.engine import SessionEngine
from trading_platform.live.journal import EventJournal, TradeCsvJournal
from trading_platform.live.models import (
    IST,
    CandleSource,
    CompletedCandle,
    DailyOhlc,
    ExecutionTier,
    OptionQuote,
    RunMode,
    SpotTick,
    WarmupData,
)
from trading_platform.live.providers.base import ManualClock, PollResult
from trading_platform.live.snapshot import SnapshotWriter
from trading_platform.strategies.base import BaseStrategy, StrategyContext

SYNTH = "SYNTHETIC"
BASE_PRICE = 25850.0  # ATM strike in the standard test chain
LABEL_RESEARCH = "RESEARCH_PAPER_PROVISIONAL"
LABEL_REPLAY = "REPLAY_SIMULATED"
LABEL_SHADOW = "SHADOW_ONLY"


# --------------------------------------------------------------------------- strategies


class ScriptedStrategy(BaseStrategy):
    """Emits scripted signals on named candles.  SYNTHETIC; implements the strategy protocol."""

    def __init__(
        self,
        name: str,
        script: dict[str, dict[str, Any]] | None = None,
        method: str = "METHOD_1",
    ) -> None:
        super().__init__(name, {})
        self.method = method
        self.script = dict(script or {})
        self.seen: list[datetime] = []
        self.reset_calls = 0
        self.evaluated = 0

    def prepare(self, data: list[Bar] | None, config: dict[str, Any]) -> None:
        return None

    def reset_state(self) -> None:
        super().reset_state()
        self.reset_calls += 1
        self.seen.clear()

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        context.add_bar(bar)
        return self.generate_signal(context)

    def generate_signal(self, context: StrategyContext) -> Signal | None:
        bar = context.latest_bar
        assert bar is not None
        self.evaluated += 1
        self.seen.append(bar.exchange_time)
        spec = self.script.get(f"{bar.exchange_time:%H:%M}")
        if spec is None:
            return None
        direction = spec.get("direction", SignalDirection.SELL)
        stop_pts = spec.get("stop_points", 40.0)
        target_pts = spec.get("target_points", 70.0)
        ref = bar.close
        sign = 1.0 if direction == SignalDirection.BUY else -1.0
        return Signal(
            strategy_name=self.name,
            method_id=f"{self.name}_SYNTHETIC",
            symbol="NIFTY",
            direction=direction,
            timestamp=bar.exchange_time,
            reference_price=ref,
            stop_loss=round(ref - sign * stop_pts, 2),
            target=round(ref + sign * target_pts, 2),
            reason=f"{SYNTH} scripted {self.name} signal",
            metadata={
                "method": self.method,
                "stop_points": stop_pts,
                "target_points": target_pts,
                "conditions_satisfied": ["synthetic_condition_a", "synthetic_condition_b"],
                "conditions_failed": [],
                "levels": {"pdh": 26000.0, "pdl": 25700.0},
                "trigger_candle": {
                    "o": bar.open,
                    "h": bar.high,
                    "l": bar.low,
                    "c": bar.close,
                    "time": bar.exchange_time.isoformat(),
                },  # fmt: skip
            },
        )

    def evaluation_snapshot(self, day_key: Any, context: StrategyContext | None = None) -> dict:
        return {
            "state": "MONITORING",
            "conditions_satisfied": ["synthetic_condition_a"],
            "conditions_failed": ["synthetic_condition_z"],
            "levels": {"pdh": 26000.0},
            "state_reason": "SYNTHETIC",
        }


class RaisingStrategy(ScriptedStrategy):
    """A strategy that must never be evaluated (RF) or that blows up (error isolation)."""

    def __init__(self, name: str, *, boom_at: str | None = None) -> None:
        super().__init__(name)
        self.boom_at = boom_at

    def on_bar(self, context: StrategyContext, bar: Bar) -> Signal | None:
        self.evaluated += 1
        if self.boom_at is None or f"{bar.exchange_time:%H:%M}" == self.boom_at:
            raise RuntimeError(f"{self.name} must not run / SYNTHETIC failure")
        return None


# --------------------------------------------------------------------------- levels builder


class FakeBuilder:
    """Implements the agreed LiveReferenceBuilder contract with fixed SYNTHETIC levels."""

    problem_list: list[str] = []
    instances: list[FakeBuilder] = []

    def __init__(self, warmup: WarmupData, trading_date: date) -> None:
        self.trading_date = trading_date
        self.completed: list[datetime] = []
        self.state_calls = 0
        FakeBuilder.instances.append(self)

    def problems(self) -> list[str]:
        return list(type(self).problem_list)

    def state_for_bar(self, bar: Bar) -> dict[str, Any]:
        self.state_calls += 1
        return {
            "pdh": 26000.0,
            "pdl": 25700.0,
            "prev_close": 25820.0,
            "pd_res_body": 25980.0,
            "pd_supp_body": 25720.0,
            "pd_supp_body_top": 25740.0,
            "ema_200": 25800.0,
            "levels_date": self.trading_date.isoformat(),
        }

    def on_bar_completed(self, bar: Bar) -> None:
        if bar.exchange_time not in self.completed:
            self.completed.append(bar.exchange_time)

    def display_levels(self) -> dict[str, Any]:
        return {
            "PDH": 26000.0, "PDL": 25700.0, "PDC": 25820.0,
            "support_zone": [25700.0, 25740.0], "resistance_zone": [25980.0, 26000.0],
            "IRH": None, "IRL": None, "EMA200": 25800.0, "sources": {}, "notes": SYNTH,
        }  # fmt: skip

    def restore_today(self, bars: list[Bar]) -> None:
        for b in bars:
            self.on_bar_completed(b)


def make_warmup() -> WarmupData:
    prev = DailyOhlc(date(2026, 10, 27), 25800.0, 26000.0, 25700.0, 25820.0)
    return WarmupData(previous_session=prev, daily_bars=[], intraday_bars=[], source_notes=[SYNTH])


# --------------------------------------------------------------------------- builders


def auth_for(
    name: str, tier: ExecutionTier = ExecutionTier.RESEARCH_PAPER, label: str | None = None
) -> AuthDecision:
    if label is None:
        label = {
            ExecutionTier.RESEARCH_PAPER: LABEL_RESEARCH,
            ExecutionTier.SHADOW_ONLY: LABEL_SHADOW,
            ExecutionTier.DISABLED: "DISABLED",
            ExecutionTier.AUTHOR_APPROVED: "AUTHOR_APPROVED",
        }[tier]
    return AuthDecision(tier=tier, label=label, reasons=(SYNTH,), strategy=name)


def candle(
    hh: int,
    mm: int,
    o: float = BASE_PRICE,
    h: float | None = None,
    lo: float | None = None,
    c: float | None = None,
    source: CandleSource = CandleSource.PROVIDER_COMPLETED,
) -> CompletedCandle:
    c = o if c is None else c
    return CompletedCandle(
        open_time=at(hh, mm),
        open=o,
        high=max(o, c, h if h is not None else max(o, c) + 5),
        low=min(o, c, lo if lo is not None else min(o, c) - 5),
        close=c,
        volume=1000,
        source=source,
    )


def spot_tick(price: float, t: datetime, **kw: Any) -> SpotTick:
    return SpotTick(price=price, exchange_time=kw.pop("exchange_time", t), received_at=t, **kw)


def quote(
    scrip: int,
    bid: float,
    ask: float,
    t: datetime,
    *,
    symbol: str = "NIFTY SYNTHETIC",
    ask_qty: int = 5000,
) -> OptionQuote:
    return OptionQuote(
        scrip_code=scrip, symbol=symbol, bid=bid, ask=ask, bid_qty=5000, ask_qty=ask_qty,
        server_time=t, received_at=t,
    )  # fmt: skip


def with_account(c, **kw):
    return replace(c, account=replace(c.account, **kw))


def with_exec(c, **kw):
    return replace(c, execution=replace(c.execution, **kw))


# --------------------------------------------------------------------------- harness


class Harness:
    """A real SessionEngine on fakes: ManualClock, tmp journals, scripted strategies."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        strategies: dict[str, BaseStrategy] | None = None,
        tiers: dict[str, ExecutionTier] | None = None,
        mode: RunMode = RunMode.LIVE,
        config: Any = None,
        start_at: datetime | None = None,
        label: str | None = None,
        problems: list[str] | None = None,
        root_name: str = "rt",
        clock: ManualClock | None = None,
        min_interval: float = 0.0,
    ) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / root_name
        self.cfg = config or cfg()
        self.mode = mode
        self.clock = clock or ManualClock(start_at or at(9, 10))
        FakeBuilder.problem_list = list(problems or [])
        FakeBuilder.instances = []
        self.strategies = strategies or {
            "PDS": ScriptedStrategy("PDS", {"10:00": {"direction": SignalDirection.SELL}})
        }
        default_tier = ExecutionTier.RESEARCH_PAPER
        tiers = tiers or {}
        self.auth: dict[str, AuthDecision] = {}
        for name in self.strategies:
            tier = tiers.get(name, default_tier)
            lab = label
            if lab is None and mode == RunMode.REPLAY and tier != ExecutionTier.DISABLED:
                lab = LABEL_REPLAY
            self.auth[name] = auth_for(name, tier, lab)
        self.trades = TradeCsvJournal(self.root / "journal" / "trades.csv")
        self.events = EventJournal(self.root / "journal")
        self.ckpt = self.root / "checkpoint.json"
        self.snap_path = self.root / "live_state.json"
        self.writer = SnapshotWriter(
            self.snap_path, session_id="SYNTH-S1", session_date=TRADE_DATE.isoformat(),
            mode=mode.value, label=label or LABEL_RESEARCH, min_interval_seconds=min_interval,
        )  # fmt: skip
        self.logger = logging.getLogger(f"engine-test-{id(self)}")
        self.logger.propagate = False
        self.contracts = standard_contracts()
        self.engine = self.new_engine()
        self.spot_price = BASE_PRICE

    def new_engine(self, *, clock: ManualClock | None = None, **over: Any) -> SessionEngine:
        kw: dict[str, Any] = dict(
            clock=clock or self.clock,
            mode=self.mode,
            strategies=self.strategies,
            auth=self.auth,
            contracts=self.contracts,
            warmup=make_warmup(),
            levels_builder_cls=FakeBuilder,
            trades_journal=self.trades,
            events=self.events,
            checkpoint_path=self.ckpt,
            snapshot_writer=self.writer,
            error_logger=self.logger,
            session_id="SYNTH-S1",
            provider_info={"provider": "SYNTHETIC-FAKE"},
            label_for_records=LABEL_REPLAY if self.mode == RunMode.REPLAY else LABEL_RESEARCH,
        )
        kw.update(over)
        return SessionEngine(self.cfg, **kw)

    # ---- time
    def set(self, t: datetime) -> None:
        self.clock.set(t)

    # ---- drivers
    def start(self):
        return self.engine.start_session(TRADE_DATE)

    def warm(self, upto: tuple[int, int] = (10, 0), start: tuple[int, int] = (9, 15)) -> list:
        """Feed flat candles from ``start`` up to (excluding) ``upto``; returns all events."""
        out: list = []
        t = at(*start)
        stop = at(*upto)
        while t < stop:
            out += self.send_candle(t.hour, t.minute)
            t += timedelta(minutes=5)
        return out

    def send_candle(self, hh: int, mm: int, *, delay: float = 2.0, **kw: Any) -> list:
        c = candle(hh, mm, **kw)
        self.set(c.close_time + timedelta(seconds=delay))
        return self.engine.on_candle(c)

    def contract(self, side: str):
        """The ATM-at-BASE_PRICE contract of ``side`` (fixed, so quotes keep matching it)."""
        ce, pe = self.engine.resolver.candidate_pair(BASE_PRICE, TRADE_DATE, self.clock.now())
        return ce if side == "CE" else pe

    def poll(
        self,
        t: datetime,
        spot: float | None = None,
        *,
        bid: float | None = None,
        ask: float | None = None,
        side: str | None = None,
        scrip: int | None = None,
        quote_t: datetime | None = None,
        spot_t: datetime | None = None,
        extra: dict[int, OptionQuote] | None = None,
        errors: list[str] | None = None,
        no_spot: bool = False,
    ) -> list:
        self.set(t)
        if spot is not None:
            self.spot_price = spot
        tick = None if no_spot else spot_tick(self.spot_price, spot_t or t)
        quotes: dict[int, OptionQuote] = dict(extra or {})
        if bid is not None and ask is not None:
            code = scrip if scrip is not None else self.contract(side or "PE").scrip_code
            quotes[code] = quote(code, bid, ask, quote_t or t)
        return self.engine.on_poll(PollResult(spot=tick, quotes=quotes, errors=errors or []))

    def tick(self, t: datetime) -> list:
        self.set(t)
        return self.engine.on_tick_clock()

    # ---- readers
    def rows(self) -> list[dict[str, str]]:
        return self.trades.read_trades().rows

    def event_kinds(self) -> list[str]:
        return [e["kind"] for e in self.events.read_events(TRADE_DATE)]

    def journal_events(self, kind: str) -> list[dict]:
        return [e for e in self.events.read_events(TRADE_DATE) if e["kind"] == kind]


def kinds(events: list) -> list[str]:
    return [e.kind for e in events]


def find(events: list, kind: str) -> list:
    return [e for e in events if e.kind == kind]


def open_trade(h: Harness, side: str = "PE", *, ask: float = 100.0, bid: float = 99.0):
    """Warm up, fire a 10:00 signal and fill it at 10:05:03.  Returns (signal events, fill events)."""
    direction = SignalDirection.BUY if side == "CE" else SignalDirection.SELL
    name = next(iter(h.strategies))
    h.strategies[name].script = {"10:00": {"direction": direction}}
    h.start()
    h.warm((10, 0))
    h.poll(at(9, 59, 58), BASE_PRICE)
    ev1 = h.send_candle(10, 0, delay=1.0)  # closes 10:05 -> engine clock 10:05:01
    ev2 = h.poll(at(10, 5, 3), BASE_PRICE, bid=bid, ask=ask, side=side)
    return ev1, ev2


__all__ = [
    "IST", "Harness", "ScriptedStrategy", "RaisingStrategy", "FakeBuilder", "auth_for", "candle",
    "spot_tick", "quote", "kinds", "find", "open_trade", "with_account", "with_exec",
    "LABEL_RESEARCH", "LABEL_REPLAY", "LABEL_SHADOW", "BASE_PRICE", "TRADE_DATE", "at", "SYNTH",
]  # fmt: skip
