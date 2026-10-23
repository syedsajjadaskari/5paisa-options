"""Continuous Market Data Recorder and Prospective Options Archive using DuckDB and Parquet."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from trading_platform.brokers.fivepaisa import FivePaisaBroker
from trading_platform.core.models import Bar
from trading_platform.data.live_market_feed import (
    MAX_PROVIDER_FUTURE_SKEW_SECONDS,
    Level2Quote,
    LiveMarketFeed,
    SpotTick,
)

logger = logging.getLogger(__name__)

TZ_IST = ZoneInfo("Asia/Kolkata")
TZ_UTC = ZoneInfo("UTC")


class MarketDataRecorder:
    """Continuously records underlying spot, 1m/5m candles, and prospective option chain depth."""

    def __init__(
        self,
        db_path: str | Path = "data/processed/paper_trading.duckdb",
        archive_dir: str | Path = "data/processed/options_archive_verified",
        run_id: str = "RECORDER_RUN",
        max_quote_age_seconds: float = 5.0,
        starting_capital: float = 250000.0,
        option_request_interval_seconds: float = 0.2,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.archive_dir = Path(archive_dir)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.max_quote_age_seconds = float(max_quote_age_seconds)
        self.starting_capital = float(starting_capital)
        self.option_request_interval_seconds = max(0.0, float(option_request_interval_seconds))
        self._last_option_request_monotonic: float | None = None
        self.last_archive_stats: dict[str, int] = {
            "considered": 0,
            "valid": 0,
            "rejected": 0,
            "deduplicated": 0,
        }
        self._init_duckdb()

        # In-memory buffer for high-frequency option snapshots before flushing to Parquet
        self._options_buffer: list[dict[str, Any]] = []

    def _stable_id(self, prefix: str, *parts: object) -> str:
        """Create an idempotent key for one provider observation."""
        material = "|".join(str(part) for part in parts).encode("utf-8")
        return f"{prefix}_{hashlib.sha256(material).hexdigest()[:24]}"

    def _init_duckdb(self) -> None:
        """Initialize DuckDB analytical schema."""
        con = duckdb.connect(str(self.db_path))
        con.execute("BEGIN TRANSACTION")
        con.execute("""
            CREATE TABLE IF NOT EXISTS account_metadata (
                account_namespace VARCHAR PRIMARY KEY,
                account_type VARCHAR,
                starting_capital DOUBLE,
                funding_source VARCHAR,
                actual_broker_cash_imported BOOLEAN,
                created_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS market_events (
                event_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                ingestion_timestamp TIMESTAMP,
                scrip_code BIGINT,
                symbol VARCHAR,
                last_rate DOUBLE,
                high DOUBLE,
                low DOUBLE,
                is_stale BOOLEAN,
                raw_provider_timestamp VARCHAR,
                provenance_status VARCHAR,
                validation_reason VARCHAR,
                raw_response_json VARCHAR
            );
            CREATE TABLE IF NOT EXISTS candles_1m (
                bar_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                exchange_time TIMESTAMP,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                is_complete BOOLEAN
            );
            CREATE TABLE IF NOT EXISTS candles_5m (
                bar_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                exchange_time TIMESTAMP,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                is_complete BOOLEAN
            );
            CREATE TABLE IF NOT EXISTS options_archive (
                snapshot_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                provider_timestamp TIMESTAMP,
                receipt_timestamp TIMESTAMP,
                scrip_code BIGINT,
                symbol VARCHAR,
                expiry DATE,
                strike DOUBLE,
                option_type VARCHAR,
                best_bid DOUBLE,
                best_ask DOUBLE,
                bid_qty INTEGER,
                ask_qty INTEGER,
                last_price DOUBLE,
                lot_size INTEGER,
                is_stale BOOLEAN,
                provenance_status VARCHAR,
                validation_reason VARCHAR,
                raw_response_json VARCHAR
            );
            CREATE TABLE IF NOT EXISTS signals (
                signal_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                strategy_name VARCHAR,
                method_id VARCHAR,
                direction VARCHAR,
                reference_price DOUBLE,
                stop_loss DOUBLE,
                target DOUBLE,
                reason VARCHAR,
                is_executed BOOLEAN
            );
            CREATE TABLE IF NOT EXISTS contract_selections (
                selection_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                signal_id VARCHAR,
                status VARCHAR,
                option_type VARCHAR,
                strike DOUBLE,
                expiry DATE,
                dte INTEGER,
                scrip_code BIGINT,
                symbol VARCHAR,
                best_bid DOUBLE,
                best_ask DOUBLE,
                spread DOUBLE,
                reason VARCHAR
            );
            CREATE TABLE IF NOT EXISTS virtual_orders (
                order_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                symbol VARCHAR,
                side VARCHAR,
                quantity INTEGER,
                requested_price DOUBLE,
                fill_price DOUBLE,
                status VARCHAR
            );
            CREATE TABLE IF NOT EXISTS virtual_fills (
                fill_id VARCHAR PRIMARY KEY,
                order_id VARCHAR,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                symbol VARCHAR,
                side VARCHAR,
                quantity INTEGER,
                price DOUBLE,
                slippage DOUBLE,
                brokerage DOUBLE,
                statutory_charges DOUBLE
            );
            CREATE TABLE IF NOT EXISTS completed_trades (
                trade_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                strategy_name VARCHAR,
                method_id VARCHAR,
                symbol VARCHAR,
                quantity INTEGER,
                entry_time TIMESTAMP,
                entry_price DOUBLE,
                exit_time TIMESTAMP,
                exit_price DOUBLE,
                entry_spot DOUBLE,
                exit_spot DOUBLE,
                underlying_points_pnl DOUBLE,
                option_gross_pnl DOUBLE,
                option_net_pnl DOUBLE,
                total_costs DOUBLE,
                exit_reason VARCHAR
            );
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                available_cash DOUBLE,
                invested_capital DOUBLE,
                unrealized_pnl DOUBLE,
                realized_pnl DOUBLE,
                total_equity DOUBLE,
                daily_drawdown_pct DOUBLE
            );
            CREATE TABLE IF NOT EXISTS data_feed_interruptions (
                event_id VARCHAR PRIMARY KEY,
                run_id VARCHAR,
                timestamp TIMESTAMP,
                error_type VARCHAR,
                details VARCHAR
            );
        """)
        con.execute(
            """INSERT OR IGNORE INTO account_metadata VALUES (?, ?, ?, ?, ?, ?)""",
            [
                "PAPER_VIRTUAL",
                "SIMULATED_OPTIONS_ACCOUNT",
                self.starting_capital,
                "CONFIGURED_SIMULATION_CAPITAL",
                False,
                datetime.now(TZ_IST),
            ],
        )
        # Explicit schema evolution keeps the provenance columns present in
        # pre-existing DuckDB files without rewriting historical raw records.
        for table, column, definition in [
            ("market_events", "raw_provider_timestamp", "VARCHAR"),
            ("market_events", "ingestion_timestamp", "TIMESTAMP"),
            ("market_events", "provenance_status", "VARCHAR"),
            ("market_events", "validation_reason", "VARCHAR"),
            ("market_events", "raw_response_json", "VARCHAR"),
            ("options_archive", "provenance_status", "VARCHAR"),
            ("options_archive", "validation_reason", "VARCHAR"),
            ("options_archive", "raw_response_json", "VARCHAR"),
            ("candles_1m", "is_complete", "BOOLEAN DEFAULT TRUE"),
            ("candles_5m", "is_complete", "BOOLEAN DEFAULT TRUE"),
        ]:
            con.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"
            )
        con.execute("COMMIT")
        con.close()

    def record_feed_interruption(self, error_type: str, details: str = "") -> None:
        """Record a data feed or broker connectivity disruption."""
        try:
            con = duckdb.connect(str(self.db_path))
            event_id = f"INT_{uuid.uuid4().hex[:8]}"
            con.execute(
                """
                INSERT INTO data_feed_interruptions VALUES (?, ?, ?, ?, ?)
            """,
                [
                    event_id,
                    self.run_id,
                    datetime.now(TZ_IST),
                    error_type,
                    details,
                ],
            )
            con.close()
        except Exception as e:
            logger.debug(f"Failed to record feed interruption: {e}")

    def record_spot_tick(self, tick: SpotTick) -> None:
        """Record raw underlying spot observation."""
        try:
            con = duckdb.connect(str(self.db_path))
            event_id = self._stable_id(
                "EVT",
                self.run_id,
                tick.scrip_code,
                tick.raw_provider_timestamp or tick.provider_timestamp.isoformat(),
            )
            con.execute("BEGIN TRANSACTION")
            con.execute(
                """
                INSERT OR IGNORE INTO market_events (
                    event_id, run_id, timestamp, ingestion_timestamp, scrip_code,
                    symbol, last_rate, high, low, is_stale, raw_provider_timestamp,
                    provenance_status, validation_reason, raw_response_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    event_id,
                    self.run_id,
                    tick.provider_timestamp,
                    tick.ingestion_timestamp,
                    tick.scrip_code,
                    tick.symbol,
                    tick.last_rate,
                    tick.high,
                    tick.low,
                    tick.is_stale,
                    tick.raw_provider_timestamp,
                    "VALID_EXCHANGE_TIMESTAMPED" if tick.provenance_verified else "UNVERIFIABLE",
                    None if tick.provenance_verified else "Provider provenance was not verified",
                    tick.raw_response_json,
                ],
            )
            con.execute("COMMIT")
            con.close()
        except Exception as e:
            try:
                con.execute("ROLLBACK")
                con.close()
            except Exception:
                pass
            logger.debug(f"Failed to record spot tick: {e}")

    def record_candle(self, bar: Bar, interval: str = "5m", is_complete: bool = True) -> None:
        """Record completed 1m or 5m NIFTY candle."""
        tbl = "candles_1m" if interval == "1m" else "candles_5m"
        try:
            con = duckdb.connect(str(self.db_path))
            bar_id = self._stable_id("BAR", self.run_id, interval, bar.exchange_time.isoformat())
            con.execute("BEGIN TRANSACTION")
            con.execute(
                f"""
                INSERT OR IGNORE INTO {tbl} (
                    bar_id, run_id, exchange_time, open, high, low, close, volume, is_complete
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    bar_id,
                    self.run_id,
                    bar.exchange_time,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    is_complete,
                ],
            )
            con.execute("COMMIT")
            con.close()
        except Exception as e:
            try:
                con.execute("ROLLBACK")
                con.close()
            except Exception:
                pass
            logger.debug(f"Failed to record candle: {e}")

    def record_option_snapshot(
        self,
        quote: Level2Quote,
        expiry: date,
        strike: float,
        option_type: str,
        lot_size: int,
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        """Record genuine Level 2 options quote snapshot into prospective archive."""
        if not quote.provenance_verified:
            logger.warning("Skipping option snapshot without verified provenance: %s", quote.symbol)
            self.last_archive_stats["rejected"] += 1
            return
        quote_age = (quote.ingestion_timestamp - quote.provider_timestamp).total_seconds()
        if (
            quote_age < -MAX_PROVIDER_FUTURE_SKEW_SECONDS
            or quote.is_stale
            or quote_age > self.max_quote_age_seconds
            or quote.best_bid <= 0
            or quote.best_ask <= 0
            or quote.best_bid > quote.best_ask
            or quote.bid_qty <= 0
            or quote.ask_qty <= 0
        ):
            logger.warning("Skipping non-executable option snapshot: %s", quote.symbol)
            self.last_archive_stats["rejected"] += 1
            return
        snapshot_id = self._stable_id(
            "OPT",
            self.run_id,
            quote.scrip_code,
            quote.raw_provider_timestamp or quote.provider_timestamp.isoformat(),
            expiry,
            strike,
            option_type,
        )
        rec = {
            "snapshot_id": snapshot_id,
            "run_id": self.run_id,
            "provider_timestamp": quote.provider_timestamp,
            "receipt_timestamp": quote.ingestion_timestamp,
            "scrip_code": quote.scrip_code,
            "symbol": quote.symbol,
            "expiry": expiry,
            "strike": strike,
            "option_type": option_type,
            "best_bid": quote.best_bid,
            "best_ask": quote.best_ask,
            "bid_qty": quote.bid_qty,
            "ask_qty": quote.ask_qty,
            "last_price": quote.last_price,
            "lot_size": lot_size,
            "is_stale": quote.is_stale,
            "provenance_status": "VALID_EXCHANGE_TIMESTAMPED",
            "validation_reason": "provider timestamp, positive two-sided quote, and contract metadata verified",
            "raw_response_json": json.dumps(raw_response, sort_keys=True, default=str)
            if raw_response is not None
            else quote.raw_response_json,
        }

        try:
            con = duckdb.connect(str(self.db_path))
            con.execute("BEGIN TRANSACTION")
            existing = con.execute(
                "SELECT 1 FROM options_archive WHERE snapshot_id = ?",
                [snapshot_id],
            ).fetchone()
            if existing:
                con.execute("ROLLBACK")
                self.last_archive_stats["deduplicated"] += 1
                return
            con.execute(
                """
                INSERT INTO options_archive VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                list(rec.values()),
            )
            con.execute("COMMIT")
            con.close()
            self._options_buffer.append(rec)
            self.last_archive_stats["valid"] += 1
        except Exception as e:
            self.last_archive_stats["rejected"] += 1
            try:
                con.execute("ROLLBACK")
                con.close()
            except Exception:
                pass
            logger.debug(f"Failed to record option snapshot in DuckDB: {e}")

        # Flush buffer to daily Parquet every 50 records
        if len(self._options_buffer) >= 50:
            self.flush_options_archive_parquet()

    def flush_options_archive_parquet(self) -> Path | None:
        """Flush in-memory option snapshots to date-partitioned Parquet."""
        if not self._options_buffer:
            return None

        df = pd.DataFrame(self._options_buffer)
        today_str = datetime.now(TZ_IST).strftime("%Y-%m-%d")
        partition_dir = self.archive_dir / f"date={today_str}"
        partition_dir.mkdir(parents=True, exist_ok=True)

        parquet_file = partition_dir / f"options_archive_{self.run_id}_{uuid.uuid4().hex[:6]}.parquet"
        df.to_parquet(parquet_file, compression="zstd")
        self._options_buffer.clear()
        return parquet_file

    def archive_atm_option_chain(
        self,
        broker: FivePaisaBroker,
        spot_price: float,
        strike_radius: int = 2,  # ATM +/- 2 strikes (5 strikes total)
    ) -> int:
        """Capture prospective option chain snapshots for ATM and neighboring strikes."""
        self.last_archive_stats = {
            "considered": 0,
            "valid": 0,
            "rejected": 0,
            "deduplicated": 0,
        }
        if not broker.is_connected:
            return 0

        atm_strike = round(spot_price / 50.0) * 50.0
        target_strikes = [atm_strike + (i * 50.0) for i in range(-strike_radius, strike_radius + 1)]

        df = broker.get_scrip_master()
        today = datetime.now(TZ_IST).date()
        today_str = today.isoformat()

        mask = (
            (df["Exch"] == "N")
            & (df["ExchType"] == "D")
            & (df["SymbolRoot"] == "NIFTY")
            & (df["Expiry"].str.slice(0, 10) >= today_str)
        )
        nifty_df = df[mask].copy()
        if nifty_df.empty:
            return 0

        # Find nearest weekly expiry
        expiries = sorted(nifty_df["Expiry"].str.slice(0, 10).unique())
        nearest_expiry_str = expiries[0]
        nearest_expiry = date.fromisoformat(nearest_expiry_str)

        # Filter for target strikes and types
        subset = nifty_df[
            (nifty_df["Expiry"].str.slice(0, 10) == nearest_expiry_str)
            & (nifty_df["StrikeRate"].isin(target_strikes))
        ]

        recorded_count = 0
        feed = LiveMarketFeed(broker=broker, max_staleness_seconds=self.max_quote_age_seconds)
        feed.is_connected = True
        for _, row in subset.iterrows():
            self.last_archive_stats["considered"] += 1
            scrip = int(row["ScripCode"])
            opt_type = str(row["ScripType"])
            strike = float(row["StrikeRate"])
            lot_size = int(row["LotSize"])
            name = str(row["Name"])

            try:
                if self._last_option_request_monotonic is not None:
                    elapsed = time.monotonic() - self._last_option_request_monotonic
                    wait_seconds = self.option_request_interval_seconds - elapsed
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)
                self._last_option_request_monotonic = time.monotonic()
                quote = feed.fetch_option_depth(scrip_code=scrip, symbol=name)
                if quote is None:
                    self.last_archive_stats["rejected"] += 1
                    continue
                raw_response = (
                    json.loads(quote.raw_response_json)
                    if quote.raw_response_json
                    else None
                )
                self.record_option_snapshot(
                    quote=quote,
                    expiry=nearest_expiry,
                    strike=strike,
                    option_type=opt_type,
                    lot_size=lot_size,
                    raw_response=raw_response,
                )
                recorded_count = self.last_archive_stats["valid"]
            except Exception:
                self.last_archive_stats["rejected"] += 1
                continue

        return recorded_count
