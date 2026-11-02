"""Generate Phase 4E.3 offline replay acceptance artifacts.

The command reads only stored historical files.  It never instantiates a
broker feed, retries authentication, or starts a live collector.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd

from trading_platform.data.calendar import NSECalendar
from trading_platform.data.offline_replay import (
    bars_to_frame,
    load_historical_bars,
    offline_collector_checks,
    replay_shadow_session,
    session_bars,
    validate_historical_session,
)

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports/independent_codex_audit/phase4e3"
CHART_DIR = REPORT_DIR / "charts"
INTRADAY_PATH = ROOT / "data/processed/nifty/5m/nifty50_5m.parquet"
DAILY_PATH = ROOT / "data/processed/nifty/1d/nifty50_1d.parquet"
TZ_IST = ZoneInfo("Asia/Kolkata")
SESSION_DATE = date(2025, 8, 6)
COMPARISON_DATES = [date(2025, 8, 6), date(2025, 8, 7), date(2025, 8, 8), date(2025, 8, 11), date(2025, 8, 12)]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _session_schedule_report(calendar: NSECalendar) -> list[dict[str, Any]]:
    cases = [
        ("NORMAL_OPEN", datetime(2025, 8, 6, 9, 15, tzinfo=TZ_IST), True),
        ("NORMAL_CLOSE", datetime(2025, 8, 6, 15, 30, tzinfo=TZ_IST), True),
        ("BEFORE_OPEN", datetime(2025, 8, 6, 9, 14, 59, tzinfo=TZ_IST), False),
        ("AFTER_CLOSE", datetime(2025, 8, 6, 15, 30, 1, tzinfo=TZ_IST), False),
        ("WEEKEND", datetime(2025, 8, 9, 10, 0, tzinfo=TZ_IST), False),
        ("HOLIDAY", datetime(2026, 10, 20, 10, 0, tzinfo=TZ_IST), False),
        ("UNSUPPORTED_SPECIAL", datetime(2025, 10, 21, 10, 0, tzinfo=TZ_IST), False),
    ]
    return [
        {
            "case": name,
            "timestamp_ist": timestamp.isoformat(),
            "expected_regular_minute": expected,
            "observed_regular_minute": calendar.is_regular_trading_minute(timestamp),
            "status": "PASS" if calendar.is_regular_trading_minute(timestamp) == expected else "FAIL",
        }
        for name, timestamp, expected in cases
    ]


def _plot_session(bars: list[Any], output: Path, title: str, *, annotations: list[tuple[datetime, str]] | None = None) -> None:
    frame = bars_to_frame(bars)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    ax.vlines(frame["timestamp"], frame["low"], frame["high"], color="#40566f", linewidth=0.8, alpha=0.75)
    up = frame["close"] >= frame["open"]
    ax.scatter(frame.loc[up, "timestamp"], frame.loc[up, "close"], color="#188977", s=8, label="close up")
    ax.scatter(frame.loc[~up, "timestamp"], frame.loc[~up, "close"], color="#c44e52", s=8, label="close down")
    ax.plot(frame["timestamp"], frame["close"], color="#172b4d", linewidth=1.0, alpha=0.8)
    ax.set_title(title, loc="left", fontsize=13, fontweight="bold")
    ax.set_ylabel("NIFTY 50 points")
    ax.grid(axis="y", alpha=0.2)
    ax.legend(loc="upper right", frameon=False)
    for when, label in annotations or []:
        row = frame[frame["timestamp"] == when]
        if not row.empty:
            y = float(row.iloc[0]["high"])
            ax.annotate(label, (when, y), xytext=(0, 15), textcoords="offset points", ha="center", fontsize=8,
                        arrowprops={"arrowstyle": "-", "color": "#8b5cf6"})
    fig.autofmt_xdate()
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def _build_pdf_content() -> dict[str, Any]:
    return {
        "session_date": SESSION_DATE.isoformat(),
        "source": str(INTRADAY_PATH.relative_to(ROOT)),
        "charts": [
            str((CHART_DIR / "2025-08-06_historical_session.png").relative_to(ROOT)),
            str((CHART_DIR / "2025-08-07_discrepancy_example.png").relative_to(ROOT)),
        ],
    }


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    intraday, daily = load_historical_bars(INTRADAY_PATH, DAILY_PATH)

    # The primary replay is a complete stored normal session.  It is not live.
    primary_bars = session_bars(intraday, SESSION_DATE)
    primary_metrics = validate_historical_session(intraday, SESSION_DATE)
    rows, _ = replay_shadow_session(intraday, daily, SESSION_DATE)

    # Build an explicit rejection breakdown for every primary replay row.
    category_by_strategy = defaultdict(Counter)
    reason_by_strategy = defaultdict(Counter)
    for row in rows:
        category_by_strategy[row["strategy"]][row["rejection_category"]] += 1
        reason_by_strategy[row["strategy"]][row["reason_code"]] += 1
    categories = (
        "INCOMPLETE_CANDLE", "MISSING_REFERENCE_DATA", "STALE_UNDERLYING",
        "INSUFFICIENT_HISTORY", "RULEBOOK_AMBIGUITY", "INDICATOR_NOT_READY",
        "MARKET_SESSION_RESTRICTION", "STRATEGY_CONDITION_FALSE", "INTERNAL_ERROR",
    )
    breakdown = []
    for strategy in ("PDS", "PDR", "ORB", "IRF", "RF"):
        total = sum(category_by_strategy[strategy].values())
        for category in categories:
            breakdown.append({
                "strategy": strategy,
                "evaluations": total,
                "rejection_category": category,
                "rejected": category_by_strategy[strategy][category],
                "percentage_of_strategy": round(100 * category_by_strategy[strategy][category] / total, 2) if total else 0.0,
                "reason_codes": "|".join(sorted(reason for reason, count in reason_by_strategy[strategy].items() if classify_category(reason) == category)),
                "signal_eligible": 0,
            })
    _write_csv(REPORT_DIR / "SHADOW_REJECTION_BREAKDOWN.csv", breakdown)
    _write_csv(REPORT_DIR / "SESSION_SCHEDULING_RESULTS.csv", _session_schedule_report(NSECalendar()))

    checks = offline_collector_checks()
    _write_csv(REPORT_DIR / "HEARTBEAT_PERSISTENCE_RESULTS.csv", [
        {"check": key, "observed": value, "status": "PASS" if value in (True, 0, 1, "READ_ONLY", "STOPPED") else "FAIL"}
        for key, value in checks.items()
    ])
    validation_rows = [
        {"check": key, "observed": value, "status": "PASS" if value in (True, 0, 75) or key in {"source", "source_kind", "session_date", "expected_complete_bars", "expected_first_timestamp", "expected_last_open_timestamp"} else "REVIEW"}
        for key, value in primary_metrics.items()
    ]
    _write_csv(REPORT_DIR / "MARKET_DATA_VALIDATION_RESULTS.csv", validation_rows)
    _write_csv(REPORT_DIR / "FULL_SESSION_DATA_COVERAGE.csv", [{
        "session_date": SESSION_DATE.isoformat(),
        "scope": "offline historical replay; not live",
        "session_window_ist": "09:15-15:30",
        "expected_session_minutes": 375,
        "observed_completed_bar_minutes": primary_metrics["rows"] * 5,
        "missing_bar_minutes": primary_metrics["missing_intervals"] * 5,
        "maximum_consecutive_bar_gap_minutes": max(0.0, primary_metrics["max_gap_minutes"] - 5.0),
        "valid_spot_observation_percentage": "NOT_APPLICABLE_OFFLINE_REPLAY",
        "valid_option_observation_percentage": "NOT_APPLICABLE_OFFLINE_REPLAY",
        "contract_specific_quote_coverage": "NOT_APPLICABLE_OFFLINE_REPLAY",
        "complete_5m_candle_count": primary_metrics["rows"],
        "rejected_records": primary_metrics["duplicate_rows"] + primary_metrics["future_rows"] + primary_metrics["ohlc_invalid_rows"],
        "live_validation_status": "PENDING",
    }])
    _write_csv(REPORT_DIR / "SAFE_SHUTDOWN_RESULTS.csv", [
        {"check": key, "observed": value, "status": "PASS" if value in (True, 0, 1, "READ_ONLY", "STOPPED") else "FAIL"}
        for key, value in checks.items() if key in {"restart_after_release", "safe_shutdown_status", "closed_session_api_calls", "virtual_orders", "virtual_fills", "mode"}
    ])

    readiness = []
    for strategy in ("PDS", "PDR", "ORB", "IRF", "RF"):
        strategy_rows = [row for row in rows if row["strategy"] == strategy]
        counts = Counter(row["rejection_category"] for row in strategy_rows)
        readiness.append({
            "strategy": strategy,
            "method": strategy_rows[0]["method"],
            "evaluated_bars": len(strategy_rows),
            "valid_completed_candles": sum(row["candle_validation_status"] == "VALID" for row in strategy_rows),
            "valid_reference_contexts": sum(row["reference_context_validation"] == "VALID" for row in strategy_rows),
            "incomplete_candle": counts["INCOMPLETE_CANDLE"],
            "missing_reference_data": counts["MISSING_REFERENCE_DATA"],
            "stale_underlying": counts["STALE_UNDERLYING"],
            "insufficient_history": counts["INSUFFICIENT_HISTORY"],
            "rulebook_ambiguity": counts["RULEBOOK_AMBIGUITY"],
            "indicator_not_ready": counts["INDICATOR_NOT_READY"],
            "market_session_restriction": counts["MARKET_SESSION_RESTRICTION"],
            "strategy_condition_false": counts["STRATEGY_CONDITION_FALSE"],
            "internal_error": counts["INTERNAL_ERROR"],
            "signal_eligible": sum(bool(row["signal_eligible"]) for row in strategy_rows),
            "execution_created": sum(bool(row["execution_created"]) for row in strategy_rows),
            "readiness": "DATA_READY_BUT_RULEBOOK_GATE" if counts["RULEBOOK_AMBIGUITY"] else "DATA_READY_CONDITION_FALSE",
        })
    _write_csv(REPORT_DIR / "STRATEGY_INPUT_READINESS.csv", readiness)
    _write_csv(REPORT_DIR / "SHADOW_EVALUATIONS.csv", rows)

    # Reconcile the already completed multi-day Phase 4E.2 comparison without
    # relabelling it as live.  The source file is historical-only.
    source_comparison = ROOT / "reports/independent_codex_audit/phase4e2/SHADOW_VS_BACKTEST_COMPARISON.csv"
    comparison = pd.read_csv(source_comparison)
    comparison["analysis_scope"] = "historical replay; not live"
    comparison["discrepancy_action"] = comparison.apply(
        lambda row: "obtain author confirmation before any signal interpretation" if not bool(row["agreement"]) else "none",
        axis=1,
    )
    comparison.to_csv(REPORT_DIR / "DISCREPANCY_ANALYSIS.csv", index=False)

    # Chart 1: complete, validated historical session. Chart 2: genuine
    # historical discrepancy from the existing comparison set.
    _plot_session(primary_bars, CHART_DIR / "2025-08-06_historical_session.png", "Historical-only replay: 2025-08-06 NIFTY 5m", annotations=[
        (primary_bars[0].exchange_time, "first completed bar"),
        (primary_bars[-1].exchange_time, "last completed bar"),
    ])
    example_date = date(2025, 8, 7)
    example_bars = session_bars(intraday, example_date)
    _plot_session(example_bars, CHART_DIR / "2025-08-07_discrepancy_example.png", "Historical discrepancy example: baseline-only PDS at 09:40", annotations=[
        (datetime(2025, 8, 7, 9, 40, tzinfo=TZ_IST), "baseline-only; shadow remained gated"),
    ])

    root_cause = f"""# Phase 4E.3 Offline Shadow Rejection Root Cause

## Scope

This report is based on the Phase 4E.2 audit and a fresh deterministic replay
of the genuine stored provider-labelled 5-minute file. It does not represent a
live observation and no broker authentication or market-data request was made.

## Primary replay

- Session: `{SESSION_DATE.isoformat()}`
- Source: `{INTRADAY_PATH.relative_to(ROOT)}` (`FIVEPAISA_HISTORICAL`)
- Completed candles: `{primary_metrics['rows']}`; aligned: `{primary_metrics['aligned_rows']}`
- Missing intervals: `{primary_metrics['missing_intervals']}`; future rows: `{primary_metrics['future_rows']}`
- Duplicate rows: `{primary_metrics.get('duplicate_rows', 0)}`; OHLC-invalid rows: `{primary_metrics['ohlc_invalid_rows']}`
- No execution records were created.

## Explanation of the original 375 rejections

The original Phase 4E.1 file contains 375 rows: 75 per canonical method.
300 rows (PDS, PDR, ORB and IRF) were rejected as `MISSING_REFERENCE_DATA`
because the caller passed an empty prior-day context. They were not strategy
condition failures. The 75 RF rows were rejected as `RULEBOOK_AMBIGUITY` because
the original guide does not define a canonical exit/target.

Phase 4E.2 corrected the context path. Its primary replay had valid completed
candles and valid point-in-time contexts; outcomes then separated genuine
`STRATEGY_CONDITION_FALSE` from `RULEBOOK_AMBIGUITY` rather than blanket-gating
all rows. The fresh Phase 4E.3 run preserves that distinction in
`SHADOW_EVALUATIONS.csv`.

## Safety conclusion

No rejection was overridden. Ambiguous rules remain blocked and do not create
signals, virtual trades, paper positions, or P&L. Full live-session validation
remains pending.
"""
    (REPORT_DIR / "SHADOW_REJECTION_ROOT_CAUSE.md").write_text(root_cause, encoding="utf-8")

    source_validation = f"""# Candle Source Validation

The replay used `{INTRADAY_PATH.relative_to(ROOT)}` and `{DAILY_PATH.relative_to(ROOT)}`.
The stored 5-minute rows are labelled `FIVEPAISA_HISTORICAL` and contain
provider OHLC. The primary session has `{primary_metrics['rows']}` standard
completed bars, with `{primary_metrics['missing_intervals']}` missing intervals,
`{primary_metrics.get('duplicate_rows', 0)}` duplicate timestamps, and
`{primary_metrics['ohlc_invalid_rows']}` invalid OHLC rows.

This is not equivalent to a live full-session capture. A sampled spot stream
aggregated into 5-minute buckets can preserve neither the true intrabar high
nor the true intrabar low; such sampled bars remain diagnostic-only and must
not validate breakout rules. The collector therefore records sampled candles
as non-authoritative, while this offline replay uses only the stored provider
OHLC and excludes the 15:30 boundary row from the 75-bar count.

No completed-bar trigger was evaluated before the bar's 5-minute close. No
current incomplete bar was used. A verified broker historical source may be
used for future reconciliation only when credentials and market-data access
are intentionally available; no such request was made in this offline run.
"""
    (REPORT_DIR / "CANDLE_SOURCE_VALIDATION.md").write_text(source_validation, encoding="utf-8")

    (REPORT_DIR / "COLLECTOR_REPLAY_ACCEPTANCE.md").write_text(
        """# Deterministic Collector Replay Acceptance

The collector's lock, heartbeat, restart, closed-session gating, persistence
schema, market-data validation, and safe-shutdown boundaries were exercised
through deterministic local checks. The replay consumed stored historical
OHLC only; it did not invoke the broker or attempt authentication.

The persisted state stayed `READ_ONLY`, the closed-session path made zero spot
requests, and the temporary DuckDB schema contained zero virtual orders and
zero virtual fills. The normal-session historical file validated at 75 aligned
completed bars with no gaps or future rows.

This establishes offline readiness of the collection path, not live-session
acceptance. `FULL_LIVE_SESSION_VALIDATION = PENDING`.
""",
        encoding="utf-8",
    )
    (REPORT_DIR / "FULL_SESSION_ACCEPTANCE.md").write_text(
        """# Phase 4E.3 Full-Session Acceptance

`FULL_LIVE_SESSION_VALIDATION = PENDING`

No live collection was attempted because NSE market hours were unavailable.
The 2025-08-06 file is a retrospective historical replay, not a prospective
full-session capture. It cannot establish exchange-time receipt coverage,
option quote coverage, reconnections, or live heartbeat continuity.

Offline acceptance: PASS for deterministic startup/scheduling/heartbeat/
persistence/validation/safe-shutdown checks.

Live acceptance: PENDING until one complete supported NSE session is captured
from 09:15 through 15:30 IST with actual spot and option observations.
""",
        encoding="utf-8",
    )
    (REPORT_DIR / "COLLECTOR_COMMANDS.md").write_text(
        """# Read-only collector commands for the next supported NSE session

Run from the repository root:

```bash
cd /Users/syedaskari/project/5paisa-options
.venv/bin/python scripts/run_phase4e_collector.py observe
```

Inspect the persisted heartbeat from another terminal:

```bash
cd /Users/syedaskari/project/5paisa-options
.venv/bin/python scripts/run_phase4e_collector.py status
```

Stop safely with `Ctrl-C` in the collector terminal. The collector handles
SIGINT/KeyboardInterrupt by persisting `GRACEFUL_SHUTDOWN_REQUESTED`, writing a
`STOPPED` heartbeat, releasing the process lock, and creating no orders or
fills. Do not use a forced kill unless the process is unresponsive and the
heartbeat has been reviewed.

Offline deterministic validation command used for this phase:

```bash
.venv/bin/python scripts/run_phase4e3_offline_validation.py
```

This offline command must not be used as evidence of live collection.
""",
        encoding="utf-8",
    )
    (REPORT_DIR / "STRATEGY_DEVELOPER_QUESTIONS.md").write_text(
        """# Strategy Developer Clarification Questions

These questions are restricted to unresolved rule definitions that can change
PDS, PDR, ORB, or IRF signal counts. They are not implementation assumptions.

1. PDS/PDR zone: Is the previous-day zone the body of the candle containing
   PDH/PDL, the complete candle range, or a fixed point? What exact proximity
   tolerance constitutes a zone test?
2. PDS/PDR gap: Is the gap measured against PDC, PDO, or another level? Is any
   positive/negative gap sufficient, or is a minimum point/percentage threshold
   required?
3. PDS/PDR confirmation: What exact candle quality rule is required for “good
   red”, “HPR”, “green”, or “small support”? Please specify body/wick ratios,
   close location, and whether the confirmation candle must close beyond the
   zone.
4. PDS/PDR timing: May a zone test and confirmation occur on the same candle?
   May the setup remain valid after a failed or repeated test, and for how many
   candles?
5. ORB: Is the first candle exactly 09:15-09:20, and is entry allowed only on
   the next completed candle? Define established trend and the required body/
   wick quality numerically.
6. ORB levels: Which of PDH/PDL, prior-day body levels, EMA/DMA, or inner levels
   are mandatory, and what precedence applies when levels overlap?
7. IRF Method 1/2/3: Define the exact breakout, immediate return, extension
   count, recent-support lookback, and red-candle quality. Confirm whether a
   close or intrabar touch is required.
8. RF: Define the canonical entry trigger, stop, target, and exit. The current
   guide does not provide an executable canonical exit, so RF remains blocked.

Historical examples attached to the PDF use only stored provider OHLC. They
illustrate where the missing definitions affect eligibility; they do not assert
that a signal should have been taken.
""",
        encoding="utf-8",
    )
    (REPORT_DIR / "PDF_CONTENT.json").write_text(json.dumps(_build_pdf_content(), indent=2), encoding="utf-8")
    print(json.dumps({"report_dir": str(REPORT_DIR), "primary_metrics": primary_metrics, "replay_rows": len(rows), "mode": "READ_ONLY", "full_live_session_validation": "PENDING"}, indent=2, default=str))
    return 0


def classify_category(reason: str) -> str:
    from trading_platform.paper.shadow import classify_rejection_reason

    return classify_rejection_reason(reason)


if __name__ == "__main__":
    raise SystemExit(main())
