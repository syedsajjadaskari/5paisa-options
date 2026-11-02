"""Joint portfolio simulation, datasets, outputs and integrity tests (SYNTHETIC bars)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_bt_support import DAY, ScriptedStrategy, at, flat_bars, replace_bar

from trading_platform.backtesting.constants import ENGINE_VERSION_LABEL, PORTFOLIO_PRIORITY
from trading_platform.backtesting.engine import (
    RejectionReason,
    SignalStatus,
    UnderlyingBacktestEngine,
)
from trading_platform.backtesting.portfolio_runner import (
    BacktestParams,
    build_authorized_strategies,
    load_nifty_bars,
    parse_manifest_hashes,
    run_portfolio_backtest,
    sha256_file,
    validate_portfolio_invariants,
    verify_dataset_hashes,
    write_run_outputs,
)
from trading_platform.core.enums import SignalDirection

BUY = SignalDirection.BUY
SELL = SignalDirection.SELL
REPO = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ authorized strategies


def test_build_authorized_strategies_is_the_four_in_priority_order_without_rf():
    strategies = build_authorized_strategies()
    assert tuple(strategies) == ("PDS", "ORB", "IRF", "PDR") == tuple(PORTFOLIO_PRIORITY)
    assert "RF" not in strategies
    assert {type(s).__name__ for s in strategies.values()} == {
        "PDSStrategy", "ORBStrategy", "IRFStrategy", "PDRStrategy",
    }  # fmt: skip
    assert strategies["PDS"].config["method"] == "METHOD_1"
    assert strategies["ORB"].config["method"] == "METHOD_1"
    assert strategies["IRF"].config["method"] == "METHOD_2"
    assert all(s.name == key for key, s in strategies.items())


def test_build_authorized_strategies_returns_fresh_instances_each_call():
    a, b = build_authorized_strategies(), build_authorized_strategies()
    assert all(a[k] is not b[k] for k in a)


# ------------------------------------------------------------------ joint simulation


def _three(schedule_a, schedule_b, schedule_c):
    return [
        ScriptedStrategy("AAA", schedule_a),
        ScriptedStrategy("BBB", schedule_b),
        ScriptedStrategy("CCC", schedule_c),
    ]


def test_same_candle_tie_break_follows_explicit_priority_list():
    bars = flat_bars(at(9, 15), 12, 100.0)
    sig = {at(9, 15): BUY}
    strategies = _three(sig, sig, sig)
    engine = UnderlyingBacktestEngine()
    result = engine.simulate(strategies, bars, priority=["CCC", "AAA", "BBB"])
    assert [t.strategy_name for t in result.trades] == ["CCC"]
    by_strategy = {s.strategy_name: s for s in result.signals}
    assert by_strategy["CCC"].status == SignalStatus.FILLED
    for loser in ("AAA", "BBB"):
        assert by_strategy[loser].rejection_reason == RejectionReason.LOST_TIE_BREAK
        assert by_strategy[loser].blocking_signal_id == by_strategy["CCC"].signal_id

    # a different explicit list flips the winner; default order == order of the list given
    flipped = engine.simulate(_three(sig, sig, sig), bars, priority=["BBB", "CCC", "AAA"])
    assert [t.strategy_name for t in flipped.trades] == ["BBB"]
    default = engine.simulate(_three(sig, sig, sig), bars)
    assert [t.strategy_name for t in default.trades] == ["AAA"]


def test_fifo_by_signal_time_earlier_signal_wins_even_with_lower_priority():
    bars = flat_bars(at(9, 15), 12, 100.0)
    strategies = _three({at(9, 25): BUY}, {at(9, 15): BUY}, {})
    result = UnderlyingBacktestEngine().simulate(strategies, bars, priority=["AAA", "BBB", "CCC"])
    assert [t.strategy_name for t in result.trades] == ["BBB"]
    late = next(s for s in result.signals if s.strategy_name == "AAA")
    assert late.rejection_reason == RejectionReason.OVERLAP_POSITION_OPEN
    assert late.blocking_trade_id == result.trades[0].trade_id


def test_only_one_position_at_a_time_and_every_bar_reaches_every_strategy():
    bars = flat_bars(at(9, 15), 40, 100.0)
    # flat bars never hit stop/target: the first position stays open until the 15:15 EOD bar
    # (not in range) -> forced close at the end.  Everything else must be rejected.
    spam = {b.exchange_time: BUY for b in bars[:20]}
    strategies = _three(spam, spam, spam)
    result = UnderlyingBacktestEngine().simulate(strategies, bars, priority=["AAA", "BBB", "CCC"])
    assert len(result.trades) == 1
    assert all(s.bars_seen == len(bars) for s in strategies)
    reasons = {s.rejection_reason for s in result.signals if s.status == SignalStatus.REJECTED}
    assert reasons <= {"OVERLAP_POSITION_OPEN", "LOST_TIE_BREAK"}
    assert validate_portfolio_invariants(result.trades) == []
    assert len(result.signals) == 60


def test_after_entry_cutoff_signals_are_recorded_not_dropped_silently():
    bars = flat_bars(at(14, 10), 8, 100.0)
    strategies = _three({at(14, 25): BUY}, {at(14, 35): SELL}, {})
    result = UnderlyingBacktestEngine().simulate(strategies, bars)
    assert result.trades == []
    assert [s.rejection_reason for s in result.signals] == ["AFTER_ENTRY_CUTOFF"] * 2


def test_invariant_validator_detects_overlap_and_duplicates():
    bars = flat_bars(at(9, 15), 12, 100.0)
    replace_bar(bars, at(9, 25), 100, 101, 60, 100)
    result = UnderlyingBacktestEngine().simulate(
        [ScriptedStrategy("AAA", {at(9, 15): BUY, at(9, 25): BUY})], bars
    )
    ok = result.trades
    assert validate_portfolio_invariants(ok) == []
    assert validate_portfolio_invariants([ok[0], ok[0]])  # duplicates + overlap flagged


# ------------------------------------------------------------------ full runner on synthetic data


def _synthetic_bars():
    bars = []
    for d in range(2):
        day = DAY + timedelta(days=d)
        day_bars = flat_bars(at(9, 15, day), 75, 100.0)  # 09:15 .. 15:25
        replace_bar(day_bars, at(9, 35, day), 100, 101, 60, 100)  # stops the first BUY
        bars += day_bars
    return bars


def _factory():
    day2 = DAY + timedelta(days=1)
    return {
        "AAA": ScriptedStrategy("AAA", {at(9, 15): BUY, at(10, 0, day2): SELL}),
        "BBB": ScriptedStrategy("BBB", {at(9, 15): BUY, at(9, 40): SELL, at(14, 25): BUY}),
        "CCC": ScriptedStrategy("CCC", {at(12, 0): BUY}),
    }


def _params(**kw):
    return BacktestParams(
        priority=("AAA", "BBB", "CCC"), window_start=None, window_end=None, **kw
    )


def test_runner_produces_four_distinct_consistent_datasets():
    res = run_portfolio_backtest(_synthetic_bars(), None, _params(), _factory)
    assert res.raw_signals_consistent
    assert res.invariant_violations == []

    sig = {r["signal_id"]: r for r in res.signals}
    assert len(sig) == len(res.signals) == 6  # every raw signal, none lost
    # accepted portfolio trades carry the signal id of an ACCEPTED signal
    for t in res.portfolio_trades:
        assert sig[t.signal_id]["portfolio_status"] == "FILLED"
        assert sig[t.signal_id]["portfolio_trade_id"] == t.trade_id
    # rejected dataset == REJECTED signals, each with a reason
    assert {r["signal_id"] for r in res.rejected_signals} == {
        k for k, v in sig.items() if v["portfolio_status"] == "REJECTED"
    }
    assert all(r["portfolio_reason"] for r in res.rejected_signals)
    # tie on 09:15: AAA wins by priority, BBB loses the tie-break
    lost = [r for r in res.rejected_signals if r["portfolio_reason"] == "LOST_TIE_BREAK"]
    assert [r["strategy"] for r in lost] == ["BBB"]
    # standalone run of each strategy trades its own signal even when the portfolio rejected it
    standalone_ids = {t.signal_id for t in res.standalone_trades}
    assert lost[0]["signal_id"] in standalone_ids
    assert len(res.standalone_trades) > len(res.portfolio_trades)
    # cutoff-rejected signal is in the rejected dataset
    assert any(r["portfolio_reason"] == "AFTER_ENTRY_CUTOFF" for r in res.rejected_signals)
    # joint portfolio trades never overlap
    ordered = sorted(res.portfolio_trades, key=lambda t: t.entry_time)
    assert all(b.entry_time > a.exit_time for a, b in zip(ordered, ordered[1:], strict=False))


def test_runner_is_deterministic():
    a = run_portfolio_backtest(_synthetic_bars(), None, _params(), _factory)
    b = run_portfolio_backtest(_synthetic_bars(), None, _params(), _factory)
    assert a.signals == b.signals
    assert a.portfolio_trades == b.portfolio_trades
    assert a.standalone_trades == b.standalone_trades
    assert a.summary == b.summary


def test_summary_window_selects_reported_rows_only():
    day2 = DAY + timedelta(days=1)
    res = run_portfolio_backtest(
        _synthetic_bars(), None, _params().__class__(
            priority=("AAA", "BBB", "CCC"), window_start=day2, window_end=day2
        ), _factory
    )  # fmt: skip
    in_win = [r for r in res.signals if r["in_window"]]
    assert in_win and all(r["date"] == day2.isoformat() for r in in_win)
    assert res.summary["window"]["raw_signals"] == len(in_win)
    assert res.summary["full_dataset"]["raw_signals"] == len(res.signals)
    assert res.summary["engine_version"] == ENGINE_VERSION_LABEL
    assert res.summary["unit"] == "NIFTY_INDEX_POINTS"


# ------------------------------------------------------------------ outputs


def test_write_run_outputs_creates_the_seven_files_and_manifest(tmp_path: Path):
    res = run_portfolio_backtest(_synthetic_bars(), None, _params(), _factory)
    out = tmp_path / "reports" / "backtest" / "run1"
    files = write_run_outputs(res, out, tmp_path, "run1", dataset_checks=[{"path": "x"}])
    assert {p.name for p in files.values()} == {
        "signals.csv", "standalone_trades.csv", "portfolio_trades.csv",
        "rejected_signals.csv", "summary.json", "summary.md", "manifest.json",
    }  # fmt: skip
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["engine_version"] == "V3_PDF_CORRECTED"
    assert manifest["unit"] == "NIFTY_INDEX_POINTS"
    assert len(manifest["params_hash_sha256"]) == 64
    assert manifest["row_counts"]["portfolio_trades"] == len(res.portfolio_trades)
    assert manifest["strategies_excluded"][0].startswith("RF")
    assert manifest["output_file_sha256"]["signals"] == sha256_file(out / "signals.csv")
    assert "git_sha" in manifest and "dataset_hashes" in manifest
    md = (out / "summary.md").read_text()
    assert "NIFTY INDEX-POINT" in md and "WITHOUT any optimization" in md
    header = (out / "portfolio_trades.csv").read_text().splitlines()[0]
    assert header.startswith("trade_id,signal_id,strategy")


def test_params_hash_changes_with_parameters(tmp_path: Path):
    a = run_portfolio_backtest(_synthetic_bars(), None, _params(), _factory)
    b = run_portfolio_backtest(_synthetic_bars(), None, _params(slippage_points=1.0), _factory)
    ma = write_run_outputs(a, tmp_path / "a", tmp_path, "a")["manifest"]
    mb = write_run_outputs(b, tmp_path / "b", tmp_path, "b")["manifest"]
    assert json.loads(ma.read_text())["params_hash_sha256"] != json.loads(mb.read_text())[
        "params_hash_sha256"
    ]


def test_outputs_refuse_frozen_dirs_and_existing_run_dirs(tmp_path: Path):
    res = run_portfolio_backtest(_synthetic_bars(), None, _params(), _factory)
    for frozen in ("reports/phase2d", "reports/phase3a/x", "reports/independent_codex_audit", "docs", "data/raw"):
        with pytest.raises(ValueError, match="frozen"):
            write_run_outputs(res, tmp_path / frozen, tmp_path, "r")
    out = tmp_path / "reports" / "backtest" / "dup"
    write_run_outputs(res, out, tmp_path, "dup")
    with pytest.raises(FileExistsError):
        write_run_outputs(res, out, tmp_path, "dup")


# ------------------------------------------------------------------ dataset hash verification


def test_manifest_hash_parsing_and_verification(tmp_path: Path):
    good = tmp_path / "data/processed/nifty/5m/nifty50_5m.parquet"
    bad = tmp_path / "data/processed/nifty/1d/nifty50_1d.parquet"
    for p, content in ((good, b"five"), (bad, b"daily")):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    manifest = tmp_path / "manifest.md"
    manifest.write_text(
        "| NIFTY 5m | `data/processed/nifty/5m/nifty50_5m.parquet` | 4 | 1 | "
        f"`{sha256_file(good)}` |\n"
        "| NIFTY 1d | `data/processed/nifty/1d/nifty50_1d.parquet` | 5 | 1 | "
        f"`{'0' * 64}` |\n"
        "| DuckDB | `data/processed/market_data.duckdb` | ~2.5 MB | Multi-table | Live |\n"
    )
    assert set(parse_manifest_hashes(manifest)) == {
        "data/processed/nifty/5m/nifty50_5m.parquet",
        "data/processed/nifty/1d/nifty50_1d.parquet",
    }
    checks = {c["path"]: c["status"] for c in verify_dataset_hashes(tmp_path, manifest)}
    assert checks["data/processed/nifty/5m/nifty50_5m.parquet"] == "MATCH"
    assert checks["data/processed/nifty/1d/nifty50_1d.parquet"] == "MISMATCH"  # warn, no raise
    assert checks["data/processed/nifty/1m/nifty50_1m.parquet"] == "MISSING_FILE"


# ------------------------------------------------------------------ real data smoke (read-only)


@pytest.mark.skipif(
    not (REPO / "data/processed/nifty/5m/nifty50_5m.parquet").exists(),
    reason="verified historical dataset not present",
)
def test_real_dataset_smoke_invariants_hold_without_asserting_numbers():
    """End-to-end on the real files; asserts structure/invariants only (strategies are in flux)."""
    bars_1d, bars_5m = load_nifty_bars(REPO)
    res = run_portfolio_backtest(bars_5m, bars_1d)  # four authorized strategies
    assert res.invariant_violations == []
    assert res.raw_signals_consistent
    names = {t.strategy_name for t in res.standalone_trades} | {
        t.strategy_name for t in res.portfolio_trades
    }
    assert names <= set(PORTFOLIO_PRIORITY) and "RF" not in names
    for t in res.portfolio_trades:
        assert t.entry_time.time() < BacktestParams().entry_cutoff
        assert t.exit_time.date() == t.entry_time.date()  # nothing carried overnight
        assert t.signal_id and t.slippage_points == 0.5
    assert len(res.portfolio_trades) <= len(res.standalone_trades)
