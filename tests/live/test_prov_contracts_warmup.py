"""load_contracts(), previous_session_and_warmup() and startup_checks() (fake data, no network)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from prov_fakes import (
    TEST_DAY,
    FakeSdkClient,
    StubBroker,
    at,
    bars_frame,
    five_min_session,
    make_clock,
    spot_response,
)

from trading_platform.core.enums import DataSource
from trading_platform.data.calendar import NSECalendar
from trading_platform.live.providers.fivepaisa import (
    FivePaisaLiveProvider,
    ProviderConfig,
    ProviderError,
)


def master_row(code, name, root="NIFTY", expiry="2026-11-03 14:30:00", typ="CE", strike=25900.0,
               lot=65, exch="N", exch_type="D", full=None):
    return {
        "Exch": exch, "ExchType": exch_type, "ScripCode": code, "Name": name, "FullName": full or name,
        "Expiry": expiry, "ScripType": typ, "StrikeRate": strike, "LotSize": lot, "SymbolRoot": root,
    }


@pytest.fixture
def rig(tmp_path):
    client = FakeSdkClient()
    broker = StubBroker(tmp_path, client)
    provider = FivePaisaLiveProvider(
        broker=broker,
        config=ProviderConfig(min_call_interval_s=0.0),
        clock=make_clock(at(8, 30)),
        sleep_fn=lambda s: None,
        monotonic_fn=lambda: 0.0,
    )
    return provider, broker, client


# ---------------------------------------------------------------- contracts


def test_contracts_are_nifty_index_options_only_with_real_lot_sizes(rig):
    provider, broker, _ = rig
    broker.master = pd.DataFrame(
        [
            master_row(1, "NIFTY 03 Nov 2026 CE 25900.00", full="NIFTY 03 Nov 2026 CE 25900.00"),
            master_row(2, "NIFTY 03 Nov 2026 PE 25900.00", typ="PE"),
            master_row(3, "NIFTY 27 Oct 2026 CE 25900.00", expiry="2026-10-27 14:30:00"),  # expired
            master_row(4, "NIFTY 28 Oct 2026 CE 25850.00", expiry="2026-10-28 14:30:00"),  # today ok
            master_row(5, "BANKNIFTY 03 Nov 2026 CE 55000.00", root="BANKNIFTY", lot=30),
            master_row(6, "FINNIFTY 03 Nov 2026 CE 25000.00", root="FINNIFTY", lot=60),
            master_row(7, "MIDCPNIFTY 03 Nov 2026 CE 13000.00", root="MIDCPNIFTY", lot=120),
            master_row(8, "NIFTYNXT50 03 Nov 2026 CE 70000.00", root="NIFTYNXT50", lot=25),
            master_row(9, "BANKNIFTY 03 Nov 2026 CE 55000.00", root="NIFTY", lot=30),  # bad root, name guards
            master_row(10, "NIFTY 03 Nov 2026 XX 0.00", typ="XX", strike=0.0),  # future
            master_row(11, "NIFTY 03 Nov 2026 CE 25900.00", exch_type="C"),  # cash segment
            master_row(12, "NIFTY 03 Nov 2026 CE 25950.00", lot=float("nan")),  # no lot size
            master_row(13, "NIFTY 03 Nov 2026 CE 26000.00", expiry=None),
        ]
    )
    contracts = provider.load_contracts(TEST_DAY)
    assert [c.scrip_code for c in contracts] == [4, 1, 2]
    assert {c.lot_size for c in contracts} == {65}  # from the master; never the old default 25
    first = contracts[1]
    assert (first.symbol, first.expiry, first.strike, first.option_type) == (
        "NIFTY 03 Nov 2026 CE 25900.00", date(2026, 11, 3), 25900.0, "CE",
    )
    assert contracts[0].expiry == TEST_DAY


def test_contract_lot_sizes_follow_the_master_not_a_constant(rig):
    provider, broker, _ = rig
    broker.master = pd.DataFrame([master_row(1, "NIFTY 03 Nov 2026 CE 25900.00", lot=75)])
    assert provider.load_contracts(TEST_DAY)[0].lot_size == 75


def test_expiry_and_strike_formats_are_parsed_robustly(rig):
    provider, broker, _ = rig
    broker.master = pd.DataFrame(
        [
            master_row(1, "NIFTY 03 Nov 2026 CE 25900.00", expiry="2026-11-03"),
            master_row(2, "NIFTY 03 Nov 2026 PE 25900.00", typ="PE", expiry="2026-11-03T00:00:00"),
            master_row(3, "NIFTY 10 Nov 2026 CE 26000.00", expiry="/Date(1794137400000+0530)/"),
            master_row(4, "NIFTY 17 Nov 2026 CE 26100.00", expiry="2026-11-17 14:30:00", strike=0.0),
            master_row(5, "NIFTY 17 Nov 2026 PE 26100.00", typ="pe ", expiry="17 Nov 2026"),
        ]
    )
    contracts = {c.scrip_code: c for c in provider.load_contracts(TEST_DAY)}
    assert set(contracts) == {1, 2, 3, 4, 5}
    assert contracts[3].expiry == date(2026, 11, 9) or contracts[3].expiry.year == 2026
    assert contracts[4].strike == 26100.0  # recovered from the name
    assert contracts[5].option_type == "PE" and contracts[5].expiry == date(2026, 11, 17)


def test_no_contracts_fails_closed(rig):
    provider, broker, _ = rig
    broker.master = pd.DataFrame([master_row(1, "NIFTY 27 Oct 2026 CE 25900.00", expiry="2026-10-27")])
    with pytest.raises(ProviderError):
        provider.load_contracts(TEST_DAY)


def test_contract_symbols_label_later_quotes(rig):
    provider, broker, client = rig
    broker.master = pd.DataFrame([master_row(77, "NIFTY 03 Nov 2026 CE 25900.00")])
    provider.load_contracts(TEST_DAY)
    from prov_fakes import depth_response

    client.spot = lambda req: spot_response(22500.0, at(8, 30))
    client.depth = lambda req: depth_response(77, 10.0, 11.0, at(8, 30))
    assert provider.poll([77]).quotes[77].symbol == "NIFTY 03 Nov 2026 CE 25900.00"


# ---------------------------------------------------------------- warm-up


def trading_days_before(day: date, count: int) -> list[date]:
    cal = NSECalendar()
    out, cursor = [], day - timedelta(days=1)
    while len(out) < count:
        if cal.is_trading_day(cursor):
            out.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(out)


def intraday_frame(days: list[date], bars: int = 75, extra_stub: bool = True) -> pd.DataFrame:
    rows = []
    for i, d in enumerate(days):
        session = five_min_session(d, 22000.0 + i * 10, bars=bars)
        rows.extend(session)
        if extra_stub:
            rows.append((datetime(d.year, d.month, d.day, 15, 30), 22100.0, 22100.0, 22100.0, 22100.0))
            rows.append((datetime(d.year, d.month, d.day, 15, 35), 22100.0, 22101.0, 22099.0, 22100.0))
    return bars_frame(rows)


def daily_frame(days: list[date], last_close: float | None = None,
                closes: dict[date, float] | None = None) -> pd.DataFrame:
    rows = []
    for i, d in enumerate(days):
        base = 21000.0 + i
        rows.append((datetime(d.year, d.month, d.day), base, base + 50, base - 50, base + 10))
    overrides = dict(closes or {})
    if last_close is not None:
        overrides[days[-1]] = last_close
    for i, d in enumerate(days):
        if d in overrides:
            c = overrides[d]
            rows[i] = (datetime(d.year, d.month, d.day), c - 5, c + 40, c - 40, c)
    return bars_frame(rows)


def test_warmup_builds_bars_drops_stubs_and_derives_previous_session(rig):
    provider, broker, _ = rig
    intraday_days = trading_days_before(TEST_DAY, 10)
    daily_days = trading_days_before(TEST_DAY, 300)
    prev = intraday_days[-1]
    expected_close = five_min_session(prev, 22000.0 + 9 * 10)[-1][4]
    broker.history["5m"] = intraday_frame(intraday_days)
    broker.history["1d"] = daily_frame(daily_days, last_close=expected_close)
    warm = provider.previous_session_and_warmup(TEST_DAY)
    assert prev == date(2026, 10, 27)
    assert warm.previous_session.session_date == prev
    assert warm.previous_session.close == pytest.approx(expected_close)
    assert len(warm.daily_bars) == 300 and len(warm.intraday_bars) == 750
    assert all(b.exchange_time.date() < TEST_DAY for b in warm.intraday_bars + warm.daily_bars)
    assert all(b.exchange_time.time() <= datetime(2000, 1, 1, 15, 25).time() for b in warm.intraday_bars)
    assert all(b.source == DataSource.FIVEPAISA_HISTORICAL for b in warm.intraday_bars)
    assert warm.intraday_bars[0].interval.value == "5m" and warm.daily_bars[0].interval.value == "1d"
    assert any("dropped" in n for n in warm.source_notes)
    # requested windows end the day before and cover the lookback
    assert broker.history_calls[0][0] == "1d" and broker.history_calls[0][2] == "2026-10-27"
    assert broker.history_calls[1][0] == "5m"


def test_warmup_drops_todays_partial_vendor_rows(rig):
    provider, broker, _ = rig
    days = trading_days_before(TEST_DAY, 10)
    frame = intraday_frame(days)
    today_rows = bars_frame(five_min_session(TEST_DAY, 22500.0, bars=3))
    broker.history["5m"] = pd.concat([frame, today_rows], ignore_index=True)
    prev_close = five_min_session(days[-1], 22000.0 + 9 * 10)[-1][4]
    broker.history["1d"] = daily_frame(
        trading_days_before(TEST_DAY, 260) + [TEST_DAY], closes={days[-1]: prev_close}
    )
    warm = provider.previous_session_and_warmup(TEST_DAY)
    assert max(b.exchange_time.date() for b in warm.intraday_bars) < TEST_DAY
    assert max(b.exchange_time.date() for b in warm.daily_bars) < TEST_DAY


def test_insufficient_daily_history_fails_closed(rig):
    provider, broker, _ = rig
    broker.history["5m"] = intraday_frame(trading_days_before(TEST_DAY, 10))
    broker.history["1d"] = daily_frame(trading_days_before(TEST_DAY, 100))
    with pytest.raises(ProviderError, match="daily"):
        provider.previous_session_and_warmup(TEST_DAY)


def test_insufficient_intraday_history_fails_closed(rig):
    provider, broker, _ = rig
    broker.history["5m"] = intraday_frame(trading_days_before(TEST_DAY, 7))  # 525 bars < 600
    broker.history["1d"] = daily_frame(trading_days_before(TEST_DAY, 300))
    with pytest.raises(ProviderError, match="5m"):
        provider.previous_session_and_warmup(TEST_DAY)


def test_incomplete_previous_session_fails_closed(rig):
    provider, broker, _ = rig
    days = trading_days_before(TEST_DAY, 10)
    rows = []
    for d in days[:-1]:
        rows.extend(five_min_session(d, 22000.0))
    rows.extend(five_min_session(days[-1], 22000.0, bars=20))
    broker.history["5m"] = bars_frame(rows)
    broker.history["1d"] = daily_frame(trading_days_before(TEST_DAY, 300))
    with pytest.raises(ProviderError, match="previous session"):
        provider.previous_session_and_warmup(TEST_DAY)


def test_daily_bar_that_disagrees_with_5m_data_fails_closed(rig):
    provider, broker, _ = rig
    days = trading_days_before(TEST_DAY, 10)
    broker.history["5m"] = intraday_frame(days)
    broker.history["1d"] = daily_frame(trading_days_before(TEST_DAY, 300), last_close=15000.0)
    with pytest.raises(ProviderError, match="disagrees"):
        provider.previous_session_and_warmup(TEST_DAY)


def test_previous_session_falls_back_to_5m_aggregate_when_daily_bar_missing(rig):
    provider, broker, _ = rig
    days = trading_days_before(TEST_DAY, 10)
    broker.history["5m"] = intraday_frame(days)
    broker.history["1d"] = daily_frame(trading_days_before(TEST_DAY, 300)[:-1])  # no 10-27 bar
    warm = provider.previous_session_and_warmup(TEST_DAY)
    assert warm.previous_session.session_date == days[-1]
    assert any("derived from 5m" in n for n in warm.source_notes)


def test_history_endpoint_failure_is_a_provider_error_without_secrets(rig):
    provider, broker, _ = rig
    broker.history["1d"] = RuntimeError("HTTP 500 Bearer abcdefghijklmnop1234")
    with pytest.raises(ProviderError) as info:
        provider.previous_session_and_warmup(TEST_DAY)
    assert "abcdefghijklmnop1234" not in str(info.value)


# ---------------------------------------------------------------- startup checks


def checks_by_name(results):
    return {r.name: r for r in results}


def test_startup_checks_pass_on_a_closed_market_without_leaking_anything(rig):
    provider, broker, client = rig
    broker.is_connected = True
    broker.master = pd.DataFrame([master_row(1, "NIFTY 03 Nov 2026 CE 25900.00")])
    client.spot = lambda req: spot_response(22500.0, at(8, 29, 59))
    results = checks_by_name(provider.startup_checks(TEST_DAY))
    assert set(results) == {
        "login", "token_valid", "calendar", "previous_trading_day", "scrip_master", "spot_freshness",
    }
    assert all(r.ok for r in results.values()), {k: v.detail for k, v in results.items() if not v.ok}
    assert results["previous_trading_day"].detail == "2026-10-27"
    assert results["spot_freshness"].blocking is False  # 08:30 is outside the session
    assert "65" in results["scrip_master"].detail
    blob = " ".join(r.detail for r in results.values())
    assert "stub-access-token" not in blob and "test-user-key" not in blob


def test_stale_spot_inside_the_session_is_a_blocking_failure(rig):
    provider, broker, client = rig
    provider._clock.set(at(10, 0, 0))
    broker.master = pd.DataFrame([master_row(1, "NIFTY 03 Nov 2026 CE 25900.00")])
    client.spot = lambda req: spot_response(22500.0, at(9, 59, 0))  # 60 s old
    spot_check = checks_by_name(provider.startup_checks(TEST_DAY))["spot_freshness"]
    assert spot_check.ok is False and spot_check.blocking is True and "tick age" in spot_check.detail


def test_non_trading_day_and_failed_login_are_reported(rig):
    provider, broker, client = rig
    broker.is_connected = False
    broker.client = None
    broker.connect = lambda: False  # type: ignore[method-assign]
    broker.connection_category = "AUTH_FAILED"
    broker.connection_detail = "TOTP authentication failed (no access token returned)"
    broker.master = pd.DataFrame([master_row(1, "NIFTY 03 Nov 2026 CE 25900.00")])
    sunday = date(2026, 11, 1)
    results = checks_by_name(provider.startup_checks(sunday))
    assert results["login"].ok is False and "AUTH_FAILED" in results["login"].detail
    assert results["token_valid"].ok is False
    assert results["calendar"].ok is False
    assert results["spot_freshness"].ok is False
    assert client.calls == []  # no market-data call without a session


def test_missing_scrip_master_is_reported_not_raised(rig):
    provider, broker, client = rig
    broker.master = None
    client.spot = lambda req: spot_response(22500.0, at(8, 29, 59))
    results = checks_by_name(provider.startup_checks(TEST_DAY))
    assert results["scrip_master"].ok is False
