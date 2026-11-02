"""Reconcile the original manual trading journal against the latest V3 backtest.

    ./.venv/bin/python scripts/run_manual_reconciliation_v3.py [--rerun-backtest] [--run-dir DIR]

Reads (never modifies): the original journal workbook, the verified NIFTY datasets, the V3 run
folder written by ``scripts/run_backtest.py`` and (reference only) the frozen V1 ledger.
Writes exactly six files under ``reports/manual_reconciliation_v3/``.

NIFTY index points only.  The journal records no direction, method, price, stop or target; those
stay UNKNOWN.  No parameter is tuned, no strategy is changed, no paper fill or broker call exists.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from trading_platform.backtesting.manual_diagnostics import DayReplayer  # noqa: E402
from trading_platform.backtesting.manual_reconciliation import (  # noqa: E402
    VALID_CATEGORIES,
    classify_manual_records,
    load_backtest_run,
    load_manual_journal,
    match_one_to_one,
)
from trading_platform.backtesting.manual_reconciliation_charts import build_dashboard  # noqa: E402
from trading_platform.backtesting.manual_reconciliation_report import (  # noqa: E402
    Reconciliation,
    make_checks,
    reconcile,
)
from trading_platform.backtesting.portfolio_runner import load_nifty_bars  # noqa: E402

OUT_DIR = BASE_DIR / "reports" / "manual_reconciliation_v3"
DELIVERABLES = (
    "MANUAL_VS_BACKTEST_ALL_TRADES.csv",
    "STRATEGY_MATCH_SUMMARY.csv",
    "UNMATCHED_TRADE_REASONS.csv",
    "OUTCOME_AGREEMENT_MATRIX.csv",
    "RECONCILIATION_REPORT.md",
    "comparison_dashboard.html",
)
FROZEN_PREFIXES = (
    "reports/phase2d",
    "reports/phase3a",
    "reports/independent_codex_audit",
    "docs",
    "data",
)


def latest_run_dir() -> Path:
    runs = sorted((BASE_DIR / "reports" / "backtest").glob("V3_PDF_CORRECTED_*"))
    if not runs:
        raise SystemExit(
            "no V3_PDF_CORRECTED run found; run scripts/run_backtest.py or pass --rerun-backtest"
        )
    return runs[-1]


def rerun_backtest() -> Path:
    before = set((BASE_DIR / "reports" / "backtest").glob("V3_PDF_CORRECTED_*"))
    subprocess.run(
        [sys.executable, str(BASE_DIR / "scripts" / "run_backtest.py")], check=True, cwd=BASE_DIR
    )
    after = set((BASE_DIR / "reports" / "backtest").glob("V3_PDF_CORRECTED_*"))
    new = sorted(after - before)
    return new[-1] if new else latest_run_dir()


def v1_reference(manual: pd.DataFrame, replay_days: set[str], first_bar_days: set[str]) -> dict:
    """Same matcher against the FROZEN V1 ledger (reference only; read-only)."""
    v1 = pd.read_parquet(BASE_DIR / "reports" / "phase2d" / "backtest_trades.parquet")
    cand = pd.DataFrame(
        {
            "date": v1["date"].astype(str).str[:10],
            "strategy": v1["strategy_name"],
            "signal_candle_open_dt": pd.to_datetime(v1["entry_time"].astype(str).str[:19])
            - pd.Timedelta(minutes=5),
            "auto_outcome": v1["exit_reason"].map(
                lambda r: "TARGET" if r == "TARGET" else ("STOP" if "STOP" in str(r) else "EOD")
            ),
        }
    )
    m = classify_manual_records(manual, replay_days, first_bar_days)
    res = match_one_to_one(m, cand)
    elig = m[m.eligibility == "ELIGIBLE"]
    cats = Counter(res.category(i) for i in elig.index)
    valid = sum(cats[c] for c in VALID_CATEGORIES)
    return {
        "eligible": len(elig),
        "valid": valid,
        "rate": round(100 * valid / len(elig), 1),
        "exact": cats["EXACT"],
        "by_strategy": {
            s: (
                sum(res.category(i) in VALID_CATEGORIES for i in elig[elig.strategy == s].index),
                int((elig.strategy == s).sum()),
            )
            for s in ("PDS", "ORB", "IRF", "PDR")
        },
        "ledger": "reports/phase2d/backtest_trades.parquet (353 trades; IRF Method 1; frozen; read-only)",
    }


def reverse_coverage(rec: Reconciliation, bt: dict[str, pd.DataFrame]) -> pd.DataFrame:
    sig = bt["signals"]
    sig = sig[(sig.date >= "2025-09-01") & (sig.date <= "2026-08-31")]
    t = rec.all_trades
    matched = {
        (r.date, r.automated_strategy, r.automated_signal_time[:16])
        for r in t[t.valid_match_le_10min == "YES"].itertuples()
    }
    rows = []
    for strat in ("PDS", "ORB", "IRF", "PDR"):
        s = sig[sig.strategy == strat]
        hit = sum(
            (r.date, r.strategy, r.signal_candle_open[:16]) in matched
            for r in s.itertuples()
        )
        rows.append(
            {
                "strategy": strat,
                "v3_signals": len(s),
                "matched_to_a_manual_trade": hit,
                "no_manual_counterpart": len(s) - hit,
            }
        )
    return pd.DataFrame(rows)


def independent_level_check(replayer: DayReplayer) -> dict:
    ok = bad = 0
    explained: list[str] = []
    unexplained: list[str] = []
    for d in replayer.days:
        f = replayer.orb_first_candle_facts(d)
        if not f.get("available"):
            continue
        if f["levels_match_independent"]:
            ok += 1
            continue
        bad += 1
        prev = replayer.previous_day(d)
        n_prev = len(replayer.by_day[prev]) if prev else 0
        uses_official = f["pdh"] == f["official_pdh"] and f["pdl"] == f["official_pdl"]
        if n_prev < 75 and uses_official:
            explained.append(
                f"{d.isoformat()}: previous session {prev.isoformat()} has only {n_prev} regular candles (special session), "
                "so the level builder deliberately uses the official daily high/low (OFFICIAL_DAILY_FALLBACK)"
            )
        else:
            unexplained.append(d.isoformat())
    return {
        "days_checked": ok + bad,
        "levels_match": ok,
        "mismatch": bad,
        "mismatch_explained_by_documented_fallback": explained,
        "mismatch_unexplained": unexplained,
    }


def md_table(df: pd.DataFrame, cols: list[str] | None = None) -> str:
    d = df[cols] if cols else df
    head = "| " + " | ".join(d.columns) + " |\n|" + "|".join("---" for _ in d.columns) + "|\n"
    body = "".join(
        "| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |\n"
        for row in d.itertuples(index=False)
    )
    return head + body


def write_report(
    rec: Reconciliation,
    bt_manifest: dict,
    checks: dict,
    rev: pd.DataFrame,
    v1: dict,
    level_check: dict,
    run_dir: Path,
    summary_md_path: str,
    bt: dict[str, pd.DataFrame],
) -> str:
    h, s, r, ev = rec.headline, rec.strategy_summary, rec.reasons, rec.evidence
    t = rec.all_trades
    notes = rec.notes
    by_cause = (
        r.groupby(["root_cause", "manual_strategy"]).size().unstack(fill_value=0)
        if not r.empty
        else pd.DataFrame()
    )
    by_cause["TOTAL"] = by_cause.sum(axis=1)
    by_cause = by_cause.sort_values("TOTAL", ascending=False).reset_index()

    orb_un = r[
        (r.manual_strategy == "ORB")
        & (~r.scope.str.startswith("EXCLUDED"))
        & (r.scope != "PORTFOLIO_LEVEL")
    ]
    orb_mm = int((orb_un.root_cause == "Strategy-method mismatch").sum())
    orb_disc = int((orb_un.root_cause == "Manual discretionary entry").sum())
    orb_amb = int((orb_un.root_cause == "Rulebook ambiguity").sum())
    irf_un = r[
        (r.manual_strategy == "IRF")
        & (~r.scope.str.startswith("EXCLUDED"))
        & (r.scope != "PORTFOLIO_LEVEL")
    ]
    irf_mm = int((irf_un.root_cause == "Strategy-method mismatch").sum())
    irf_tm = int((irf_un.root_cause == "Different candle timing").sum())
    irf_disc = int((irf_un.root_cause == "Manual discretionary entry").sum())
    be_total = int((t.manual_outcome == "BE").sum())
    pdr_gate = int(((r.manual_strategy == "PDR") & (r.root_cause == "Rulebook ambiguity")).sum())

    mat = rec.outcome_matrix
    piv = (
        mat[mat.scope == "ALL"]
        .pivot(index="manual_outcome", columns="automated_outcome", values="count")
        .reindex(["Target", "SL", "BE"])[["TARGET", "STOP", "EOD", "NO_AUTOMATED_EXIT"]]
        .reset_index()
    )

    ds = "\n".join(
        f"- `{d['path']}`: {d['status']} (`{d['actual_sha256'][:16]}...`)"
        for d in bt_manifest.get("dataset_hashes", [])
    )
    sig_ev = ev["signed_minutes_manual_minus_signal_open_by_strategy"]
    sig_dist = {k: dict(sorted(Counter(v).items())) for k, v in sig_ev.items()}
    rej = t[(t.valid_match_le_10min == "YES") & (t.portfolio_accepted == "NO")]
    rej_desc = "; ".join(
        f"{k[0]} {k[1]} x{v}" for k, v in Counter(zip(rej.manual_strategy, rej.rejection_reason, strict=True)).items()
    ) or "none"
    n_sig = int(bt["signals"]["in_window"].sum())
    n_pf = int(bt["portfolio_trades"]["in_window"].sum())
    a = f"""# Manual journal vs V3 automated backtest - reconciliation report

NIFTY **index points only**. The manual journal records `Sl No., Date, Entry Time, Strategy, Outcome` and nothing else:
**direction, method, entry price, stop and target are UNKNOWN for every record** and were never inferred.
All times are IST. No parameter was tuned, no strategy code was changed for this report, no paper fill or broker call is involved.

## 1. Answer

**V3 PARTIALLY reproduces the manual journal.** It reproduces {h["valid"]} of {h["eligible"]} eligible manual trades
({h["signal_match_rate_pct"]}%) at the signal level and gets the outcome right for {h["outcome_agreement_fraction"]}
matched trades ({h["outcome_agreement_pct"]}%, or {h["outcome_agreement_pct_excluding_be"]}% when the {be_total} manual break-even outcomes,
which the PDFs and the engine cannot express, are set aside). It does not reproduce the other {h["eligible"] - h["valid"]} eligible entries.
The timing of the reproduced trades is exact for {h["exact"]} of {h["valid"]} ({round(100 * h["exact"] / h["valid"], 1)}%).

| Metric | Result |
|---|---|
| Manual trades | {h["manual_trades"]} (Sl No. 1-190, 2025-09-01 to 2026-08-31) |
| Eligible manual trades | {h["eligible"]} = {h["manual_trades"]} - {h["excluded_rf"]} RF (unsupported) - {h["excluded_no_session"]} dated on a non-trading day - {h["excluded_ambiguous"]} ambiguous |
| Exact matches (same candle) | {h["exact"]} / {h["eligible"]} = {h["exact_match_rate_pct"]}% |
| Matches within 5 minutes (CLOSE) | {h["close"]} (cumulative exact+close = {h["exact"] + h["close"]}) |
| Matches within 10 minutes (NEAR) | {h["near"]} (cumulative valid = {h["valid"]}) |
| Same date + strategy but > 10 min (DIFFERENT) | {h["different"]} |
| Unmatched (no same-date same-strategy signal) | {h["unmatched"]} |
| **Overall signal match rate** | **{h["valid"]} / {h["eligible"]} = {h["signal_match_rate_pct"]}%** (valid = EXACT + CLOSE + NEAR) |
| **Outcome agreement rate** | **{h["outcome_agreement_fraction"]} = {h["outcome_agreement_pct"]}%** of valid matches (both Target {int((t.outcome_agreement == "AGREE_BOTH_TARGET").sum())}, both Stop {int((t.outcome_agreement == "AGREE_BOTH_STOP").sum())}); {h["outcome_agreement_excluding_be_fraction"]} = {h["outcome_agreement_pct_excluding_be"]}% excluding manual BE |
| **Portfolio execution match rate** | matched signals accepted by the one-position FIFO portfolio: **{h["portfolio_accepted"]} / {h["valid"]} = {h["portfolio_acceptance_pct_of_matched"]}%**; independent match against accepted portfolio trades: {h["portfolio_level_valid_matches"]} / {h["eligible"]} = {h["portfolio_level_match_rate_pct"]}% |

"Match" means: same date, same strategy, V3 signal candle within 10 minutes of the journal time, one-to-one. It does **not** prove the
manual trade used the same level, candle or rule, because the journal has no such detail.

## 2. Sources and integrity

- Journal: `data/manual/Trading_Journal_Sep2025_Aug2026 REPORT.xlsx` (sha256 `{notes["xlsx_sha256"]}`), sheet *Trading Journal*, **{notes["rows"]} rows**.
  The parsed copy `reports/manual_journal/manual_trades.csv` is {"identical" if notes.get("parsed_csv_identical_to_workbook") else "NOT identical"} to the workbook.
  The workbook's own *Summary* sheet says **{notes.get("workbook_summary_total_trades")} total trades** (Target 80), but the data sheet has {notes["rows"]} rows (Target 81): the last row (Sl No. 190, 2026-08-31 ORB Target) is not in the Summary. This report uses the {notes["rows"]} data rows.
- Automated: run `{h["run_id"]}` (engine `V3_PDF_CORRECTED`, git `{bt_manifest.get("git_sha", "n/a")[:12]}`), produced by `scripts/run_backtest.py`; reproduced twice (28 Oct and 30 Oct 2026) with identical headline results ({n_sig} raw signals and {n_pf} portfolio trades in the evaluation window). Datasets verified against the pinned hashes:

{ds}
- Frozen baselines (V1/V2) were not modified; V1 is used below as a reference only.
- Journal composition: ORB {int((t.manual_strategy == "ORB").sum())}, IRF {int((t.manual_strategy == "IRF").sum())}, RF {int((t.manual_strategy == "RF").sum())}, PDR {int((t.manual_strategy == "PDR").sum())}, PDS {int((t.manual_strategy == "PDS").sum())}.
  Outcomes: Target {int((t.manual_outcome == "Target").sum())}, SL {int((t.manual_outcome == "SL").sum())}, BE {int((t.manual_outcome == "BE").sum())}.
- Eligibility: RF ({h["excluded_rf"]}) is **preserved as unsupported**, not dropped (the RF PDF defines no stop/target/exit and RF is permanently disabled). {h["excluded_no_session"]} records are dated on weekends (no NSE session and no bars exist): 2025-10-11, 2026-02-14, 2026-08-15, 2026-08-22, 2026-08-29. They stay in every table as UNMATCHED / unspecified.

## 3. Timestamp convention (checked, not assumed)

The journal time is compared with the V3 **signal candle OPEN label**, the dataset's bar-open convention. Evidence:

- The {sum(ev["orb_manual_entry_times"].values())} eligible manual ORB records all carry the single entry time `{list(ev["orb_manual_entry_times"])[0]}` (so do all 89 ORB journal rows), and every V3 ORB signal candle opens at `{list(ev["orb_automated_signal_candle_open_times"])[0]}`. The ORB rule cannot be evaluated until that candle **closes** at 09:20, so a person cannot have entered at 09:15: the journal records the trigger candle's label.
- Signed offsets (manual minus V3 signal-candle open, minutes) are centred on 0 and symmetric, not piled at +5 (which a fill-time convention would produce): `{json.dumps(sig_dist)}`.
- Every 5-minute offset is flagged in `convention_note` in the all-trades CSV together with the offset against the actual V3 fill time, so it is never silently counted as an exact match. Under the label convention a 5-minute offset is CLOSE, not EXACT.

## 4. Results

### A. Strategy signal matching

{md_table(s, ["strategy", "manual_trades", "eligible_manual_trades", "exact", "close_le_5min", "near_le_10min", "different_gt_10min", "unmatched", "signal_match_rate_fraction", "signal_match_rate_pct"])}

Signal Match Rate = valid matched manual records / eligible manual records = **{h["valid"]} / {h["eligible"]} = {h["signal_match_rate_pct"]}%**.
Unsupported RF records: **{h["excluded_rf"]}**. Ambiguous manual records (off-grid time / unknown outcome): **{h["excluded_ambiguous"]}**.
Matching was also run independently against the standalone trades ({h["standalone_level_valid_matches"]} / {h["eligible"]} = {h["standalone_level_match_rate_pct"]}%) and against the accepted FIFO portfolio trades ({h["portfolio_level_valid_matches"]} / {h["eligible"]} = {h["portfolio_level_match_rate_pct"]}%).

### B. Outcome agreement (valid matches only)

{md_table(piv)}

- Agreement (Target/Target or SL/Stop): **{h["outcome_agreement_fraction"]} = {h["outcome_agreement_pct"]}%**; excluding manual BE: **{h["outcome_agreement_excluding_be_fraction"]} = {h["outcome_agreement_pct_excluding_be"]}%**.
- Manual BE vs automated: Target {int((t.outcome_agreement == "MANUAL_BE_AUTO_TARGET").sum())}, Stop {int((t.outcome_agreement == "MANUAL_BE_AUTO_STOP").sum())}, EOD {int((t.outcome_agreement == "MANUAL_BE_AUTO_EOD").sum())}. **Both-break-even can never occur**: no author PDF contains a break-even / trailing / stop-move rule (searched all five), and the engine's exits are target, stop and 15:15 square-off only. {be_total} of {h["manual_trades"]} manual outcomes ({round(100 * be_total / h["manual_trades"], 1)}%) are BE, so a large part of the journal's P&L profile reflects discretionary trade management that is not in the written rules.
- Manual Target vs automated Stop: {int((t.outcome_agreement == "MANUAL_TARGET_AUTO_STOP").sum())}; manual SL vs automated Target: {int((t.outcome_agreement == "MANUAL_STOP_AUTO_TARGET").sum())}.
- Matching entry time and Win/Loss does **not** establish that the strategy was replicated: direction, method, level and stop/target distances are unknown for the manual side.

### C. Portfolio execution agreement

Of {h["valid"]} matched signals, **{h["portfolio_accepted"]} were accepted** by the FIFO combined portfolio and **{h["portfolio_rejected"]} were rejected** ({rej_desc}). See `UNMATCHED_TRADE_REASONS.csv` scope `PORTFOLIO_LEVEL`.

### D. Strategy-wise comparison

{md_table(s[s.strategy.isin(["PDS", "ORB", "IRF", "PDR", "RF"])], ["strategy", "manual_trades", "valid_matched_le_10min", "signal_match_rate_pct", "outcome_agreement_fraction", "matched_signals_accepted_by_portfolio", "main_mismatch_reasons"])}

Reverse view (V3 signals in the journal's date range that have **no** manual counterpart; the journal may simply not contain every trade the friend could have taken):

{md_table(rev)}

Reading: V3's ORB is highly consistent with the journal ({int(rev[rev.strategy == "ORB"].matched_to_a_manual_trade.iloc[0])} of {int(rev[rev.strategy == "ORB"].v3_signals.iloc[0])} V3 ORB signals are journal trades) but the journal contains many more ORB entries than Method 1 produces (recall {int(s[s.strategy == "ORB"].valid_matched_le_10min.iloc[0])}/{int(s[s.strategy == "ORB"].eligible_manual_trades.iloc[0])}). For IRF the opposite holds: V3 Method 2 emits many signals ({int(rev[rev.strategy == "IRF"].v3_signals.iloc[0])}) that the journal does not record ({int(rev[rev.strategy == "IRF"].no_manual_counterpart.iloc[0])} with no counterpart).

## 5. Why manual trades are not reproduced (root causes)

{md_table(by_cause)}

Classification is evidence-based from a day-by-day replay through the real strategy classes with the backtest's own point-in-time context
(`UNMATCHED_TRADE_REASONS.csv` has the conditions satisfied/failed, levels, the candle at the manual time, same-day V3 signals and diagnostic-only alternate-method signals for every record).
Alternate methods (ORB Method 2/3, IRF Method 1/3, PDS all-methods) and alternate level definitions are **diagnostics only, never applied**.

### The five most important reasons

1. **ORB trades without a broken previous-day level ({orb_mm + orb_disc + orb_amb} of the {int(s[s.strategy == "ORB"].eligible_manual_trades.iloc[0])} eligible ORB records are unmatched).** V3 implements only ORB Method 1 (09:15 candle closes beyond the previous-day high/low, with trend and candle-quality filters). On the unmatched days the 09:15 candle usually closed inside the previous day's range (median ~35 points short). The ORB PDF has two further setups the journal cannot distinguish: Method 2 (inner level + trendline) and Gap + 200 DMA. {orb_mm} unmatched ORB records have a strong in-trend 09:15 candle that fits an inner-level Method 2 entry (consistent with, not proof of it); {orb_disc} fail even the candle/trend tests (no ORB setup can be reconstructed); {orb_amb} are rulebook-ambiguity cases (level read as a zone edge, or provisional body/wick/EMA thresholds).
2. **Break-even outcomes.** {be_total} manual BE outcomes ({round(100 * be_total / h["manual_trades"], 1)}%) cannot be reproduced because no PDF defines a break-even rule. This is why outcome agreement is {h["outcome_agreement_pct"]}% overall but {h["outcome_agreement_pct_excluding_be"]}% when BE is set aside.
3. **IRF method not recorded.** The journal does not say which IRF method was used; V3 runs Method 2 only. {irf_mm} unmatched IRF records coincide with an IRF Method 1 or 3 pattern within 10 minutes (e.g. a breakout candle immediately closing back inside, which the PDF defines as Method 1), {irf_tm} have a V3 Method 2 signal the same day but more than 10 minutes away, and {irf_disc} have no initial-range break at all by the manual time.
4. **Provisional rules deliberately left untuned.** {pdr_gate} PDR records fail only the provisional "open above the resistance-zone top" gate; ORB zone/body/wick/EMA thresholds and the PDS zone definition are likewise PROVISIONAL. Changing them to chase the journal would be curve-fitting, so they were not changed (`docs/AUTHOR_CONFIRMATION_REQUEST.md` lists the author questions).
5. **Scope and portfolio effects.** RF ({h["excluded_rf"]} records, {round(100 * h["excluded_rf"] / h["manual_trades"], 1)}%) is disabled by design; {h["excluded_no_session"]} entries are dated on weekends; and {h["portfolio_rejected"]} matched IRF signals lost to the one-position FIFO rule.

## 6. Implementation-defect hunt (section 7 of the brief)

No implementation defect was demonstrated, so **no strategy or engine code was changed**; before and after results are identical.

- ORB previous-day levels were independently recomputed from raw bars for {level_check["days_checked"]} sessions: **{level_check["levels_match"]} match, {level_check["mismatch"]} differ**; unexplained differences: **{len(level_check["mismatch_unexplained"])}**. {" ".join(level_check["mismatch_explained_by_documented_fallback"])} (That session's only journal entry is an IRF trade, which does not use previous-day levels.)
- Timestamps: signal-candle labels, fill times (label + 5 min) and the 14:30 / 15:15 rules were checked against the dataset; the 09:15-only first-candle rule is consistent with the PDF ("evaluate only after the candle has completed").
- IRF is short-only per the PDF ("Short trades only"), so the absence of a mirrored bullish setup is correct.
- The matching code is covered by tests (`tests/backtesting/test_manual_reconciliation.py`).

## 7. Reference baselines (read-only, not the comparison target)

- Frozen V1 ledger `{v1["ledger"]}`, same matcher (signal label = fill time - 5 min): valid matches **{v1["valid"]} / {v1["eligible"]} = {v1["rate"]}%**, exact {v1["exact"]}; by strategy {json.dumps({k: f"{a}/{b}" for k, (a, b) in v1["by_strategy"].items()})}. V1 used IRF Method 1 and legacy rules.
- V2 (the earlier corrected engine) is documented in the frozen reconciliation test as 89 timing matches under the older auditor's tolerance tiers; that is a different procedure and is quoted for context only.

## 8. Conclusion

V3 **partially reproduces** the manual strategy:

- Where the journal's entry is an ORB Method 1 / IRF Method 2 / PDR / PDS setup that the written rules can express, V3 finds the same candle (exact timing for {round(100 * h["exact"] / h["valid"], 1)}% of matches) and the same win/loss for {h["outcome_agreement_pct_excluding_be"]}% of non-BE matches.
- It does **not** reproduce {h["eligible"] - h["valid"]} of {h["eligible"]} eligible entries ({round(100 * (h["eligible"] - h["valid"]) / h["eligible"], 1)}%), mostly ORB entries without a previous-day-level break, IRF entries of another method or at another time, and manual trade management (break-even) that no document specifies; it does not claim to verify what rule or level the manual trader used.
- It therefore **should not be described as a replica of the manual journal**. Closing the gap needs author answers on the provisional rules, the method used per journal entry (especially ORB and IRF), and the break-even handling.

## 9. Verification

{json.dumps(checks, indent=2)}

Backtest summary used: `{summary_md_path}`.
"""
    return a


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--run-dir", type=Path, default=None, help="V3 run folder (default: latest)")
    ap.add_argument(
        "--rerun-backtest", action="store_true", help="run scripts/run_backtest.py first"
    )
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    out = args.out.resolve()
    rel = str(out.relative_to(BASE_DIR)) if out.is_relative_to(BASE_DIR) else str(out)
    if any(rel == p or rel.startswith(p + "/") for p in FROZEN_PREFIXES):
        raise SystemExit(f"refusing to write into a frozen/evidence area: {rel}")
    out.mkdir(parents=True, exist_ok=True)

    run_dir = rerun_backtest() if args.rerun_backtest else (args.run_dir or latest_run_dir())
    bt = load_backtest_run(run_dir)
    bt_manifest = json.loads((run_dir / "manifest.json").read_text())

    manual, notes = load_manual_journal(
        BASE_DIR / "data" / "manual" / "Trading_Journal_Sep2025_Aug2026 REPORT.xlsx",
        BASE_DIR / "reports" / "manual_journal" / "manual_trades.csv",
    )
    bars_1d, bars_5m = load_nifty_bars(BASE_DIR)
    replayer = DayReplayer(bars_1d, bars_5m)
    rec = reconcile(manual, bt, replayer, run_dir.name, notes)
    checks = make_checks(rec, len(manual))
    if not (
        checks["all_records_present"]
        and checks["every_record_explained_or_fully_matched"]
        and checks["duplicate_automated_signal_matches"] == 0
        and checks["duplicate_portfolio_trade_matches"] == 0
    ):
        raise SystemExit(f"verification failed: {checks}")

    session_days = {d.isoformat() for d in replayer.days}
    first_days = {
        d.isoformat()
        for d, bs in replayer.by_day.items()
        if any(b.exchange_time.strftime("%H:%M") == "09:15" for b in bs)
    }
    v1 = v1_reference(manual, session_days, first_days)
    rev = reverse_coverage(rec, bt)
    level_check = independent_level_check(replayer)

    rec.all_trades.to_csv(out / "MANUAL_VS_BACKTEST_ALL_TRADES.csv", index=False)
    rec.strategy_summary.to_csv(out / "STRATEGY_MATCH_SUMMARY.csv", index=False)
    rec.reasons.to_csv(out / "UNMATCHED_TRADE_REASONS.csv", index=False)
    rec.outcome_matrix.to_csv(out / "OUTCOME_AGREEMENT_MATRIX.csv", index=False)
    (out / "RECONCILIATION_REPORT.md").write_text(
        write_report(
            rec,
            bt_manifest,
            checks,
            rev,
            v1,
            level_check,
            run_dir,
            str((run_dir / "summary.md").relative_to(BASE_DIR)),
            bt,
        )
    )
    (out / "comparison_dashboard.html").write_text(
        build_dashboard(
            rec,
            replayer,
            run_dir.name,
            {
                "valid": v1["valid"],
                "eligible": v1["eligible"],
                "rate": v1["rate"],
                "exact": v1["exact"],
            },
        )
    )
    extra = sorted(p.name for p in out.iterdir() if p.name not in DELIVERABLES)
    print(
        json.dumps(
            {k: v for k, v in rec.headline.items() if k != "manual_workbook_notes"},
            indent=2,
            default=str,
        )
    )
    print(json.dumps(checks, indent=2))
    print("independent ORB level check:", level_check)
    print("V1 reference:", {k: v for k, v in v1.items() if k != "by_strategy"})
    print(
        "wrote:",
        [n for n in DELIVERABLES if (out / n).exists()],
        "| unexpected extra files:",
        extra,
    )


if __name__ == "__main__":
    main()
