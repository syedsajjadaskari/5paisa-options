"""ReplayProvider on SYNTHETIC history (tmp parquet files): SIMULATED data, no network."""

from __future__ import annotations

import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from test_replay_helpers import (
    EARLY_DAY,
    SYNTH_LOT,
    TEST_DAY,
    at,
    write_dataset,
    write_master,
)

from trading_platform.live.models import CandleSource, RunMode
from trading_platform.live.providers.base import ManualClock, MarketDataProvider
from trading_platform.live.providers.replay import (
    QUOTE_SOURCE,
    SIM_SCRIP_BASE,
    SPOT_SOURCE,
    FaultInjection,
    ReplayProvider,
    ReplayProviderError,
    SimulatedOptionModel,
    read_master_lot_size,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    return write_dataset(tmp_path_factory.mktemp("replay_ds"))


def make(dataset, day: date = TEST_DAY, **kw) -> ReplayProvider:
    p5, p1, pm = dataset
    kw.setdefault("clock", ManualClock(at(9, 0, day=day)))
    return ReplayProvider(day, p5, p1, scrip_master_path=pm, **kw)


@pytest.fixture(scope="module")
def prov(dataset):
    return make(dataset)


# --------------------------------------------------------------------------- protocol


def test_implements_protocol_and_mode(prov):
    assert isinstance(prov, MarketDataProvider)
    assert prov.mode is RunMode.REPLAY
    assert prov.run_label == "REPLAY_SIMULATED"


# --------------------------------------------------------------------------- tick path


def test_tick_path_respects_ohlc_touches_high_low_and_open_close(prov):
    n = prov.ticks_per_bar
    ticks = prov.ticks
    assert len(ticks) == len(prov.candles) * n == 75 * 12
    for i, c in enumerate(prov.candles):
        seg = ticks[i * n : (i + 1) * n]
        prices = [p for _, p in seg]
        assert all(c.low <= p <= c.high for p in prices), c
        assert max(prices) == c.high and min(prices) == c.low
        assert prices[0] == c.open and prices[-1] == c.close
        assert seg[0][0] == c.open_time
        assert all(c.open_time <= t < c.close_time for t, _ in seg)


def test_tick_times_are_evenly_spaced_in_bar(prov):
    n = prov.ticks_per_bar
    seg = prov.ticks[:n]
    gaps = {(b[0] - a[0]).total_seconds() for a, b in zip(seg, seg[1:], strict=False)}
    assert gaps == {300 / n}


def test_intrabar_order_modes(dataset):
    heur = make(dataset)
    low = make(dataset, intrabar_order="LOW_FIRST")
    high = make(dataset, intrabar_order="HIGH_FIRST")
    n = heur.ticks_per_bar
    saw_green = saw_red = False
    for i, c in enumerate(heur.candles):
        if c.high == c.low:
            continue
        for mode, expect_low_first in ((heur, c.close >= c.open), (low, True), (high, False)):
            prices = [p for _, p in mode.ticks[i * n : (i + 1) * n]]
            first_low = prices.index(c.low) < prices.index(c.high)
            assert first_low == expect_low_first
        saw_green |= c.close >= c.open
        saw_red |= c.close < c.open
    assert saw_green and saw_red


def test_ticks_per_bar_configurable_and_validated(dataset):
    p = make(dataset, ticks_per_bar=4)
    assert len(p.ticks) == 75 * 4
    for i, c in enumerate(p.candles):
        prices = [x for _, x in p.ticks[i * 4 : (i + 1) * 4]]
        assert max(prices) == c.high and min(prices) == c.low
    with pytest.raises(ValueError):
        make(dataset, ticks_per_bar=3)
    with pytest.raises(ValueError):
        make(dataset, intrabar_order="WHATEVER")  # type: ignore[arg-type]


def test_deterministic_ticks_contracts_and_quotes(dataset):
    a, b = make(dataset), make(dataset)
    assert a.ticks == b.ticks
    assert a.load_contracts(TEST_DAY) == b.load_contracts(TEST_DAY)
    scrips = [c.scrip_code for c in a.load_contracts(TEST_DAY)[:3]]
    for p in (a, b):
        p.clock.set(at(11, 3, 25))
    assert a.poll(scrips) == b.poll(scrips)


def test_stub_row_1530_is_not_a_candle(prov):
    assert prov.candles[-1].open_time == at(15, 25)
    assert all(c.open_time.time().hour * 60 + c.open_time.minute <= 15 * 60 + 25 for c in prov.candles)


# --------------------------------------------------------------------------- warm-up


def test_warmup_strictly_before_date_and_sizes(prov):
    w = prov.previous_session_and_warmup(TEST_DAY)
    assert len(w.daily_bars) >= 250 and len(w.intraday_bars) >= 600
    assert all(b.exchange_time.date() < TEST_DAY for b in w.daily_bars)
    assert all(b.exchange_time.date() < TEST_DAY for b in w.intraday_bars)
    assert w.previous_session.session_date == date(2026, 2, 10)
    assert w.previous_session.session_date < TEST_DAY
    # 15:30 stubs dropped
    assert all(b.exchange_time.hour * 60 + b.exchange_time.minute <= 15 * 60 + 25 for b in w.intraday_bars)
    times = [b.exchange_time for b in w.intraday_bars]
    assert times == sorted(times)
    assert any("SIMULATED_REPLAY" in n for n in w.source_notes)


def test_warmup_refuses_insufficient_history(dataset):
    p = make(dataset, day=EARLY_DAY)
    with pytest.raises(ReplayProviderError, match="insufficient prior-session 5m history"):
        p.previous_session_and_warmup(EARLY_DAY)
    checks = {c.name: c for c in p.startup_checks(EARLY_DAY)}
    assert checks["warmup"].ok is False
    assert checks["warmup"].detail.startswith("SIMULATED_REPLAY")


def test_wrong_date_is_refused(prov):
    with pytest.raises(ReplayProviderError):
        prov.previous_session_and_warmup(TEST_DAY - timedelta(days=1))
    with pytest.raises(ReplayProviderError):
        prov.load_contracts(TEST_DAY - timedelta(days=1))
    assert prov.completed_candles(TEST_DAY - timedelta(days=1), at(16, 0)) == []
    assert not {c.name: c.ok for c in prov.startup_checks(TEST_DAY - timedelta(days=1))}[
        "trading_date"
    ]


def test_holiday_and_missing_date_refused(dataset):
    p5, p1, pm = dataset
    with pytest.raises(ReplayProviderError, match="not a supported NSE"):
        ReplayProvider(date(2026, 1, 26), p5, p1, scrip_master_path=pm)
    with pytest.raises(ReplayProviderError, match="clean bars"):
        ReplayProvider(date(2026, 2, 12), p5, p1, scrip_master_path=pm)  # no data that day
    with pytest.raises(ReplayProviderError, match="not found"):
        ReplayProvider(TEST_DAY, p5.parent / "nope.parquet", p1, scrip_master_path=pm)


def test_startup_checks_labelled_simulated(prov):
    checks = prov.startup_checks(TEST_DAY)
    assert all(c.ok for c in checks), checks
    assert all(c.detail.startswith("SIMULATED_REPLAY") for c in checks)
    assert {"mode", "warmup", "lot_size", "trading_date"} <= {c.name for c in checks}
    assert not [c for c in checks if c.name == "network"][0].blocking


# --------------------------------------------------------------------------- lot size


def test_lot_size_from_master_and_nifty_only(tmp_path, dataset):
    assert read_master_lot_size(dataset[2])[0] == SYNTH_LOT  # BANKNIFTY decoy (30) ignored
    pm = write_master(tmp_path / "m.parquet", lots=(75,))
    assert read_master_lot_size(pm)[0] == 75
    p = make((dataset[0], dataset[1], pm))
    assert {c.lot_size for c in p.load_contracts(TEST_DAY)} == {75}


def test_lot_size_never_assumed(tmp_path, dataset):
    with pytest.raises(ReplayProviderError, match="not found"):
        read_master_lot_size(tmp_path / "missing.parquet")
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"not parquet")
    with pytest.raises(ReplayProviderError, match="unreadable"):
        read_master_lot_size(bad)
    p = make((dataset[0], dataset[1], tmp_path / "missing.parquet"))
    with pytest.raises(ReplayProviderError):
        p.load_contracts(TEST_DAY)
    assert not {c.name: c.ok for c in p.startup_checks(TEST_DAY)}["lot_size"]


def test_ambiguous_lot_size_raises(tmp_path):
    import pandas as pd

    rows = [
        {"Exch": "N", "ExchType": "D", "SymbolRoot": "NIFTY", "ScripType": t, "LotSize": lot,
         "Expiry": "2026-10-27"}
        for t, lot in (("CE", 65), ("PE", 75))
    ]
    pm = tmp_path / "amb.parquet"
    pd.DataFrame(rows).to_parquet(pm)
    with pytest.raises(ReplayProviderError, match="ambiguous"):
        read_master_lot_size(pm)


# --------------------------------------------------------------------------- contracts


def test_contracts_simulated_grid_and_expiries(prov):
    contracts = prov.load_contracts(TEST_DAY)
    assert contracts == sorted(contracts, key=lambda c: (c.expiry, c.strike, c.option_type))
    assert all(c.scrip_code >= SIM_SCRIP_BASE for c in contracts)
    assert len({c.scrip_code for c in contracts}) == len(contracts)
    assert all(c.symbol.startswith("SIMULATED ") for c in contracts)
    assert all(c.strike % 50 == 0 for c in contracts)
    assert all(c.expiry >= TEST_DAY for c in contracts)
    expiries = sorted({c.expiry for c in contracts})
    assert len(expiries) >= 5
    # expiry weekday convention: Tuesday (or the previous trading day when Tuesday is a holiday)
    assert all(e.weekday() == 1 or e.weekday() == 0 for e in expiries)
    assert date(2026, 2, 17) in expiries  # next Tuesday
    # monthly (last Tuesday of the month) present
    assert date(2026, 2, 24) in expiries
    # CE and PE listed for every (expiry, strike)
    pairs = {(c.expiry, c.strike): set() for c in contracts}
    for c in contracts:
        pairs[(c.expiry, c.strike)].add(c.option_type)
    assert all(v == {"CE", "PE"} for v in pairs.values())
    # the strike grid brackets the day's real range
    lo = min(c.low for c in prov.candles)
    hi = max(c.high for c in prov.candles)
    strikes = {c.strike for c in contracts}
    assert min(strikes) < lo - 500 and max(strikes) > hi + 500


def test_contracts_work_with_the_real_resolver(prov):
    """The live ContractResolver accepts the simulated chain (ATM, CE/PE, lot from the master)."""
    from trading_platform.live.config import load_live_config
    from trading_platform.live.contracts import ContractResolver
    from trading_platform.live.models import Direction

    cfg = load_live_config(ROOT / "configs" / "live_paper.yaml")
    r = ContractResolver(prov.load_contracts(TEST_DAY), cfg)
    spot = prov.candles[0].open
    ce = r.select(Direction.BULLISH, spot, TEST_DAY)
    pe = r.select(Direction.BEARISH, spot, TEST_DAY)
    assert ce.option_type == "CE" and pe.option_type == "PE"
    assert ce.strike == pe.strike and abs(ce.strike - spot) <= 25
    assert ce.lot_size == SYNTH_LOT


# --------------------------------------------------------------------------- completed candles


def test_completed_candles_never_look_ahead(prov):
    all_c = prov.candles
    probes = [at(9, 14, 59), at(9, 15), at(9, 19, 59), at(9, 20), at(9, 20, 1), at(12, 0, 7),
              at(15, 29, 59), at(15, 30), at(18, 0)]
    for as_of in probes:
        got = prov.completed_candles(TEST_DAY, as_of)
        assert all(c.close_time <= as_of for c in got)
        assert got == [c for c in all_c if c.close_time <= as_of]
    assert prov.completed_candles(TEST_DAY, at(9, 19, 59)) == []
    assert len(prov.completed_candles(TEST_DAY, at(9, 20))) == 1
    assert len(prov.completed_candles(TEST_DAY, at(15, 30))) == 75


def test_completed_candles_labelled_and_match_history(prov):
    got = prov.completed_candles(TEST_DAY, at(15, 30))
    assert {c.source for c in got} == {CandleSource.SIMULATED_REPLAY}
    assert got[0].open_time == at(9, 15)
    # the completed candle agrees with its last SIMULATED tick
    n = prov.ticks_per_bar
    assert all(c.close == prov.ticks[(i + 1) * n - 1][1] for i, c in enumerate(got))


def test_candle_delay_models_provider_lag(dataset):
    p = make(dataset, candle_delay_s=3)
    assert p.completed_candles(TEST_DAY, at(9, 20, 2)) == []
    assert len(p.completed_candles(TEST_DAY, at(9, 20, 3))) == 1


# --------------------------------------------------------------------------- poll / quotes


def test_poll_before_open_has_no_spot(dataset):
    p = make(dataset)
    p.clock.set(at(9, 14, 59))
    r = p.poll([])
    assert r.spot is None and r.errors and r.errors[0].startswith("SIMULATED_REPLAY")


def test_poll_follows_clock_and_never_returns_future_ticks(dataset):
    p = make(dataset)
    times = [t for t, _ in p.ticks]
    for k in (0, 1, 5, 11, 12, 300, 899):
        t, price = p.ticks[k]
        for probe in (t, t + timedelta(seconds=10)):
            if k + 1 < len(times) and probe >= times[k + 1]:
                continue
            p.clock.set(probe)
            s = p.poll([]).spot
            assert s is not None
            assert (s.price, s.exchange_time) == (price, t)
            assert s.received_at == probe and s.exchange_time <= probe
    p.clock.set(at(16, 30))
    last = p.poll([]).spot
    assert last is not None and last.price == p.candles[-1].close


def test_poll_day_high_low_running_and_prev_close(dataset):
    p = make(dataset)
    w = p.previous_session_and_warmup(TEST_DAY)
    hi = lo = None
    for step in p.run_events():
        s = step.poll_result.spot
        hi = s.price if hi is None else max(hi, s.price)
        lo = s.price if lo is None else min(lo, s.price)
        assert (s.day_high, s.day_low) == (hi, lo)
        assert s.prev_close == w.previous_session.close
    assert hi == max(c.high for c in p.candles) and lo == min(c.low for c in p.candles)


def test_quotes_for_watched_scrips_only_and_consistent_with_model(dataset):
    p = make(dataset)
    contracts = p.load_contracts(TEST_DAY)
    ce = contracts[10]
    p.clock.set(at(10, 31, 40))
    res = p.poll([ce.scrip_code, ce.scrip_code])
    assert set(res.quotes) == {ce.scrip_code} and res.errors == []
    q, spot = res.quotes[ce.scrip_code], res.spot
    assert q.server_time == q.received_at == p.clock.now()
    assert (q.bid, q.ask) == SimulatedOptionModel().book(ce, spot.price, p.clock.now())
    assert q.is_valid_book and q.bid < q.ask
    assert q.symbol == ce.symbol and q.source == QUOTE_SOURCE


def test_unknown_scrip_reported_not_fabricated(dataset):
    p = make(dataset)
    p.clock.set(at(10, 0))
    res = p.poll([123456])
    assert res.quotes == {} and any("unknown scrip" in e for e in res.errors)


def test_option_quote_moves_with_spot_direction(dataset):
    p = make(dataset)
    contracts = p.load_contracts(TEST_DAY)
    spot0 = p.candles[0].open
    ce = min((c for c in contracts if c.option_type == "CE" and c.expiry == date(2026, 2, 17)),
             key=lambda c: abs(c.strike - spot0))
    pe = next(c for c in contracts if c.option_type == "PE" and (c.expiry, c.strike) == (ce.expiry, ce.strike))
    quotes = []
    for step in p.run_events([ce.scrip_code, pe.scrip_code]):
        quotes.append((step.poll_result.spot.price, step.poll_result.quotes[ce.scrip_code],
                       step.poll_result.quotes[pe.scrip_code]))
    # whenever spot rises between consecutive ticks within ~1 minute, CE mid rises more than PE
    ups = downs = 0
    for (s0, c0, p0), (s1, c1, p1) in zip(quotes, quotes[1:], strict=False):
        if abs(s1 - s0) < 3 or (c1.received_at - c0.received_at).total_seconds() > 60:
            continue
        mid = lambda q: (q.bid + q.ask) / 2  # noqa: E731
        # time decay is tiny vs a >=3 point move at this vol
        if s1 > s0:
            ups += 1
            assert mid(c1) > mid(c0) and mid(p1) < mid(p0)
        else:
            downs += 1
            assert mid(c1) < mid(c0) and mid(p1) > mid(p0)
    assert ups > 10 and downs > 10


# --------------------------------------------------------------------------- driver


def test_run_events_clock_candles_and_decision_time(dataset):
    p = make(dataset)
    ce = p.load_contracts(TEST_DAY)[10]
    steps = list(p.run_events([ce.scrip_code]))
    times = [s.now for s in steps]
    assert times == sorted(times) and len(set(times)) == len(times)
    assert times[0] == at(9, 15) and times[-1] == at(15, 30)
    due = [c for s in steps for c in s.completed_candles_due]
    assert due == p.candles  # each exactly once, in order
    for s in steps:
        for c in s.completed_candles_due:
            assert s.now == c.close_time  # delivered at its close, not before
            # a quote fetched in the same step is NOT older than the candle's decision time
            if s.poll_result.quotes:
                assert s.poll_result.quotes[ce.scrip_code].received_at >= c.close_time
        assert s.poll_result.spot.received_at == s.now
    assert p.clock.now() == at(15, 30)
    assert all(s.label == "REPLAY_SIMULATED" for s in steps)
    assert [s.step_index for s in steps] == list(range(len(steps)))


def test_run_events_watchlist_is_reread_each_step(dataset):
    p = make(dataset)
    contracts = p.load_contracts(TEST_DAY)
    watch: list[int] = []
    seen = []
    for i, step in enumerate(p.run_events(watch)):
        if i == 3:
            watch.append(contracts[0].scrip_code)
        seen.append(len(step.poll_result.quotes))
        if i == 6:
            break
    assert seen[:4] == [0, 0, 0, 0] and seen[4:] == [1, 1, 1]
    # callable form
    p2 = make(dataset)
    first = next(iter(p2.run_events(lambda: [contracts[1].scrip_code])))
    assert set(first.poll_result.quotes) == {contracts[1].scrip_code}


def test_close_is_idempotent_and_stops_driver(dataset):
    p = make(dataset)
    it = p.run_events()
    next(it)
    p.close()
    p.close()
    assert list(it) == []


# --------------------------------------------------------------------------- forming candle


def test_forming_candle_is_partial_sampled_and_within_bar(dataset):
    p = make(dataset)
    p.clock.set(at(9, 14))
    assert p.sampled_forming_candle() is None
    n = p.ticks_per_bar
    for k in range(n):
        p.clock.set(at(9, 15) + timedelta(seconds=25 * k + 3))
        fc = p.sampled_forming_candle()
        c = p.candles[0]
        assert fc is not None and fc.source is CandleSource.SAMPLED_TICKS
        assert fc.open_time == c.open_time and fc.open == c.open
        assert c.low <= fc.low <= fc.high <= c.high
        assert fc.close == p.ticks[k][1]
    assert (fc.high, fc.low, fc.close) == (c.high, c.low, c.close)  # last tick completes it
    p.clock.set(at(15, 30))
    assert p.sampled_forming_candle() is None
    assert "SIMULATED_REPLAY" in p.forming_candle_label


# --------------------------------------------------------------------------- fault injection


def test_drop_quotes_window(dataset):
    p = make(dataset, faults=FaultInjection(drop_quotes_between=((at(10, 0), at(10, 5)),)))
    scrip = p.load_contracts(TEST_DAY)[0].scrip_code
    p.clock.set(at(9, 59, 59))
    assert scrip in p.poll([scrip]).quotes
    p.clock.set(at(10, 0, 30))
    r = p.poll([scrip])
    assert r.spot is not None and r.quotes == {} and any("outage" in e for e in r.errors)
    p.clock.set(at(10, 5))
    assert scrip in p.poll([scrip]).quotes


def test_stale_quotes_freeze_and_age_grows(dataset):
    p = make(dataset, faults=FaultInjection(stale_between=((at(10, 0), at(10, 10)),)))
    scrip = p.load_contracts(TEST_DAY)[0].scrip_code
    p.clock.set(at(10, 1))
    q1 = p.poll([scrip]).quotes[scrip]
    p.clock.set(at(10, 4))
    q2 = p.poll([scrip]).quotes[scrip]
    assert q1 == q2  # frozen values and timestamps
    assert q1.received_at == q1.server_time == at(10, 0)
    assert (p.clock.now() - q2.received_at) == timedelta(minutes=4)  # engine sees growing age
    p.clock.set(at(10, 10))
    assert p.poll([scrip]).quotes[scrip].received_at == at(10, 10)


def test_stale_after_open_ended(dataset):
    p = make(dataset, faults=FaultInjection(stale_after=at(11, 0)))
    scrip = p.load_contracts(TEST_DAY)[0].scrip_code
    p.clock.set(at(14, 0))
    assert p.poll([scrip]).quotes[scrip].received_at == at(11, 0)


def test_zero_bid_window_is_an_invalid_book(dataset):
    p = make(dataset, faults=FaultInjection(zero_bid_between=((at(12, 0), at(12, 10)),)))
    scrip = p.load_contracts(TEST_DAY)[0].scrip_code
    p.clock.set(at(12, 1))
    q = p.poll([scrip]).quotes[scrip]
    assert q.bid == 0.0 and q.ask > 0 and not q.is_valid_book
    p.clock.set(at(12, 10))
    assert p.poll([scrip]).quotes[scrip].is_valid_book


def test_spot_outage_reports_disconnected_and_no_data(dataset):
    p = make(dataset, faults=FaultInjection(spot_outage_between=((at(13, 0), at(13, 1)),)))
    scrip = p.load_contracts(TEST_DAY)[0].scrip_code
    p.clock.set(at(13, 0, 30))
    r = p.poll([scrip])
    assert r.spot is None and r.quotes == {} and r.errors[0].startswith("DISCONNECTED")
    p.clock.set(at(13, 1))
    assert p.poll([scrip]).spot is not None
    assert any(c.name == "fault_injection" for c in p.startup_checks(TEST_DAY))


# --------------------------------------------------------------------------- labelling / safety


def test_every_emitted_object_is_labelled_simulated(dataset):
    p = make(dataset)
    contracts = p.load_contracts(TEST_DAY)
    assert all("SIMULATED" in c.symbol for c in contracts)
    scrips = [c.scrip_code for c in contracts[:4]]
    n_quotes = 0
    for step in p.run_events(scrips):
        s = step.poll_result.spot
        assert s.source == SPOT_SOURCE and "SIMULATED" in s.source
        for q in step.poll_result.quotes.values():
            assert "SIMULATED" in q.source and "SIMULATED" in q.symbol
            n_quotes += 1
        for c in step.completed_candles_due:
            assert c.source is CandleSource.SIMULATED_REPLAY
    assert n_quotes > 0
    assert all("SIMULATED" in c.detail for c in p.startup_checks(TEST_DAY))
    import trading_platform.live.providers.replay as mod

    doc = mod.__doc__.replace("\n", " ").upper()
    assert "NOT MARKET QUOTES" in doc and "SIMULATED" in doc
    assert "NEVER BE MIXED WITH THE LIVE LEDGER" in doc


def test_module_has_no_broker_or_network_imports():
    code = (
        "import sys; sys.path.insert(0, {!r});"
        "import trading_platform.live.providers.replay as m;"
        "bad=[k for k in sys.modules if k.startswith(('py5paisa','requests','httpx','urllib3',"
        "'trading_platform.brokers'))];"
        "print('BAD', bad) if bad else print('CLEAN')"
    ).format(str(ROOT / "src"))
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "CLEAN", out.stdout
    src = (ROOT / "src/trading_platform/live/providers/replay.py").read_text()
    for line in src.splitlines():
        if line.startswith(("import ", "from ")):
            assert "brokers" not in line and "requests" not in line, line
