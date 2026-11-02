"""Authorization matrix tests (live/authorization.py)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_core_helpers import cfg

from trading_platform.live.authorization import (
    RESEARCH_PAPER_ACK,
    authorize,
    authorize_all,
    can_fill,
    provisional_rules,
)
from trading_platform.live.config import ResearchPaperConfig, StrategyConfig
from trading_platform.live.models import ExecutionTier

ACTIVE = ["PDS", "ORB", "IRF", "PDR"]


def research_cfg(*, enabled=True, ack=RESEARCH_PAPER_ACK, strategies=("PDS", "ORB")):
    return replace(cfg(), research_paper=ResearchPaperConfig(enabled, ack, tuple(strategies)))


def test_ack_phrase_is_exact():
    assert RESEARCH_PAPER_ACK == (
        "I UNDERSTAND THESE RULES ARE PROVISIONAL AND NOT AUTHOR-APPROVED; SIMULATED FILLS ONLY"
    )


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("name", ACTIVE)
def test_default_config_is_shadow_only_even_with_cli_flag(name, flag):
    d = authorize(name, cfg(), flag)
    assert d.tier == ExecutionTier.SHADOW_ONLY
    assert d.label == "SHADOW_ONLY"
    assert not d.can_fill and not can_fill(d.tier)
    assert d.reasons


@pytest.mark.parametrize("flag", [False, True])
def test_rf_always_disabled_even_if_listed_and_fully_opted_in(flag):
    c = research_cfg(strategies=("PDS", "RF"))  # constructed programmatically (loader would reject)
    c = replace(
        c, strategies={**c.strategies, "RF": StrategyConfig("RF", True, ExecutionTier.SHADOW_ONLY)}
    )
    d = authorize("RF", c, flag)
    assert d.tier == ExecutionTier.DISABLED and d.label == "DISABLED"
    assert not d.can_fill
    assert authorize("rf", c, flag).tier == ExecutionTier.DISABLED


def test_unknown_strategy_denied():
    assert authorize("XYZ", cfg(), True).tier == ExecutionTier.DISABLED
    assert authorize("", cfg(), True).tier == ExecutionTier.DISABLED


def test_disabled_in_config_denied_even_if_listed():
    c = research_cfg(strategies=("PDS",))
    c = replace(
        c,
        strategies={
            **c.strategies,
            "PDS": StrategyConfig("PDS", False, ExecutionTier.DISABLED, "METHOD_1"),
        },
    )
    assert authorize("PDS", c, True).tier == ExecutionTier.DISABLED
    c2 = replace(
        c,
        strategies={
            **c.strategies,
            "PDS": StrategyConfig("PDS", True, ExecutionTier.DISABLED, "METHOD_1"),
        },
    )
    assert authorize("PDS", c2, True).tier == ExecutionTier.DISABLED


def test_full_opt_in_grants_research_paper_with_provisional_label():
    c = research_cfg()
    d = authorize("PDS", c, True)
    assert d.tier == ExecutionTier.RESEARCH_PAPER
    assert d.label == "RESEARCH_PAPER_PROVISIONAL"
    assert d.can_fill and can_fill(d.tier)
    assert d.provisional_rules  # disclosures from PDSStrategy.PROVISIONAL_RULES
    # not listed -> stays shadow
    assert authorize("IRF", c, True).tier == ExecutionTier.SHADOW_ONLY


@pytest.mark.parametrize(
    "kwargs,flag,missing_text",
    [
        ({"enabled": False}, True, "enabled is false"),
        ({"ack": ""}, True, "acknowledgement"),
        ({"ack": RESEARCH_PAPER_ACK.lower()}, True, "acknowledgement"),
        ({"ack": RESEARCH_PAPER_ACK + " "}, True, "acknowledgement"),
        ({"ack": " " + RESEARCH_PAPER_ACK}, True, "acknowledgement"),
        ({}, False, "CLI flag"),
        ({"strategies": ()}, True, "not listed"),
        ({"strategies": ("ORB",)}, True, "not listed"),
    ],
)
def test_every_opt_in_condition_is_required(kwargs, flag, missing_text):
    d = authorize("PDS", research_cfg(**kwargs), flag)
    assert d.tier == ExecutionTier.SHADOW_ONLY
    assert missing_text in " ".join(d.reasons)


def test_non_bool_cli_flag_does_not_count():
    assert authorize("PDS", research_cfg(), 1).tier == ExecutionTier.SHADOW_ONLY  # type: ignore[arg-type]
    assert authorize("PDS", research_cfg(), "yes").tier == ExecutionTier.SHADOW_ONLY  # type: ignore[arg-type]


def test_author_approved_is_never_granted():
    for flag in (False, True):
        for c in (cfg(), research_cfg()):
            for name in ACTIVE + ["RF"]:
                assert authorize(name, c, flag).tier != ExecutionTier.AUTHOR_APPROVED
    # a programmatically requested AUTHOR_APPROVED / RESEARCH_PAPER tier in the strategy block is ignored
    c = cfg()
    c = replace(
        c,
        strategies={
            **c.strategies,
            "PDS": StrategyConfig("PDS", True, ExecutionTier.AUTHOR_APPROVED, "METHOD_1"),
        },
    )
    d = authorize("PDS", c, True)
    assert d.tier == ExecutionTier.SHADOW_ONLY
    assert "ignored" in " ".join(d.reasons)


def test_can_fill_helper():
    assert can_fill(ExecutionTier.RESEARCH_PAPER)
    assert not can_fill(ExecutionTier.SHADOW_ONLY)
    assert not can_fill(ExecutionTier.DISABLED)


def test_authorize_all_covers_rf_and_active():
    res = authorize_all(research_cfg(), True)
    assert set(res) == {"PDS", "ORB", "IRF", "PDR", "RF"}
    assert res["RF"].tier == ExecutionTier.DISABLED
    assert res["PDS"].tier == ExecutionTier.RESEARCH_PAPER
    assert res["PDR"].tier == ExecutionTier.SHADOW_ONLY


def test_provisional_rules_tolerates_unknown_strategy_and_missing_attr(monkeypatch):
    assert provisional_rules("NOPE") == ()
    assert provisional_rules("RF") == ()
    import trading_platform.live.authorization as auth

    monkeypatch.setitem(
        auth._STRATEGY_CLASSES, "PDS", ("trading_platform.strategies.nonexistent", "X")
    )
    assert provisional_rules("PDS") == ()  # import failure tolerated
