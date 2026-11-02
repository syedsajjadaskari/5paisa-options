"""Manual trading journal vs V3 backtest reconciliation: loading, eligibility and matching.

Pure pandas logic (no strategy code, no network, no broker).  The manual journal records ONLY
``Sl No., Date, Entry Time, Strategy, Outcome``.  Direction, method, entry price, stop and
target are NOT recorded and therefore stay ``UNKNOWN`` everywhere; nothing is inferred.

Timestamp convention (verified, see ``convention_evidence``)
------------------------------------------------------------
Manual ``Entry Time`` is compared with the automated SIGNAL CANDLE OPEN label (the dataset's
bar-open convention).  Every manual ORB entry is stamped 09:15 although the ORB rule cannot
be evaluated before the 09:15 candle CLOSES at 09:20, so a human cannot have filled at 09:15:
the journal time is the trigger candle's open label.  The automated FILL (open of the next
bar) is reported alongside as a sensitivity so a one-candle offset is never silently
classified as a match without the convention being visible.

All timestamps are IST wall-clock.  Automated ISO strings carry ``+05:30`` (asserted); manual
times are naive IST.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

MANUAL_COLUMNS = ["sl_no", "date", "entry_time", "strategy", "outcome"]
AUTOMATED_STRATEGIES = ("PDS", "ORB", "IRF", "PDR")
UNSUPPORTED_STRATEGIES = ("RF",)
UNKNOWN = "UNKNOWN"

CATEGORY_ORDER = ["EXACT", "CLOSE", "NEAR", "DIFFERENT", "UNMATCHED"]
VALID_CATEGORIES = ("EXACT", "CLOSE", "NEAR")  # <= 10 minutes
SESSION_FIRST = time(9, 15)
SESSION_LAST_BAR = time(15, 25)


# --------------------------------------------------------------------------- loading


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manual_journal(
    xlsx_path: Path, parsed_csv_path: Path | None = None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read the ORIGINAL workbook (authoritative) and cross-check the parsed CSV copy."""
    raw = pd.read_excel(xlsx_path, sheet_name="Trading Journal", header=1)
    raw = raw.dropna(subset=["Sl No."]).copy()
    df = pd.DataFrame(
        {
            "sl_no": raw["Sl No."].astype(int).to_numpy(),
            "date": pd.to_datetime(raw["Date"]).dt.strftime("%Y-%m-%d").to_numpy(),
            "entry_time": raw["Entry Time"].map(_hhmm).to_numpy(),
            "strategy": raw["Strategy"].astype(str).str.strip().str.upper().to_numpy(),
            "outcome": raw["Outcome"].astype(str).str.strip().to_numpy(),
        }
    )
    notes: dict[str, Any] = {"xlsx_sha256": sha256_file(xlsx_path), "rows": len(df)}
    summary = pd.read_excel(xlsx_path, sheet_name="Summary", header=None)
    stated = summary[summary[0].astype(str).str.strip() == "Total trades"]
    if not stated.empty:
        notes["workbook_summary_total_trades"] = int(stated.iloc[0, 1])
    notes["workbook_summary_matches_rows"] = notes.get("workbook_summary_total_trades") == len(df)
    if parsed_csv_path is not None and parsed_csv_path.exists():
        csv = pd.read_csv(parsed_csv_path, dtype=str)
        same = (
            len(csv) == len(df)
            and (csv["sl_no"].astype(int).to_numpy() == df["sl_no"].to_numpy()).all()
            and (csv["date"].to_numpy() == df["date"].to_numpy()).all()
            and (csv["entry_time"].to_numpy() == df["entry_time"].to_numpy()).all()
            and (csv["strategy"].str.upper().to_numpy() == df["strategy"].to_numpy()).all()
            and (csv["outcome"].to_numpy() == df["outcome"].to_numpy()).all()
        )
        notes["parsed_csv_identical_to_workbook"] = bool(same)
    return df, notes


def _hhmm(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    if isinstance(value, time):
        return value.strftime("%H:%M")
    text = str(value).strip()
    return text[:5]


def manual_outcome_code(outcome: str) -> str:
    o = (outcome or "").strip().upper()
    return {"TARGET": "TARGET", "SL": "SL", "BE": "BE"}.get(o, UNKNOWN)


def auto_outcome_code(exit_reason: str | float | None) -> str:
    if exit_reason is None or (isinstance(exit_reason, float) and np.isnan(exit_reason)):
        return "NO_TRADE"
    r = str(exit_reason).upper()
    if r == "TARGET":
        return "TARGET"
    if r in ("STOP_LOSS", "STOP", "INTRABAR_SL_CONFLICT", "GAP_STOP"):
        return "STOP"
    if "EOD" in r or "SQUARE" in r or "FORCE" in r:
        return "EOD"
    return r


# --------------------------------------------------------------------------- eligibility


def classify_manual_records(
    manual: pd.DataFrame, session_dates: set[str], first_bar_dates: set[str]
) -> pd.DataFrame:
    """Add eligibility columns.  Every record stays in the table (nothing is dropped)."""
    df = manual.copy()
    df["manual_ts"] = pd.to_datetime(df["date"] + " " + df["entry_time"])
    status, reason = [], []
    for r in df.itertuples():
        t = r.manual_ts
        on_grid = t.minute % 5 == 0
        in_session = SESSION_FIRST <= t.time() <= SESSION_LAST_BAR
        if r.strategy in UNSUPPORTED_STRATEGIES:
            s, why = (
                "UNSUPPORTED_RF",
                "RF is permanently disabled (the PDF defines no stop/target/exit rules)",
            )
        elif r.strategy not in AUTOMATED_STRATEGIES:
            s, why = "UNSUPPORTED_STRATEGY", f"strategy '{r.strategy}' is not in the V3 portfolio"
        elif r.date not in session_dates:
            s, why = (
                "NON_TRADING_DAY",
                "no NSE session / no historical bars exist for this date (weekend or holiday)",
            )
        elif manual_outcome_code(r.outcome) == UNKNOWN:
            s, why = "AMBIGUOUS", f"outcome '{r.outcome}' is not Target/SL/BE"
        elif not (on_grid and in_session):
            s, why = "AMBIGUOUS", "entry time is not on the 5-minute grid inside 09:15-15:25"
        elif r.date not in first_bar_dates:
            s, why = "NO_FIRST_BAR", "the session has no 09:15 candle (special session)"
        else:
            s, why = "ELIGIBLE", ""
        status.append(s)
        reason.append(why)
    df["eligibility"] = status
    df["eligibility_reason"] = reason
    return df


# --------------------------------------------------------------------------- candidates


def _naive_ist(series: pd.Series) -> pd.Series:
    s = series.astype(str)
    if not s.str.endswith("+05:30").all():
        bad = s[~s.str.endswith("+05:30")].head(3).tolist()
        raise ValueError(f"automated timestamps must be IST (+05:30); got {bad}")
    return pd.to_datetime(s.str[:19])


def load_backtest_run(run_dir: Path) -> dict[str, pd.DataFrame]:
    """Load the four V3 datasets, with naive-IST datetime columns added."""
    out: dict[str, pd.DataFrame] = {}
    for name in ("signals", "standalone_trades", "portfolio_trades", "rejected_signals"):
        df = pd.read_csv(run_dir / f"{name}.csv")
        for col in ("signal_candle_open", "signal_candle_close", "entry_time", "exit_time"):
            if col in df.columns:
                df[f"{col}_dt"] = _naive_ist(df[col]) if len(df) else pd.to_datetime(df[col])
        out[name] = df
    return out


def build_signal_candidates(bt: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per raw engine signal with its standalone trade outcome and portfolio status."""
    sig = bt["signals"].copy()
    sa = bt["standalone_trades"].set_index("signal_id")
    pf = bt["portfolio_trades"].set_index("signal_id")
    sig["standalone_exit_reason"] = sig["signal_id"].map(sa["exit_reason"])
    sig["auto_outcome"] = sig["standalone_exit_reason"].map(auto_outcome_code)
    sig["portfolio_accepted"] = sig["signal_id"].isin(pf.index)
    return sig


def build_trade_candidates(trades: pd.DataFrame) -> pd.DataFrame:
    t = trades.copy()
    t["auto_outcome"] = t["exit_reason"].map(auto_outcome_code)
    return t


# --------------------------------------------------------------------------- matching


def category_for(abs_minutes: float | None) -> str:
    if abs_minutes is None or (isinstance(abs_minutes, float) and np.isnan(abs_minutes)):
        return "UNMATCHED"
    if abs_minutes == 0:
        return "EXACT"
    if abs_minutes <= 5:
        return "CLOSE"
    if abs_minutes <= 10:
        return "NEAR"
    return "DIFFERENT"


@dataclass(frozen=True)
class MatchResult:
    """Manual row index -> (candidate row index | None, signed minutes manual-minus-automated)."""

    pairs: dict[int, tuple[int | None, float | None]]

    def category(self, manual_idx: int) -> str:
        cand, diff = self.pairs.get(manual_idx, (None, None))
        return "UNMATCHED" if cand is None else category_for(abs(diff))


def match_one_to_one(
    manual: pd.DataFrame,
    candidates: pd.DataFrame,
    candidate_time_col: str = "signal_candle_open_dt",
    outcome_col: str = "auto_outcome",
) -> MatchResult:
    """Optimal one-to-one assignment inside each (date, strategy) group.

    Cost = |manual time - candidate time| in minutes; a manual-vs-automated outcome mismatch adds
    a 0.001 tie-break (outcome is only used to choose between otherwise-equal candidates).
    Only ELIGIBLE manual records are matched.  A candidate is never used twice.
    """
    pairs: dict[int, tuple[int | None, float | None]] = {}
    for idx in manual.index:
        pairs[idx] = (None, None)
    elig = manual[manual["eligibility"] == "ELIGIBLE"]
    for (date, strat), grp in elig.groupby(["date", "strategy"]):
        cand = candidates[(candidates["date"] == date) & (candidates["strategy"] == strat)]
        if cand.empty:
            continue
        m_idx = list(grp.index)
        c_idx = list(cand.index)
        cost = np.zeros((len(m_idx), len(c_idx)))
        diffs = np.zeros_like(cost)
        for i, mi in enumerate(m_idx):
            mt = manual.at[mi, "manual_ts"]
            mo = manual_outcome_code(manual.at[mi, "outcome"])
            for j, cj in enumerate(c_idx):
                d = (mt - cand.at[cj, candidate_time_col]).total_seconds() / 60.0
                diffs[i, j] = d
                ao = cand.at[cj, outcome_col] if outcome_col in cand.columns else None
                tie = 0.0 if _outcomes_agree(mo, ao) else 0.001
                cost[i, j] = abs(d) + tie
        rows, cols = linear_sum_assignment(cost)
        for r, c in zip(rows, cols, strict=True):
            pairs[m_idx[r]] = (c_idx[c], float(diffs[r, c]))
    return MatchResult(pairs)


def _outcomes_agree(manual_code: str, auto_code: Any) -> bool:
    return (manual_code, auto_code) in {("TARGET", "TARGET"), ("SL", "STOP")}


def outcome_agreement_label(manual_code: str, auto_code: str) -> str:
    """Strict, non-inferential agreement label for one matched pair."""
    if auto_code in ("NO_TRADE", None) or (isinstance(auto_code, float) and np.isnan(auto_code)):
        return "UNKNOWN_NO_AUTOMATED_EXIT"
    if manual_code == UNKNOWN:
        return "UNKNOWN_MANUAL_OUTCOME"
    if manual_code == "TARGET" and auto_code == "TARGET":
        return "AGREE_BOTH_TARGET"
    if manual_code == "SL" and auto_code == "STOP":
        return "AGREE_BOTH_STOP"
    if manual_code == "BE":
        return {
            "TARGET": "MANUAL_BE_AUTO_TARGET",
            "STOP": "MANUAL_BE_AUTO_STOP",
            "EOD": "MANUAL_BE_AUTO_EOD",
        }.get(auto_code, "MANUAL_BE_AUTO_OTHER")
    pretty = {"TARGET": "TARGET", "SL": "STOP"}[manual_code]
    return f"MANUAL_{pretty}_AUTO_{auto_code}"


# --------------------------------------------------------------------------- evidence


def convention_evidence(
    manual: pd.DataFrame, sig_cand: pd.DataFrame, match: MatchResult
) -> dict[str, Any]:
    """Facts that support (or contradict) the 'journal time == signal candle open label' reading."""
    orb_manual = manual[(manual["strategy"] == "ORB") & (manual["eligibility"] == "ELIGIBLE")]
    orb_times = orb_manual["entry_time"].value_counts().to_dict()
    orb_signal_times = (
        sig_cand[sig_cand["strategy"] == "ORB"]["signal_candle_open_dt"]
        .dt.strftime("%H:%M")
        .value_counts()
        .to_dict()
    )
    signed: dict[str, list[int]] = {}
    for idx, (cand, diff) in match.pairs.items():
        if cand is None:
            continue
        s = manual.at[idx, "strategy"]
        signed.setdefault(s, []).append(int(round(diff)))
    return {
        "orb_manual_entry_times": orb_times,
        "orb_automated_signal_candle_open_times": orb_signal_times,
        "signed_minutes_manual_minus_signal_open_by_strategy": {
            k: sorted(v) for k, v in signed.items()
        },
    }
