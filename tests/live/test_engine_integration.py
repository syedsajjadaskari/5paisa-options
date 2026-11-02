"""Engine + REAL strategies + REAL LiveReferenceBuilder over verified historical NIFTY days.

Everything on the OPTION side is SYNTHETIC/SIMULATED (premium = 80 + intrinsic, 1-point
spread, REPLAY_SIMULATED label, separate runtime root).  The underlying candles come from the
verified historical parquet dataset (read-only).  No network, no broker, no secrets.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

from trading_platform.backtesting.portfolio_runner import (  # noqa: E402
    build_authorized_strategies,
    load_nifty_bars,
)
from trading_platform.live.authorization import AuthDecision  # noqa: E402
from trading_platform.live.checkpoint import RecoveryAction, load_checkpoint, recovery_decision  # noqa: E402
from trading_platform.live.config import load_live_config  # noqa: E402
from trading_platform.live.engine import SessionEngine  # noqa: E402
from trading_platform.live.journal import EventJournal, TradeCsvJournal  # noqa: E402
from trading_platform.live.levels import LiveReferenceBuilder  # noqa: E402
from trading_platform.live.models import (  # noqa: E402
    IST,
    CandleSource,
    CompletedCandle,
    ContractSpec,
    DailyOhlc,
    ExecutionTier,
    OptionQuote,
    RunMode,
    SpotTick,
    WarmupData,
)
from trading_platform.live.providers.base import ManualClock, PollResult  # noqa: E402
from trading_platform.live.snapshot import SnapshotWriter  # noqa: E402

BASE = Path(__file__).resolve().parents[2]
PARQUET = BASE / "data" / "processed" / "nifty" / "5m" / "nifty50_5m.parquet"
pytestmark = pytest.mark.skipif(not PARQUET.exists(), reason="verified historical dataset absent")

DAYS = ["2025-10-01", "2025-10-24", "2025-11-20", "2025-12-12", "2026-03-09", "2026-06-10"]
REPLAY = "REPLAY_SIMULATED"


@pytest.fixture(scope="module")
def history():
    return load_nifty_bars(BASE)


def chain(day: date, anchor: float) -> list[ContractSpec]:
    exp = day + timedelta(days=(3 - day.weekday()) % 7 + 7)
    out, code = [], 7000
    centre = int(anchor // 50 * 50)
    for k in range(centre - 1500, centre + 1500, 50):
        for t in ("CE", "PE"):
            out.append(
                ContractSpec(code, f"NIFTY {exp:%d %b %Y} {t} {k:.2f}", exp, float(k), t, 75)
            )
            code += 1
    return out


class Sim:
    """Drives one engine over historical bars with SIMULATED option quotes."""

    def __init__(self, history, day: date, root: Path, clock: ManualClock | None = None):
        bars_1d, bars_5m = history
        self.day = day
        prior5 = [b for b in bars_5m if b.exchange_time.date() < day]
        prior1d = [b for b in bars_1d if b.exchange_time.date() < day]
        self.today = [
            b for b in bars_5m
            if b.exchange_time.date() == day
            and (b.exchange_time.hour, b.exchange_time.minute) < (15, 30)
        ]  # fmt: skip
        last = prior1d[-1]
        warm = WarmupData(
            DailyOhlc(last.exchange_time.date(), last.open, last.high, last.low, last.close),
            prior1d, prior5, ["SYNTHETIC-TEST"],
        )  # fmt: skip
        contracts = chain(day, self.today[0].open)
        self.by_code = {c.scrip_code: c for c in contracts}
        self.clock = clock or ManualClock(datetime(day.year, day.month, day.day, 9, 10, tzinfo=IST))
        strategies = build_authorized_strategies()
        auth = {
            n: AuthDecision(ExecutionTier.RESEARCH_PAPER, REPLAY, ("test",), n) for n in strategies
        }
        log = logging.getLogger("engine-integration")
        log.propagate = False
        self.eng = SessionEngine(
            load_live_config(), clock=self.clock, mode=RunMode.REPLAY, strategies=strategies,
            auth=auth, contracts=contracts, warmup=warm, levels_builder_cls=LiveReferenceBuilder,
            trades_journal=TradeCsvJournal(root / "journal" / "trades.csv"),
            events=EventJournal(root / "journal"), checkpoint_path=root / "checkpoint.json",
            snapshot_writer=SnapshotWriter(root / "live_state.json", mode="REPLAY", label=REPLAY),
            error_logger=log, session_id=f"SIM-{day}", label_for_records=REPLAY,
        )  # fmt: skip
        self.events: list = []

    def start(self):
        res = self.eng.start_session(self.day)
        self.events.extend(self.eng.start_events)
        return res

    @staticmethod
    def candle_of(b) -> CompletedCandle:
        return CompletedCandle(
            b.exchange_time, b.open, b.high, b.low, b.close, int(b.volume),
            CandleSource.SIMULATED_REPLAY,
        )  # fmt: skip

    def poll(self, t: datetime, spot: float) -> None:
        self.clock.set(t)
        quotes = {}
        for s in self.eng.watch_scrips():
            c = self.by_code[s]
            intrinsic = (
                max(0.0, spot - c.strike) if c.option_type == "CE" else max(0.0, c.strike - spot)
            )
            p = 80.0 + intrinsic
            quotes[s] = OptionQuote(
                s, c.symbol, round(p - 0.5, 2), round(p + 0.5, 2), 1000, 1000, t, t, "SIMULATED"
            )  # fmt: skip
        self.events.extend(
            self.eng.on_poll(PollResult(SpotTick(spot, t, t, source="SIMULATED"), quotes))
        )

    def drive(self, bars) -> None:
        for b in bars:
            t0 = b.exchange_time
            path = (
                [b.open, b.low, b.high, b.close]
                if b.close >= b.open
                else [b.open, b.high, b.low, b.close]
            )
            for sec, px in zip((1, 80, 160, 298), path, strict=True):
                self.poll(t0 + timedelta(seconds=sec), px)
            self.clock.set(t0 + timedelta(minutes=5, seconds=2))
            self.events.extend(self.eng.on_candle(self.candle_of(b)))
            self.poll(t0 + timedelta(minutes=5, seconds=3), b.close)
            self.events.extend(self.eng.on_tick_clock())


def replay_day(history, day: date, root: Path):
    sim = Sim(history, day, root)
    start = sim.start()
    sim.drive(sim.today)
    return sim.eng, start, sim.events


def test_real_strategies_and_levels_replay_invariants(tmp_path, history):
    root = tmp_path / "replay"
    total_signals = 0
    cumulative = 0.0
    for day_s in DAYS:
        day = date.fromisoformat(day_s)
        eng, start, events = replay_day(history, day, root)
        assert start.ok, start
        assert not [e for e in events if e.kind == "ERROR"], [
            e.text for e in events if e.kind == "ERROR"
        ]
        assert not [e for e in events if e.kind == "CANDLE_BLOCKED"]
        total_signals += len([e for e in events if e.kind == "SIGNAL"])
        assert eng.position is None and eng.pending is None  # everything resolved by 15:15
        rows = eng.trades_journal.read_trades().rows
        cumulative = sum(float(r["net_pnl"]) for r in rows)
        assert eng.account.cash == pytest.approx(250000.0 + cumulative, abs=0.02)
        for r in rows:
            assert r["label"] == REPLAY and r["session_id"].startswith("SIM-")
            assert float(r["gross_pnl"]) - float(r["total_charges"]) == pytest.approx(
                float(r["net_pnl"]), abs=0.011
            )
            assert (
                datetime.fromisoformat(r["entry_time"]).time() < datetime(2000, 1, 1, 14, 30).time()
            )
            assert r["exit_reason"] in {"TARGET", "STOP", "EOD", "RISK"}
        snap = eng.snapshot()
        states = {s["name"]: s["state"] for s in snap["strategies"]}
        assert states.pop("RF") == "DISABLED"
        assert set(states.values()) <= {"MONITORING", "DONE_FOR_DAY", "WARMING_UP"}
        assert snap["levels"]["PDH"] is not None and snap["levels"]["IRH"] is not None
        # the final checkpoint is consistent with the ledger on every day
        nxt = day + timedelta(days=1)
        decision = recovery_decision(
            load_checkpoint(root / "checkpoint.json"), rows, today=nxt,
            now=datetime(nxt.year, nxt.month, nxt.day, 9, 0, tzinfo=IST), run_mode="REPLAY",
        )  # fmt: skip
        assert decision.action == RecoveryAction.START_FRESH, decision.reason
    assert total_signals >= 1  # the chosen days are known to produce signals
    ids = [
        r["trade_id"] for r in TradeCsvJournal(root / "journal" / "trades.csv").read_trades().rows
    ]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("day_s,restart_hm", [("2025-11-20", (10, 30)), ("2026-01-07", (10, 0))])
def test_restart_mid_day_with_restore_candles_matches_a_continuous_run(
    tmp_path, history, day_s, restart_hm
):
    day = date.fromisoformat(day_s)
    cont = Sim(history, day, tmp_path / "continuous")
    cont.start()
    cont.drive(cont.today)

    first = Sim(history, day, tmp_path / "restarted")
    first.start()
    cut = [b for b in first.today if (b.exchange_time.hour, b.exchange_time.minute) < restart_hm]
    rest = [b for b in first.today if b not in cut]
    first.drive(cut)
    # --- new process: fresh strategies, fresh builder, same files; provider re-supplies candles
    second = Sim(
        history, day, tmp_path / "restarted",
        clock=ManualClock(cut[-1].exchange_time + timedelta(minutes=5, seconds=10)),
    )  # fmt: skip
    res = second.start()
    assert res.ok and res.recovery.startswith("RESUME")
    assert second.eng.pending is None
    second.eng.restore_candles([second.candle_of(b) for b in cut])
    second.drive(rest)

    def after(sim):
        t0 = datetime(day.year, day.month, day.day, *restart_hm, tzinfo=IST)
        return [
            e.payload["signal_id"] for e in sim.events
            if e.kind == "SIGNAL" and datetime.fromisoformat(e.payload["candle_open_time"]) >= t0
        ]  # fmt: skip

    assert after(second) == after(cont)
    rows_a = TradeCsvJournal(tmp_path / "continuous" / "journal" / "trades.csv").read_trades().rows
    rows_b = TradeCsvJournal(tmp_path / "restarted" / "journal" / "trades.csv").read_trades().rows
    keys = (
        "trade_id",
        "entry_time",
        "entry_premium",
        "exit_time",
        "exit_premium",
        "exit_reason",
        "net_pnl",
    )
    assert [{k: r[k] for k in keys} for r in rows_b] == [{k: r[k] for k in keys} for r in rows_a]
    assert second.eng.account.cash == pytest.approx(cont.eng.account.cash, abs=0.01)
    a, b = cont.eng.snapshot()["levels"], second.eng.snapshot()["levels"]
    for k in ("PDH", "PDL", "PDC", "IRH", "IRL", "EMA200"):
        assert a[k] == pytest.approx(b[k]), k
