"""Config loader/validator tests (live/config.py + configs/live_paper.yaml)."""

from __future__ import annotations

import copy
from datetime import time
from pathlib import Path

import pytest
import yaml
from test_core_helpers import cfg

from trading_platform.live.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    load_live_config,
    parse_live_config,
)
from trading_platform.live.models import ExecutionTier


def raw() -> dict:
    return copy.deepcopy(yaml.safe_load(DEFAULT_CONFIG_PATH.read_text()))


def test_default_yaml_matches_documented_defaults():
    c = cfg()
    assert c.account.starting_capital_inr == 250000
    assert c.account.max_open_positions == 1
    assert c.account.max_lots == 2
    assert c.account.max_premium_allocation_pct == 20.0
    assert c.account.max_daily_loss_pct == 10.0
    assert c.session.entry_cutoff == time(14, 30)
    assert c.session.square_off == time(15, 15)
    assert c.session.market_open == time(9, 15)
    assert c.session.market_close == time(15, 30)
    assert c.execution.entry_slippage_points == 0.5
    assert c.execution.exit_slippage_points == 0.5
    assert c.execution.max_spread_points == 5.0
    assert c.execution.max_spread_pct == 2.0
    assert c.execution.max_quote_age_seconds == 5.0
    assert c.execution.expiry_policy == "NEAREST"
    assert c.execution.min_days_to_expiry == 0
    assert c.execution.strike_selection == "ATM"
    assert c.execution.strike_step == 50
    assert c.candles.source == "PROVIDER" and c.candles.fail_closed is True
    assert c.candles.provider_poll_delay_seconds == 3
    assert c.candles.provider_wait_max_seconds == 90
    assert c.candles.spot_crosscheck_tolerance_points == 15.0
    assert c.polling.interval_seconds == 1.0
    assert c.paths.runtime_dir == "runtime"


def test_default_strategies_shadow_only_and_rf_disabled_research_off():
    c = cfg()
    for name in ("PDS", "ORB", "IRF", "PDR"):
        assert c.strategies[name].enabled is True
        assert c.strategies[name].tier == ExecutionTier.SHADOW_ONLY
    assert c.strategies["PDS"].method == "METHOD_1"
    assert c.strategies["IRF"].method == "METHOD_2"
    assert c.strategies["PDR"].method == "RETEST_BOUNCE"
    assert c.strategies["RF"].enabled is False
    assert c.strategies["RF"].tier == ExecutionTier.DISABLED
    assert c.research_paper.enabled is False
    assert c.research_paper.acknowledgement == ""
    assert c.research_paper.strategies == ()


def test_paths_resolve_against_base_dir(tmp_path: Path):
    c = load_live_config(base_dir=tmp_path)
    assert c.runtime_path == tmp_path / "runtime"
    assert c.scrip_master_path == tmp_path / "data/reference/scrip_master.parquet"


def test_missing_file_is_a_clear_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_live_config(tmp_path / "nope.yaml")


def test_empty_file_is_an_error(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("")
    with pytest.raises(ConfigError, match="empty"):
        load_live_config(p)


def test_invalid_yaml_is_an_error(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("account: [unclosed")
    with pytest.raises(ConfigError, match="cannot read"):
        load_live_config(p)


def test_load_from_custom_path_roundtrip(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(raw()))
    assert load_live_config(p).account.max_lots == 2


def test_unknown_top_level_key_rejected():
    r = raw()
    r["surprise"] = 1
    with pytest.raises(ConfigError, match="unknown key"):
        parse_live_config(r)


@pytest.mark.parametrize(
    "section", ["account", "session", "execution", "candles", "polling", "paths", "research_paper"]
)
def test_unknown_key_in_each_section_rejected(section):
    r = raw()
    r[section]["bogus"] = 1
    with pytest.raises(ConfigError, match="unknown key"):
        parse_live_config(r)


def test_unknown_strategy_name_and_key_rejected():
    r = raw()
    r["strategies"]["XYZ"] = {"enabled": True, "method": "METHOD_1", "tier": "SHADOW_ONLY"}
    with pytest.raises(ConfigError, match="unknown strategy"):
        parse_live_config(r)
    r = raw()
    r["strategies"]["PDS"]["extra"] = 1
    with pytest.raises(ConfigError, match="unknown key"):
        parse_live_config(r)


@pytest.mark.parametrize(
    "section",
    [
        "account",
        "session",
        "execution",
        "strategies",
        "research_paper",
        "candles",
        "polling",
        "paths",
    ],
)
def test_missing_section_rejected(section):
    r = raw()
    del r[section]
    with pytest.raises(ConfigError, match="missing required"):
        parse_live_config(r)


def test_missing_key_rejected():
    r = raw()
    del r["account"]["max_lots"]
    with pytest.raises(ConfigError, match="max_lots"):
        parse_live_config(r)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("account", "starting_capital_inr", 0),
        ("account", "starting_capital_inr", -5),
        ("account", "starting_capital_inr", "250000"),
        ("account", "starting_capital_inr", True),
        ("account", "max_open_positions", 2),
        ("account", "max_open_positions", 0),
        ("account", "max_lots", 0),
        ("account", "max_lots", 1.5),
        ("account", "max_premium_allocation_pct", 0),
        ("account", "max_premium_allocation_pct", 150),
        ("account", "max_daily_loss_pct", 0),
        ("account", "max_daily_loss_pct", float("nan")),
        ("execution", "entry_slippage_points", -0.1),
        ("execution", "exit_slippage_points", -1),
        ("execution", "max_spread_points", 0),
        ("execution", "max_spread_pct", 0),
        ("execution", "max_quote_age_seconds", 0),
        ("execution", "expiry_policy", "FARTHEST"),
        ("execution", "min_days_to_expiry", -1),
        ("execution", "strike_selection", "OTM_1"),
        ("execution", "strike_step", 0),
        ("execution", "allow_expiry_day", "yes"),
        ("candles", "source", "SAMPLED_TICKS"),
        ("candles", "fail_closed", False),
        ("candles", "provider_poll_delay_seconds", -1),
        ("candles", "provider_wait_max_seconds", 0),
        ("candles", "spot_crosscheck_tolerance_points", 0),
        ("polling", "interval_seconds", 0),
        ("paths", "runtime_dir", ""),
        ("research_paper", "enabled", "true"),
    ],
)
def test_invalid_values_rejected(section, key, value):
    r = raw()
    r[section][key] = value
    with pytest.raises(ConfigError):
        parse_live_config(r)


@pytest.mark.parametrize(
    "cutoff,square,close",
    [
        ("15:20", "15:15", "15:30"),
        ("14:30", "14:30", "15:30"),
        ("14:30", "15:15", "15:00"),
        ("09:00", "15:15", "15:30"),
    ],
)
def test_session_time_ordering_enforced(cutoff, square, close):
    r = raw()
    r["session"].update(entry_cutoff_ist=cutoff, square_off_ist=square, market_close_ist=close)
    with pytest.raises(ConfigError, match="session"):
        parse_live_config(r)


@pytest.mark.parametrize("bad", ["2:30", "14:60", 1430, "14:30:00", "", None])
def test_bad_time_format_rejected(bad):
    r = raw()
    r["session"]["entry_cutoff_ist"] = bad
    with pytest.raises(ConfigError):
        parse_live_config(r)


def test_square_off_may_equal_market_close():
    r = raw()
    r["session"]["square_off_ist"] = "15:30"
    assert parse_live_config(r).session.square_off == time(15, 30)


def test_candle_wait_must_cover_poll_delay():
    r = raw()
    r["candles"]["provider_poll_delay_seconds"] = 100
    with pytest.raises(ConfigError, match="provider_wait_max"):
        parse_live_config(r)


@pytest.mark.parametrize("tier", ["RESEARCH_PAPER", "AUTHOR_APPROVED", "BOGUS"])
def test_strategy_block_cannot_grant_fill_tiers(tier):
    r = raw()
    r["strategies"]["PDS"]["tier"] = tier
    with pytest.raises(ConfigError):
        parse_live_config(r)


def test_strategy_method_must_match_strategy():
    r = raw()
    r["strategies"]["PDS"]["method"] = "METHOD_2"
    with pytest.raises(ConfigError, match="method"):
        parse_live_config(r)


def test_rf_cannot_be_enabled():
    r = raw()
    r["strategies"]["RF"] = {"enabled": True, "tier": "SHADOW_ONLY"}
    with pytest.raises(ConfigError, match="permanently disabled"):
        parse_live_config(r)


def test_research_strategy_list_validated():
    r = raw()
    r["research_paper"]["strategies"] = ["PDS", "RF"]
    with pytest.raises(ConfigError, match="RF"):
        parse_live_config(r)
    r["research_paper"]["strategies"] = ["PDS", "PDS"]
    with pytest.raises(ConfigError, match="duplicate"):
        parse_live_config(r)
    r["research_paper"]["strategies"] = "PDS"
    with pytest.raises(ConfigError):
        parse_live_config(r)
    r["research_paper"]["strategies"] = ["pds", "orb"]
    assert parse_live_config(r).research_paper.strategies == ("PDS", "ORB")


def test_allow_expiry_day_optional_default_true():
    r = raw()
    r["execution"].pop("allow_expiry_day")
    assert parse_live_config(r).execution.allow_expiry_day is True
