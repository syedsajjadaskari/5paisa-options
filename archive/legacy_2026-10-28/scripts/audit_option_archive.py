"""Read-only classification and provenance manifest for legacy option archives."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

SYMBOL_RE = re.compile(r"^NIFTY\s+\d{1,2}\s+[A-Z]{3}\s+\d{4}\s+(CE|PE)\s+([0-9]+(?:\.[0-9]+)?)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(row: pd.Series, seen_ids: set[str]) -> str:
    snapshot_id = str(row.get("snapshot_id", ""))
    if snapshot_id in seen_ids:
        return "DUPLICATE"
    seen_ids.add(snapshot_id)

    bid = float(row.get("best_bid", 0) or 0)
    ask = float(row.get("best_ask", 0) or 0)
    last = float(row.get("last_price", 0) or 0)
    if bid <= 0 or ask <= 0 or last <= 0 or bid > ask:
        return "INVALID_PRICE"

    provider = row.get("provider_timestamp")
    receipt = row.get("receipt_timestamp")
    if pd.isna(provider) or pd.isna(receipt) or provider == receipt:
        return "RECEIPT_TIMESTAMP_ONLY"

    symbol = str(row.get("symbol", ""))
    match = SYMBOL_RE.fullmatch(symbol)
    if not match or match.group(1) != str(row.get("option_type", "")):
        return "WRONG_CONTRACT"

    try:
        provider_dt = pd.Timestamp(provider)
        receipt_dt = pd.Timestamp(receipt)
        if provider_dt.tzinfo is None:
            provider_dt = provider_dt.tz_localize("Asia/Kolkata")
        if receipt_dt.tzinfo is None:
            receipt_dt = receipt_dt.tz_localize("Asia/Kolkata")
        age = (receipt_dt - provider_dt).total_seconds()
        if age < 0 or age > 5:
            return "VALID_FOR_RESEARCH_ONLY"
    except (TypeError, ValueError):
        return "RECEIPT_TIMESTAMP_ONLY"
    return "VALID_EXCHANGE_TIMESTAMPED"


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    source = repo / "data/processed/options_archive"
    out = repo / "reports/independent_codex_audit/remediation"
    out.mkdir(parents=True, exist_ok=True)

    seen_ids: set[str] = set()
    rows: list[dict[str, object]] = []
    total = Counter()
    for path in sorted(source.rglob("*.parquet")):
        df = pd.read_parquet(path)
        counts = Counter(classify(row, seen_ids) for _, row in df.iterrows())
        total.update(counts)
        rows.append(
            {
                "partition": str(path.relative_to(repo)),
                "sha256": sha256(path),
                "rows": len(df),
                "classification": ";".join(
                    f"{category}:{counts[category]}" for category in sorted(counts)
                ),
                "quarantined": "TRUE" if any(k != "VALID_EXCHANGE_TIMESTAMPED" for k in counts) else "FALSE",
            }
        )

    csv_path = out / "OPTIONS_ARCHIVE_CLASSIFICATION.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["partition"])
        writer.writeheader()
        writer.writerows(rows)
        writer.writerow(
            {
                "partition": "__SUMMARY__",
                "sha256": "",
                "rows": sum(int(r["rows"]) for r in rows),
                "classification": ";".join(
                    f"{category}:{total.get(category, 0)}"
                    for category in (
                        "VALID_EXCHANGE_TIMESTAMPED",
                        "VALID_FOR_RESEARCH_ONLY",
                        "RECEIPT_TIMESTAMP_ONLY",
                        "INVALID_PRICE",
                        "WRONG_CONTRACT",
                        "DUPLICATE",
                    )
                ),
                "quarantined": "TRUE",
            }
        )

    db_path = repo / "data/processed/paper_trading.duckdb"
    db_summary: dict[str, object] = {"path": str(db_path), "rows": None, "categories": "not classified"}
    if db_path.exists():
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            db_summary["rows"] = int(con.execute("SELECT COUNT(*) FROM options_archive").fetchone()[0])
            db_summary["provider_equals_receipt"] = int(
                con.execute(
                    "SELECT COUNT(*) FROM options_archive WHERE provider_timestamp = receipt_timestamp"
                ).fetchone()[0]
            )
            db_summary["nonpositive_price_rows"] = int(
                con.execute(
                    """SELECT COUNT(*) FROM options_archive
                       WHERE best_bid <= 0 OR best_ask <= 0 OR last_price <= 0 OR best_bid > best_ask"""
                ).fetchone()[0]
            )
        finally:
            con.close()

    manifest = {
        "generated_at_ist": datetime.now().astimezone().isoformat(),
        "source": str(source),
        "source_policy": "READ_ONLY; originals preserved; all legacy partitions with no authoritative exchange timestamp are quarantined",
        "file_count": len(rows),
        "row_count": sum(int(r["rows"]) for r in rows),
        "row_classification": dict(total),
        "duckdb": db_summary,
        "verified_collection_path": "data/processed/options_archive_verified",
        "paper_fill_policy": "Only VALID_EXCHANGE_TIMESTAMPED rows with positive two-sided quotes and verified contract metadata may be used",
    }
    (out / "OPTIONS_ARCHIVE_PROVENANCE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
