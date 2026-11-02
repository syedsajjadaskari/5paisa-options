"""FivePaisaLiveProvider.poll(): mapping, validation, throttling, back-off, serialization."""

from __future__ import annotations

import threading

import pytest
from prov_fakes import (
    FakeSdkClient,
    StubBroker,
    at,
    depth_response,
    make_clock,
    spot_response,
    tickdt,
)

from trading_platform.live.models import OptionQuote, RunMode, SpotTick
from trading_platform.live.providers.base import MarketDataProvider
from trading_platform.live.providers.fivepaisa import (
    DEPTH_SOURCE_LABEL,
    SPOT_SOURCE_LABEL,
    FivePaisaLiveProvider,
    ProviderConfig,
)

CE, PE = 51001, 51002


class Rig:
    def __init__(self, tmp_path, **cfg):
        self.client = FakeSdkClient()
        self.broker = StubBroker(tmp_path, self.client)
        self.clock = make_clock(at(10, 0, 1))
        self.mono = 100.0
        self.sleeps: list[float] = []

        def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            self.mono += seconds

        defaults = {"min_call_interval_s": 0.0}
        defaults.update(cfg)
        self.provider = FivePaisaLiveProvider(
            broker=self.broker,
            config=ProviderConfig(**defaults),
            clock=self.clock,
            sleep_fn=sleep,
            monotonic_fn=lambda: self.mono,
        )
        self.client.spot = lambda req: spot_response(22500.0, at(10, 0, 0))
        self.client.depth = lambda req: depth_response(
            req[0]["ScripCode"], 120.0, 121.0, at(10, 0, 0)
        )


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_provider_satisfies_the_protocol(rig):
    assert isinstance(rig.provider, MarketDataProvider)
    assert rig.provider.mode == RunMode.LIVE


def test_successful_poll_maps_spot_and_quotes_with_honest_provenance(rig):
    result = rig.provider.poll([CE, PE])
    assert result.errors == [] and result.rate_limited is False
    spot = result.spot
    assert isinstance(spot, SpotTick)
    assert spot.price == 22500.0 and spot.exchange_time == at(10, 0, 0)
    assert spot.received_at == at(10, 0, 1)
    assert (spot.day_high, spot.day_low, spot.prev_close) == (22520.0, 22480.0, 22495.0)
    assert spot.source == SPOT_SOURCE_LABEL
    assert set(result.quotes) == {CE, PE}
    q = result.quotes[CE]
    assert isinstance(q, OptionQuote)
    assert (q.bid, q.ask, q.bid_qty, q.ask_qty) == (120.0, 121.0, 650, 130)
    assert q.server_time == at(10, 0, 0) and q.received_at == at(10, 0, 1)
    assert q.source == DEPTH_SOURCE_LABEL and "SERVER_TIME" in q.source
    assert q.is_valid_book


def test_only_requested_scrips_are_fetched_with_the_expected_requests(rig):
    rig.provider.poll([CE])
    assert rig.client.depth_requests() == [CE]
    spot_req = next(req for kind, req in rig.client.calls if kind == "spot")
    assert spot_req == [{"Exch": "N", "ExchType": "C", "ScripCode": 999920000}]
    depth_req = next(req for kind, req in rig.client.calls if kind == "depth")
    assert depth_req == [{"Exchange": "N", "ExchangeType": "D", "ScripCode": CE}]
    rig.provider.poll([])
    assert rig.client.depth_requests() == [CE]  # empty watch list -> no depth calls


def test_depth_is_reused_within_the_configured_interval(rig):
    rig.provider.poll([CE])
    rig.mono += 0.4
    again = rig.provider.poll([CE])
    assert rig.client.depth_requests() == [CE]
    assert CE in again.quotes
    rig.mono += 1.0
    rig.provider.poll([CE])
    assert rig.client.depth_requests() == [CE, CE]


def test_watchlist_is_deduplicated_and_truncated_loudly(tmp_path):
    rig = Rig(tmp_path, max_depth_scrips_per_poll=3)
    result = rig.provider.poll([1, 2, 2, 3, 4, 5])
    assert rig.client.depth_requests() == [1, 2, 3]
    assert any("WATCHLIST_TRUNCATED" in e for e in result.errors)


@pytest.mark.parametrize(
    "response, fragment",
    [
        (None, "EMPTY_RESPONSE"),
        ("", "EMPTY_RESPONSE"),
        ("garbage", "MALFORMED"),
        ([], "MALFORMED"),
        ({"Status": 1, "Message": "bad request", "Data": []}, "API_REJECTED"),
        ({"Status": 0, "Data": []}, "MALFORMED"),
        ({"Status": 0, "Data": ["x"]}, "MALFORMED"),
    ],
)
def test_malformed_spot_responses_become_errors_not_crashes(rig, response, fragment):
    rig.client.spot = lambda req: response
    result = rig.provider.poll([])
    assert result.spot is None
    assert any(fragment in e for e in result.errors)


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"Token": 12345}, "IDENTITY_MISMATCH"),
        ({"Symbol": "BANKNIFTY"}, "IDENTITY_MISMATCH"),
        ({"Token": None, "Symbol": None}, "IDENTITY_UNVERIFIED"),
        ({"ExchType": "D"}, "IDENTITY_MISMATCH"),
        ({"LastRate": 0}, "INVALID_PAYLOAD"),
        ({"LastRate": "abc"}, "SDK_EXCEPTION"),
        ({"TickDt": None}, "UNSUPPORTED_TIMESTAMP"),
        ({"TickDt": "garbage"}, "UNSUPPORTED_TIMESTAMP"),
        ({"TickDt": tickdt(at(10, 0, 30))}, "FUTURE_QUOTE"),
    ],
)
def test_spot_identity_and_timestamp_validation_fails_closed(rig, overrides, fragment):
    rig.client.spot = lambda req: spot_response(22500.0, at(10, 0, 0), **overrides)
    result = rig.provider.poll([])
    assert result.spot is None and any(fragment in e for e in result.errors)
    assert rig.provider.last_spot is None  # a rejected tick never becomes the latest proof


def test_spot_identity_uses_token_when_symbol_is_absent(rig):
    rig.client.spot = lambda req: spot_response(22500.0, at(10, 0, 0), Symbol=None)
    assert rig.provider.poll([]).spot is not None


def test_depth_identity_and_timestamp_validation(rig):
    cases = [
        ({"ScripCode": 999}, "IDENTITY_MISMATCH"),
        ({"ScripCode": None}, "IDENTITY_UNVERIFIED"),
        ({"ExchType": "C"}, "IDENTITY_MISMATCH"),
        ({"TimeStamp": None}, "UNSUPPORTED_TIMESTAMP"),
    ]
    for overrides, fragment in cases:
        rig.client.depth = lambda req, o=overrides: depth_response(
            req[0]["ScripCode"], 1.0, 2.0, at(10, 0, 0), **o
        )
        rig.provider._depth_cache.clear()
        result = rig.provider.poll([CE])
        assert CE not in result.quotes
        assert any(fragment in e and f"DEPTH[{CE}]" in e for e in result.errors), (overrides, result.errors)


def test_empty_or_crossed_books_are_reported_truthfully_not_invented(rig):
    rig.client.depth = lambda req: depth_response(req[0]["ScripCode"], 0.0, 0.0, at(10, 0, 0))
    q = rig.provider.poll([CE]).quotes[CE]
    assert (q.bid, q.ask) == (0.0, 0.0) and not q.is_valid_book
    rig.provider._depth_cache.clear()
    rig.client.depth = lambda req: depth_response(req[0]["ScripCode"], 130.0, 120.0, at(10, 0, 0))
    q = rig.provider.poll([CE]).quotes[CE]
    assert not q.is_valid_book  # engine decides CROSSED_OR_WIDE_QUOTE


def test_missing_book_side_gives_zero_not_a_fabricated_price(rig):
    rig.client.depth = lambda req: depth_response(
        req[0]["ScripCode"], 100.0, 101.0, at(10, 0, 0), Bids=[], Asks=[]
    )
    q = rig.provider.poll([CE]).quotes[CE]
    assert (q.bid, q.ask, q.bid_qty, q.ask_qty) == (0.0, 0.0, 0, 0)


def test_sdk_exceptions_are_contained_and_redacted(rig):
    rig.client.spot = RuntimeError("boom RequestToken=SUPERSECRETVALUE123 failed")
    result = rig.provider.poll([])
    assert result.spot is None and result.errors
    assert "SUPERSECRETVALUE123" not in " ".join(result.errors)


def test_rate_limit_sets_flag_skips_depth_and_backs_off_exponentially(rig):
    rig.client.spot = {"Status": 1, "Message": "Too many requests"}
    first = rig.provider.poll([CE])
    assert first.rate_limited is True and rig.client.depth_requests() == []
    calls = len(rig.client.calls)
    blocked = rig.provider.poll([CE])  # inside the 2 s back-off: no SDK call at all
    assert blocked.rate_limited is True and len(rig.client.calls) == calls
    assert "backing off" in blocked.errors[0]
    rig.mono += 2.1
    second = rig.provider.poll([CE])
    assert second.rate_limited is True and len(rig.client.calls) == calls + 1
    rig.mono += 3.0  # the doubled (4 s) back-off has not elapsed yet
    assert len(rig.client.calls) == calls + 1 and rig.provider.poll([CE]).rate_limited is True
    assert len(rig.client.calls) == calls + 1
    rig.mono += 2.0
    rig.client.spot = lambda req: spot_response(22500.0, at(10, 0, 0))
    ok = rig.provider.poll([CE])
    assert ok.rate_limited is False and ok.spot is not None
    assert rig.provider._backoff_s == 0.0


def test_rate_limit_on_depth_stops_the_cycle_but_keeps_the_spot(rig):
    rig.client.depth = {"Status": 1, "Message": "rate limit exceeded"}
    result = rig.provider.poll([CE, PE])
    assert result.rate_limited and result.spot is not None
    assert rig.client.depth_requests() == [CE]


def test_calls_are_throttled_to_the_minimum_interval(tmp_path):
    rig = Rig(tmp_path, min_call_interval_s=0.25)
    rig.provider.poll([CE, PE])  # 1 spot + 2 depth
    assert len(rig.sleeps) == 2 and all(s == pytest.approx(0.25) for s in rig.sleeps)


def test_disconnected_broker_blocks_all_sdk_calls(rig):
    rig.broker.always_connected = False
    rig.broker.is_connected = False
    rig.broker._next_relogin_at = float("inf")  # inside a re-login back-off: no login attempt
    result = rig.provider.poll([CE])
    assert result.spot is None and result.errors[0].startswith("DISCONNECTED")
    assert rig.client.calls == []
    assert rig.provider.connection_status == "DISCONNECTED"


def test_repeated_empty_spot_responses_flip_status_to_disconnected(rig):
    rig.broker.always_connected = False
    rig.broker._next_relogin_at = float("inf")  # no automatic re-login attempt in this test
    rig.client.spot = lambda req: None
    for _ in range(3):
        rig.provider.poll([])
    assert rig.provider.connection_status == "DISCONNECTED"
    result = rig.provider.poll([])
    assert result.errors[0].startswith("DISCONNECTED")


def test_successful_polls_keep_status_connected(rig):
    rig.provider.poll([CE])
    assert rig.provider.connection_status == "CONNECTED"


def test_sdk_calls_from_many_threads_never_overlap(rig):
    original = rig.client.fetch_market_feed_scrip

    def slow(req):
        threading.Event().wait(0.002)
        return original(req)

    rig.client.fetch_market_feed_scrip = slow  # type: ignore[method-assign]
    rig.broker.client = rig.client
    threads = [threading.Thread(target=lambda: [rig.provider.poll([CE, PE]) for _ in range(5)]) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert rig.client.max_in_flight == 1


def test_sampled_ticks_buffer_is_bounded(tmp_path):
    rig = Rig(tmp_path, tick_buffer_size=50)
    for i in range(200):
        rig.client.spot = lambda req, i=i: spot_response(22500.0 + i, at(10, 0, 0))
        rig.provider.poll([])
    assert len(rig.provider._ticks) == 50


def test_close_is_idempotent_and_stops_polling(rig):
    rig.provider.close()
    rig.provider.close()
    result = rig.provider.poll([CE])
    assert result.errors == ["PROVIDER_CLOSED"] and rig.client.calls == []
