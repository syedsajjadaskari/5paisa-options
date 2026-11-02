"""Deny-by-default execution authorization for the live paper application.

``authorize(strategy_name, cfg, cli_research_flag)`` is the ONE place that decides whether a
strategy may create virtual fills:

* RF (and any unknown / disabled strategy)                      -> DISABLED
* every enabled strategy, by default                            -> SHADOW_ONLY (no fills)
* RESEARCH_PAPER requires ALL of:
    - ``research_paper.enabled`` is true,
    - ``research_paper.acknowledgement`` equals :data:`RESEARCH_PAPER_ACK` exactly,
    - the ``--research-paper`` CLI flag was given,
    - the strategy is listed in ``research_paper.strategies``
                                                                -> RESEARCH_PAPER (label
                                                                   ``RESEARCH_PAPER_PROVISIONAL``)
* AUTHOR_APPROVED is never granted by this build.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from trading_platform.live.config import ACTIVE_STRATEGIES, LiveConfig
from trading_platform.live.models import ExecutionTier

RESEARCH_PAPER_ACK = (
    "I UNDERSTAND THESE RULES ARE PROVISIONAL AND NOT AUTHOR-APPROVED; SIMULATED FILLS ONLY"
)

LABEL_RESEARCH = "RESEARCH_PAPER_PROVISIONAL"
LABEL_SHADOW = "SHADOW_ONLY"
LABEL_DISABLED = "DISABLED"

_STRATEGY_CLASSES: dict[str, tuple[str, str]] = {
    "PDS": ("trading_platform.strategies.pds", "PDSStrategy"),
    "ORB": ("trading_platform.strategies.orb", "ORBStrategy"),
    "IRF": ("trading_platform.strategies.irf", "IRFStrategy"),
    "PDR": ("trading_platform.strategies.pdr", "PDRStrategy"),
}


@dataclass(frozen=True)
class AuthDecision:
    tier: ExecutionTier
    label: str
    reasons: tuple[str, ...]
    strategy: str = ""
    provisional_rules: tuple[str, ...] = ()

    @property
    def can_fill(self) -> bool:
        return can_fill(self.tier)


def can_fill(tier: ExecutionTier) -> bool:
    """True only for tiers that may create (virtual) fills."""
    return tier in (ExecutionTier.RESEARCH_PAPER, ExecutionTier.AUTHOR_APPROVED)


def provisional_rules(strategy_name: str) -> tuple[str, ...]:
    """Provisional-rule disclosures from ``<Strategy>.PROVISIONAL_RULES`` (tolerates absence)."""
    target = _STRATEGY_CLASSES.get(strategy_name.upper())
    if target is None:
        return ()
    try:
        cls = getattr(importlib.import_module(target[0]), target[1])
        rules = getattr(cls, "PROVISIONAL_RULES", ())
        return tuple(str(r) for r in rules)
    except Exception:  # noqa: BLE001 - disclosure lookup must never break authorization
        return ()


def _decision(
    tier: ExecutionTier, label: str, reasons: list[str], name: str, with_rules: bool
) -> AuthDecision:
    rules = provisional_rules(name) if with_rules else ()
    return AuthDecision(tier, label, tuple(reasons), strategy=name, provisional_rules=rules)


def authorize(strategy_name: str, cfg: LiveConfig, cli_research_flag: bool) -> AuthDecision:
    """Return the execution tier for ``strategy_name`` (deny by default)."""
    name = str(strategy_name).upper()

    if name == "RF":
        return _decision(
            ExecutionTier.DISABLED, LABEL_DISABLED, ["RF is permanently disabled"], name, False
        )

    scfg = cfg.strategies.get(name)
    if name not in ACTIVE_STRATEGIES or scfg is None:
        return _decision(
            ExecutionTier.DISABLED,
            LABEL_DISABLED,
            [f"strategy {strategy_name!r} is unknown or not configured"],
            name,
            False,
        )
    if not scfg.enabled or scfg.tier == ExecutionTier.DISABLED:
        return _decision(
            ExecutionTier.DISABLED,
            LABEL_DISABLED,
            [f"{name} is disabled in configuration"],
            name,
            False,
        )

    reasons: list[str] = []
    if scfg.tier not in (ExecutionTier.SHADOW_ONLY,):
        reasons.append(
            f"configured tier {scfg.tier.value} ignored: fills are granted only via research_paper"
        )

    rp = cfg.research_paper
    missing: list[str] = []
    if not rp.enabled:
        missing.append("research_paper.enabled is false")
    if rp.acknowledgement != RESEARCH_PAPER_ACK:
        missing.append("research_paper.acknowledgement does not equal the required phrase")
    if cli_research_flag is not True:
        missing.append("CLI flag --research-paper not given")
    if name not in {s.upper() for s in rp.strategies}:
        missing.append(f"{name} not listed in research_paper.strategies")

    if not missing:
        reasons.append(
            "RESEARCH_PAPER granted: explicit opt-in (config + acknowledgement + CLI flag + "
            "strategy listed); PROVISIONAL rules, SIMULATED fills only"
        )
        return _decision(ExecutionTier.RESEARCH_PAPER, LABEL_RESEARCH, reasons, name, True)

    reasons.append("SHADOW_ONLY by default (no fills): " + "; ".join(missing))
    return _decision(ExecutionTier.SHADOW_ONLY, LABEL_SHADOW, reasons, name, True)


def authorize_all(cfg: LiveConfig, cli_research_flag: bool) -> dict[str, AuthDecision]:
    """Authorize every known strategy (RF included, always DISABLED)."""
    return {n: authorize(n, cfg, cli_research_flag) for n in (*ACTIVE_STRATEGIES, "RF")}
