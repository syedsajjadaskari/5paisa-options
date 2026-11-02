"""Manual journal vs V3 reconciliation: matching, eligibility and classification rules.

Unit tests use SYNTHETIC rows (clearly labelled); the integration test uses the real journal and
the latest V3 run when they exist.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from trading_platform.backtesting.manual_diagnostics import ReplayOutcome, classify_unmatched
from trading_platform.backtesting.manual_reconciliation import (
    _naive_ist,
    category_for,
    classify_manual_records,
    load_manual_journal,
    manual_outcome_code,
    match_one_to_one,
    outcome_agreement_label,
)

ROOT = Path(__file__).resolve().parents[2]


def _manual(rows: list[tuple[int, str, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["sl_no", "date", "entry_time", "strategy", "outcome"])


def _cands(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "strategy", "t", "auto_outcome"])
    df["signal_candle_open_dt"] = pd.to_datetime(df["date"] + " " + df["t"])
    return df


SESSIONS = {"2026-01-05", "2026-01-06"}


def _elig(manual: pd.DataFrame) -> pd.DataFrame:
    return classify_manual_records(manual, SESSIONS, SESSIONS)


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [
        (0, "EXACT"),
        (5, "CLOSE"),
        (5.1, "NEAR"),
        (10, "NEAR"),
        (10.01, "DIFFERENT"),
        (None, "UNMATCHED"),
    ],
)
def test_category_boundaries(minutes, expected):
    assert category_for(minutes) == expected


def test_one_to_one_never_reuses_a_candidate():
    manual = _elig(
        _manual(
            [(1, "2026-01-05", "09:15", "ORB", "SL"), (2, "2026-01-05", "09:20", "ORB", "Target")]
        )
    )
    cands = _cands([("2026-01-05", "ORB", "09:15", "STOP")])
    res = match_one_to_one(manual, cands)
    used = [c for c, _ in res.pairs.values() if c is not None]
    assert len(used) == len(set(used)) == 1
    assert sorted(res.category(i) for i in manual.index) == ["EXACT", "UNMATCHED"]
    # the closer manual record wins the single candidate
    assert res.category(manual.index[0]) == "EXACT"


def test_assignment_minimises_total_distance_not_greedy_order():
    manual = _elig(
        _manual([(1, "2026-01-05", "09:40", "IRF", "SL"), (2, "2026-01-05", "10:00", "IRF", "SL")])
    )
    cands = _cands([("2026-01-05", "IRF", "09:45", "STOP"), ("2026-01-05", "IRF", "10:05", "STOP")])
    res = match_one_to_one(manual, cands)
    assert [res.pairs[i][1] for i in manual.index] == [-5.0, -5.0]  # 09:40->09:45, 10:00->10:05


def test_matching_never_crosses_strategy_or_date():
    manual = _elig(_manual([(1, "2026-01-05", "09:15", "ORB", "SL")]))
    cands = _cands([("2026-01-05", "IRF", "09:15", "STOP"), ("2026-01-06", "ORB", "09:15", "STOP")])
    assert match_one_to_one(manual, cands).category(manual.index[0]) == "UNMATCHED"


def test_outcome_breaks_ties_only():
    manual = _elig(_manual([(1, "2026-01-05", "10:00", "IRF", "Target")]))
    cands = _cands(
        [("2026-01-05", "IRF", "09:55", "STOP"), ("2026-01-05", "IRF", "10:05", "TARGET")]
    )
    res = match_one_to_one(manual, cands)
    cand, diff = res.pairs[manual.index[0]]
    assert (
        cands.at[cand, "auto_outcome"] == "TARGET" and diff == -5.0
    )  # equal distance -> outcome decides


def test_eligibility_keeps_every_record_and_flags_rf_weekend_and_off_grid():
    manual = _manual(
        [
            (1, "2026-01-05", "09:15", "ORB", "SL"),
            (2, "2026-01-05", "09:15", "RF", "SL"),
            (3, "2026-01-04", "09:15", "ORB", "SL"),
            (4, "2026-01-05", "09:17", "IRF", "SL"),
            (5, "2026-01-05", "09:20", "IRF", "??"),
        ]
    )
    out = _elig(manual)
    assert list(out.eligibility) == [
        "ELIGIBLE",
        "UNSUPPORTED_RF",
        "NON_TRADING_DAY",
        "AMBIGUOUS",
        "AMBIGUOUS",
    ]
    assert len(out) == len(manual)
    res = match_one_to_one(out, _cands([("2026-01-05", "ORB", "09:15", "STOP")]))
    assert res.category(out.index[1]) == "UNMATCHED"  # RF is never matched


@pytest.mark.parametrize(
    ("manual", "auto", "expected"),
    [
        ("TARGET", "TARGET", "AGREE_BOTH_TARGET"),
        ("SL", "STOP", "AGREE_BOTH_STOP"),
        ("TARGET", "STOP", "MANUAL_TARGET_AUTO_STOP"),
        ("SL", "TARGET", "MANUAL_STOP_AUTO_TARGET"),
        ("BE", "TARGET", "MANUAL_BE_AUTO_TARGET"),
        ("BE", "STOP", "MANUAL_BE_AUTO_STOP"),
        ("BE", "EOD", "MANUAL_BE_AUTO_EOD"),
        ("TARGET", "NO_TRADE", "UNKNOWN_NO_AUTOMATED_EXIT"),
    ],
)
def test_outcome_agreement_labels_never_infer_break_even(manual, auto, expected):
    assert outcome_agreement_label(manual, auto) == expected


def test_manual_outcome_codes():
    assert [manual_outcome_code(x) for x in ("Target", "SL", "BE", "x")] == [
        "TARGET",
        "SL",
        "BE",
        "UNKNOWN",
    ]


def test_automated_timestamps_must_be_ist():
    assert _naive_ist(pd.Series(["2026-01-05T09:15:00+05:30"])).iloc[0] == pd.Timestamp(
        "2026-01-05 09:15"
    )
    with pytest.raises(ValueError):
        _naive_ist(pd.Series(["2026-01-05T09:15:00+00:00"]))


def test_workbook_loader_reports_summary_mismatch(tmp_path):
    path = tmp_path / "journal.xlsx"
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame(
            [
                ["TITLE", None, None, None, None],
                ["Sl No.", "Date", "Entry Time", "Strategy", "Outcome"],
                [1, "2026-01-05", "09:15", "ORB", "SL"],
                [2, "2026-01-06", "09:20", "IRF", "Target"],
            ]
        ).to_excel(xw, sheet_name="Trading Journal", header=False, index=False)
        pd.DataFrame([["Summary", None], ["Metric", "Value"], ["Total trades", 1]]).to_excel(
            xw, sheet_name="Summary", header=False, index=False
        )
    df, notes = load_manual_journal(path)
    assert (
        len(df) == 2
        and list(df.strategy) == ["ORB", "IRF"]
        and list(df.entry_time) == ["09:15", "09:20"]
    )
    assert (
        notes["workbook_summary_total_trades"] == 1
        and notes["workbook_summary_matches_rows"] is False
    )


# ----------------------------------------------------------------------------- classifier


def _snap(**kw):
    return ReplayOutcome(snapshot_at_manual=kw, snapshot_eod=kw)


def test_alternate_method_hit_beats_timing_for_irf():
    rc = classify_unmatched(
        "IRF",
        "10:15",
        _snap(status="ARMED"),
        ["09:40"],
        {"IRF_M1_IMMEDIATE_FAILURE": [{"candle_open": "10:05", "direction": "SELL", "rule": "x"}]},
        None,
    )
    assert rc.category == "Strategy-method mismatch"


def test_same_day_signal_far_away_is_timing():
    rc = classify_unmatched("IRF", "10:15", _snap(status="ARMED"), ["13:00"], {}, None)
    assert rc.category == "Different candle timing"


def test_irf_no_breakout_is_discretionary_not_ambiguity():
    rc = classify_unmatched(
        "IRF",
        "10:10",
        _snap(status="WAITING_BREAKOUT | waiting for a candle to close above IRH"),
        [],
        {},
        None,
    )
    assert rc.category == "Manual discretionary entry"


def test_pdr_gate_is_rulebook_ambiguity_and_data_problem_is_data():
    rc = classify_unmatched("PDR", "09:20", _snap(reason="OPEN_NOT_ABOVE_ZONE"), [], {}, None)
    assert rc.category == "Rulebook ambiguity" and rc.basis == "PROVISIONAL_RULE"
    data = classify_unmatched(
        "ORB", "09:15", _snap(state="BLOCKED", reason="NO_FIRST_BAR"), [], {}, {"available": True}
    )
    assert data.category == "Missing or invalid market data"


def _orb_facts(**kw):
    base = {
        "available": True,
        "beyond_v3_level": False,
        "c": 100.0,
        "pdh": 130.0,
        "pdl": 80.0,
        "close_vs_pdh": -30.0,
        "close_vs_pdl": 20.0,
        "body_ratio": 0.9,
        "ema_200": 90.0,
        "upper_wick_ratio": 0.0,
        "lower_wick_ratio": 0.0,
        "levels_match_independent": True,
        "alt_level_flags": {},
        "passes_filters_long": False,
        "passes_filters_short": False,
    }
    base.update(kw)
    return base


def test_orb_strong_candle_short_of_level_is_method_mismatch_not_proof():
    rc = classify_unmatched("ORB", "09:15", _snap(), [], {}, _orb_facts(passes_filters_long=True))
    assert rc.category == "Strategy-method mismatch"
    assert "not proof" in rc.sub_reason.lower()  # worded as consistent-with, never as established


def test_orb_weak_candle_short_of_level_is_discretionary():
    rc = classify_unmatched("ORB", "09:15", _snap(), [], {}, _orb_facts())
    assert rc.category == "Manual discretionary entry"


def test_orb_alt_level_that_would_pass_is_rulebook_ambiguity():
    rc = classify_unmatched(
        "ORB",
        "09:15",
        _snap(),
        [],
        {},
        _orb_facts(alt_level_flags={"long_zone_body_edge_would_pass_filters": True}),
    )
    assert rc.category == "Rulebook ambiguity"


def test_independent_level_disagreement_marks_suspected_defect():
    rc = classify_unmatched(
        "ORB", "09:15", _snap(), [], {}, _orb_facts(levels_match_independent=False)
    )
    assert rc.implementation_defect == "SUSPECTED"


# ----------------------------------------------------------------------------- real data


def _latest_run() -> Path | None:
    runs = sorted((ROOT / "reports" / "backtest").glob("V3_PDF_CORRECTED_*"))
    return runs[-1] if runs else None


@pytest.mark.skipif(
    not (ROOT / "data" / "manual").exists() or _latest_run() is None,
    reason="real journal or V3 run not available",
)
def test_real_journal_reconciliation_accounts_for_every_record():
    from trading_platform.backtesting.manual_diagnostics import DayReplayer
    from trading_platform.backtesting.manual_reconciliation import load_backtest_run
    from trading_platform.backtesting.manual_reconciliation_report import make_checks, reconcile
    from trading_platform.backtesting.portfolio_runner import load_nifty_bars

    manual, notes = load_manual_journal(
        ROOT / "data" / "manual" / "Trading_Journal_Sep2025_Aug2026 REPORT.xlsx",
        ROOT / "reports" / "manual_journal" / "manual_trades.csv",
    )
    assert len(manual) == 190 and notes["parsed_csv_identical_to_workbook"] is True
    bars_1d, bars_5m = load_nifty_bars(ROOT)
    run = _latest_run()
    rec = reconcile(manual, load_backtest_run(run), DayReplayer(bars_1d, bars_5m), run.name, notes)
    checks = make_checks(rec, 190)
    assert checks["all_records_present"] and checks["every_record_explained_or_fully_matched"]
    assert (
        checks["duplicate_automated_signal_matches"] == 0
        and checks["duplicate_portfolio_trade_matches"] == 0
    )
    assert checks["rf_records_identified"] == 17
    h = rec.headline
    assert (
        h["manual_trades"] == 190
        and h["eligible"] + h["excluded_rf"] + h["excluded_no_session"] + h["excluded_ambiguous"]
        == 190
    )
    assert h["exact"] + h["close"] + h["near"] == h["valid"] <= h["eligible"]
    assert h["valid"] + h["different"] + h["unmatched"] == h["eligible"]
    assert (
        rec.all_trades.loc[rec.all_trades.eligibility == "UNSUPPORTED_RF", "match_category"]
        == "UNMATCHED"
    ).all()
