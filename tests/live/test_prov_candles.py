"""completed_candles(): a vendor bar is returned only when proven closed and cross-checked."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
from prov_fakes import (
    TEST_DAY,
    FakeSdkClient,
    StubBroker,
    at,
    bars_frame,
    make_clock,
    spot_response,
)

from trading_platform.live.models import CandleSource
from trading_platform.live.providers.fivepaisa import (
    FivePaisaLiveProvider,
    ProviderConfig,
    ProviderError,
)


class Rig:
    def __init__(self, tmp_path, **cfg):
        self.client = FakeSdkClient()
        self.broker = StubBroker(tmp_path, self.client)
        self.clock = make_clock(at(9, 14))
        defaults = {
            "min_call_interval_s": 0.0,
            "candle_fetch_min_interval_s": 0.0,
            "candle_safety_delay_s": 3.0,
            "spot_crosscheck_tolerance": 15.0,
        }
        defaults.update(cfg)
        self.provider = FivePaisaLiveProvider(
            broker=self.broker,
            config=ProviderConfig(**defaults),
            clock=self.clock,
            sleep_fn=lambda s: None,
            monotonic_fn=lambda: 0.0,
        )

    def ticks(self, series: list[tuple[datetime, float]]) -> None:
        """Feed spot ticks through the real poll() path (received 0.5 s after the tick)."""
        for when, price in series:
            self.clock.set(when + timedelta(milliseconds=500))
            self.client.spot = lambda req, w=when, p=price: spot_response(p, w)
            result = self.provider.poll([])
            assert result.spot is not None, result.errors

    def vendor(self, rows) -> None:
        self.broker.history["5m"] = bars_frame(rows)

    def candles(self, as_of: datetime):
        self.clock.set(as_of)
        return self.provider.completed_candles(TEST_DAY, as_of)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def bar(hh, mm, o, h, low, c):
    return (at(hh, mm).replace(tzinfo=None), o, h, low, c)


def test_closed_candle_with_matching_sampled_ticks_is_accepted(rig):
    rig.ticks([(at(9, 16), 22500.0), (at(9, 19, 59), 22504.0), (at(9, 20, 2), 22505.0)])
    rig.vendor([bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0)])
    got = rig.candles(at(9, 20, 5))
    assert len(got) == 1
    c = got[0]
    assert c.source == CandleSource.PROVIDER_COMPLETED
    assert c.open_time == at(9, 15) and c.close_time == at(9, 20)
    assert (c.open, c.high, c.low, c.close) == (22495.0, 22510.0, 22490.0, 22505.0)
    assert "accepted 1 new" in rig.provider.candle_diagnostics_text


def test_forming_candle_is_rejected(rig):
    rig.ticks([(at(9, 19, 59), 22504.0), (at(9, 22), 22520.0)])
    rig.vendor(
        [bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0), bar(9, 20, 22505.0, 22525.0, 22500.0, 22519.0)]
    )
    got = rig.candles(at(9, 22, 1))
    assert [c.open_time for c in got] == [at(9, 15)]
    assert "NOT_CLOSED_AS_OF" in rig.provider.candle_diagnostics["rejected"]
    assert rig.provider.candle_diagnostics["rejected"]["NOT_CLOSED_AS_OF"] == ["09:20"]


def test_safety_delay_blocks_a_candle_closing_inside_the_delay(rig):
    rig.ticks([(at(9, 19, 59), 22504.0), (at(9, 20, 1), 22505.0)])
    rig.vendor([bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0)])
    assert rig.candles(at(9, 20, 2)) == []  # 2 s after close < 3 s delay
    assert "BEFORE_FIRST_CLOSE" in rig.provider.candle_diagnostics["summary"]
    assert rig.broker.history_calls == []  # not even asked
    assert len(rig.candles(at(9, 20, 4))) == 1


def test_candle_not_proven_closed_when_spot_feed_lags_behind_the_close(rig):
    rig.ticks([(at(9, 19, 50), 22504.0)])  # latest valid spot is BEFORE the 09:20 close
    rig.vendor([bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0)])
    assert rig.candles(at(9, 20, 30)) == []
    assert "NOT_PROVEN_CLOSED_BY_SPOT" in rig.provider.candle_diagnostics["rejected"]


def test_1530_stub_is_rejected_and_the_last_real_candle_kept(rig):
    rig.ticks([(at(15, 29, 58), 22400.0), (at(15, 30, 3), 22401.0)])
    rig.vendor([bar(15, 25, 22390.0, 22410.0, 22385.0, 22400.0), bar(15, 30, 22400.0, 22400.0, 22400.0, 22400.0)])
    got = rig.candles(at(15, 31))
    assert [c.open_time for c in got] == [at(15, 25)]
    assert rig.provider.candle_diagnostics["rejected"]["STUB_1530"] == ["15:30"]


def test_close_mismatch_against_sampled_ticks_is_rejected(rig):
    rig.ticks([(at(9, 19, 59), 22504.0), (at(9, 20, 2), 22505.0)])
    rig.vendor([bar(9, 15, 22495.0, 22560.0, 22490.0, 22545.0)])  # vendor close 40 pts away
    assert rig.candles(at(9, 20, 5)) == []
    assert "CROSSCHECK_MISMATCH_CLOSE" in rig.provider.candle_diagnostics["rejected"]


def test_range_mismatch_against_sampled_ticks_is_rejected(rig):
    rig.ticks([(at(9, 16), 22600.0), (at(9, 19, 59), 22504.0), (at(9, 20, 2), 22505.0)])
    rig.vendor([bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0)])  # a sampled 22600 tick is outside
    assert rig.candles(at(9, 20, 5)) == []
    assert "CROSSCHECK_MISMATCH_RANGE" in rig.provider.candle_diagnostics["rejected"]


def test_no_sampled_data_near_the_close_means_untrusted_by_default(rig):
    rig.ticks([(at(9, 17), 22500.0), (at(9, 22), 22510.0)])  # nothing within 20 s of 09:20:00
    rig.vendor([bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0)])
    assert rig.candles(at(9, 22, 5)) == []
    assert "NO_SAMPLE_UNTRUSTED" in rig.provider.candle_diagnostics["rejected"]


def test_opt_in_trust_without_samples_requires_the_candle_to_have_aged(tmp_path):
    rig = Rig(tmp_path, require_sample_crosscheck=False, uncrosschecked_min_age_s=60.0)
    rig.ticks([(at(9, 20, 30), 22510.0)])
    rig.vendor([bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0)])
    assert rig.candles(at(9, 20, 40)) == []
    assert "NO_SAMPLE_TOO_RECENT" in rig.provider.candle_diagnostics["rejected"]
    rig.ticks([(at(9, 21, 30), 22512.0)])
    assert len(rig.candles(at(9, 21, 40))) == 1


def test_no_spot_proof_means_no_candles_and_no_endpoint_call(rig):
    rig.vendor([bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0)])
    assert rig.candles(at(9, 21)) == []
    assert "NO_SPOT_PROOF" in rig.provider.candle_diagnostics["summary"]
    assert rig.broker.history_calls == []


def test_empty_endpoint_fails_closed_and_ticks_are_never_promoted(rig):
    rig.ticks([(at(9, 16), 22500.0), (at(9, 19, 59), 22504.0), (at(9, 20, 2), 22505.0)])
    assert rig.candles(at(9, 20, 5)) == []
    assert rig.provider.candle_diagnostics["endpoint_status"] == "EMPTY"
    assert "ENDPOINT_EMPTY" in rig.provider.candle_diagnostics["summary"]
    forming = rig.provider.sampled_forming_candle()
    assert forming is not None and forming.source == CandleSource.SAMPLED_TICKS


def test_endpoint_error_returns_previously_accepted_candles_only(rig):
    rig.ticks([(at(9, 19, 59), 22504.0), (at(9, 20, 2), 22505.0), (at(9, 24, 59), 22520.0), (at(9, 25, 2), 22521.0)])
    rig.vendor([bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0)])
    assert len(rig.candles(at(9, 20, 5))) == 1
    rig.broker.history["5m"] = RuntimeError("HTTP 500 RequestToken=LEAKYSECRET12345")
    got = rig.candles(at(9, 25, 5))
    assert [c.open_time for c in got] == [at(9, 15)]
    summary = rig.provider.candle_diagnostics_text
    assert "ENDPOINT_ERROR" in summary and "LEAKYSECRET12345" not in summary


def test_accepted_candles_are_cached_and_not_refetched_when_complete(rig):
    rig.ticks([(at(9, 19, 59), 22504.0), (at(9, 20, 2), 22505.0)])
    rig.vendor([bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0)])
    rig.candles(at(9, 20, 5))
    n = len(rig.broker.history_calls)
    got = rig.candles(at(9, 20, 8))
    assert len(got) == 1 and len(rig.broker.history_calls) == n
    assert rig.provider.candle_diagnostics["endpoint_status"] == "NOT_CALLED"


def test_fetch_is_rate_limited_while_candles_are_outstanding(tmp_path):
    rig = Rig(tmp_path, candle_fetch_min_interval_s=5.0)
    now = [0.0]
    rig.provider._monotonic = lambda: now[0]
    rig.ticks([(at(9, 20, 2), 22505.0)])
    rig.candles(at(9, 20, 5))
    n = len(rig.broker.history_calls)
    now[0] = 2.0
    rig.candles(at(9, 20, 8))
    assert len(rig.broker.history_calls) == n
    assert rig.provider.candle_diagnostics["endpoint_status"] == "THROTTLED"
    now[0] = 6.0
    rig.candles(at(9, 20, 12))
    assert len(rig.broker.history_calls) == n + 1


def test_malformed_vendor_rows_are_rejected(rig):
    rig.ticks([(at(9, 19, 59), 22504.0), (at(9, 20, 2), 22505.0)])
    rows = pd.DataFrame(
        [
            {"timestamp": at(9, 15).replace(tzinfo=None), "open": 22495, "high": 22400, "low": 22490, "close": 22505, "volume": 0},
            {"timestamp": at(9, 17).replace(tzinfo=None), "open": 22495, "high": 22510, "low": 22490, "close": 22505, "volume": 0},
            {"timestamp": at(9, 15, 0).replace(tzinfo=None) - timedelta(days=1), "open": 1, "high": 2, "low": 1, "close": 2, "volume": 0},
        ]
    )
    rig.broker.history["5m"] = rows
    assert rig.candles(at(9, 20, 5)) == []
    rejected = rig.provider.candle_diagnostics["rejected"]
    assert "MISALIGNED" in rejected and "NOT_TODAY" in rejected
    assert rig.provider.candle_diagnostics["invalid_rows"] == 1  # high < open dropped as invalid


def test_before_the_first_candle_closes_nothing_is_requested(rig):
    rig.ticks([(at(9, 16), 22500.0)])
    assert rig.candles(at(9, 18)) == []
    assert rig.broker.history_calls == []
    assert "BEFORE_FIRST_CLOSE" in rig.provider.candle_diagnostics["summary"]


def test_request_window_covers_today_inclusive_or_exclusive_end(rig):
    rig.ticks([(at(9, 20, 2), 22505.0)])
    rig.candles(at(9, 20, 5))
    interval, start, end = rig.broker.history_calls[0]
    assert interval == "5m" and start == "2026-10-28" and end == "2026-10-29"


def test_result_is_cumulative_ascending_and_respects_as_of(rig):
    rig.ticks(
        [(at(9, 19, 59), 22504.0), (at(9, 20, 2), 22505.0), (at(9, 24, 59), 22520.0), (at(9, 25, 2), 22521.0)]
    )
    rig.vendor(
        [bar(9, 20, 22505.0, 22525.0, 22500.0, 22520.0), bar(9, 15, 22495.0, 22510.0, 22490.0, 22505.0)]
    )
    got = rig.candles(at(9, 25, 5))
    assert [c.open_time for c in got] == [at(9, 15), at(9, 20)]
    assert [c.open_time for c in rig.provider.completed_candles(TEST_DAY, at(9, 21))] == [at(9, 15)]


def test_sampled_forming_candle_is_display_only_and_volume_is_not_invented(rig):
    assert rig.provider.sampled_forming_candle() is None
    rig.ticks([(at(9, 20, 5), 22500.0), (at(9, 21), 22520.0), (at(9, 22), 22490.0), (at(9, 23), 22510.0)])
    rig.clock.set(at(9, 23, 1))
    c = rig.provider.sampled_forming_candle()
    assert c is not None and c.source == CandleSource.SAMPLED_TICKS
    assert (c.open_time, c.open, c.high, c.low, c.close, c.volume) == (
        at(9, 20), 22500.0, 22520.0, 22490.0, 22510.0, 0,
    )
    rig.clock.set(at(9, 26))  # feed stalled past the bucket: not forming any more
    assert rig.provider.sampled_forming_candle() is None


def test_provider_error_is_a_trading_platform_error():
    from trading_platform.core.exceptions import TradingPlatformError

    assert issubclass(ProviderError, TradingPlatformError)
