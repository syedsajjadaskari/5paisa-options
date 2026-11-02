"""Unit tests for HistoricalReconciliationAuditor and Phase 2D pipeline."""

from __future__ import annotations

from pathlib import Path

from trading_platform.backtesting.reconciliation_audit import HistoricalReconciliationAuditor


def test_historical_reconciliation_auditor_pipeline(tmp_path: Path) -> None:
    """Verify that HistoricalReconciliationAuditor executes smoothly and produces all deliverables."""
    auditor = HistoricalReconciliationAuditor(
        base_dir=Path("/Users/syedaskari/project/5paisa-options"),
        slippage_points=0.50,
    )
    # Redirect output to tmp_path to avoid modifying production artifacts during unit testing
    auditor.reports_dir = tmp_path / "phase2d_test"
    auditor.reports_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load data
    auditor.load_data()
    assert len(auditor.bars_1d) >= 600
    assert len(auditor.bars_5m) >= 20000
    assert len(auditor.manual_df) == 190

    # 2. Run backtests
    auditor.run_backtests()
    # V3_PDF_CORRECTED strategies (PDF-conformant PDS/PDR/ORB/IRF).  The frozen V1/V2 ledgers
    # (353/332 trades) live untouched under reports/phase2d and reports/independent_codex_audit.
    assert len(auditor.all_automated_trades) == 230
    assert "ORB" in auditor.backtest_reports
    assert "IRF" in auditor.backtest_reports
    assert "PDS" in auditor.backtest_reports

    # 3. Reconcile manual trades
    auditor.reconcile_manual_trades()
    assert len(auditor.reconciled_trades) == 190

    # Verify weekend classification
    weekend_trades = [r for r in auditor.reconciled_trades if r.tolerance_tier == "NON_TRADING_DAY"]
    assert len(weekend_trades) == 5

    # V3_PDF_CORRECTED strategy/reference-level semantics produce 87 timing matches
    # for the pinned source data (V2 produced 89, the superseded V1 90/91).  PDS matches only
    # 2 manual-journal trades under the PDF-faithful gap-down/first-red-candle rules.
    matched = [
        r for r in auditor.reconciled_trades if r.tolerance_tier in ("EXACT_0M", "LE_5M", "LE_10M")
    ]
    assert len(matched) == 87

    # Verify binary outcome agreement
    agreed = [
        r
        for r in auditor.reconciled_trades
        if r.outcome_agreement in ("AGREED_TARGET", "AGREED_SL")
    ]
    assert len(agreed) == 65

    # 4. Compute metrics
    metrics = auditor.calculate_strategy_metrics()
    assert len(metrics) == 11
    orb_metric = next(m for m in metrics if m.strategy_name == "ORB" and "PDH/PDL" in m.method_name)
    assert orb_metric.total_matched_count == 45
    assert orb_metric.outcome_agreement_pct > 95.0
    assert orb_metric.net_index_points > 500.0

    # 5. Export deliverables
    auditor.export_all_deliverables()
    assert (auditor.reports_dir / "strategy_comparison.csv").exists()
    assert (auditor.reports_dir / "manual_vs_automated.csv").exists()
    assert (auditor.reports_dir / "matched_trades.csv").exists()
    assert (auditor.reports_dir / "missed_manual_trades.csv").exists()
    assert (auditor.reports_dir / "extra_automated_signals.csv").exists()
    assert (auditor.reports_dir / "outcome_disagreements.csv").exists()
    assert (auditor.reports_dir / "backtest_trades.parquet").exists()
    assert (auditor.reports_dir / "equity_curves.html").exists()
    assert (auditor.reports_dir / "validation_dashboard.html").exists()
