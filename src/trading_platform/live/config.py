"""Config loader/validator for the live paper-trading application.

Single source of live configuration: ``configs/live_paper.yaml``.  The loader is strict:

* a missing file is an error (never silent defaults),
* unknown keys anywhere are an error,
* missing required keys are an error,
* every numeric/semantic constraint of ``docs/live_app_architecture.md`` is validated.

No I/O happens here except reading the YAML file in :func:`load_live_config`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

import yaml

from trading_platform.live.models import ExecutionTier

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "live_paper.yaml"

#: Strategies the application knows about.  RF is permanently disabled.
ACTIVE_STRATEGIES: tuple[str, ...] = ("PDS", "ORB", "IRF", "PDR")
DISABLED_STRATEGIES: tuple[str, ...] = ("RF",)
KNOWN_STRATEGIES: tuple[str, ...] = ACTIVE_STRATEGIES + DISABLED_STRATEGIES

#: The only method each strategy may be configured with.
STRATEGY_METHODS: dict[str, str] = {
    "PDS": "METHOD_1",
    "ORB": "METHOD_1",
    "IRF": "METHOD_2",
    "PDR": "RETEST_BOUNCE",
}

EXPIRY_POLICIES: tuple[str, ...] = ("NEAREST", "NEXT")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ConfigError(ValueError):
    """Raised for a missing, malformed or unsafe live configuration."""


# --------------------------------------------------------------------------- dataclasses


@dataclass(frozen=True)
class AccountConfig:
    starting_capital_inr: float
    max_open_positions: int
    max_lots: int
    max_premium_allocation_pct: float  # of SESSION-OPENING equity
    max_daily_loss_pct: float  # of SESSION-OPENING equity


@dataclass(frozen=True)
class SessionConfig:
    market_open: time
    entry_cutoff: time
    square_off: time
    market_close: time


@dataclass(frozen=True)
class ExecutionConfig:
    entry_slippage_points: float
    exit_slippage_points: float
    max_spread_points: float
    max_spread_pct: float
    max_quote_age_seconds: float
    expiry_policy: str  # NEAREST | NEXT
    min_days_to_expiry: int
    strike_selection: str  # ATM
    strike_step: float
    allow_expiry_day: bool = True


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    enabled: bool
    tier: ExecutionTier  # DISABLED or SHADOW_ONLY only; fills are granted by authorization.py
    method: str | None = None


@dataclass(frozen=True)
class ResearchPaperConfig:
    enabled: bool
    acknowledgement: str
    strategies: tuple[str, ...]


@dataclass(frozen=True)
class CandlesConfig:
    source: str
    fail_closed: bool
    provider_poll_delay_seconds: float
    provider_wait_max_seconds: float
    spot_crosscheck_tolerance_points: float
    #: Fail-closed default: a vendor candle is accepted only if this process sampled a spot tick
    #: near its close.  Setting it false accepts candles older than the age below UNCHECKED.
    require_sample_crosscheck: bool = True
    uncrosschecked_min_age_seconds: float = 60.0


@dataclass(frozen=True)
class PollingConfig:
    interval_seconds: float
    option_depth_interval_seconds: float


@dataclass(frozen=True)
class PathsConfig:
    runtime_dir: str
    scrip_master: str


@dataclass(frozen=True)
class LiveConfig:
    account: AccountConfig
    session: SessionConfig
    execution: ExecutionConfig
    strategies: dict[str, StrategyConfig]
    research_paper: ResearchPaperConfig
    candles: CandlesConfig
    polling: PollingConfig
    paths: PathsConfig
    base_dir: Path = field(default=PROJECT_ROOT)

    def resolve_path(self, value: str) -> Path:
        """Resolve a ``paths`` entry against ``base_dir`` (absolute paths are kept)."""
        p = Path(value)
        return p if p.is_absolute() else self.base_dir / p

    @property
    def runtime_path(self) -> Path:
        return self.resolve_path(self.paths.runtime_dir)

    @property
    def scrip_master_path(self) -> Path:
        return self.resolve_path(self.paths.scrip_master)


# --------------------------------------------------------------------------- helpers


def _mapping(raw: Any, where: str, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a mapping, got {type(raw).__name__}")
    optional = optional or set()
    unknown = sorted(set(map(str, raw)) - required - optional)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {unknown}")
    missing = sorted(required - set(raw))
    if missing:
        raise ConfigError(f"{where}: missing required key(s) {missing}")
    return raw


def _num(
    raw: dict,
    key: str,
    where: str,
    *,
    minimum: float | None = None,
    exclusive_min: bool = False,
    maximum: float | None = None,
) -> float:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise ConfigError(f"{where}.{key}: expected a number, got {v!r}")
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        raise ConfigError(f"{where}.{key}: must be finite, got {v!r}")
    if minimum is not None and (f < minimum or (exclusive_min and f == minimum)):
        op = ">" if exclusive_min else ">="
        raise ConfigError(f"{where}.{key}: must be {op} {minimum}, got {v!r}")
    if maximum is not None and f > maximum:
        raise ConfigError(f"{where}.{key}: must be <= {maximum}, got {v!r}")
    return f


def _int(raw: dict, key: str, where: str, *, minimum: int | None = None) -> int:
    v = raw[key]
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError(f"{where}.{key}: expected an integer, got {v!r}")
    if minimum is not None and v < minimum:
        raise ConfigError(f"{where}.{key}: must be >= {minimum}, got {v!r}")
    return v


def _bool(raw: dict, key: str, where: str) -> bool:
    v = raw[key]
    if not isinstance(v, bool):
        raise ConfigError(f"{where}.{key}: expected true/false, got {v!r}")
    return v


def _str(raw: dict, key: str, where: str, *, allow_empty: bool = False) -> str:
    v = raw[key]
    if not isinstance(v, str):
        raise ConfigError(f"{where}.{key}: expected a string, got {v!r}")
    if not allow_empty and not v.strip():
        raise ConfigError(f"{where}.{key}: must not be empty")
    return v


def _hhmm(raw: dict, key: str, where: str) -> time:
    v = raw[key]
    if not isinstance(v, str) or not (m := _TIME_RE.match(v)):
        raise ConfigError(f"{where}.{key}: expected a quoted 'HH:MM' string, got {v!r}")
    return time(int(m.group(1)), int(m.group(2)))


# --------------------------------------------------------------------------- section parsers


def _parse_account(raw: Any) -> AccountConfig:
    w = "account"
    d = _mapping(
        raw,
        w,
        {
            "starting_capital_inr",
            "max_open_positions",
            "max_lots",
            "max_premium_allocation_pct",
            "max_daily_loss_pct",
        },
    )
    cfg = AccountConfig(
        starting_capital_inr=_num(d, "starting_capital_inr", w, minimum=0.0, exclusive_min=True),
        max_open_positions=_int(d, "max_open_positions", w, minimum=1),
        max_lots=_int(d, "max_lots", w, minimum=1),
        max_premium_allocation_pct=_num(
            d, "max_premium_allocation_pct", w, minimum=0.0, exclusive_min=True, maximum=100.0
        ),
        max_daily_loss_pct=_num(
            d, "max_daily_loss_pct", w, minimum=0.0, exclusive_min=True, maximum=100.0
        ),
    )
    if cfg.max_open_positions != 1:
        raise ConfigError(
            f"account.max_open_positions: only 1 is supported by the single-position ledger, "
            f"got {cfg.max_open_positions}"
        )
    return cfg


def _parse_session(raw: Any) -> SessionConfig:
    w = "session"
    d = _mapping(
        raw, w, {"entry_cutoff_ist", "square_off_ist", "market_open_ist", "market_close_ist"}
    )
    cfg = SessionConfig(
        market_open=_hhmm(d, "market_open_ist", w),
        entry_cutoff=_hhmm(d, "entry_cutoff_ist", w),
        square_off=_hhmm(d, "square_off_ist", w),
        market_close=_hhmm(d, "market_close_ist", w),
    )
    if not (cfg.market_open < cfg.entry_cutoff < cfg.square_off <= cfg.market_close):
        raise ConfigError(
            "session: require market_open < entry_cutoff < square_off <= market_close, got "
            f"{cfg.market_open} / {cfg.entry_cutoff} / {cfg.square_off} / {cfg.market_close}"
        )
    return cfg


def _parse_execution(raw: Any) -> ExecutionConfig:
    w = "execution"
    d = _mapping(
        raw,
        w,
        {
            "entry_slippage_points",
            "exit_slippage_points",
            "max_spread_points",
            "max_spread_pct",
            "max_quote_age_seconds",
            "expiry_policy",
            "min_days_to_expiry",
            "strike_selection",
            "strike_step",
        },
        {"allow_expiry_day"},
    )
    policy = _str(d, "expiry_policy", w).upper()
    if policy not in EXPIRY_POLICIES:
        raise ConfigError(
            f"execution.expiry_policy: must be one of {EXPIRY_POLICIES}, got {policy!r}"
        )
    selection = _str(d, "strike_selection", w).upper()
    if selection != "ATM":
        raise ConfigError(f"execution.strike_selection: only 'ATM' is supported, got {selection!r}")
    allow_expiry_day = _bool(d, "allow_expiry_day", w) if "allow_expiry_day" in d else True
    return ExecutionConfig(
        entry_slippage_points=_num(d, "entry_slippage_points", w, minimum=0.0),
        exit_slippage_points=_num(d, "exit_slippage_points", w, minimum=0.0),
        max_spread_points=_num(d, "max_spread_points", w, minimum=0.0, exclusive_min=True),
        max_spread_pct=_num(d, "max_spread_pct", w, minimum=0.0, exclusive_min=True),
        max_quote_age_seconds=_num(d, "max_quote_age_seconds", w, minimum=0.0, exclusive_min=True),
        expiry_policy=policy,
        min_days_to_expiry=_int(d, "min_days_to_expiry", w, minimum=0),
        strike_selection=selection,
        strike_step=_num(d, "strike_step", w, minimum=0.0, exclusive_min=True),
        allow_expiry_day=allow_expiry_day,
    )


def _parse_strategies(raw: Any) -> dict[str, StrategyConfig]:
    w = "strategies"
    if not isinstance(raw, dict):
        raise ConfigError(f"{w}: expected a mapping, got {type(raw).__name__}")
    unknown = sorted(set(map(str, raw)) - set(KNOWN_STRATEGIES))
    if unknown:
        raise ConfigError(f"{w}: unknown strategy name(s) {unknown}")
    out: dict[str, StrategyConfig] = {}
    for name in KNOWN_STRATEGIES:
        if name not in raw:
            continue
        sw = f"strategies.{name}"
        entry = raw[name]
        if name in DISABLED_STRATEGIES:
            d = _mapping(entry, sw, {"enabled", "tier"}, {"method"})
        else:
            d = _mapping(entry, sw, {"enabled", "method", "tier"})
        enabled = _bool(d, "enabled", sw)
        tier_text = _str(d, "tier", sw).upper()
        try:
            tier = ExecutionTier(tier_text)
        except ValueError as exc:
            raise ConfigError(f"{sw}.tier: unknown tier {tier_text!r}") from exc
        if name in DISABLED_STRATEGIES:
            if enabled or tier != ExecutionTier.DISABLED:
                raise ConfigError(
                    f"{sw}: {name} is permanently disabled (enabled=false, tier=DISABLED)"
                )
            out[name] = StrategyConfig(name, False, ExecutionTier.DISABLED, d.get("method"))
            continue
        if tier not in (ExecutionTier.DISABLED, ExecutionTier.SHADOW_ONLY):
            raise ConfigError(
                f"{sw}.tier: {tier.value} cannot be set here. Allowed: DISABLED, SHADOW_ONLY. "
                "Simulated fills are only granted through the research_paper block + CLI flag; "
                "AUTHOR_APPROVED is never granted by this build."
            )
        method = _str(d, "method", sw).upper()
        if method != STRATEGY_METHODS[name]:
            raise ConfigError(
                f"{sw}.method: {name} supports only {STRATEGY_METHODS[name]!r}, got {method!r}"
            )
        out[name] = StrategyConfig(name, enabled, tier, method)
    return out


def _parse_research(raw: Any) -> ResearchPaperConfig:
    w = "research_paper"
    d = _mapping(raw, w, {"enabled", "acknowledgement", "strategies"})
    enabled = _bool(d, "enabled", w)
    ack = _str(d, "acknowledgement", w, allow_empty=True)
    strategies = d["strategies"]
    if not isinstance(strategies, list) or not all(isinstance(s, str) for s in strategies):
        raise ConfigError(f"{w}.strategies: expected a list of strategy names")
    names = tuple(s.upper() for s in strategies)
    bad = sorted(set(names) - set(ACTIVE_STRATEGIES))
    if bad:
        raise ConfigError(
            f"{w}.strategies: {bad} not allowed (permitted: {list(ACTIVE_STRATEGIES)}; "
            "RF is permanently disabled)"
        )
    if len(set(names)) != len(names):
        raise ConfigError(f"{w}.strategies: duplicate entries")
    return ResearchPaperConfig(enabled=enabled, acknowledgement=ack, strategies=names)


def _parse_candles(raw: Any) -> CandlesConfig:
    w = "candles"
    d = _mapping(
        raw,
        w,
        {
            "source",
            "fail_closed",
            "provider_poll_delay_seconds",
            "provider_wait_max_seconds",
            "spot_crosscheck_tolerance_points",
        },
        {"require_sample_crosscheck", "uncrosschecked_min_age_seconds"},
    )
    source = _str(d, "source", w).upper()
    if source != "PROVIDER":
        raise ConfigError(
            f"candles.source: only 'PROVIDER' (completed provider candles) is allowed, got {source!r}"
        )
    if not _bool(d, "fail_closed", w):
        raise ConfigError(
            "candles.fail_closed: must be true (the live path never trades untrusted candles)"
        )
    cfg = CandlesConfig(
        source=source,
        fail_closed=True,
        provider_poll_delay_seconds=_num(d, "provider_poll_delay_seconds", w, minimum=0.0),
        provider_wait_max_seconds=_num(
            d, "provider_wait_max_seconds", w, minimum=0.0, exclusive_min=True
        ),
        spot_crosscheck_tolerance_points=_num(
            d, "spot_crosscheck_tolerance_points", w, minimum=0.0, exclusive_min=True
        ),
        require_sample_crosscheck=(
            _bool(d, "require_sample_crosscheck", w) if "require_sample_crosscheck" in d else True
        ),
        uncrosschecked_min_age_seconds=(
            _num(d, "uncrosschecked_min_age_seconds", w, minimum=0.0, exclusive_min=True)
            if "uncrosschecked_min_age_seconds" in d
            else 60.0
        ),
    )
    if cfg.provider_wait_max_seconds < cfg.provider_poll_delay_seconds:
        raise ConfigError(
            "candles: provider_wait_max_seconds must be >= provider_poll_delay_seconds"
        )
    return cfg


def _parse_polling(raw: Any) -> PollingConfig:
    w = "polling"
    d = _mapping(raw, w, {"interval_seconds", "option_depth_interval_seconds"})
    return PollingConfig(
        interval_seconds=_num(d, "interval_seconds", w, minimum=0.0, exclusive_min=True),
        option_depth_interval_seconds=_num(
            d, "option_depth_interval_seconds", w, minimum=0.0, exclusive_min=True
        ),
    )


def _parse_paths(raw: Any) -> PathsConfig:
    w = "paths"
    d = _mapping(raw, w, {"runtime_dir", "scrip_master"})
    return PathsConfig(
        runtime_dir=_str(d, "runtime_dir", w), scrip_master=_str(d, "scrip_master", w)
    )


# --------------------------------------------------------------------------- public API

_TOP_LEVEL = {
    "account",
    "session",
    "execution",
    "strategies",
    "research_paper",
    "candles",
    "polling",
    "paths",
}


def parse_live_config(raw: Any, base_dir: Path | None = None) -> LiveConfig:
    """Validate an already-loaded mapping and build a :class:`LiveConfig`."""
    d = _mapping(raw, "<root>", _TOP_LEVEL)
    return LiveConfig(
        account=_parse_account(d["account"]),
        session=_parse_session(d["session"]),
        execution=_parse_execution(d["execution"]),
        strategies=_parse_strategies(d["strategies"]),
        research_paper=_parse_research(d["research_paper"]),
        candles=_parse_candles(d["candles"]),
        polling=_parse_polling(d["polling"]),
        paths=_parse_paths(d["paths"]),
        base_dir=base_dir or PROJECT_ROOT,
    )


def load_live_config(path: str | Path | None = None, base_dir: Path | None = None) -> LiveConfig:
    """Load and validate ``configs/live_paper.yaml`` (or ``path``).

    Raises :class:`ConfigError` if the file is missing/unreadable/invalid.  There are no
    silent defaults: every required key must be present.
    """
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.is_file():
        raise ConfigError(
            f"live config file not found: {cfg_path}. Refusing to start with implicit defaults."
        )
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read live config {cfg_path}: {exc}") from exc
    if raw is None:
        raise ConfigError(f"live config {cfg_path} is empty")
    return parse_live_config(raw, base_dir=base_dir)
