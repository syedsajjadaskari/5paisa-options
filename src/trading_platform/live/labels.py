"""Human-readable labels for strategies, rule ids, directions and exit reasons.

One shared place so the terminal, the dashboard and the journals show the same wording.
Pure functions, no I/O.  Unknown ids degrade gracefully to a readable form of the id
(never raise, never invent a rule that the code did not fire).

IMPORTANT: these are *descriptions of what the code's rule checked*.  Several underlying
rules are PROVISIONAL interpretations of the author PDFs (see each strategy's
``PROVISIONAL_RULES``); a readable sentence here never implies author approval.
"""

from __future__ import annotations

# strategy -> (short label shown in tables, long name)
_STRATEGY_METHOD: dict[str, tuple[str, str]] = {
    "PDS": ("PDS M1", "PDS Method 1 (Previous Day Support, short)"),
    "ORB": ("ORB M1", "ORB Method 1 (Opening Range / PDH-PDL breakout)"),
    "IRF": ("IRF M2", "IRF Method 2 (Initial Range Failure, extended move)"),
    "PDR": ("PDR Retest", "PDR Retest Bounce (Previous Day Resistance, long)"),
    "RF": ("RF", "RF (permanently disabled)"),
}

# rule id (Signal.method_id) -> sentence describing the confirmation that fired
_RULE_SENTENCES: dict[str, str] = {
    "ORB_METHOD_1_PDH_BREAKOUT": "NIFTY completed a breakout confirmation above the previous-day high (PDH)",
    "ORB_METHOD_1_PDL_BREAKDOWN": "NIFTY completed a breakdown confirmation below the previous-day low (PDL)",
    "ORB_METHOD_2_INNER_RESISTANCE_BREAKOUT": "NIFTY broke out above the inner resistance level (ORB Method 2)",
    "ORB_METHOD_2_INNER_SUPPORT_BREAKDOWN": "NIFTY broke down below the inner support level (ORB Method 2)",
    "ORB_METHOD_3_GAP_ABOVE_200DMA": "Gap above the 200 DMA confirmed (ORB Method 3)",
    "ORB_METHOD_3_GAP_BELOW_200DMA": "Gap below the 200 DMA confirmed (ORB Method 3)",
    "PDS_METHOD_1_STRONG_RED_CANDLE": (
        "Strong red candle rejected the previous-day support zone after a gap-down open"
    ),
    "PDS_METHOD_2_HPR_PIN_BAR": (
        "Red pin bar with a long upper wick rejected the previous-day support zone"
    ),
    "PDS_METHOD_3_SMALL_SUPPORT_BREAK": (
        "Price broke the small support formed near the previous-day support zone"
    ),
    "PDR_RETEST_BOUNCE": (
        "Green bounce confirmed at the previous-day resistance zone after a gap-up open"
    ),
    "IRF_METHOD_1_IMMEDIATE_FAILURE": "Initial-range breakout failed immediately (IRF Method 1)",
    "IRF_METHOD_2_SUPPORT_BREAK": (
        "After an extended move above the initial range high, a candle closed below the pullback support"
    ),
    "IRF_METHOD_2_RESISTANCE_BREAK": (
        "After an extended move above the initial range high, a red candle closed back below the new resistance"
    ),
    "IRF_METHOD_3_LIQUIDITY_SWEEP": "Liquidity sweep beyond the initial range reversed (IRF Method 3)",
}

_EXIT_SENTENCES = {
    "TARGET": "Underlying NIFTY reached the target level",
    "STOP": "Underlying NIFTY reached the stop level",
    "EOD": "Intraday square-off time reached (15:15 IST)",
    "RISK": "Daily loss limit protection triggered",
    "STRATEGY_EXIT": "Strategy exit rule triggered",
    "SHUTDOWN_UNRESOLVED": "Shutdown with an unresolved position (no fill was invented)",
}


def strategy_label(strategy: str, method_or_rule: str | None = None) -> str:
    """Short table label, e.g. ``ORB M1`` / ``IRF M2`` / ``PDR Retest``.

    ``method_or_rule`` is accepted for call-site symmetry; the labels follow the authorized
    portfolio configuration (one method per strategy), but a rule id from a non-authorized
    method (e.g. ``ORB_METHOD_2_...``) is shown with its own method number so it is never
    mislabelled as Method 1.
    """
    name = (strategy or "?").upper()
    base = _STRATEGY_METHOD.get(name, (name, name))[0]
    if method_or_rule:
        rule = method_or_rule.upper()
        if rule.startswith(f"{name}_METHOD_"):
            number = rule[len(name) + len("_METHOD_") :].split("_", 1)[0]
            if number.isdigit():
                return f"{name} M{number}"
    return base


def strategy_long_name(strategy: str) -> str:
    name = (strategy or "?").upper()
    return _STRATEGY_METHOD.get(name, (name, name))[1]


def rule_sentence(rule_id: str | None, fallback: str | None = None) -> str:
    """Plain-English description of the rule id that fired (never invents a rule)."""
    if not rule_id:
        return fallback or "not recorded"
    key = rule_id.upper()
    if key in _RULE_SENTENCES:
        return _RULE_SENTENCES[key]
    if fallback and fallback.strip() and fallback.strip().upper() != key:
        return fallback.strip()
    return key.replace("_", " ").capitalize()


def exit_sentence(reason: str | None) -> str:
    if not reason:
        return "not recorded"
    return _EXIT_SENTENCES.get(reason.upper(), reason.replace("_", " ").capitalize())


def direction_label(direction: str | None, option_type: str | None = None) -> str:
    """``BUY CE`` / ``BUY PE`` from a BULLISH/BEARISH direction or an explicit option type."""
    if option_type:
        return f"BUY {option_type.upper()}"
    d = (direction or "").upper()
    if d in {"BULLISH", "BUY", "LONG"}:
        return "BUY CE"
    if d in {"BEARISH", "SELL", "SHORT"}:
        return "BUY PE"
    return d or "n/a"


def tier_label(tier: str | None, label: str | None = None, *, replay: bool = False) -> str:
    """Honest tier text.  A replay is never described as an 'explicit opt-in'."""
    t = (tier or "").upper()
    if replay or (label or "").upper().startswith("REPLAY"):
        return "REPLAY-SIMULATED (not an opt-in)"
    return {
        "DISABLED": "DISABLED",
        "SHADOW_ONLY": "SHADOW ONLY (signals logged, no virtual fills)",
        "RESEARCH_PAPER": "RESEARCH PAPER (explicit opt-in; PROVISIONAL rules, simulated fills)",
        "AUTHOR_APPROVED": "AUTHOR APPROVED",
    }.get(t, t or "n/a")
