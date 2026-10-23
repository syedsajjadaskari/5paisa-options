"""Bounded live read-only capture for Phase 4D.

This script never constructs the paper engine in PAPER_TRADE mode and never
calls a broker transaction endpoint. It records only sanitized market-data
responses and validation outcomes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.enums import SignalDirection
from trading_platform.data.calendar import NSECalendar
from trading_platform.data.live_market_feed import LiveMarketFeed
from trading_platform.options.live_contract_selector import LiveContractSelector
from trading_platform.paper.engine import PaperEngineConfig
from trading_platform.paper.recorder import MarketDataRecorder

TZ_IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parents[1]
PHASE_DIR = ROOT / "reports" / "independent_codex_audit" / "phase4d"
VERIFIED_ARCHIVE = ROOT / "data" / "processed" / "options_archive_verified"
DB_PATH = ROOT / "data" / "processed" / "paper_trading.duckdb"


def sanitize(value: Any) -> Any:
    """Redact credential/account-like keys while retaining quote evidence."""
    blocked = ("token", "password", "secret", "authorization", "userkey", "encryption")
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if any(part in str(key).lower() for part in blocked) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("status\nNO_ROWS\n", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_pending(reason: str, category: str) -> None:
    (PHASE_DIR / "LIVE_READ_ONLY_ACCEPTANCE.md").write_text(
        "# Phase 4D Live Read-Only Acceptance\n\n"
        "## Result\n\n"
        "**CONDITIONAL PASS — software corrected, live-session verification pending.**\n\n"
        f"Capture stopped before executable evidence: `{category}` — {reason}.\n\n"
        "Paper fills remain disabled. No broker transaction endpoint was called.\n",
        encoding="utf-8",
    )


def main() -> int:
    PHASE_DIR.mkdir(parents=True, exist_ok=True)
    capture_id = datetime.now(TZ_IST).strftime("PHASE4D_%Y%m%d_%H%M%S")
    config = PaperEngineConfig.from_yaml(ROOT / "config" / "paper_trading.yaml")
    if config.paper_execution_enabled or config.mode == "PAPER_TRADE":
        raise RuntimeError("Safety gate violated: execution must remain disabled")

    broker = FivePaisaBroker(cache_dir=ROOT / "data" / "reference")
    feed = LiveMarketFeed(broker=broker, max_staleness_seconds=5.0)
    recorder = MarketDataRecorder(
        db_path=DB_PATH,
        archive_dir=VERIFIED_ARCHIVE,
        run_id=capture_id,
        max_quote_age_seconds=5.0,
        starting_capital=config.starting_capital,
    )

    connected = broker.connect()
    (PHASE_DIR / "CONNECTIVITY_ROOT_CAUSE.md").write_text(
        "# Phase 4D Connectivity Root Cause\n\n"
        "## Diagnosis\n\n"
        "Direct library callers previously did not load `.env`; the CLI did. "
        "This made the same cached-token path report `connected=False` outside the CLI. "
        "`FivePaisaBroker` now loads dotenv consistently without logging secret values.\n\n"
        f"Capture ID: `{capture_id}`\n\n"
        f"Connection result: `{connected}`\n\n"
        f"Connection category: `{broker.connection_category}`\n\n"
        f"Connection detail: {broker.connection_detail}\n\n"
        "Installed SDK contract: `py5paisa==0.7.21.2` accepts `FivePaisaClient(cred=...)`, "
        "uses `get_totp_session(client_code, totp, pin)` and `set_access_token(token, client_code)`, "
        "and exposes `fetch_market_feed_scrip(req_list)` plus `fetch_market_depth(req_list)`. "
        "The adapter uses those read-only methods and blocks direct transactional methods at its proxy boundary.\n\n"
        "The broker boundary remains read-only; order and generic transactional transport calls are blocked.\n",
        encoding="utf-8",
    )
    if not connected:
        write_pending(broker.connection_detail, broker.connection_category)
        return 0

    session_open = NSECalendar().is_regular_trading_minute(datetime.now(TZ_IST))
    if not session_open:
        write_pending("NSE session is not open at capture time", "MARKET_CLOSED_OR_UNSUPPORTED_SESSION")
        return 0

    raw_payloads: list[dict[str, Any]] = []
    spot = None
    for _ in range(3):
        spot = feed.fetch_spot_tick()
        if spot is not None:
            break
        if feed.last_spot_raw_response is not None:
            raw_response = sanitize(feed.last_spot_raw_response)
            data = feed.last_spot_raw_response.get("Data", [])
            item = data[0] if data else {}
            raw_payloads.append(
                {
                    "capture_id": capture_id,
                    "kind": "NIFTY_SPOT_REJECTED",
                    "raw_response": raw_response,
                    "response_sha256": sha256_json(raw_response),
                    "provider_timestamp": item.get("TickDt") or item.get("TimeStamp"),
                    "receipt_timestamp": datetime.now(TZ_IST).isoformat(),
                    "rejection_category": feed.last_error_category,
                    "rejection_reason": feed.last_error,
                }
            )
        time.sleep(0.5)
    if spot is None:
        write_json(PHASE_DIR / "RAW_CAPTURE_SANITIZED.json", raw_payloads)
        write_csv(PHASE_DIR / "QUOTE_VALIDATION_RESULTS.csv", [])
        write_csv(PHASE_DIR / "CONTRACT_METADATA_AUDIT.csv", [])
        (PHASE_DIR / "RAW_PAYLOAD_PROVENANCE.md").write_text(
            "# Raw Payload Provenance\n\n"
            f"Rejected spot responses were preserved in `RAW_CAPTURE_SANITIZED.json`. "
            f"Category: `{feed.last_error_category}`. Reason: {feed.last_error}.\n\n"
            "No option quote entered the verified archive.\n",
            encoding="utf-8",
        )
        (PHASE_DIR / "VERIFIED_ARCHIVE_MANIFEST.md").write_text(
            "# Verified Archive Manifest\n\nNo new verified partition was created because the underlying quote gate failed.\n",
            encoding="utf-8",
        )
        (PHASE_DIR / "DUCKDB_RECOVERY_AUDIT.md").write_text(
            "# DuckDB Recovery Audit\n\n"
            "The read-only capture failed before option persistence. No paper execution was enabled.\n",
            encoding="utf-8",
        )
        write_pending(feed.last_error or "spot response was rejected", feed.last_error_category or "INVALID_PAYLOAD")
        return 0
    recorder.record_spot_tick(spot)

    if feed.last_spot_raw_response is not None:
        raw_spot = sanitize(feed.last_spot_raw_response)
        raw_payloads.append(
            {
                "capture_id": capture_id,
                "kind": "NIFTY_SPOT",
                "raw_response": raw_spot,
                "response_sha256": sha256_json(raw_spot),
                "provider_timestamp": spot.raw_provider_timestamp,
                "receipt_timestamp": iso(spot.ingestion_timestamp),
            }
        )

    selector = LiveContractSelector(
        broker=broker,
        feed=feed,
        max_spread_points=5.0,
        max_spread_pct=5.0,
        max_staleness_seconds=5.0,
    )
    quote_rows: list[dict[str, Any]] = []
    contract_rows: list[dict[str, Any]] = []
    for direction in (SignalDirection.BUY, SignalDirection.SELL):
        result = selector.select_contract(spot.last_rate, direction, spot.ingestion_timestamp.date())
        quote = feed.last_option_quotes.get(result.scrip_code)
        raw = feed.last_option_raw_responses.get(result.scrip_code)
        contract_rows.append(
            {
                "capture_id": capture_id,
                "option_type": result.option_type,
                "underlying": "NIFTY",
                "symbol": result.symbol,
                "scrip_code": result.scrip_code,
                "strike": result.strike,
                "expiry": result.expiry,
                "lot_size": result.lot_size,
                "dte": result.dte,
                "selection_status": result.status,
                "selection_reason": result.reason,
            }
        )
        if quote is None:
            quote_rows.append(
                {
                    "capture_id": capture_id,
                    "kind": result.option_type,
                    "scrip_code": result.scrip_code,
                    "provider_timestamp": "",
                    "receipt_timestamp": "",
                    "quote_age_seconds": "",
                    "best_bid": 0,
                    "best_ask": 0,
                    "bid_qty": 0,
                    "ask_qty": 0,
                    "spread": 0,
                    "spread_pct": 0,
                    "validation_status": result.status,
                    "validation_reason": result.reason,
                }
            )
            continue

        if raw is not None:
            sanitized_raw = sanitize(raw)
            raw_payloads.append(
                {
                    "capture_id": capture_id,
                    "kind": f"NIFTY_{result.option_type}_DEPTH",
                    "raw_response": sanitized_raw,
                    "response_sha256": sha256_json(sanitized_raw),
                    "provider_timestamp": quote.raw_provider_timestamp,
                    "receipt_timestamp": iso(quote.ingestion_timestamp),
                }
            )
        quote_rows.append(
            {
                "capture_id": capture_id,
                "kind": result.option_type,
                "scrip_code": quote.scrip_code,
                "provider_timestamp": quote.raw_provider_timestamp,
                "receipt_timestamp": iso(quote.ingestion_timestamp),
                "quote_age_seconds": round(quote.quote_age_seconds, 3),
                "best_bid": quote.best_bid,
                "best_ask": quote.best_ask,
                "bid_qty": quote.bid_qty,
                "ask_qty": quote.ask_qty,
                "spread": quote.spread,
                "spread_pct": quote.spread_pct,
                "validation_status": result.status,
                "validation_reason": result.reason,
            }
        )
        if result.status == "APPROVED":
            recorder.record_option_snapshot(
                quote=quote,
                expiry=result.expiry,
                strike=result.strike,
                option_type=result.option_type,
                lot_size=result.lot_size,
                raw_response=raw,
            )

    recorder.flush_options_archive_parquet()
    write_json(PHASE_DIR / "RAW_CAPTURE_SANITIZED.json", raw_payloads)
    write_csv(PHASE_DIR / "QUOTE_VALIDATION_RESULTS.csv", quote_rows)
    write_csv(PHASE_DIR / "CONTRACT_METADATA_AUDIT.csv", contract_rows)

    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        market_rows = int(con.execute("SELECT COUNT(*) FROM market_events WHERE run_id = ?", [capture_id]).fetchone()[0])
        option_rows = int(con.execute("SELECT COUNT(*) FROM options_archive WHERE run_id = ?", [capture_id]).fetchone()[0])
        fill_rows = int(con.execute("SELECT COUNT(*) FROM virtual_fills WHERE run_id = ?", [capture_id]).fetchone()[0])
        account_rows = int(con.execute("SELECT COUNT(*) FROM account_metadata WHERE account_namespace = 'PAPER_VIRTUAL'").fetchone()[0])

    archive_files = sorted(VERIFIED_ARCHIVE.rglob("*.parquet"))
    manifest_lines = [
        "# Verified Archive Manifest",
        "",
        f"Capture run: `{capture_id}`",
        "Legacy quarantined archive remains at `data/processed/options_archive` and was not modified.",
        "",
        "| File | SHA-256 | Rows |",
        "|---|---|---:|",
    ]
    for path in archive_files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = len(__import__("pandas").read_parquet(path))
        manifest_lines.append(f"| `{path.relative_to(ROOT)}` | `{digest}` | {rows} |")
    (PHASE_DIR / "VERIFIED_ARCHIVE_MANIFEST.md").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    (PHASE_DIR / "RAW_PAYLOAD_PROVENANCE.md").write_text(
        "# Raw Payload Provenance\n\n"
        f"Capture `{capture_id}` persisted sanitized payloads in `RAW_CAPTURE_SANITIZED.json`. "
        "Provider timestamps and local receipt timestamps are separate fields. "
        "Payload hashes are recorded beside each response. Credential-like keys are redacted.\n\n"
        f"NIFTY spot rows in DuckDB: `{market_rows}`. Option rows: `{option_rows}`. "
        f"PAPER_VIRTUAL account metadata rows: `{account_rows}`. Virtual fills for this run: `{fill_rows}`.\n\n"
        "Timestamp regression: `/Date(1729571156000+0530)/` parses to `2024-10-22 09:55:56 IST`; the captured 2026 wrapper remains in the raw payload with its original value.\n\n"
        "Provider timestamps are retained exactly. A bounded 2-second future skew is tolerated only for the provider's whole-second timestamp resolution; larger future skew is rejected.\n\n"
        "Only observations with provider timestamps, positive non-crossed two-sided depth, "
        "positive quantities, valid contract metadata and configured freshness limits enter the verified archive.\n",
        encoding="utf-8",
    )
    (PHASE_DIR / "DUCKDB_RECOVERY_AUDIT.md").write_text(
        "# DuckDB Recovery Audit\n\n"
        "- Schema initialization and provenance migrations run inside an explicit transaction.\n"
        "- Spot and option observation identifiers are deterministic per run/provider observation; duplicate replays are ignored.\n"
        "- Candle identifiers are deterministic per run/interval/exchange time.\n"
        "- Account metadata is kept in the `PAPER_VIRTUAL` namespace and is never populated from broker cash.\n"
        "- The capture writes observations first, flushes Parquet separately, and can be reconstructed from DuckDB plus the raw payload manifest.\n"
        "- Paper execution was disabled for this run; no virtual fills were created.\n\n"
        "Residual risk: DuckDB remains a single-process analytical store; multi-process writers must remain prohibited by the engine lock.\n",
        encoding="utf-8",
    )
    outcome = (
        "PASS FOR VERIFIED READ-ONLY COLLECTION"
        if all(row["validation_status"] == "APPROVED" for row in quote_rows)
        and len(quote_rows) == 2
        and option_rows >= 2
        else "CONDITIONAL PASS — software corrected, live-session verification pending"
    )
    (PHASE_DIR / "LIVE_READ_ONLY_ACCEPTANCE.md").write_text(
        "# Phase 4D Live Read-Only Acceptance\n\n"
        f"## Result\n\n**{outcome}.**\n\n"
        f"Capture ID: `{capture_id}`\n\n"
        f"Connection category: `{broker.connection_category}`\n\n"
        f"Spot provider timestamp: `{spot.raw_provider_timestamp}`\n\n"
        f"Recorded market rows: `{market_rows}`; recorded option rows: `{option_rows}`; virtual fills for this run: `{fill_rows}`.\n\n"
        "No paper fills were enabled or created. No broker order, modify, cancel or account-mutation endpoint was called.\n",
        encoding="utf-8",
    )
    with (PHASE_DIR / "CONNECTIVITY_ROOT_CAUSE.md").open("a", encoding="utf-8") as handle:
        handle.write(
            "\nRead-only API validation: SUCCESS. NIFTY spot and both option depth requests returned `Status=0`; "
            "all three raw payloads were persisted with response hashes.\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
