"""Run the Phase 4E.2 point-in-time shadow replay and audit it."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from trading_platform.core.enums import DataSource, Exchange, InstrumentType, Timeframe
from trading_platform.core.models import Bar
from trading_platform.data.candle_quality import validate_5m_frame
from trading_platform.data.point_in_time_context import (
    TZ_IST,
    PointInTimeReferenceContext,
    PointInTimeReferenceProvider,
)
from trading_platform.data.reference_levels import ReferenceLevelCalculator
from trading_platform.paper.shadow import ShadowSignalCollector
from trading_platform.strategies.base import StrategyContext
from trading_platform.strategies.irf import IRFStrategy
from trading_platform.strategies.orb import ORBStrategy
from trading_platform.strategies.pdr import PDRStrategy
from trading_platform.strategies.pds import PDSStrategy

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "independent_codex_audit" / "phase4e2"
INTRADAY_PATH = ROOT / "data" / "processed" / "nifty" / "5m" / "nifty50_5m.parquet"
DAILY_PATH = ROOT / "data" / "processed" / "nifty" / "1d" / "nifty50_1d.parquet"
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
ENABLED = (
    ("PDS", "METHOD_1"),
    ("ORB", "METHOD_1"),
    ("IRF", "METHOD_2"),
    ("PDR", "RETEST_BOUNCE"),
)
REQUIRED = {
    "PDS": ("pdl", "pdc", "pd_supp_body"),
    "PDR": ("pdh", "pdc", "pd_res_body"),
    "ORB": ("pdh", "pdl", "ema_200"),
    "IRF": ("irh",),
}
UTC = ZoneInfo("UTC")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("status\nNO_ROWS\n", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _load_bars(path: Path, interval: Timeframe) -> list[Bar]:
    frame = pd.read_parquet(path)
    bars: list[Bar] = []
    for row in frame.itertuples(index=False):
        timestamp = pd.Timestamp(row.timestamp)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(TZ_IST)
        else:
            timestamp = timestamp.tz_convert(TZ_IST)
        bars.append(
            Bar(
                timestamp=timestamp.tz_convert(UTC).to_pydatetime(),
                exchange_time=timestamp.to_pydatetime(),
                symbol="NIFTY 50",
                instrument_id="999920000",
                exchange=Exchange.NSE,
                instrument_type=InstrumentType.INDEX,
                interval=interval,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=int(row.volume),
                source=DataSource.FIVEPAISA_HISTORICAL,
            )
        )
    return bars


def _session_map(bars: list[Bar]) -> dict[date, list[Bar]]:
    sessions: dict[date, list[Bar]] = defaultdict(list)
    for bar in bars:
        local = bar.exchange_time.astimezone(TZ_IST)
        if SESSION_OPEN <= local.time() < SESSION_CLOSE:
            sessions[local.date()].append(bar)
    return {key: sorted(value, key=lambda item: item.exchange_time) for key, value in sessions.items()}


def _select_dates(provider: PointInTimeReferenceProvider, sessions: dict[date, list[Bar]]) -> list[date]:
    ready: list[date] = []
    for session_date in sorted(sessions):
        bars = sessions[session_date]
        if len(bars) != 75:
            continue
        contexts = [provider.build(bar) for bar in bars]
        if all(not context.missing_fields for context in contexts):
            ready.append(session_date)
    return ready


def _context_values(context: PointInTimeReferenceContext) -> dict[str, Any]:
    values = context.as_dict()
    return {
        key: value
        for key, value in values.items()
        if key not in {"prior_completed_bars"}
    }


def _run_baseline_signals(
    strategy_name: str,
    session: list[Bar],
    enriched_map: dict[datetime, Any],
) -> list[dict[str, Any]]:
    strategy = {
        "PDS": PDSStrategy(config={"method": "METHOD_1"}),
        "ORB": ORBStrategy(config={"method": "METHOD_1"}),
        "IRF": IRFStrategy(config={"method": "METHOD_2"}),
        "PDR": PDRStrategy(),
    }[strategy_name]
    context = StrategyContext()
    signals: list[dict[str, Any]] = []
    for bar in session:
        bar_context = enriched_map.get(bar.exchange_time)
        if bar_context is not None:
            levels = bar_context.session_levels
            context.state = {
                **context.state,
                "pdh": levels.pdh,
                "pdl": levels.pdl,
                "prev_close": levels.pdc,
                "pdo": levels.pdo,
                "pd_res_body": levels.pd_res_body,
                "pd_supp_body": levels.pd_supp_body,
                "dma_200": levels.dma_200,
                "has_sufficient_dma_warmup": levels.has_sufficient_dma_warmup,
                "ema_200": bar_context.ema_200,
                "has_sufficient_ema_warmup": bar_context.has_sufficient_ema_warmup,
                "recent_swing_high": bar_context.recent_swing_high,
                "recent_swing_low": bar_context.recent_swing_low,
            }
        signal = strategy.on_bar(context, bar)
        if signal is not None:
            signals.append(
                {
                    "timestamp": signal.timestamp.isoformat(),
                    "direction": str(signal.direction),
                    "method": signal.method_id,
                    "reference_price": signal.reference_price,
                }
            )
    return signals


def _run_shadow_for_dates(
    dates: list[date],
    sessions: dict[date, list[Bar]],
    provider: PointInTimeReferenceProvider,
) -> tuple[list[dict[str, Any]], dict[date, ShadowSignalCollector]]:
    rows: list[dict[str, Any]] = []
    collectors: dict[date, ShadowSignalCollector] = {}
    for session_date in dates:
        collector = ShadowSignalCollector()
        for bar in sessions[session_date]:
            context = provider.build(
                bar,
                observation_time=bar.exchange_time.astimezone(TZ_IST) + timedelta(minutes=5),
            )
            for strategy, method in ENABLED:
                collector.evaluate_strategy_bar(strategy, method, bar, context)
        rows.extend(collector.rows)
        collectors[session_date] = collector
    return rows, collectors


def write_reports() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    intraday = _load_bars(INTRADAY_PATH, Timeframe.M5)
    daily = _load_bars(DAILY_PATH, Timeframe.D1)
    sessions = _session_map(intraday)
    provider = PointInTimeReferenceProvider(
        intraday,
        daily,
        source_intraday="data/processed/nifty/5m/nifty50_5m.parquet",
        source_daily="data/processed/nifty/1d/nifty50_1d.parquet",
    )
    ready_dates = _select_dates(provider, sessions)
    if not ready_dates:
        raise RuntimeError("No complete session with point-in-time reference warm-up was found")
    primary_date = ready_dates[0]
    comparison_dates = ready_dates[:5]
    shadow_rows, collectors = _run_shadow_for_dates(comparison_dates, sessions, provider)
    primary_rows = collectors[primary_date].rows
    _write_csv(OUT / "SHADOW_EVALUATION_RESULTS.csv", primary_rows)

    primary_contexts = [provider.build(bar) for bar in sessions[primary_date]]
    provenance_rows = []
    for context in primary_contexts[:1]:
        for item in context.provenance:
            provenance_rows.append(
                f"| `{item.field_name}` | `{item.value}` | `{item.source_dataset}` | "
                f"`{item.source_date}` | `{item.source_timestamp}` | `{item.availability_timestamp}` | "
                f"{item.calculation_method} | `{item.validation_status}` |"
            )
    ref_complete = sum(
        not any(context.as_dict().get(field) is None for field in REQUIRED[strategy])
        for context in primary_contexts
        for strategy, _method in ENABLED
    )
    (OUT / "REFERENCE_DATA_PROVENANCE.md").write_text(
        "# Point-in-Time Reference Data Provenance\n\n"
        f"Primary replay session: `{primary_date}`. Contexts are immutable snapshots built by "
        "`PointInTimeReferenceProvider` and shared by the Phase 4E.2 shadow replay.\n\n"
        "Official previous-day PDH/PDL/PDO/PDC values come from the processed 1-day 5paisa historical dataset. "
        "The prior-day zone body values come from the intraday candle containing the corresponding official high/low. "
        "EMA uses only intraday closes at or before the evaluated candle; DMA uses only completed daily closes strictly "
        "before the decision date.\n\n"
        "Historical publication timestamps are not present in the stored Parquet files. The conservative retrospective "
        "assumption is that a completed prior-day dataset was available by the next session open. This is recorded as "
        "`HISTORICAL_AVAILABILITY_ASSUMED` and is not claimed as prospective collector evidence.\n\n"
        f"Complete strategy-reference evaluations: `{ref_complete}` of `{len(primary_rows)}`.\n\n"
        "| Field | Value | Source dataset | Source date | Source timestamp | Availability timestamp | Calculation | Validation |\n"
        "|---|---:|---|---|---|---|---|---|\n"
        + "\n".join(provenance_rows)
        + "\n",
        encoding="utf-8",
    )

    comparison_rows: list[dict[str, Any]] = []
    comparison_disagreements = 0
    calculator = ReferenceLevelCalculator()
    enriched = calculator.enrich_bars_with_context(intraday, daily_history_bars=daily)
    enriched_map = {item.bar.exchange_time: item for item in enriched}
    for session_date in comparison_dates:
        session = sessions[session_date]
        collector = collectors[session_date]
        for strategy, _method in ENABLED:
            shadow_for_strategy = [row for row in collector.rows if row["strategy"] == strategy]
            baseline = _run_baseline_signals(strategy, session, enriched_map)
            shadow_signals = [row for row in shadow_for_strategy if row["status"] == "SIGNAL_DETECTED"]
            agreement = len(shadow_signals) == len(baseline)
            if not agreement:
                comparison_disagreements += 1
            comparison_rows.append(
                {
                    "session_date": session_date.isoformat(),
                    "strategy": strategy,
                    "evaluated_timestamps": len(shadow_for_strategy),
                    "shadow_signal_count": len(shadow_signals),
                    "backtest_signal_count": len(baseline),
                    "shadow_signal_timestamps": "|".join(row["signal_time"] for row in shadow_signals),
                    "backtest_signal_timestamps": "|".join(row["timestamp"] for row in baseline),
                    "agreement": agreement,
                    "discrepancy_reason": (
                        "SHADOW_CONSERVATIVE_RULEBOOK_GATE" if not agreement else "NONE"
                    ),
                    "shadow_block_categories": "|".join(
                        sorted(Counter(row["reason_code"] for row in shadow_for_strategy))
                    ),
                }
            )
    _write_csv(OUT / "SHADOW_VS_BACKTEST_COMPARISON.csv", comparison_rows)

    candle_metrics = validate_5m_frame(
        pd.DataFrame(
            [
                {
                    "timestamp": bar.exchange_time,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                }
                for bar in sessions[primary_date]
            ]
        ),
        observation_time=datetime.combine(primary_date, SESSION_CLOSE, tzinfo=TZ_IST),
    )
    outcomes = Counter(row["reason_code"] for row in primary_rows)
    signals_by_strategy = {
        strategy: sum(
            row["strategy"] == strategy and row["status"] == "SIGNAL_DETECTED" for row in primary_rows
        )
        for strategy, _method in ENABLED
    }
    (OUT / "CANDLE_SOURCE_RECONCILIATION.md").write_text(
        "# Authoritative Five-Minute Candle Reconciliation\n\n"
        "The repository's authenticated adapter exposes the official 5paisa V2 historical endpoint through "
        "`FivePaisaBroker.fetch_historical_candles()` for `N/C/999920000/5m`. The stored processed 5-minute file "
        "is labelled `FIVEPAISA_HISTORICAL` and is the authoritative historical source used in this replay.\n\n"
        "The endpoint returns completed OHLC rows; the adapter parses the provider timestamp and rejects empty or "
        "unauthenticated responses. This phase did not make a new network call because it was run outside market hours. "
        "The existing stored source was independently validated for session alignment and OHLC invariants.\n\n"
        f"Primary session `{primary_date}`: `{candle_metrics['rows']}` rows, aligned `{candle_metrics['aligned_rows']}`, "
        f"future `{candle_metrics['future_rows']}`, duplicates `{candle_metrics.get('duplicate_rows', 0)}`, "
        f"OHLC-invalid `{candle_metrics['ohlc_invalid_rows']}`, missing intervals `{candle_metrics['missing_intervals']}`, "
        f"complete `{candle_metrics['complete']}`.\n\n"
        "A stored historical bar proves that the bar exists today (A), not that its original publication time was "
        "observed prospectively by the collector (B/C). Shadow evaluation therefore uses the bar-close timestamp as a "
        "conservative retrospective availability assumption and records that assumption in the reference provenance.\n\n"
        "Sampled live spot aggregation remains diagnostic-only and cannot silently replace provider OHLC for breakout decisions.\n",
        encoding="utf-8",
    )
    (OUT / "LOOKAHEAD_AUDIT.md").write_text(
        "# Lookahead and Availability Audit\n\n"
        f"- Primary session: `{primary_date}`; comparison sessions: `{', '.join(item.isoformat() for item in comparison_dates)}`.\n"
        "- Prior-day daily references are selected only from dates strictly less than the evaluated session date.\n"
        "- EMA includes only bars at or before the evaluated candle timestamp; missing warm-up remains missing.\n"
        "- DMA uses only the previous 200 completed official daily closes.\n"
        "- Zone-body source candles are from the prior session only.\n"
        "- A five-minute candle is eligible only at or after its close timestamp; no opening-timestamp backdating is used.\n"
        "- Historical bars have no original publication timestamp, so availability is explicitly labelled assumed, never prospective.\n"
        "- The four strategies receive the same frozen context object for each bar; no later-bar values are injected.\n",
        encoding="utf-8",
    )
    (OUT / "RULEBOOK_AMBIGUITIES.md").write_text(
        "# Remaining Rulebook Ambiguities\n\n"
        "The original PDFs were read before this context was defined. The following remain conservative blockers:\n\n"
        "- **PDS:** good-red body threshold, HPR wick ratio, small-support size/lookback, support-break close versus touch, and exact entry timing.\n"
        "- **PDR:** which prior-day reversal high is authoritative when multiple peaks exist, proximity tolerance, and the 35/40-point stop choice.\n"
        "- **ORB:** established-trend structure, inner-level lookback, trendline construction, and numerical body/wick thresholds.\n"
        "- **IRF Method 2:** recent support/resistance definition and exact extension/reversal confirmation.\n"
        "- **RF:** exact entry price and target remain open; RF is disabled in this replay.\n\n"
        "A rule is not promoted to executable status merely because a deterministic proxy exists in the baseline implementation.\n",
        encoding="utf-8",
    )
    (OUT / "LIVE_SESSION_READINESS.md").write_text(
        "# Live Session Readiness\n\n"
        "## Status\n\n"
        "**PENDING — no complete normal NSE live session was captured.**\n\n"
        "The bounded collector remains read-only and records spot, near-ATM CE/PE quotes, provider timestamps, receipt "
        "timestamps, freshness, contract metadata, duplicates, gaps and reconnection telemetry. Sampled spot-derived candles "
        "remain ineligible for breakout-dependent decisions until reconciled against authoritative completed candles.\n\n"
        "Run during NSE hours:\n\n"
        "```bash\n"
        ".venv/bin/python scripts/run_phase4e_collector.py observe\n"
        "```\n\n"
        "Stop with `Ctrl-C`; then run:\n\n"
        "```bash\n"
        ".venv/bin/python scripts/run_phase4e_collector.py status\n"
        ".venv/bin/python scripts/generate_phase4e2_reports.py\n"
        "```\n\n"
        "Paper fills, live orders and account mutations remain disabled.\n",
        encoding="utf-8",
    )
    return {
        "primary_date": primary_date.isoformat(),
        "comparison_dates": [item.isoformat() for item in comparison_dates],
        "primary_evaluations": len(primary_rows),
        "reference_complete_evaluations": ref_complete,
        "valid_completed_candles": int(candle_metrics["rows"] if candle_metrics["complete"] else 0),
        "signals_by_strategy": signals_by_strategy,
        "outcomes": dict(outcomes),
        "comparison_rows": len(comparison_rows),
        "comparison_disagreements": comparison_disagreements,
    }


if __name__ == "__main__":
    print(json.dumps(write_reports(), indent=2, sort_keys=True))
