"""SimulatedOptionModel: deterministic, monotone, bid<ask (SYNTHETIC, not a market model)."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date

import pytest
from test_replay_helpers import TEST_DAY, at

from trading_platform.live.models import ContractSpec
from trading_platform.live.providers.replay import (
    OptionModelParams,
    SimulatedOptionModel,
)

EXPIRY = date(2026, 2, 17)  # Tuesday, 6 days after TEST_DAY
NOW = at(10, 0)
MODEL = SimulatedOptionModel()


def contract(strike: float, kind: str, expiry: date = EXPIRY) -> ContractSpec:
    return ContractSpec(9_000_001, f"SIMULATED {kind} {strike}", expiry, strike, kind, 65)


def test_ce_premium_non_decreasing_in_spot_and_pe_non_increasing():
    ce, pe = contract(25000, "CE"), contract(25000, "PE")
    spots = [24000 + 5 * i for i in range(0, 400)]
    ce_p = [MODEL.premium(ce, s, NOW) for s in spots]
    pe_p = [MODEL.premium(pe, s, NOW) for s in spots]
    assert all(b >= a for a, b in zip(ce_p, ce_p[1:], strict=False))
    assert all(b <= a for a, b in zip(pe_p, pe_p[1:], strict=False))
    assert ce_p[-1] > ce_p[0] and pe_p[0] > pe_p[-1]


def test_bid_below_ask_everywhere_and_on_tick_grid():
    for kind in ("CE", "PE"):
        for strike in range(22000, 28001, 250):
            c = contract(strike, kind)
            for spot in (23000.0, 24500.0, 25000.05, 25513.35, 27000.0):
                for now in (at(9, 15), at(14, 59), at(15, 29, 59)):
                    bid, ask = MODEL.book(c, spot, now)
                    assert 0 < bid < ask
                    assert round(bid / 0.05, 6) == pytest.approx(round(bid / 0.05))
                    assert round(ask / 0.05, 6) == pytest.approx(round(ask / 0.05))


def test_half_spread_bounded_between_005_and_05():
    p = MODEL.params
    for prem_spot in (22000.0, 24000.0, 25000.0, 25200.0):
        c = contract(25000, "CE")
        bid, ask = MODEL.book(c, prem_spot, NOW)
        # full spread = 2 half-spreads (<= 2*0.5) + at most one tick of snapping; the smallest
        # possible spread is one tick (deep OTM: bid floored at 0.05, ask 0.10)
        assert 0.05 - 1e-9 <= ask - bid <= 2 * p.max_half_spread + 0.1 + 1e-9
    # high premium: the 0.5 cap binds, spread ~1.0
    bid, ask = MODEL.book(contract(24000, "CE"), 25000.0, NOW)
    assert ask - bid == pytest.approx(1.0, abs=0.06)


def test_spread_percent_fits_default_gate_for_normal_premiums():
    """ATM premium: spread well inside the default 2% max_spread_pct of live_paper.yaml."""
    c = contract(25000, "CE")
    bid, ask = MODEL.book(c, 25000.0, NOW)
    assert (ask - bid) / ((ask + bid) / 2) * 100 < 2.0


def test_atm_delta_half_and_itm_otm_levels():
    sd = MODEL.sd_points(25000.0, EXPIRY, NOW)
    ce = contract(25000, "CE")
    assert MODEL.delta(ce, 25000.0, NOW) == pytest.approx(0.5, abs=1e-9)
    assert MODEL.delta(ce, 25000.0 + 1.2816 * sd, NOW) == pytest.approx(0.90, abs=0.005)
    assert MODEL.delta(ce, 25000.0 - 1.2816 * sd, NOW) == pytest.approx(0.10, abs=0.005)
    pe = contract(25000, "PE")
    assert MODEL.delta(pe, 25000.0, NOW) == pytest.approx(-0.5, abs=1e-9)
    assert MODEL.delta(pe, 25000.0 - 1.2816 * sd, NOW) == pytest.approx(-0.90, abs=0.005)


def test_premium_moves_with_spot_by_delta():
    """A 20-point underlying move changes the CE premium by ~delta*20 (plausible option P&L)."""
    ce = contract(25000, "CE")
    for spot in (24900.0, 25000.0, 25100.0):
        d = MODEL.delta(ce, spot, NOW)
        gain = MODEL.premium(ce, spot + 20, NOW) - MODEL.premium(ce, spot, NOW)
        assert gain == pytest.approx(d * 20, rel=0.08)
    pe = contract(25000, "PE")
    loss = MODEL.premium(pe, 25020, NOW) - MODEL.premium(pe, 25000, NOW)
    assert loss == pytest.approx(MODEL.delta(pe, 25010, NOW) * 20, rel=0.08)


def test_put_call_parity_holds_away_from_the_floor():
    ce, pe = contract(25000, "CE"), contract(25000, "PE")
    for spot in (24800.0, 25000.0, 25150.0):
        diff = MODEL.premium(ce, spot, NOW) - MODEL.premium(pe, spot, NOW)
        assert diff == pytest.approx(spot - 25000, abs=1e-6)


def test_atm_level_is_plausible_and_decays_with_time():
    ce = contract(25000, "CE")
    morning = MODEL.premium(ce, 25000.0, at(9, 30))
    afternoon = MODEL.premium(ce, 25000.0, at(15, 0))
    assert 80 < morning < 300  # ~0.4 * 0.13 * spot * sqrt(6d/365)
    assert afternoon < morning
    expiry_day = date(2026, 2, 11)
    assert MODEL.premium(contract(25000, "CE", expiry_day), 25000.0, at(10, 0)) < morning


def test_floor_at_expiry_and_deep_otm():
    ce = contract(26000, "CE", TEST_DAY)
    assert MODEL.premium(ce, 24000.0, at(15, 29, 59)) == 0.05
    bid, ask = MODEL.book(ce, 24000.0, at(15, 29, 59))
    assert bid == 0.05 and ask == pytest.approx(0.10)
    # after expiry close the curve is pure intrinsic (>= floor), never negative / NaN
    itm = contract(24000, "CE", TEST_DAY)
    p = MODEL.premium(itm, 24500.0, at(15, 30))
    assert math.isfinite(p) and p == pytest.approx(500.0, abs=1.0)


def test_deterministic():
    ce = contract(25100, "CE")
    a = [MODEL.book(ce, 25000.0 + i, NOW) for i in range(50)]
    b = [SimulatedOptionModel().book(ce, 25000.0 + i, NOW) for i in range(50)]
    assert a == b


def test_params_are_overridable_and_validated_inputs():
    wide = SimulatedOptionModel(replace(OptionModelParams(), iv_proxy=0.26))
    ce = contract(25000, "CE")
    assert wide.premium(ce, 25000.0, NOW) > MODEL.premium(ce, 25000.0, NOW)
    with pytest.raises(ValueError):
        MODEL.premium(ce, 0.0, NOW)
    with pytest.raises(ValueError):
        MODEL.premium(ContractSpec(1, "x", EXPIRY, 25000.0, "XX", 65), 25000.0, NOW)
