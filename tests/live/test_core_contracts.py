"""ContractResolver tests on SYNTHETIC contract lists (live/contracts.py)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_core_helpers import (
    EXPIRY_NEAR,
    EXPIRY_NEXT,
    TRADE_DATE,
    at,
    cfg,
    grid,
    make_chain,
    standard_contracts,
    with_exec,
)

from trading_platform.live.contracts import ContractResolver, ContractSelectionError
from trading_platform.live.models import ContractSpec, Direction, EntryRejection


def resolver(contracts=None, **exec_kw) -> ContractResolver:
    return ContractResolver(
        contracts if contracts is not None else standard_contracts(), with_exec(cfg(), **exec_kw)
    )


def test_direction_maps_to_option_type():
    r = resolver()
    assert r.select(Direction.BULLISH, 25874.0, TRADE_DATE).option_type == "CE"
    assert r.select(Direction.BEARISH, 25874.0, TRADE_DATE).option_type == "PE"


def test_atm_strike_is_nearest_listed_strike_and_nearest_expiry():
    c = resolver().select(Direction.BEARISH, 25874.0, TRADE_DATE, at(10, 5))
    assert c.strike == 25850.0
    assert c.expiry == EXPIRY_NEAR  # past expiry skipped, nearest future chosen
    c2 = resolver().select(Direction.BULLISH, 25876.0, TRADE_DATE)
    assert c2.strike == 25900.0


def test_exact_tie_resolves_to_lower_strike_not_bankers_rounding():
    # 25775/50 = 515.5 -> round() would give 516 -> 25800 (banker's); ATM tie must be 25750.
    c = resolver().select(Direction.BULLISH, 25775.0, TRADE_DATE)
    assert c.strike == 25750.0
    # the opposite tie: 25825/50 = 516.5 -> round() gives 516 -> 25800; tie -> lower 25800
    assert resolver().select(Direction.BULLISH, 25825.0, TRADE_DATE).strike == 25800.0
    # tie where round() would go UP: 25875/50 = 517.5 -> 518 -> 25900; we deliberately pick lower
    assert resolver().select(Direction.BULLISH, 25875.0, TRADE_DATE).strike == 25850.0


def test_atm_among_listed_strikes_with_gaps():
    strikes = [25700.0, 25750.0, 25800.0, 25950.0, 26000.0]  # 25850/25900 not listed
    contracts = make_chain(EXPIRY_NEAR, strikes, 75, 2000)
    r = resolver(contracts, strike_step=100)
    assert r.select(Direction.BULLISH, 25860.0, TRADE_DATE).strike == 25800.0
    assert r.select(Direction.BULLISH, 25890.0, TRADE_DATE).strike == 25950.0


def test_gap_wider_than_strike_step_fails_closed():
    strikes = [25700.0, 25800.0, 26200.0]
    r = resolver(make_chain(EXPIRY_NEAR, strikes, 75, 2000))  # strike_step 50
    with pytest.raises(ContractSelectionError) as ei:
        r.select(Direction.BULLISH, 26000.0, TRADE_DATE)
    assert ei.value.rejection == EntryRejection.NO_CONTRACT
    assert "gap" in ei.value.detail


def test_lot_size_comes_from_master_row_not_constant():
    r = resolver()
    assert r.select(Direction.BULLISH, 25850.0, TRADE_DATE).lot_size == 75
    # next-expiry policy picks the chain with lot 65
    r2 = resolver(expiry_policy="NEXT")
    c = r2.select(Direction.BULLISH, 25850.0, TRADE_DATE)
    assert c.expiry == EXPIRY_NEXT and c.lot_size == 65


@pytest.mark.parametrize("bad", [0, -75])
def test_non_positive_lot_size_rejected(bad):
    contracts = [
        replace(c, lot_size=bad) if c.expiry == EXPIRY_NEAR else c for c in standard_contracts()
    ]
    with pytest.raises(ContractSelectionError, match="lot_size"):
        resolver(contracts).select(Direction.BULLISH, 25850.0, TRADE_DATE)


def test_non_int_lot_size_rejected():
    contracts = [
        replace(c, lot_size=75.0) if c.expiry == EXPIRY_NEAR else c for c in standard_contracts()
    ]  # type: ignore[arg-type]
    with pytest.raises(ContractSelectionError, match="lot_size"):
        resolver(contracts).select(Direction.BEARISH, 25850.0, TRADE_DATE)


def test_expiry_day_handling_configurable():
    # trading on the near expiry day itself
    r_allow = resolver(allow_expiry_day=True, min_days_to_expiry=0)
    assert r_allow.select(Direction.BULLISH, 25850.0, EXPIRY_NEAR).expiry == EXPIRY_NEAR
    r_deny = resolver(allow_expiry_day=False, min_days_to_expiry=0)
    assert r_deny.select(Direction.BULLISH, 25850.0, EXPIRY_NEAR).expiry == EXPIRY_NEXT


def test_min_days_to_expiry_policy():
    r = resolver(min_days_to_expiry=7)  # Nov 3 is 6 days away from Oct 28
    assert r.select(Direction.BULLISH, 25850.0, TRADE_DATE).expiry == EXPIRY_NEXT
    with pytest.raises(ContractSelectionError, match="no eligible expiry"):
        resolver(min_days_to_expiry=30).select(Direction.BULLISH, 25850.0, TRADE_DATE)


def test_expired_contract_excluded_after_market_close_on_expiry_day():
    r = resolver()
    assert r.eligible_expiries(EXPIRY_NEAR, at(11, 0, day=EXPIRY_NEAR)) == [
        EXPIRY_NEAR,
        EXPIRY_NEXT,
    ]
    assert r.eligible_expiries(EXPIRY_NEAR, at(15, 30, day=EXPIRY_NEAR)) == [EXPIRY_NEXT]


def test_next_policy_needs_two_eligible_expiries():
    only_one = make_chain(EXPIRY_NEAR, grid(), 75, 2000)
    with pytest.raises(ContractSelectionError, match="NEXT"):
        resolver(only_one, expiry_policy="NEXT").select(Direction.BULLISH, 25850.0, TRADE_DATE)


def test_strike_listed_on_one_side_only_is_not_used_for_atm():
    contracts = make_chain(EXPIRY_NEAR, [25800.0, 25900.0], 75, 2000) + make_chain(
        EXPIRY_NEAR, [25850.0], 75, 2900, types=("CE",)
    )  # 25850 has CE only
    r = resolver(contracts, strike_step=100)
    ce, pe = r.candidate_pair(25849.0, TRADE_DATE)
    assert ce.strike == pe.strike == 25800.0  # 25850 is CE-only -> nearest CE+PE strike
    assert ce.option_type == "CE" and pe.option_type == "PE"


def test_candidate_pair_matches_select():
    r = resolver()
    ce, pe = r.candidate_pair(25874.0, TRADE_DATE, at(9, 20))
    assert (ce.option_type, pe.option_type) == ("CE", "PE")
    assert ce.strike == pe.strike == 25850.0 and ce.expiry == pe.expiry
    assert r.select(Direction.BULLISH, 25874.0, TRADE_DATE) == ce
    assert r.select(Direction.BEARISH, 25874.0, TRADE_DATE) == pe


def test_nearby_pairs_width():
    pairs = resolver().nearby_pairs(25874.0, TRADE_DATE, width=1)
    assert [p[0].strike for p in pairs] == [25800.0, 25850.0, 25900.0]
    edge = resolver().nearby_pairs(25700.0, TRADE_DATE, width=2)
    assert [p[0].strike for p in edge][0] == 25700.0


def test_empty_or_invalid_inputs_fail_closed():
    with pytest.raises(ContractSelectionError):
        resolver([]).select(Direction.BULLISH, 25850.0, TRADE_DATE)
    for bad_spot in (0.0, -1.0, float("nan")):
        with pytest.raises(ContractSelectionError):
            resolver().select(Direction.BULLISH, bad_spot, TRADE_DATE)
    with pytest.raises(ContractSelectionError, match="direction"):
        resolver().select("SIDEWAYS", 25850.0, TRADE_DATE)  # type: ignore[arg-type]


def test_malformed_master_rows_are_ignored_and_counted():
    bad = ContractSpec(9, "X", EXPIRY_NEAR, 25850.0, "XX", 75)
    bad2 = ContractSpec(10, "Y", EXPIRY_NEAR, 0.0, "CE", 75)
    r = resolver(standard_contracts() + [bad, bad2])
    assert r.malformed_count == 2
    assert r.select(Direction.BULLISH, 25850.0, TRADE_DATE).strike == 25850.0


def test_duplicate_conflicting_rows_fail_closed():
    extra = ContractSpec(99999, "dup", EXPIRY_NEAR, 25850.0, "CE", 75)
    with pytest.raises(ContractSelectionError, match="ambiguous"):
        resolver(standard_contracts() + [extra]).select(Direction.BULLISH, 25850.0, TRADE_DATE)
    # the PE side of the same strike is unaffected
    assert resolver(standard_contracts() + [extra]).select(Direction.BEARISH, 25850.0, TRADE_DATE)


def test_try_select_returns_typed_failure():
    c, err = resolver([]).try_select(Direction.BULLISH, 25850.0, TRADE_DATE)
    assert c is None and err is not None and err.rejection == EntryRejection.NO_CONTRACT
    c, err = resolver().try_select(Direction.BULLISH, 25850.0, TRADE_DATE)
    assert err is None and c is not None
