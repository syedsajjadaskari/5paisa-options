"""Configuration loader with environment interpolation and caching."""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from trading_platform.core.exceptions import ConfigurationError

load_dotenv()


def interpolate_env_vars(data: Any) -> Any:
    """Recursively interpolate ${VAR:default} patterns in configuration dictionaries."""
    env_pattern = re.compile(r"\$\{([A-Za-z0-9_]+)(?::([^}]*))?\}")

    if isinstance(data, dict):
        return {k: interpolate_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [interpolate_env_vars(item) for item in data]
    elif isinstance(data, str):

        def replace(match: re.Match) -> str:
            var_name = match.group(1)
            default = match.group(2) if match.group(2) is not None else ""
            return os.getenv(var_name, default)

        return env_pattern.sub(replace, data)
    return data


class ConfigManager:
    """Loads and caches platform YAML configurations."""

    def __init__(self, config_dir: str | Path = "configs") -> None:
        self.config_dir = Path(config_dir)
        self._cache: dict[str, dict[str, Any]] = {}

    def load_yaml(self, relative_path: str) -> dict[str, Any]:
        """Load, interpolate and cache a YAML file relative to config_dir."""
        if relative_path in self._cache:
            return self._cache[relative_path]

        file_path = self.config_dir / relative_path
        if not file_path.exists():
            raise ConfigurationError(f"Configuration file not found: {file_path}")

        try:
            with open(file_path, encoding="utf-8") as f:
                raw_data = yaml.safe_load(f) or {}
            interpolated = interpolate_env_vars(raw_data)
            self._cache[relative_path] = interpolated
            return interpolated
        except Exception as e:
            raise ConfigurationError(f"Failed to parse {file_path}: {e}") from e

    def get_app_config(self) -> dict[str, Any]:
        """Return configs/app.yaml."""
        return self.load_yaml("app.yaml")

    def get_market_config(self) -> dict[str, Any]:
        """Return configs/market.yaml."""
        return self.load_yaml("market.yaml")

    def get_data_config(self) -> dict[str, Any]:
        """Return configs/data.yaml."""
        return self.load_yaml("data.yaml")

    def get_risk_config(self) -> dict[str, Any]:
        """Return configs/risk.yaml."""
        return self.load_yaml("risk.yaml")

    def get_strategy_config(self, strategy_name: str) -> dict[str, Any]:
        """Return configs/strategies/{strategy_name.lower()}.yaml."""
        return self.load_yaml(f"strategies/{strategy_name.lower()}.yaml")
