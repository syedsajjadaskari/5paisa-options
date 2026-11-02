"""SYNTHETIC fakes for the test_app_* tests.  No network, no broker, no secrets, not market data.

``SyntheticLiveProvider`` plays a scripted trading day for the LIVE code path of the app: the
ManualClock advances by ``step_s`` on every loop poll, candles are generated from a scripted
spot function (``PROVIDER_COMPLETED`` source, as the real provider would label proven-complete
candles) and option books follow 80 + intrinsic with a 1-point spread.  Every price here is
fabricated for tests.
"""

from __future__ import annotations

import io
import os
import signal
import threading
from collections.abc import Callable
from concurrent.futures import Future
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from engine_fakes import BASE_PRICE, FakeBuilder, ScriptedStrategy
from rich.console import Console
from test_core_helpers import TRADE_DATE, at, standard_contracts

from trading_platform.core.enums import SignalDirection
from trading_platform.live.app import AppHooks, main
from trading_platform.live.authorization import RESEARCH_PAPER_ACK
from trading_platform.live.config import DEFAULT_CONFIG_PATH
from trading_platform.live.models import (
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
from trading_platform.live.providers.base import CheckResult, ManualClock, PollResult

SYNTH = "SYNTHETIC"


class FakeTime:
    """monotonic()/sleep() pair: sleeping advances the monotonic clock instantly."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


class InlineExecutor:
    """Runs submitted work immediately in the caller (deterministic stand-in for the worker)."""

    def __init__(self) -> None:
        self.submitted = 0

    def submit(self, fn: Callable[..., Any], *args: Any) -> Future:
        self.submitted += 1
        fut: Future = Future()
        try:
            fut.set_result(fn(*args))
        except BaseException as exc:  # noqa: BLE001
            fut.set_exception(exc)
        return fut

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        return None


def synthetic_warmup(day: date = TRADE_DATE) -> WarmupData:
    prev = DailyOhlc(day - timedelta(days=1), 25800.0, 26000.0, 25700.0, 25820.0)
    return WarmupData(prev, [object()], [object()], [f"{SYNTH} warm-up placeholder"])


def flat_then_slide(
    slide_from: datetime | None = None, slope_per_min: float = -4.0, floor: float = 25700.0
) -> Callable[[datetime], float]:
    """Flat at BASE_PRICE, optionally sliding down from ``slide_from``."""

    def spot(t: datetime) -> float:
        if slide_from is None or t <= slide_from:
            return BASE_PRICE
        minutes = (t - slide_from).total_seconds() / 60.0
        return max(floor, BASE_PRICE + slope_per_min * minutes)

    return spot


class SyntheticLiveProvider:
    """A scripted LIVE-mode provider (see module docstring)."""

    mode = RunMode.LIVE
    connection_status = "CONNECTED"

    def __init__(
        self,
        clock: ManualClock,
        *,
        day: date = TRADE_DATE,
        step_s: float = 15.0,
        spot_fn: Callable[[datetime], float] | None = None,
        candle_delay_s: float = 1.0,
        withhold_candles_from: datetime | None = None,
        login_ok: bool = True,
        login_detail: str = "SYNTHETIC login ok",
        warmup_error: str | None = None,
        no_contracts: bool = False,
        stop_at: datetime | None = None,
        stop_event: threading.Event | None = None,
        sigint_at: datetime | None = None,
        signal_num: int = signal.SIGINT,
        signal_repeat: int = 1,
        extra_checks: list[CheckResult] | None = None,
        spot_age_s: float = 0.0,
    ) -> None:
        self.clock = clock
        self.day = day
        self.step = timedelta(seconds=step_s)
        self.spot_fn = spot_fn or flat_then_slide()
        self.candle_delay = timedelta(seconds=candle_delay_s)
        self.withhold_from = withhold_candles_from
        self.login_ok = login_ok
        self.login_detail = login_detail
        self.warmup_error = warmup_error
        self.no_contracts = no_contracts
        self.stop_at = stop_at
        self.stop_event = stop_event
        self.sigint_at = sigint_at
        self.signal_num = signal_num
        self.signal_repeat = signal_repeat
        self.candle_threads: list[str] = []
        self.extra_checks = extra_checks or []
        self.spot_age = timedelta(seconds=spot_age_s)
        self.contracts = standard_contracts()
        self._by_code: dict[int, ContractSpec] = {c.scrip_code: c for c in self.contracts}
        self.armed = False
        self.sigint_sent = False
        self.polls = 0
        self.loop_polls = 0
        self.candle_calls = 0
        self.forming_calls = 0
        self.closed = False
        self.candle_diagnostics: dict[str, Any] = {"summary": f"{SYNTH}: candles ok"}

    # ---- startup

    def startup_checks(self, trading_date: date) -> list[CheckResult]:
        out = [
            CheckResult("login", self.login_ok, self.login_detail),
            CheckResult(
                "token_valid", self.login_ok, f"{SYNTH} token ok" if self.login_ok else "skipped"
            ),
        ]
        return out + list(self.extra_checks)

    def previous_session_and_warmup(self, trading_date: date) -> WarmupData:
        if self.warmup_error:
            raise RuntimeError(self.warmup_error)
        return synthetic_warmup(trading_date)

    def load_contracts(self, trading_date: date) -> list[ContractSpec]:
        return [] if self.no_contracts else list(self.contracts)

    # ---- data

    def _premium(self, c: ContractSpec, spot: float) -> float:
        intrinsic = (
            max(0.0, spot - c.strike) if c.option_type == "CE" else max(0.0, c.strike - spot)
        )
        return max(0.05, 80.0 + intrinsic)

    def poll(self, watch_scrips: list[int]) -> PollResult:
        self.polls += 1
        if self.armed:
            self.loop_polls += 1
            self.clock.set(self.clock.now() + self.step)
        now = self.clock.now()
        if self.sigint_at and not self.sigint_sent and now >= self.sigint_at:
            self.sigint_sent = True
            for _ in range(self.signal_repeat):
                os.kill(os.getpid(), self.signal_num)
        if self.stop_at and self.stop_event is not None and now >= self.stop_at:
            self.stop_event.set()
        spot = self.spot_fn(now)
        tick = SpotTick(
            price=spot,
            exchange_time=now - self.spot_age,
            received_at=now,
            source=SYNTH,
        )
        quotes: dict[int, OptionQuote] = {}
        for code in watch_scrips:
            c = self._by_code.get(code)
            if c is None:
                continue
            p = self._premium(c, spot)
            quotes[code] = OptionQuote(
                code,
                c.symbol,
                round(max(0.05, p - 0.5), 2),
                round(p + 0.5, 2),
                1000,
                1000,
                now,
                now,
                SYNTH,
            )
        return PollResult(spot=tick, quotes=quotes)

    def _candle(self, open_time: datetime) -> CompletedCandle:
        pts = [open_time + timedelta(seconds=s) for s in range(0, 300, 30)] + [
            open_time + timedelta(seconds=299)
        ]
        px = [self.spot_fn(t) for t in pts]
        return CompletedCandle(
            open_time, px[0], max(px), min(px), px[-1], 1000, CandleSource.PROVIDER_COMPLETED
        )

    def completed_candles(self, trading_date: date, as_of: datetime) -> list[CompletedCandle]:
        self.candle_calls += 1
        self.candle_threads.append(threading.current_thread().name)
        self.armed = True
        out: list[CompletedCandle] = []
        t = datetime(trading_date.year, trading_date.month, trading_date.day, 9, 15, tzinfo=IST)
        while (
            t + timedelta(minutes=5) + self.candle_delay <= as_of
            and t.hour * 60 + t.minute < 15 * 60 + 30
        ):
            if self.withhold_from is None or t < self.withhold_from:
                out.append(self._candle(t))
            t += timedelta(minutes=5)
        return out

    def sampled_forming_candle(self) -> CompletedCandle | None:
        self.forming_calls += 1
        return None  # display only; a real one would be SAMPLED_TICKS

    def close(self) -> None:
        self.closed = True


# --------------------------------------------------------------------------- run helpers


def scripted_strategies(
    orb_script: dict[str, dict[str, Any]] | None = None,
) -> Callable[[], dict[str, ScriptedStrategy]]:
    """Factory of the four strategy objects; only ORB signals (bearish at 10:00 by default)."""
    script = (
        orb_script if orb_script is not None else {"10:00": {"direction": SignalDirection.SELL}}
    )

    def build() -> dict[str, ScriptedStrategy]:
        return {
            "PDS": ScriptedStrategy("PDS"),
            "ORB": ScriptedStrategy("ORB", dict(script)),
            "IRF": ScriptedStrategy("IRF", method="METHOD_2"),
            "PDR": ScriptedStrategy("PDR", method="RETEST_BOUNCE"),
        }

    return build


def write_config(
    tmp_path: Path,
    *,
    research: list[str] | None = None,
    ack: str | None = None,
    candles: dict[str, float] | None = None,
    name: str = "live_test.yaml",
) -> Path:
    """Copy configs/live_paper.yaml with test edits (research opt-in, candle timings)."""
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    if research is not None:
        raw["research_paper"] = {
            "enabled": True,
            "acknowledgement": RESEARCH_PAPER_ACK if ack is None else ack,
            "strategies": research,
        }
    if candles:
        raw["candles"].update(candles)
    path = tmp_path / name
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=170, force_terminal=False, color_system=None), buf


class Run:
    """One in-process ``main()`` invocation with injected fakes and captured output."""

    def __init__(
        self,
        tmp_path: Path,
        argv: list[str] | None = None,
        *,
        provider: SyntheticLiveProvider | None = None,
        clock: ManualClock | None = None,
        start: datetime | None = None,
        config: Path | None = None,
        signals: bool = False,
        strategies: Callable[[], dict[str, Any]] | None = None,
        stop_event: threading.Event | None = None,
        provider_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.tmp = tmp_path
        self.runtime = tmp_path / "runtime"
        self.clock = clock or ManualClock(start or at(9, 56))
        self.stop_event = stop_event or threading.Event()
        self.provider = provider
        self.factory_calls = 0
        self.time = FakeTime()
        self.console, self.buf = make_console()
        self.executor = InlineExecutor()
        args = list(argv or [])
        args += ["--runtime-dir", str(self.runtime), "--no-ui"]
        if config is not None:
            args += ["--config", str(config)]
        self.argv = args
        self.hooks = AppHooks(
            sleep_fn=self.time.sleep,
            monotonic_fn=self.time.monotonic,
            executor_factory=lambda: self.executor,
            install_signals=signals,
            stop_event=self.stop_event,
            strategies_factory=strategies or scripted_strategies(),
            levels_builder_cls=FakeBuilder,
        )
        self._factory = provider_factory
        self.rc: int | None = None

    def _provider_factory(self, **kw: Any) -> Any:
        self.factory_calls += 1
        if self._factory is not None:
            return self._factory(**kw)
        assert self.provider is not None
        return self.provider

    def go(self) -> int:
        self.rc = main(
            self.argv,
            provider_factory=self._provider_factory,
            clock=self.clock,
            console=self.console,
            hooks=self.hooks,
        )
        return self.rc

    @property
    def out(self) -> str:
        return self.buf.getvalue()

    @property
    def flat(self) -> str:
        """Console text with box-drawing removed and whitespace collapsed (for phrase checks)."""
        import re

        text = re.sub(r"[│╭╮╰╯─┃┏┓┗┛━]", " ", self.buf.getvalue())
        return re.sub(r"\s+", " ", text)

    # ---- files
    @property
    def trades_csv(self) -> Path:
        return self.runtime / "journal" / "trades.csv"

    @property
    def checkpoint(self) -> Path:
        return self.runtime / "checkpoint.json"

    @property
    def snapshot(self) -> Path:
        return self.runtime / "live_state.json"

    def events(self, day: date = TRADE_DATE) -> list[dict[str, Any]]:
        import json

        p = self.runtime / "journal" / f"events_{day:%Y%m%d}.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]

    def event_kinds(self, day: date = TRADE_DATE) -> list[str]:
        return [e["kind"] for e in self.events(day)]


# --------------------------------------------------------------------------- fixtures


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def spy_sdk_logging(monkeypatch):
    """Never re-point the process-global loguru sink from a test; record the call instead."""
    from trading_platform.core import redaction

    calls: list[Any] = []

    def fake(path=None):
        calls.append(path)
        return True

    monkeypatch.setattr(redaction, "silence_sdk_logging", fake)
    return calls
