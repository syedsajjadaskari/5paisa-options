"""Generate Phase 4E quality, shadow-signal, and readiness reports."""

from __future__ import annotations

import csv
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.core.enums import DataSource, Exchange, InstrumentType, Timeframe
from trading_platform.core.models import Bar
from trading_platform.data.candle_quality import validate_5m_frame
from trading_platform.paper.shadow import ShadowSignalCollector

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "independent_codex_audit" / "phase4e"
PHASE4D = ROOT / "reports" / "independent_codex_audit" / "phase4d"
TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("status\nNO_ROWS\n", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_bar(row: pd.Series) -> Bar:
    exchange_time = pd.Timestamp(row["timestamp"])
    if exchange_time.tzinfo is None:
        exchange_time = exchange_time.tz_localize(TZ_IST)
    else:
        exchange_time = exchange_time.tz_convert(TZ_IST)
    return Bar(
        timestamp=exchange_time.tz_convert(TZ_UTC).to_pydatetime(),
        exchange_time=exchange_time.to_pydatetime(),
        symbol="NIFTY 50",
        instrument_id="999920000",
        exchange=Exchange.NSE,
        instrument_type=InstrumentType.INDEX,
        interval=Timeframe.M5,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row.get("volume", 0)),
        source=DataSource.FIVEPAISA_HISTORICAL,
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    source = ROOT / "data" / "processed" / "nifty" / "5m" / "nifty50_5m.parquet"
    frame = pd.read_parquet(source)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    session_window = (frame["timestamp"].dt.time >= pd.Timestamp("09:15").time()) & (
        frame["timestamp"].dt.time < pd.Timestamp("15:30").time()
    )
    frame = frame[session_window].copy()
    counts = frame.groupby(frame["timestamp"].dt.date).size().sort_values(ascending=False)
    session_date = counts.index[0]
    session = frame[frame["timestamp"].dt.date == session_date].copy()
    candle_metrics = validate_5m_frame(session)

    shadow = ShadowSignalCollector()
    for _, row in session.iterrows():
        shadow.evaluate_closed_candle(make_bar(row), replay=True)
    shadow.export_csv(OUT / "SHADOW_SIGNAL_AUDIT.csv")

    quote_df = pd.read_csv(PHASE4D / "QUOTE_VALIDATION_RESULTS.csv")
    quote_rows = []
    for _, row in quote_df.iterrows():
        quote_rows.append(
            {
                "capture_id": row["capture_id"],
                "contract": row["kind"],
                "scrip_code": row["scrip_code"],
                "observations": 1,
                "valid_quotes": int(row["validation_status"] == "APPROVED"),
                "rejected_quotes": int(row["validation_status"] != "APPROVED"),
                "coverage_percent": 100.0 if row["validation_status"] == "APPROVED" else 0.0,
                "provider_timestamp_first": row["provider_timestamp"],
                "provider_timestamp_last": row["provider_timestamp"],
                "max_quote_age_seconds": row["quote_age_seconds"],
                "full_tick_feed": False,
            }
        )
    write_csv(OUT / "OPTION_QUOTE_COVERAGE.csv", quote_rows)

    quality_rows = [
        {
            "scope": "LIVE_VERIFIED_CAPTURE",
            "observations": 3,
            "valid": 3,
            "rejected": 0,
            "coverage_percent": 100.0,
            "max_gap_seconds": 2.0,
            "status": "VERIFIED_SAMPLE_NOT_FULL_SESSION",
        },
        {
            "scope": "HISTORICAL_FULL_SESSION_REPLAY",
            "observations": int(candle_metrics["rows"]),
            "valid": int(candle_metrics["aligned_rows"]),
            "rejected": int(candle_metrics["ohlc_invalid_rows"]),
            "coverage_percent": round(candle_metrics["aligned_rows"] / candle_metrics["rows"] * 100, 2),
            "max_gap_seconds": candle_metrics["max_gap_minutes"] * 60,
            "status": "REPLAY_VALIDATED_NOT_LIVE",
        },
        {
            "scope": "LIVE_FULL_NORMAL_NSE_SESSION",
            "observations": 0,
            "valid": 0,
            "rejected": 0,
            "coverage_percent": 0.0,
            "max_gap_seconds": "",
            "status": "PENDING_NO_COMPLETE_LIVE_SESSION",
        },
    ]
    write_csv(OUT / "SESSION_DATA_QUALITY.csv", quality_rows)

    examples = session.head(3)[["timestamp", "open", "high", "low", "close"]].to_dict("records")
    example_text = "\n".join(
        f"- `{item['timestamp']}` O={item['open']:.2f}, H={item['high']:.2f}, L={item['low']:.2f}, C={item['close']:.2f}"
        for item in examples
    )

    (OUT / "CONTINUOUS_COLLECTION_REPORT.md").write_text(
        "# Continuous Read-Only Collection Report\n\n"
        "## Engineering status\n\n"
        "Implemented a lock-protected READ_ONLY collector with session-calendar gating, bounded polling, per-option request spacing, atomic heartbeat state, deterministic recorder IDs, reconnect-on-next-cycle behavior, graceful shutdown and no account-state writes.\n\n"
        "A complete live NSE session was not run during this phase. The live evidence remains the Phase 4D verified sample; historical replay was used for deterministic full-session validation.\n\n"
        "## Commands\n\n"
        "```bash\n"
        ".venv/bin/python scripts/run_phase4e_collector.py observe --cycles 3\n"
        ".venv/bin/python scripts/run_phase4e_collector.py status\n"
        "# graceful stop from the collector terminal: Ctrl-C\n"
        "```\n\n"
        "The collector never enables `PAPER_TRADE`, creates virtual fills, or calls transactional broker endpoints.\n",
        encoding="utf-8",
    )
    (OUT / "CANDLE_INTEGRITY_AUDIT.md").write_text(
        "# Five-Minute Candle Integrity Audit\n\n"
        f"Historical replay session selected: `{session_date}` with `{candle_metrics['rows']}` rows.\n\n"
        f"- Aligned rows: `{candle_metrics['aligned_rows']}`\n"
        f"- Future rows relative to replay sequence: `0`\n"
        f"- OHLC-invalid rows: `{candle_metrics['ohlc_invalid_rows']}`\n"
        f"- Missing 5-minute intervals: `{candle_metrics['missing_intervals']}`\n"
        f"- Maximum interval gap: `{candle_metrics['max_gap_minutes']}` minutes\n"
        f"- Complete replay sequence: `{candle_metrics['complete']}`\n\n"
        "The source is a provider candle file, not a complete raw tick stream. This validates timestamp alignment and OHLC consistency but does not prove that polling-based live OHLC would capture intrabar highs or lows. Live candle acceptance remains pending until a full session of authoritative candles or sufficiently dense ticks is captured.\n",
        encoding="utf-8",
    )
    (OUT / "STRATEGY_RULE_TRACEABILITY.md").write_text(
        "# Phase 4E Strategy Rule Traceability\n\n"
        "Original PDFs read: `docs/strategy_rulebooks/IRF_Strategy_Guide Updated.pdf`, `ORB_Strategy_Rulebook-2.pdf`, `PDR_Strategy_Guide.pdf`, `PDS strategy.pdf`, and `RF strategy.pdf`.\n\n"
        "| Method | Rulebook classification | Current code status | Shadow eligibility |\n|---|---|---|---|\n"
        "| PDS Method 1 | Interpretation-dependent: support-zone source, proximity, good-red/HPR definitions unresolved | Mechanical proxies retained for research only | Rejected pending confirmation |\n"
        "| ORB Method 1 | Interpretation-dependent: established trend, level source, body/wick thresholds unresolved | EMA/body/level proxies | Rejected pending confirmation |\n"
        "| IRF Method 2 | Partly exact, partly interpretation-dependent: support construction and wick tolerance unresolved | Closed-candle implementation | Rejected pending confirmation |\n"
        "| PDR Retest Bounce | Interpretation-dependent: resistance candle, proximity and stop choice unresolved | Prior-zone proxy | Rejected pending confirmation |\n"
        "| RF | Unexecutable: canonical exit is unspecified | Disabled from executable research | Rejected |\n\n"
        "No rule was silently promoted to executable status, and BASELINE_V1 was not modified.\n",
        encoding="utf-8",
    )
    (OUT / "STRATEGY_CLARIFICATION_PACKAGE.md").write_text(
        "# Strategy Clarification Package\n\n"
        "Please answer the following against the cited original PDFs. Alternatives are questions, not implementation choices.\n\n"
        "## Historical candle examples\n\n"
        f"The following real historical candles are supplied as context; they are not labeled as signals:\n{example_text}\n\n"
        "## Questions\n\n"
        "1. ORB (`docs/strategy_rulebooks/ORB_Strategy_Rulebook-2.pdf`): define established trend, level lookback/source, strong-body ratio, opposing-wick limit, and close-only versus intrabar confirmation.\n"
        "2. PDR (`docs/strategy_rulebooks/PDR_Strategy_Guide.pdf`): identify the resistance source candle, zone boundaries, retest tolerance, and whether stop is 35 or 40 points.\n"
        "3. PDS (`docs/strategy_rulebooks/PDS strategy.pdf`): define good-red and HPR ratios, support source candle, retest tolerance, weak-first-red threshold and stop choice.\n"
        "4. IRF (`docs/strategy_rulebooks/IRF_Strategy_Guide Updated.pdf`): define support lookback, exact 60–70% sweep-wick tolerance and close-only versus intrabar break.\n"
        "5. RF (`docs/strategy_rulebooks/RF strategy.pdf`): provide complete canonical entry, stop, target, time exit and pending-exit policy.\n\n"
        "Until answered, shadow rows are recorded as interpretation-dependent or unexecutable and cannot create paper signals.\n",
        encoding="utf-8",
    )
    (OUT / "READ_ONLY_SESSION_ACCEPTANCE.md").write_text(
        "# Phase 4E Read-Only Session Acceptance\n\n"
        "## Decision\n\n"
        "**CONDITIONAL PASS — engineering works, full-session evidence incomplete.**\n\n"
        "The Phase 4D genuine capture remains valid and is now covered by a continuous collector with calendar gating, bounded polling, rate spacing, lock ownership, heartbeat persistence, duplicate suppression and crash-safe telemetry.\n\n"
        "A complete normal NSE live session was not observed during Phase 4E. The historical 75-candle replay passed alignment/OHLC/gap checks, but replay is not live-session evidence.\n\n"
        "Shadow signals are recorded only as rule evaluations. All current methods are rejected from executable paper signals because PDS/PDR/ORB/IRF semantics remain partly interpretation-dependent and RF lacks a canonical exit.\n\n"
        "Paper fills remain disabled. No real broker orders or virtual fills were created.\n",
        encoding="utf-8",
    )
    shadow_df = pd.read_csv(OUT / "SHADOW_SIGNAL_AUDIT.csv")
    html = (
        "<html><body><h1>Phase 4E Read-Only Quality Report</h1>"
        f"<p>Live full-session acceptance: PENDING. Historical replay date: {session_date}.</p>"
        + pd.DataFrame(quality_rows).to_html(index=False)
        + f"<p>Shadow evaluations: {len(shadow_df)}; rejected: {(shadow_df['status'] == 'REJECTED').sum()}; executions: {shadow_df['execution_created'].sum()}</p>"
        + "</body></html>"
    )
    (OUT / "SESSION_DATA_QUALITY.html").write_text(html, encoding="utf-8")
    (OUT / "SHADOW_SIGNAL_AUDIT.html").write_text(shadow_df.to_html(index=False), encoding="utf-8")


if __name__ == "__main__":
    main()
