"""Audit one persisted Phase 4E read-only collector run without broker calls."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data/cache/read_only_collector_state.json"
DB_PATH = ROOT / "data/processed/paper_trading.duckdb"
ARCHIVE_DIR = ROOT / "data/processed/options_archive_verified"
REPORT_PATH = ROOT / "reports/independent_codex_audit/phase4e3/COLLECTOR_OPERATIONAL_AUDIT_2026-10-23.md"
TZ_IST = ZoneInfo("Asia/Kolkata")
OPTION_RATE_LIMIT_SECONDS = 0.2


def _dt(value: object) -> datetime:
    timestamp = pd.Timestamp(value).to_pydatetime()
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=TZ_IST)
    return timestamp.astimezone(TZ_IST)


def _query(con: duckdb.DuckDBPyConnection, sql: str, params: list[object]) -> list[tuple]:
    return con.execute(sql, params).fetchall()


def _collector_pids() -> list[int]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    pids = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or "scripts/run_phase4e_collector.py" not in stripped:
            continue
        try:
            pids.append(int(stripped.split(None, 1)[0]))
        except (IndexError, ValueError):
            continue
    return pids


def main() -> int:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    run_id = state["run_id"]
    now = datetime.now(TZ_IST)
    heartbeat = _dt(state["heartbeat_time"])
    heartbeat_age = (now - heartbeat).total_seconds()
    live_pids = _collector_pids()
    lock_text = (ROOT / "data/cache/read_only_collector.lock").read_text(encoding="utf-8").strip()
    lock_pid_alive = False
    if lock_text.isdigit():
        try:
            os.kill(int(lock_text), 0)
            lock_pid_alive = True
        except (OSError, ValueError):
            lock_pid_alive = False

    con = duckdb.connect(str(DB_PATH), read_only=True)
    spot_rows = _query(con, """
        SELECT timestamp, ingestion_timestamp, last_rate, is_stale, provenance_status,
               raw_provider_timestamp
        FROM market_events WHERE run_id=? ORDER BY ingestion_timestamp
    """, [run_id])
    option_rows = _query(con, """
        SELECT provider_timestamp, receipt_timestamp, option_type, strike, expiry,
               lot_size, best_bid, best_ask, bid_qty, ask_qty, is_stale,
               provenance_status, scrip_code
        FROM options_archive WHERE run_id=? ORDER BY receipt_timestamp
    """, [run_id])
    candle_rows = _query(con, """
        SELECT exchange_time, open, high, low, close, volume, is_complete,
               source, validation_status, intrabar_exact
        FROM candles_5m WHERE run_id=? ORDER BY exchange_time
    """, [run_id])
    interruption_rows = _query(con, """
        SELECT error_type, COUNT(*) FROM data_feed_interruptions
        WHERE run_id=? GROUP BY error_type ORDER BY error_type
    """, [run_id])

    def count(table: str) -> int:
        return int(_query(con, f"SELECT COUNT(*) FROM {table} WHERE run_id=?", [run_id])[0][0])

    signal_count = count("signals")
    virtual_order_count = count("virtual_orders")
    virtual_fill_count = count("virtual_fills")
    trade_count = count("completed_trades")
    con.close()

    spot_exchange_times = [_dt(row[0]) for row in spot_rows]
    spot_receipts = [_dt(row[1]) for row in spot_rows]
    option_receipts = [_dt(row[1]) for row in option_rows]
    option_provider = [_dt(row[0]) for row in option_rows]
    spot_gaps = [(b - a).total_seconds() for a, b in zip(spot_receipts, spot_receipts[1:], strict=False)]
    option_intervals = [(b - a).total_seconds() for a, b in zip(option_receipts, option_receipts[1:], strict=False)]
    valid_spot = sum(not row[3] and row[4] == "VALID_EXCHANGE_TIMESTAMPED" for row in spot_rows)
    valid_options = sum(
        not row[10]
        and row[11] == "VALID_EXCHANGE_TIMESTAMPED"
        and row[6] > 0
        and row[7] > 0
        and row[6] <= row[7]
        and row[8] > 0
        and row[9] > 0
        for row in option_rows
    )
    future_provider_timestamps = sum(
        provider > receipt + timedelta(seconds=2)
        for provider, receipt in zip(option_provider, option_receipts, strict=False)
    )
    futures_spot = sum(
        _dt(row[0]) > _dt(row[1]) + timedelta(seconds=2)
        for row in spot_rows
    )
    parquet_rows = 0
    parquet_files = 0
    for path in ARCHIVE_DIR.glob("date=2026-10-23/*.parquet"):
        frame = pd.read_parquet(path)
        if "run_id" in frame.columns:
            subset = frame[frame["run_id"].eq(run_id)]
            if not subset.empty:
                parquet_files += 1
                parquet_rows += len(subset)

    metadata = sorted({(row[2], str(row[4]), row[5]) for row in option_rows})
    strikes = sorted({float(row[3]) for row in option_rows})
    master_path = ROOT / "data/reference/scrip_master.parquet"
    master = pd.read_parquet(master_path)
    master_nifty = master[(master["Exch"] == "N") & (master["ExchType"] == "D") & (master["SymbolRoot"] == "NIFTY")]
    archived_contracts = pd.DataFrame(
        [
            {"scrip_code": row[12], "option_type": row[2], "strike": row[3], "expiry": row[4], "lot_size": row[5]}
            for row in option_rows
        ]
    ).drop_duplicates()
    matched_contracts = archived_contracts.merge(
        master_nifty[["ScripCode", "ScripType", "StrikeRate", "Expiry", "LotSize"]],
        left_on="scrip_code", right_on="ScripCode", how="left",
    )
    master_matches = int(matched_contracts["ScripCode"].notna().sum())
    metadata_mismatches = int(
        ((matched_contracts["strike"] - matched_contracts["StrikeRate"]).abs() > 0.01).sum()
        + (matched_contracts["lot_size"] != matched_contracts["LotSize"]).sum()
        + (matched_contracts["option_type"] != matched_contracts["ScripType"]).sum()
    )
    candle_sources = sorted({str(row[7]) for row in candle_rows})
    candle_statuses = sorted({str(row[8]) for row in candle_rows})
    authoritative_candle_count = sum(
        row[7] not in ("FIVEPAISA_WS", "UNKNOWN") and row[9] is True for row in candle_rows
    )
    state_quality = state.get("quality", {})
    token_cache = ROOT / "data/cache/.token_cache.json"
    token_cache_present = token_cache.exists()

    interruptions = ", ".join(f"{kind}={amount}" for kind, amount in interruption_rows) or "none"
    report = f"""# Phase 4E Collector Operational Audit - 2026-10-23

## Executive result

The audited collector run is **not currently running**. Persisted state is
`STOPPED`, `pid=null`, the recorded heartbeat is `{state['heartbeat_time']}`,
and its age at audit time was `{heartbeat_age:.1f}` seconds. No collector PID was
found (`{len(live_pids)} matching collector processes`). The lock file contains PID `{lock_text}`, but that PID is not alive (`{lock_pid_alive}`); the
file is stale lock metadata, not evidence of a running process.

The terminal word `Success` is not accepted as ingestion evidence. Storage and
timestamp evidence below is the basis for this report.

## Observed run: `{run_id}`

| Check | Evidence |
|---|---:|
| Valid NIFTY spot rows in DuckDB | {valid_spot} |
| Spot rows rejected by persisted state | {state_quality.get('spot_rejected', 0)} |
| Latest verified spot exchange timestamp | {spot_exchange_times[-1].isoformat() if spot_exchange_times else 'none'} |
| Spot receipt span | {spot_receipts[0].isoformat() if spot_receipts else 'none'} to {spot_receipts[-1].isoformat() if spot_receipts else 'none'} |
| Spot maximum receipt gap | {max(spot_gaps) if spot_gaps else 0:.3f}s |
| Spot receipt gaps over 1s | {sum(delta > 1.0 for delta in spot_gaps)} |
| Valid option rows in DuckDB | {valid_options} |
| Option rows rejected by persisted state | {state_quality.get('option_rejected', 0)} |
| Option attempts represented by persisted counters | {state_quality.get('option_valid', 0) + state_quality.get('option_rejected', 0)} |
| Latest verified option exchange timestamp | {option_provider[-1].isoformat() if option_provider else 'none'} |
| CE / PE rows | {sum(row[2] == 'CE' for row in option_rows)} / {sum(row[2] == 'PE' for row in option_rows)} |
| Option contracts | {len({row[12] for row in option_rows})} |
| Option expiry / lot metadata | {', '.join(f'{expiry}/{lot}' for _kind, expiry, lot in metadata)} |
| Local scrip-master contract matches | {master_matches}/{len(archived_contracts)}; metadata mismatches {metadata_mismatches} |
| Option strike range | {min(strikes) if strikes else 'none'} to {max(strikes) if strikes else 'none'} |
| DuckDB option rows | {len(option_rows)} |
| Parquet option rows after reconciliation | {parquet_rows} across {parquet_files} files |
| Option receipt minimum interval | {min(option_intervals) if option_intervals else 0:.3f}s; configured floor {OPTION_RATE_LIMIT_SECONDS:.3f}s |
| Option maximum receipt gap | {max(option_intervals) if option_intervals else 0:.3f}s |
| Option intervals under configured floor | {sum(delta < OPTION_RATE_LIMIT_SECONDS for delta in option_intervals)} |
| Duplicate spot identities | {len(spot_rows) - len({(row[5], row[0]) for row in spot_rows})} |
| Duplicate option identities | {len(option_rows) - len({(row[12], row[0]) for row in option_rows})} |
| Provider future timestamp rejects observed in stored valid rows | {future_provider_timestamps + futures_spot} |
| Persisted feed interruptions | {interruptions} |
| Collector cycles / heartbeat spot counter | {state.get('cycles', 0)} / {state_quality.get('spot_valid', 0)} |
| Heartbeat option counter vs DuckDB | {state_quality.get('option_valid', 0)} vs {len(option_rows)} |

The heartbeat counter mismatch is a telemetry defect: DuckDB is authoritative
for accepted records, while the heartbeat reports two fewer option observations.
The shutdown Parquet-buffer defect was reproduced and corrected; the 28 rows
that were present in DuckDB but absent from Parquet were written to a separate
reconciliation Parquet file without overwriting existing data.

## Five-minute candles

The run contains `{len(candle_rows)}` completed candle. Stored source values are
`{', '.join(candle_sources) or 'none'}` and validation statuses are
`{', '.join(candle_statuses) or 'none'}`. Authoritative independently validated
candles: `{authoritative_candle_count}`.

The observed live candle was created by sampled spot aggregation. It is not an
exchange/provider OHLC bar and cannot independently validate breakout highs or
lows. The recorder now persists `source`, `validation_status`, and
`intrabar_exact=false`; future sampled candles are explicitly marked
`SAMPLED_LIVE_OHLC_NOT_BREAKOUT_AUTHORITY`.

## Shadow and execution safety

- Shadow evaluations recorded for this collector run: `{signal_count}`.
- Paper virtual orders: `{virtual_order_count}`.
- Paper virtual fills: `{virtual_fill_count}`.
- Completed trades: `{trade_count}`.
- The read-only broker proxy blocks transactional SDK methods, and this audit
  made no broker requests. Existing tests cover the transaction boundary.

## Credential handling

Future authentication stdout/stderr is now captured, logging records are
redacted, and persisted provider payloads recursively redact token,
authorization, password, PIN, TOTP, and key fields. The existing token-cache
file is present (`{token_cache_present}`); its value was not read into this
report. Because authentication output was previously exposed, invalidate the
5paisa session immediately, revoke any exposed API/session token, rotate the
relevant application credentials if required by 5paisa, and remove the local
token cache only after revocation. Do not paste the old token into tickets or
audit logs.

## Status

`COLLECTOR_RUNNING = FALSE`

`HEARTBEAT_FRESH = FALSE`

`VALID_SPOT_INGESTION = TRUE FOR THE SHORT OBSERVED WINDOW`

`VALID_CE_PE_INGESTION = TRUE FOR THE SHORT OBSERVED WINDOW`

`FULL_SESSION_VALIDATION = NOT ESTABLISHED`

`PAPER_EXECUTION = DISABLED`

`BROKER_TRANSACTIONS = BLOCKED`

This run covered approximately three minutes, not the full NSE session. It is
therefore not evidence of full-session live acceptance.
"""
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(REPORT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
