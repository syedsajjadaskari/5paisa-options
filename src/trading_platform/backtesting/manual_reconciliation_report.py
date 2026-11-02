"""Build the manual-vs-V3 reconciliation tables (no file output; see the script)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from trading_platform.backtesting.manual_diagnostics import (
    ALTERNATE_METHODS,
    V3_CLASSES,
    DayReplayer,
    RootCause,
    classify_unmatched,
)
from trading_platform.backtesting.manual_reconciliation import (
    CATEGORY_ORDER,
    UNKNOWN,
    VALID_CATEGORIES,
    build_signal_candidates,
    build_trade_candidates,
    classify_manual_records,
    convention_evidence,
    manual_outcome_code,
    match_one_to_one,
    outcome_agreement_label,
)

MANUAL_MISSING = "direction;method;entry_price;stop;target"
STRATEGIES = ("PDS", "ORB", "IRF", "PDR", "RF")


@dataclass
class Reconciliation:
    all_trades: pd.DataFrame
    strategy_summary: pd.DataFrame
    reasons: pd.DataFrame
    outcome_matrix: pd.DataFrame
    headline: dict[str, Any]
    evidence: dict[str, Any]
    notes: dict[str, Any]


def _f(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return value


def reconcile(
    manual_raw: pd.DataFrame,
    bt: dict[str, pd.DataFrame],
    replayer: DayReplayer,
    run_id: str,
    manual_notes: dict[str, Any],
) -> Reconciliation:
    session_dates = {d.isoformat() for d in replayer.days}
    first_bar_dates = {
        d.isoformat()
        for d, bars in replayer.by_day.items()
        if any(b.exchange_time.strftime("%H:%M") == "09:15" for b in bars)
    }
    manual = classify_manual_records(manual_raw, session_dates, first_bar_dates)

    sig = build_signal_candidates(bt)
    sa = build_trade_candidates(bt["standalone_trades"])
    pf = build_trade_candidates(bt["portfolio_trades"])
    m_sig = match_one_to_one(manual, sig)
    m_sa = match_one_to_one(manual, sa)
    m_pf = match_one_to_one(manual, pf)

    sa_by_id = sa.set_index("trade_id")
    sig_by_idx = sig
    rows: list[dict[str, Any]] = []
    reason_rows: list[dict[str, Any]] = []
    alt_cache: dict[tuple[date, str], dict[str, list[dict[str, Any]]]] = {}
    v3_cache: dict[tuple[date, str], Any] = {}

    for idx, m in manual.iterrows():
        cand, diff = m_sig.pairs[idx]
        category = m_sig.category(idx) if m.eligibility == "ELIGIBLE" else "UNMATCHED"
        valid = category in VALID_CATEGORIES
        mo = manual_outcome_code(m.outcome)
        s = sig_by_idx.loc[cand] if cand is not None else None
        trade = None
        if (
            s is not None
            and isinstance(s.standalone_trade_id, str)
            and s.standalone_trade_id in sa_by_id.index
        ):
            trade = sa_by_id.loc[s.standalone_trade_id]
        auto_out = trade["auto_outcome"] if trade is not None else "NO_TRADE"
        agreement = (
            outcome_agreement_label(mo, auto_out) if valid else "NOT_COMPARED_NO_VALID_MATCH"
        )
        accepted = bool(valid and s is not None and s.portfolio_accepted)
        sa_cat = m_sa.category(idx) if m.eligibility == "ELIGIBLE" else "UNMATCHED"
        pf_cat = m_pf.category(idx) if m.eligibility == "ELIGIBLE" else "UNMATCHED"
        pf_idx = m_pf.pairs[idx][0]

        row: dict[str, Any] = {
            "manual_serial": int(m.sl_no),
            "date": m.date,
            "manual_strategy": m.strategy,
            "manual_entry_time": m.entry_time,
            "manual_outcome": m.outcome,
            "manual_direction": UNKNOWN,
            "manual_method": UNKNOWN,
            "eligibility": m.eligibility,
            "automated_strategy": _f(s.strategy) if s is not None else "",
            "automated_method": _f(s.method) if s is not None else "",
            "automated_direction": _f(s.direction) if s is not None else "",
            "automated_signal_time": _f(s.signal_candle_open[:19]) if s is not None else "",
            "automated_signal_candle_close": _f(s.signal_candle_close[:19])
            if s is not None
            else "",
            "automated_trade_entry_time": _f(trade["entry_time"][:19]) if trade is not None else "",
            "automated_exit_time": _f(trade["exit_time"][:19]) if trade is not None else "",
            "automated_exit_reason": _f(trade["exit_reason"]) if trade is not None else "",
            "automated_nifty_entry_price": _f(trade["entry_price"]) if trade is not None else "",
            "automated_stop_loss": _f(trade["stop_loss"]) if trade is not None else "",
            "automated_target": _f(trade["target"]) if trade is not None else "",
            "automated_net_index_points": _f(trade["points_pnl"]) if trade is not None else "",
            "timestamp_diff_min_manual_minus_signal_open": "" if diff is None else int(round(diff)),
            "timestamp_diff_min_manual_minus_entry_fill": (
                ""
                if trade is None
                else int(
                    round(
                        (m.manual_ts - pd.Timestamp(trade["entry_time"][:19])).total_seconds() / 60
                    )
                )
            ),
            "match_category": category,
            "valid_match_le_10min": "YES" if valid else "NO",
            "outcome_agreement": agreement,
            "signal_generated": "YES" if valid else "NO",
            "portfolio_accepted": "YES" if accepted else "NO",
            "rejection_reason": "",
            "standalone_level_match_category": sa_cat,
            "portfolio_level_match_category": pf_cat,
            "portfolio_level_trade_id": _f(pf.loc[pf_idx, "trade_id"])
            if pf_idx is not None
            else "",
            "manual_data_missing": "YES",
            "manual_missing_fields": MANUAL_MISSING,
            "convention_note": _convention_note(diff),
            "mismatch_explanation": "",
        }

        reason: RootCause | None = None
        scope = ""
        if m.eligibility == "UNSUPPORTED_RF":
            reason = RootCause(
                "Unsupported strategy (RF disabled)",
                "RF is permanently disabled: the RF PDF defines no stop-loss, target or exit rules",
                "EXCLUDED",
                "NO",
                "preserved as unsupported, not silently dropped",
            )
            scope = "EXCLUDED_UNSUPPORTED"
        elif m.eligibility in ("NON_TRADING_DAY", "NO_FIRST_BAR"):
            reason = RootCause(
                "Unspecified or missing manual information",
                m.eligibility_reason,
                "UNVERIFIABLE_MANUAL",
                "NO",
                f"journal date {m.date} is not a session with a 09:15 candle in the verified 5m dataset",
            )
            scope = "EXCLUDED_NO_SESSION"
        elif m.eligibility != "ELIGIBLE":
            reason = RootCause(
                "Unspecified or missing manual information",
                m.eligibility_reason,
                "UNVERIFIABLE_MANUAL",
                "NO",
                m.eligibility_reason,
            )
            scope = "EXCLUDED_AMBIGUOUS"
        elif not valid:
            day = date.fromisoformat(m.date)
            hhmm = m.entry_time
            cls, cfg = V3_CLASSES[m.strategy]
            key = (day, m.strategy)
            if key not in v3_cache:
                v3_cache[key] = replayer.replay(cls, cfg, day, m.manual_ts.time())
            rep = v3_cache[key]
            if key not in alt_cache:
                alt_cache[key] = {
                    name: replayer.replay(acls, acfg, day, None).signals
                    for name, acls, acfg in ALTERNATE_METHODS.get(m.strategy, [])
                }
            same_day_v3 = [
                str(x.signal_candle_open)[11:16]
                for x in sig[(sig.date == m.date) & (sig.strategy == m.strategy)].itertuples()
            ]
            facts = replayer.orb_first_candle_facts(day) if m.strategy == "ORB" else None
            reason = classify_unmatched(m.strategy, hhmm, rep, same_day_v3, alt_cache[key], facts)
            scope = "SIGNAL_LEVEL_" + (
                "DIFFERENT_TIMING" if category == "DIFFERENT" else "UNMATCHED"
            )
            row["_diag"] = {
                "snapshot": rep.snapshot_at_manual or rep.snapshot_eod,
                "facts": facts,
                "alt": alt_cache[key],
                "bar": replayer.bar_at(day, hhmm),
                "v3_same_day": same_day_v3,
            }
        elif not accepted:
            r = (
                s.portfolio_reason
                if isinstance(s.portfolio_reason, str) and s.portfolio_reason
                else s.standalone_reason
            )
            r = r if isinstance(r, str) and r else "NOT_ACCEPTED"
            cut = "CUTOFF" in r
            reason = RootCause(
                "Session rule (14:30 entry cutoff)" if cut else "Portfolio position conflict",
                f"signal matched but {r}"
                + (
                    f" (blocked by {s.blocking_trade_id})"
                    if isinstance(s.blocking_trade_id, str) and s.blocking_trade_id
                    else ""
                ),
                "PORTFOLIO",
                "NO",
                f"standalone {s.standalone_status}/{_f(s.standalone_reason)}; portfolio {s.portfolio_status}/{_f(s.portfolio_reason)}",
            )
            scope = "PORTFOLIO_LEVEL"
            row["rejection_reason"] = r
        if reason is not None:
            row["rejection_reason"] = row["rejection_reason"] or reason.category
            row["mismatch_explanation"] = (
                f"{reason.category}: {reason.sub_reason}. Evidence: {reason.evidence}"
            )
            diag = row.get("_diag") or {}
            reason_rows.append(
                {
                    "manual_serial": int(m.sl_no),
                    "date": m.date,
                    "manual_strategy": m.strategy,
                    "manual_entry_time": m.entry_time,
                    "manual_outcome": m.outcome,
                    "scope": scope,
                    "match_category": category,
                    "root_cause": reason.category,
                    "sub_reason": reason.sub_reason,
                    "classification_basis": reason.basis,
                    "implementation_defect": reason.implementation_defect,
                    "evidence": reason.evidence,
                    "strategy_state": _state_text(diag.get("snapshot")),
                    "conditions_failed": "; ".join(
                        str(x) for x in (diag.get("snapshot") or {}).get("conditions_failed", [])
                    ),
                    "conditions_satisfied": "; ".join(
                        str(x) for x in (diag.get("snapshot") or {}).get("conditions_satisfied", [])
                    ),
                    "v3_signals_same_day": ", ".join(diag.get("v3_same_day", [])),
                    "alternate_method_signals_diagnostic_only": "; ".join(
                        f"{k}@{','.join(x['candle_open'] for x in v)}"
                        for k, v in (diag.get("alt") or {}).items()
                        if v
                    ),
                    "candle_at_manual_time_ohlc": _ohlc(diag.get("bar")),
                    "orb_first_candle_close_vs_pdh": _g(diag.get("facts"), "close_vs_pdh"),
                    "orb_first_candle_close_vs_pdl": _g(diag.get("facts"), "close_vs_pdl"),
                    "orb_levels_match_independent_recompute": _g(
                        diag.get("facts"), "levels_match_independent"
                    ),
                    "orb_alt_level_flags_sensitivity_only": _alt_flags(diag.get("facts")),
                }
            )
        rows.append(row)

    all_trades = pd.DataFrame(rows)
    all_trades = all_trades.drop(
        columns=[c for c in all_trades.columns if c.startswith("_")], errors="ignore"
    )
    reasons = pd.DataFrame(reason_rows)
    summary = _strategy_summary(all_trades, reasons, sig, sa, pf)
    matrix = _outcome_matrix(all_trades)
    headline = _headline(all_trades, reasons, summary, manual_notes, run_id)
    evidence = convention_evidence(manual, sig, m_sig)
    return Reconciliation(all_trades, summary, reasons, matrix, headline, evidence, manual_notes)


def _convention_note(diff: float | None) -> str:
    """Make any one-candle offset visible; it is classified CLOSE under the label convention."""
    if diff is None or abs(diff) != 5:
        return ""
    if diff > 0:
        return (
            "journal time is 5 min AFTER the V3 signal candle open = the V3 fill time; classified CLOSE under the "
            "candle-open-label convention (it would be EXACT only if journal times were fill times)"
        )
    return "journal time is one candle BEFORE the V3 signal candle open; classified CLOSE (not explained by any fill-time convention)"


def _g(d: dict[str, Any] | None, key: str) -> Any:
    return "" if not d else _f(d.get(key))


def _ohlc(bar: dict[str, float] | None) -> str:
    return "" if not bar else f"O {bar['o']} H {bar['h']} L {bar['l']} C {bar['c']}"


def _state_text(snap: dict[str, Any] | None) -> str:
    if not snap:
        return ""
    return " | ".join(
        str(snap[k])
        for k in ("state", "status", "phase", "disposition", "reason", "state_reason", "note")
        if snap.get(k)
    )


def _alt_flags(facts: dict[str, Any] | None) -> str:
    if not facts or not facts.get("alt_level_flags"):
        return ""
    return "; ".join(
        k for k, v in facts["alt_level_flags"].items() if v and k.endswith("_would_pass_filters")
    )


def _pct(num: int, den: int) -> float | None:
    return None if den == 0 else round(100.0 * num / den, 1)


def _strategy_summary(
    t: pd.DataFrame, reasons: pd.DataFrame, sig: pd.DataFrame, sa: pd.DataFrame, pf: pd.DataFrame
) -> pd.DataFrame:
    out = []
    rng = (t["date"].min(), t["date"].max())
    for strat in (*STRATEGIES, "TOTAL"):
        g = t if strat == "TOTAL" else t[t.manual_strategy == strat]
        elig = g[g.eligibility == "ELIGIBLE"]
        cats = Counter(elig.match_category)
        valid = elig[elig.valid_match_le_10min == "YES"]
        comparable = valid[~valid.outcome_agreement.str.startswith("UNKNOWN")]
        agree = comparable[
            comparable.outcome_agreement.isin(["AGREE_BOTH_TARGET", "AGREE_BOTH_STOP"])
        ]
        non_be = comparable[comparable.manual_outcome != "BE"]
        acc = valid[valid.portfolio_accepted == "YES"]
        if strat == "TOTAL":
            auto_sig = int(sig[(sig.date >= rng[0]) & (sig.date <= rng[1])].shape[0])
            auto_sa = int(sa[(sa.date >= rng[0]) & (sa.date <= rng[1])].shape[0])
            auto_pf = int(pf[(pf.date >= rng[0]) & (pf.date <= rng[1])].shape[0])
        elif strat == "RF":
            auto_sig = auto_sa = auto_pf = 0
        else:
            auto_sig = int(
                sig[(sig.strategy == strat) & (sig.date >= rng[0]) & (sig.date <= rng[1])].shape[0]
            )
            auto_sa = int(
                sa[(sa.strategy == strat) & (sa.date >= rng[0]) & (sa.date <= rng[1])].shape[0]
            )
            auto_pf = int(
                pf[(pf.strategy == strat) & (pf.date >= rng[0]) & (pf.date <= rng[1])].shape[0]
            )
        rs = (
            reasons[reasons.manual_strategy == strat]
            if (strat != "TOTAL" and not reasons.empty)
            else reasons
        )
        main = ""
        if not rs.empty:
            main = "; ".join(f"{k} ({v})" for k, v in Counter(rs.root_cause).most_common(3))
        out.append(
            {
                "strategy": strat,
                "manual_trades": len(g),
                "eligible_manual_trades": len(elig),
                "excluded_unsupported_rf": int((g.eligibility == "UNSUPPORTED_RF").sum()),
                "excluded_no_session": int(
                    g.eligibility.isin(["NON_TRADING_DAY", "NO_FIRST_BAR"]).sum()
                ),
                "excluded_ambiguous": int((g.eligibility == "AMBIGUOUS").sum()),
                "exact": cats.get("EXACT", 0),
                "close_le_5min": cats.get("CLOSE", 0),
                "near_le_10min": cats.get("NEAR", 0),
                "different_gt_10min": cats.get("DIFFERENT", 0),
                "unmatched": cats.get("UNMATCHED", 0),
                "valid_matched_le_10min": len(valid),
                "signal_match_rate_pct": _pct(len(valid), len(elig)),
                "signal_match_rate_fraction": f"{len(valid)}/{len(elig)}",
                "exact_match_rate_pct": _pct(cats.get("EXACT", 0), len(elig)),
                "standalone_level_valid_matches": int(
                    elig.standalone_level_match_category.isin(VALID_CATEGORIES).sum()
                ),
                "portfolio_level_valid_matches": int(
                    elig.portfolio_level_match_category.isin(VALID_CATEGORIES).sum()
                ),
                "matched_signals_accepted_by_portfolio": len(acc),
                "matched_signals_rejected_by_portfolio": len(valid) - len(acc),
                "outcome_agreement_pct_of_comparable": _pct(len(agree), len(comparable)),
                "outcome_agreement_fraction": f"{len(agree)}/{len(comparable)}",
                "outcome_agreement_pct_excluding_manual_be": _pct(len(agree), len(non_be)),
                "outcome_agreement_excluding_be_fraction": f"{len(agree)}/{len(non_be)}",
                "automated_signals_in_journal_range": auto_sig,
                "automated_standalone_trades_in_range": auto_sa,
                "automated_portfolio_trades_in_range": auto_pf,
                "main_mismatch_reasons": main,
            }
        )
    return pd.DataFrame(out)


def _outcome_matrix(t: pd.DataFrame) -> pd.DataFrame:
    valid = t[t.valid_match_le_10min == "YES"].copy()
    valid["auto_outcome"] = valid.automated_exit_reason.map(_auto_label)
    rows = []
    for scope in ("ALL", "PDS", "ORB", "IRF", "PDR"):
        g = valid if scope == "ALL" else valid[valid.manual_strategy == scope]
        for mo in ("Target", "SL", "BE"):
            for ao in ("TARGET", "STOP", "EOD", "NO_AUTOMATED_EXIT"):
                n = int(((g.manual_outcome == mo) & (g.auto_outcome == ao)).sum())
                rows.append(
                    {"scope": scope, "manual_outcome": mo, "automated_outcome": ao, "count": n}
                )
    return pd.DataFrame(rows)


def _auto_label(reason: Any) -> str:
    r = str(reason or "").upper()
    if not r:
        return "NO_AUTOMATED_EXIT"
    if r == "TARGET":
        return "TARGET"
    if "STOP" in r or "SL" in r:
        return "STOP"
    return "EOD"


def _headline(
    t: pd.DataFrame,
    reasons: pd.DataFrame,
    summary: pd.DataFrame,
    manual_notes: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    tot = summary[summary.strategy == "TOTAL"].iloc[0].to_dict()
    elig = t[t.eligibility == "ELIGIBLE"]
    valid = elig[elig.valid_match_le_10min == "YES"]
    acc = valid[valid.portfolio_accepted == "YES"]
    h = {
        "run_id": run_id,
        "manual_trades": len(t),
        "eligible": len(elig),
        "excluded_rf": int((t.eligibility == "UNSUPPORTED_RF").sum()),
        "excluded_no_session": int(t.eligibility.isin(["NON_TRADING_DAY", "NO_FIRST_BAR"]).sum()),
        "excluded_ambiguous": int((t.eligibility == "AMBIGUOUS").sum()),
        "exact": int(tot["exact"]),
        "close": int(tot["close_le_5min"]),
        "near": int(tot["near_le_10min"]),
        "valid": len(valid),
        "different": int(tot["different_gt_10min"]),
        "unmatched": int(tot["unmatched"]),
        "signal_match_rate_pct": tot["signal_match_rate_pct"],
        "exact_match_rate_pct": tot["exact_match_rate_pct"],
        "outcome_agreement_pct": tot["outcome_agreement_pct_of_comparable"],
        "outcome_agreement_fraction": tot["outcome_agreement_fraction"],
        "outcome_agreement_pct_excluding_be": tot["outcome_agreement_pct_excluding_manual_be"],
        "outcome_agreement_excluding_be_fraction": tot["outcome_agreement_excluding_be_fraction"],
        "portfolio_accepted": len(acc),
        "portfolio_rejected": len(valid) - len(acc),
        "portfolio_acceptance_pct_of_matched": _pct(len(acc), len(valid)),
        "portfolio_level_valid_matches": int(tot["portfolio_level_valid_matches"]),
        "portfolio_level_match_rate_pct": _pct(
            int(tot["portfolio_level_valid_matches"]), len(elig)
        ),
        "standalone_level_valid_matches": int(tot["standalone_level_valid_matches"]),
        "standalone_level_match_rate_pct": _pct(
            int(tot["standalone_level_valid_matches"]), len(elig)
        ),
        "manual_workbook_notes": manual_notes,
    }
    return h


def make_checks(rec: Reconciliation, manual_expected_rows: int) -> dict[str, Any]:
    """Final-verification facts: accounted records, no duplicate matches, denominators."""
    t = rec.all_trades
    any_pair_dup = t[t.automated_signal_time != ""].duplicated(
        ["date", "automated_strategy", "automated_signal_time"], keep=False
    )
    dup_pf = t[t.portfolio_level_trade_id != ""].duplicated("portfolio_level_trade_id", keep=False)
    reason_ids = set(rec.reasons.manual_serial) if not rec.reasons.empty else set()
    matched_ok = set(
        t[(t.valid_match_le_10min == "YES") & (t.portfolio_accepted == "YES")].manual_serial
    )
    return {
        "all_records_present": len(t) == manual_expected_rows and t.manual_serial.is_unique,
        "records_in_table": len(t),
        "serials_unique": bool(t.manual_serial.is_unique),
        "every_record_explained_or_fully_matched": (reason_ids | matched_ok)
        == set(t.manual_serial),
        "duplicate_automated_signal_matches": int(any_pair_dup.sum()),
        "duplicate_portfolio_trade_matches": int(dup_pf.sum()),
        "denominator_eligible_equals_sum_categories": int(
            (t.eligibility == "ELIGIBLE").sum()
            == sum(
                int(((t.eligibility == "ELIGIBLE") & (t.match_category == c)).sum())
                for c in CATEGORY_ORDER
            )
        ),
        "rf_records_identified": int((t.eligibility == "UNSUPPORTED_RF").sum()),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
