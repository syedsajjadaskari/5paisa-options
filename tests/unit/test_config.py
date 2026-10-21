"""Unit tests for ConfigManager."""

import pytest

from trading_platform.core.config import ConfigManager, interpolate_env_vars
from trading_platform.core.exceptions import ConfigurationError


def test_config_manager_loads_app_config():
    mgr = ConfigManager()
    cfg = mgr.get_app_config()
    assert "environment" in cfg
    assert "logging" in cfg
    assert cfg["timezone"] == "Asia/Kolkata"


def test_config_manager_loads_strategy_config():
    mgr = ConfigManager()
    irf_cfg = mgr.get_strategy_config("IRF")
    assert irf_cfg["strategy_name"] == "IRF"
    assert "methods" in irf_cfg
    assert irf_cfg["canonical_rules"]["target_points"] == 60.0


def test_interpolate_env_vars(monkeypatch):
    monkeypatch.setenv("TEST_VAR", "injected_value")
    data = {"key": "${TEST_VAR:default_val}", "nested": {"another": "${NON_EXISTENT:fallback}"}}
    result = interpolate_env_vars(data)
    assert result["key"] == "injected_value"
    assert result["nested"]["another"] == "fallback"


def test_nonexistent_config_raises_error():
    mgr = ConfigManager()
    with pytest.raises(ConfigurationError):
        mgr.load_yaml("nonexistent_config_file_xyz.yaml")
