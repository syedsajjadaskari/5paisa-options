"""Generate Phase 4E.1 shadow diagnostics and live-session acceptance reports."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from trading_platform.paper.shadow import classify_rejection_reason

ROOT = Path(__file__).resolve().parents[1]
PHASE4E = ROOT / "reports" / "independent_codex_audit" / "phase4e"
OUT = ROOT / "reports" / "independent_codex_audit" / "phase4e1"
DB = ROOT / "data" / "processed" / "paper_trading.duckdb"
HIST = ROOT / "data" / "processed" / "nifty" / "5m" / "nifty50_5m.parquet"
TZ_IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
EXPECTED_MINUTES = 375
EXPECTED_5M_CANDLES = 75
CATEGORIES = (
    "INCOMPLETE_CANDLE",
    "MISSING_REFERENCE_DATA",
    "STALE_UNDERLYING",
    "INSUFFICIENT_HISTORY",
    "RULEBOOK_AMBIGUITY",
    "INDICATOR_NOT_READY",
    "MARKET_SESSION_RESTRICTION",
    "STRATEGY_CONDITION_FALSE",
    "INTERNAL_ERROR",
)


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _to_ist(value: object) -> datetime:
    dt = pd.Timestamp(value).to_pydatetime()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ_IST)
    return dt.astimezone(TZ_IST)


def _in_session(value: object) -> bool:
    dt = _to_ist(value)
    return SESSION_OPEN <= dt.time() < SESSION_CLOSE


def _max_consecutive_missing(minutes: list[datetime]) -> int:
    if not minutes:
        return EXPECTED_MINUTES
    observed = {dt.replace(second=0, microsecond=0) for dt in minutes}
    first = minutes[0].replace(hour=9, minute=15, second=0, microsecond=0)
    last = first + timedelta(minutes=EXPECTED_MINUTES - 1)
    cursor = first
    longest = current = 0
    while cursor <= last:
        if cursor not in observed:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
        cursor += timedelta(minutes=1)
    return longest


def _load_shadow() -> pd.DataFrame:
    path = PHASE4E / "SHADOW_SIGNAL_AUDIT.csv"
    frame = pd.read_csv(path)
    frame["rejection_category"] = frame["reason_code"].map(classify_rejection_reason)
    return frame


def write_shadow_reports(shadow: pd.DataFrame) -> None:
    strategies = sorted(shadow["strategy"].unique())
    rows: list[dict[str, object]] = []
    for strategy in strategies:
        subset = shadow[shadow["strategy"] == strategy]
        total = len(subset)
        counts = Counter(subset["rejection_category"])
        for category in CATEGORIES:
            count = counts[category]
            reason_codes = sorted(
                subset.loc[subset["rejection_category"] == category, "reason_code"].unique()
            )
            rows.append(
                {
                    "strategy": strategy,
                    "evaluations": total,
                    "rejection_category": category,
                    "rejected": count,
                    "percentage_of_strategy": round((count / total) * 100, 2) if total else 0.0,
                    "reason_codes": "|".join(reason_codes),
                    "signal_eligible": 0,
                }
            )
    _write_csv(
        OUT / "SHADOW_REJECTION_BREAKDOWN.csv",
        rows,
        [
            "strategy",
            "evaluations",
            "rejection_category",
            "rejected",
            "percentage_of_strategy",
            "reason_codes",
            "signal_eligible",
        ],
    )

    reason_totals = Counter(shadow["rejection_category"])
    strategy_totals = Counter(shadow["strategy"])
    reason_text = ", ".join(f"{key}={value}" for key, value in sorted(reason_totals.items()))
    (OUT / "SHADOW_REJECTION_ROOT_CAUSE.md").write_text(
        "# Phase 4E.1 Shadow Rejection Root Cause\n\n"
        f"The existing audit contains {len(shadow)} rows: "
        + ", ".join(f"{key}={value}" for key, value in sorted(strategy_totals.items()))
        + ".\n\n"
        "## Exact primary reasons\n\n"
        f"Category totals are {reason_text}. Every row has a single primary category in the CSV.\n\n"
        "- 300 rows (PDS, ORB, IRF and PDR; 75 each) are `MISSING_REFERENCE_DATA`. "
        "Their rule evaluation says `completed_candle=true`, `aligned_to_5m=true`, "
        "`future_observation=false`, but `previous_day_levels_available=false`. The replay "
        "caller passed `{}` to the shadow evaluator, so these rows were not strategy-condition "
        "false; they were not eligible for a point-in-time rule evaluation.\n"
        "- 75 rows (RF; all 75 candles) are `RULEBOOK_AMBIGUITY` because the canonical RF exit/target "
        "is explicitly unresolved. This is an intentional safety gate, not a blanket data failure.\n\n"
        "## Blanket-gating investigation\n\n"
        "There is no evidence that candle alignment or future-data gating caused the 375 rejections: "
        "all 375 rows report aligned, completed, non-future candles. There is also no `STRATEGY_CONDITION_FALSE` "
        "row because the conservative shadow collector stops before evaluating strategy conditions when required "
        "inputs or rulebook semantics are unavailable. The diagnostic change records those gates separately so a "
        "future run cannot confuse missing evidence with a false setup.\n\n"
        "The next safe diagnostic step is to provide verified prior-day levels and indicator history to the shadow "
        "evaluator. PDS/PDR/ORB/IRF must still remain rejected as `RULEBOOK_AMBIGUITY` until the author confirms "
        "the unresolved definitions; RF remains non-executable until its canonical exit is specified.\n",
        encoding="utf-8",
    )

    readiness: list[dict[str, object]] = []
    for strategy in strategies:
        subset = shadow[shadow["strategy"] == strategy]
        cats = Counter(subset["rejection_category"])
        if cats["MISSING_REFERENCE_DATA"]:
            blocker = "MISSING_REFERENCE_DATA: replay supplied no point-in-time prior-day levels"
            reference = "NOT_AVAILABLE_TO_EVALUATOR"
        elif cats["RULEBOOK_AMBIGUITY"]:
            blocker = "RULEBOOK_AMBIGUITY: developer clarification required"
            reference = "NOT_EVALUATED"
        else:
            blocker = "|".join(sorted(cats))
            reference = "NOT_EVALUATED"
        readiness.append(
            {
                "strategy": strategy,
                "evaluations": len(subset),
                "completed_candles": int(
                    json.loads(subset.iloc[0]["rule_evaluations"]).get("completed_candle", False)
                )
                * len(subset),
                "missing_reference_rows": cats["MISSING_REFERENCE_DATA"],
                "stale_underlying_rows": cats["STALE_UNDERLYING"],
                "indicator_not_ready_rows": cats["INDICATOR_NOT_READY"],
                "rulebook_ambiguity_rows": cats["RULEBOOK_AMBIGUITY"],
                "reference_data_status": reference,
                "signal_eligible": 0,
                "primary_blocker": blocker,
            }
        )
    _write_csv(
        OUT / "STRATEGY_INPUT_READINESS.csv",
        readiness,
        list(readiness[0]) if readiness else ["strategy"],
    )


def _run_coverage_rows() -> list[dict[str, object]]:
    if not DB.exists():
        return []
    con = duckdb.connect(str(DB), read_only=True)
    market = con.execute(
        """
        SELECT run_id, timestamp, ingestion_timestamp, is_stale, provenance_status
        FROM market_events
        WHERE run_id LIKE 'READONLY_%' OR run_id LIKE 'PHASE4D_%'
        ORDER BY timestamp
        """
    ).fetchdf()
    options = con.execute(
        """
        SELECT run_id, provider_timestamp, receipt_timestamp, scrip_code, option_type,
               is_stale, provenance_status
        FROM options_archive
        WHERE run_id LIKE 'READONLY_%' OR run_id LIKE 'PHASE4D_%'
        ORDER BY provider_timestamp
        """
    ).fetchdf()
    candle_columns = {row[0] for row in con.execute("DESCRIBE candles_5m").fetchall()}
    complete_expression = "is_complete" if "is_complete" in candle_columns else "TRUE AS is_complete"
    candles = con.execute(
        f"""
        SELECT run_id, exchange_time, {complete_expression}
        FROM candles_5m
        WHERE run_id LIKE 'READONLY_%' OR run_id LIKE 'PHASE4D_%'
        ORDER BY exchange_time
        """
    ).fetchdf()
    con.close()

    rows: list[dict[str, object]] = []
    run_ids = sorted(
        set(market.get("run_id", [])) | set(options.get("run_id", [])) | set(candles.get("run_id", []))
    )
    for run_id in run_ids:
        m = market[market["run_id"] == run_id].copy()
        o = options[options["run_id"] == run_id].copy()
        c = candles[candles["run_id"] == run_id].copy()
        m_session = m[m["timestamp"].map(_in_session)] if not m.empty else m
        o_session = o[o["provider_timestamp"].map(_in_session)] if not o.empty else o
        timestamps = [_to_ist(value) for value in m_session["timestamp"].tolist()]
        session_date = timestamps[0].date().isoformat() if timestamps else "UNKNOWN"
        unique_minutes = sorted({value.replace(second=0, microsecond=0) for value in timestamps})
        valid_spot = int(
            sum(
                not bool(stale)
                and provenance == "VALID_EXCHANGE_TIMESTAMPED"
                for stale, provenance in zip(
                    m_session["is_stale"], m_session["provenance_status"], strict=False
                )
            )
        )
        valid_options = int(
            sum(
                not bool(stale)
                and provenance == "VALID_EXCHANGE_TIMESTAMPED"
                for stale, provenance in zip(
                    o_session["is_stale"], o_session["provenance_status"], strict=False
                )
            )
        )
        contracts = Counter(str(value) for value in o_session["option_type"].tolist())
        complete_candles = int(c["is_complete"].fillna(False).sum()) if not c.empty else 0
        rows.append(
            {
                "scope": "OBSERVED_CAPTURE",
                "run_id": run_id,
                "session_date": session_date,
                "expected_session_minutes": EXPECTED_MINUTES,
                "minutes_observed": len(unique_minutes),
                "minutes_missing": max(0, EXPECTED_MINUTES - len(unique_minutes)),
                "max_consecutive_missing_minutes": _max_consecutive_missing(unique_minutes),
                "valid_spot_observations": valid_spot,
                "valid_spot_observation_pct": round((valid_spot / len(m_session)) * 100, 2)
                if len(m_session)
                else 0.0,
                "valid_option_observations": valid_options,
                "contract_quote_coverage": ";".join(f"{key}={value}" for key, value in sorted(contracts.items())),
                "complete_5m_candles": complete_candles,
                "rejected_records": "NOT_PERSISTED_IN_ACCEPTED_TABLES",
                "rejection_reasons": "See collector heartbeat/log for rejected requests",
                "reconnection_events": "NOT_AVAILABLE_FOR_THIS_CAPTURE",
                "status": "VERIFIED_SAMPLE_NOT_FULL_SESSION",
            }
        )
    return rows


def write_coverage_report() -> None:
    rows = _run_coverage_rows()
    if not any(row["run_id"].startswith("READONLY_") and row["minutes_observed"] >= EXPECTED_MINUTES for row in rows):
        rows.append(
            {
                "scope": "FULL_SESSION_ACCEPTANCE_GATE",
                "run_id": "NONE",
                "session_date": "PENDING",
                "expected_session_minutes": EXPECTED_MINUTES,
                "minutes_observed": 0,
                "minutes_missing": EXPECTED_MINUTES,
                "max_consecutive_missing_minutes": EXPECTED_MINUTES,
                "valid_spot_observations": 0,
                "valid_spot_observation_pct": 0.0,
                "valid_option_observations": 0,
                "contract_quote_coverage": "PENDING_NO_COMPLETE_LIVE_SESSION",
                "complete_5m_candles": 0,
                "rejected_records": "PENDING_NO_COMPLETE_LIVE_SESSION",
                "rejection_reasons": "PENDING_NO_COMPLETE_LIVE_SESSION",
                "reconnection_events": "PENDING_NO_COMPLETE_LIVE_SESSION",
                "status": "PENDING_NO_COMPLETE_LIVE_SESSION",
            }
        )
    fields = [
        "scope",
        "run_id",
        "session_date",
        "expected_session_minutes",
        "minutes_observed",
        "minutes_missing",
        "max_consecutive_missing_minutes",
        "valid_spot_observations",
        "valid_spot_observation_pct",
        "valid_option_observations",
        "contract_quote_coverage",
        "complete_5m_candles",
        "rejected_records",
        "rejection_reasons",
        "reconnection_events",
        "status",
    ]
    _write_csv(OUT / "FULL_SESSION_DATA_COVERAGE.csv", rows, fields)


def write_candle_report() -> None:
    rows = []
    if HIST.exists():
        frame = pd.read_parquet(HIST)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
        session = frame[
            (frame["timestamp"].dt.time >= SESSION_OPEN)
            & (frame["timestamp"].dt.time < SESSION_CLOSE)
        ]
        counts = session.groupby(session["timestamp"].dt.date).size()
        full_days = counts[counts >= EXPECTED_5M_CANDLES]
        rows = [
            {
                "source": "data/processed/nifty/5m/nifty50_5m.parquet",
                "observed_full_session_days": int(len(full_days)),
                "sample_replay_day": str(full_days.index[0]) if not full_days.empty else "NONE",
                "sample_replay_candles": int(full_days.iloc[0]) if not full_days.empty else 0,
                "source_kind": "provider historical 5m OHLC file",
                "breakout_validation_status": "REPLAY_ONLY_NOT_LIVE",
            }
        ]
    (OUT / "CANDLE_SOURCE_VALIDATION.md").write_text(
        "# Candle Source Validation\n\n"
        "## Live path\n\n"
        "`FiveMinuteCandleAggregator` consumes `SpotTick.last_rate` and reconstructs each bar from the "
        "sampled ticks observed by the poller. Its high and low are the maximum and minimum sampled prices; "
        "the provider's session high/low fields are not an intrabar OHLC source. Therefore a sparse poll can "
        "miss the true exchange high or low. Live aggregated candles are marked `FIVEPAISA_WS` and are "
        "`INSUFFICIENT_FOR_BREAKOUT_VALIDATION` until reconciled to an authoritative provider candle.\n\n"
        "A candle is emitted only after a provider-timestamped tick crosses the next five-minute boundary. "
        "The current incomplete bar is not sent to shadow evaluation. A gap between completed buckets is "
        "recorded as missing and the affected sampled bar is not accepted as a complete continuous session.\n\n"
        "## Verified reconciliation source\n\n"
        "The broker adapter exposes authenticated 5paisa historical candles through `FivePaisaBroker.fetch_historical_candles()` "
        "using `/V2/historical/N/C/999920000/5m`. That endpoint is suitable for completed-bar reconciliation, "
        "subject to authentication and provider availability. It was not called during this out-of-hours diagnostic, "
        "so no live reconciliation is claimed.\n\n"
        "## Stored historical sample\n\n"
        + (pd.DataFrame(rows).to_csv(index=False) if rows else "No historical candle file was available.\n")
        + "\nThe historical file can validate a replay session's timestamps and OHLC invariants, but it does not "
        "convert sampled live ticks into authoritative live candles.\n",
        encoding="utf-8",
    )


def write_questions() -> None:
    examples = "No historical examples available."
    if HIST.exists():
        frame = pd.read_parquet(HIST).head(3)
        examples = "\n".join(
            f"- `{row.timestamp}` O={row.open} H={row.high} L={row.low} C={row.close}"
            for row in frame.itertuples()
        )
    (OUT / "STRATEGY_DEVELOPER_QUESTIONS.md").write_text(
        "# Strategy Developer Clarification Questions\n\n"
        "These questions use the original IRF, ORB, PDR, PDS and RF PDFs and the prior Codex audit. "
        "No answer has been inferred into the executable path.\n\n"
        "## Priority questions\n\n"
        "1. **PDS:** For the support-turned-resistance zone, which candle supplies the support body, what are the "
        "exact good-red/HPR thresholds, and what precise event arms Method 3's small-support fallback? These choices "
        "can materially change PDS counts.\n"
        "2. **PDR:** Which prior-day candle defines the resistance body, what is the retest tolerance, and is the "
        "confirmation required to close above the zone body or only to be green inside the zone? These choices can "
        "materially change PDR counts.\n"
        "3. **ORB:** Define established trend, the exact lookback/source for inner resistance/support, body and opposing-wick "
        "thresholds, and whether confirmation is close-only or intrabar. These choices can materially change ORB counts.\n\n"
        "## Remaining questions\n\n"
        "4. **IRF:** Confirm support/resistance lookback, the exact 60–70% sweep-wick calculation, and whether the break "
        "is evaluated on close only.\n"
        "5. **RF:** Provide the canonical entry, stop, target, and forced-exit policy. The PDF's open target/exit language "
        "means RF must remain `RULEBOOK_AMBIGUITY`.\n"
        "6. **All strategies:** Confirm whether a 5-minute bar timestamp denotes bar open, and whether a signal may be "
        "evaluated only after the next provider timestamp confirms that bar is closed.\n\n"
        "## Genuine historical examples\n\n"
        "The following are unlabelled candles from the verified historical file; they are examples for clarification, "
        "not claimed signals:\n\n"
        + examples
        + "\n\nUntil answered, no shadow evaluation may create a signal, virtual trade, or P&L.\n",
        encoding="utf-8",
    )


def write_acceptance() -> None:
    (OUT / "FULL_SESSION_ACCEPTANCE.md").write_text(
        "# Phase 4E.1 Full-Session Read-Only Acceptance\n\n"
        "## Decision\n\n"
        "**CONDITIONAL PASS — diagnostics and collector safeguards are ready; full-session live validation is PENDING.**\n\n"
        "A genuine full normal NSE session was not captured in Phase 4E.1. The current environment was outside "
        "09:15–15:30 IST when validation was run, and the collector stopped at the calendar gate without making API calls. "
        "The Phase 4D sample remains a verified short read-only sample, not full-session evidence.\n\n"
        "The acceptance gate requires one run with 09:15–15:30 IST coverage, actual provider timestamps, accepted spot and "
        "near-ATM CE/PE quotes, contract metadata, freshness, gaps, reconnection telemetry, and 75 completed five-minute "
        "bars. Sampled-tick OHLC must remain non-breakout-authoritative until reconciled with the authenticated 5paisa "
        "historical candle endpoint.\n\n"
        "## Safe commands\n\n"
        "Start during a normal NSE session:\n\n"
        "```bash\n"
        ".venv/bin/python scripts/run_phase4e_collector.py observe\n"
        "```\n\n"
        "Stop with `Ctrl-C`; shutdown is persisted as `STOPPED` and the lock is released. Inspect status with:\n\n"
        "```bash\n"
        ".venv/bin/python scripts/run_phase4e_collector.py status\n"
        ".venv/bin/python scripts/generate_phase4e1_reports.py\n"
        "```\n\n"
        "Paper execution, simulated fills, account mutations, live orders, optimization and BASELINE_V1 changes remain disabled.\n",
        encoding="utf-8",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    shadow = _load_shadow()
    write_shadow_reports(shadow)
    write_coverage_report()
    write_candle_report()
    write_questions()
    write_acceptance()
    print(json.dumps({"output": str(OUT), "shadow_rows": len(shadow), "decision": "CONDITIONAL_PASS"}))


if __name__ == "__main__":
    main()
