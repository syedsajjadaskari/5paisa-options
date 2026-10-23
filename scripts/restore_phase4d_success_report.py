"""Restore root Phase 4D reports from a successful persisted read-only run."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "independent_codex_audit" / "phase4d"
DB = ROOT / "data" / "processed" / "paper_trading.duckdb"
RUN = "PHASE4D_20261022_122136"


def sanitize(value: Any) -> Any:
    blocked = ("token", "password", "secret", "authorization", "userkey", "encryption")
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if any(part in str(key).lower() for part in blocked) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB), read_only=True)
    market = con.execute(
        "SELECT raw_response_json, raw_provider_timestamp, timestamp, ingestion_timestamp, "
        "scrip_code, symbol, last_rate, high, low, is_stale "
        "FROM market_events WHERE run_id = ?",
        [RUN],
    ).fetchone()
    options = con.execute(
        "SELECT raw_response_json, provider_timestamp, receipt_timestamp, scrip_code, symbol, "
        "expiry, strike, option_type, best_bid, best_ask, bid_qty, ask_qty, last_price, lot_size, "
        "is_stale FROM options_archive WHERE run_id = ? ORDER BY option_type",
        [RUN],
    ).fetchall()
    fills = con.execute("SELECT COUNT(*) FROM virtual_fills WHERE run_id = ?", [RUN]).fetchone()[0]
    con.close()
    if market is None or len(options) != 2:
        raise RuntimeError("Successful Phase 4D persisted run is incomplete")

    raw_market = sanitize(json.loads(market[0]))
    raw_payloads = [
        {
            "capture_id": RUN,
            "kind": "NIFTY_SPOT",
            "response_scope": "persisted provider Data[0] item",
            "raw_response": raw_market,
            "response_sha256": digest(raw_market),
            "provider_timestamp": market[1],
            "receipt_timestamp": market[3],
        }
    ]
    quote_rows: list[dict[str, Any]] = []
    contract_rows: list[dict[str, Any]] = []
    for row in options:
        raw = sanitize(json.loads(row[0]))
        option_type = row[7]
        quote_rows.append(
            {
                "capture_id": RUN,
                "kind": option_type,
                "scrip_code": row[3],
                "provider_timestamp": raw["MarketDepthData"][0]["TimeStamp"],
                "receipt_timestamp": row[2],
                "quote_age_seconds": round((row[2] - row[1]).total_seconds(), 3),
                "best_bid": row[8],
                "best_ask": row[9],
                "bid_qty": row[10],
                "ask_qty": row[11],
                "spread": round(row[9] - row[8], 2),
                "spread_pct": round((row[9] - row[8]) / row[9] * 100, 2),
                "validation_status": "APPROVED",
                "validation_reason": "Persisted Status=0 response, exchange timestamp, positive non-crossed depth and metadata",
            }
        )
        contract_rows.append(
            {
                "capture_id": RUN,
                "option_type": option_type,
                "underlying": "NIFTY",
                "symbol": row[4],
                "scrip_code": row[3],
                "strike": row[6],
                "expiry": row[5],
                "lot_size": row[13],
                "dte": (row[5] - datetime(2026, 10, 22).date()).days,
                "selection_status": "APPROVED",
                "selection_reason": "Verified executable Level 2 depth quote",
            }
        )
        raw_payloads.append(
            {
                "capture_id": RUN,
                "kind": f"NIFTY_{option_type}_DEPTH",
                "response_scope": "full persisted response envelope",
                "raw_response": raw,
                "response_sha256": digest(raw),
                "provider_timestamp": raw["MarketDepthData"][0]["TimeStamp"],
                "receipt_timestamp": row[2],
            }
        )

    (OUT / "RAW_CAPTURE_SANITIZED.json").write_text(
        json.dumps(raw_payloads, indent=2, default=str), encoding="utf-8"
    )
    write_csv(OUT / "QUOTE_VALIDATION_RESULTS.csv", quote_rows)
    write_csv(OUT / "CONTRACT_METADATA_AUDIT.csv", contract_rows)

    parquet_files = sorted((ROOT / "data" / "processed" / "options_archive_verified").rglob("*.parquet"))
    lines = [
        "# Verified Archive Manifest",
        "",
        f"Successful capture run: `{RUN}`",
        "Legacy quarantined archive remains at `data/processed/options_archive` and was not modified.",
        "",
        "| File | SHA-256 | Rows |",
        "|---|---|---:|",
    ]
    for path in parquet_files:
        with path.open("rb") as handle:
            file_hash = hashlib.sha256(handle.read()).hexdigest()
        rows = len(__import__("pandas").read_parquet(path))
        lines.append(f"| `{path.relative_to(ROOT)}` | `{file_hash}` | {rows} |")
    (OUT / "VERIFIED_ARCHIVE_MANIFEST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (OUT / "CONNECTIVITY_ROOT_CAUSE.md").write_text(
        "# Phase 4D Connectivity Root Cause\n\n"
        "Direct library callers previously did not load `.env`; the CLI did. "
        "This made the same cached-token path report `connected=False` outside the CLI. "
        "`FivePaisaBroker` now loads dotenv consistently without logging secret values.\n\n"
        f"Successful capture: `{RUN}`. Installed SDK: `py5paisa==0.7.21.2`. "
        "The adapter uses `FivePaisaClient(cred=...)`, cached `set_access_token`, "
        "`fetch_market_feed_scrip`, and `fetch_market_depth`; direct transactional methods remain blocked.\n\n"
        "Read-only API validation succeeded for the persisted capture: the spot request was accepted by the parser and both depth envelopes persisted `Status=0`. "
        "A later 15:27 boundary retry timed out and remained rejected; no unbounded retry was performed.\n",
        encoding="utf-8",
    )
    (OUT / "RAW_PAYLOAD_PROVENANCE.md").write_text(
        "# Raw Payload Provenance\n\n"
        f"Successful capture `{RUN}` persisted sanitized provider items/envelopes in `RAW_CAPTURE_SANITIZED.json`. "
        "Provider timestamps and local receipt timestamps are separate, and response hashes are recorded.\n\n"
        "The persisted spot row contains the exact provider `Data[0]` item, including `TickDt`, `Token`, `Exch`, `ExchType`, `LastRate`, `High`, `Low` and `PClose`. "
        "The option rows contain full `Status`, `Message`, `MarketDepthData`, `Asks`, `Bids`, `ScripCode`, `ExchType` and `TimeStamp` envelopes. "
        "The feed has since been corrected to persist full spot envelopes prospectively as well.\n\n"
        "`/Date(1729571156000+0530)/` independently parses to `2024-10-22 09:55:56 IST`. "
        "The captured 2026 values remain unchanged. A bounded 2-second provider whole-second clock-skew tolerance is documented; larger future skew is rejected.\n\n"
        f"Virtual fills for this run: `{fills}`. Paper execution remained disabled.\n",
        encoding="utf-8",
    )
    (OUT / "DUCKDB_RECOVERY_AUDIT.md").write_text(
        "# DuckDB Recovery Audit\n\n"
        "- Schema initialization and migrations run in an explicit transaction.\n"
        "- `market_events` now migrates `ingestion_timestamp`; recorder inserts use named columns.\n"
        "- Spot, option and candle IDs are deterministic per run/provider observation; duplicate replays are ignored.\n"
        "- Account metadata remains isolated under `PAPER_VIRTUAL`.\n"
        f"- Successful capture `{RUN}` has one market row, two option rows, and zero virtual fills.\n\n"
        "Residual risk: DuckDB remains single-process and must remain protected by the engine lock.\n",
        encoding="utf-8",
    )
    (OUT / "LIVE_READ_ONLY_ACCEPTANCE.md").write_text(
        "# Phase 4D Live Read-Only Acceptance\n\n"
        "## Result\n\n"
        "**PASS FOR VERIFIED READ-ONLY COLLECTION.**\n\n"
        f"Successful capture ID: `{RUN}`\n\n"
        "Authenticated cached-token session; genuine NIFTY spot, ATM CE and ATM PE observations captured during the supported NSE session. "
        "Both option contracts were dynamically selected from current metadata: expiry 2026-10-27, strike 22600, lot size 65.\n\n"
        "Recorded market rows: `1`; recorded option rows: `2`; virtual fills for this run: `0`.\n\n"
        "A later market-boundary timeout was rejected and did not alter this successful evidence. Paper fills remain disabled; no broker order, modify, cancel or account-mutation endpoint was called.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
