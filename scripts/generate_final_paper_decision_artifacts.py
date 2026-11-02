#!/usr/bin/env python3
"""Regenerate the author-confirmation artifacts from the CURRENT engine and rules.

REWRITTEN 2026-10-29.  The previous version hard-coded the rules and counts of an engine that
no longer exists (a lookback-based pivot proxy, a doubled zone buffer, outdated signal and trade
counts) and carried an unsupported verification footer.  Its output is archived byte-exact in
``archive/evidence_originals_2026-10-29/``.  This version derives everything from the code
that actually runs:

* signals / trades come from ``backtesting.portfolio_runner.run_portfolio_backtest`` (the
  V3_PDF_CORRECTED engine, the same one ``scripts/run_backtest.py`` and the paper app use);
* every question is built from ``<Strategy>.PROVISIONAL_RULES`` (the rules the PDFs leave
  open), so a rule cannot be shown to the author that the code does not implement, and a rule
  the code implements cannot be left out.

Outputs (default ``reports/paper_trading_live_session/regenerated/``; NEVER the repo root, so
the frozen ``COMBINED_PORTFOLIO_TRADES.csv`` / the archived originals are not overwritten):

* ``COMBINED_PORTFOLIO_TRADES.csv``      accepted + rejected signals of the current engine
* ``AMBIGUOUS_RULE_EXPOSURE.csv``        per-strategy exposure to the provisional rules
* ``STRATEGY_AUTHOR_CONFIRMATION.pdf``   confirm / change sheet, one line per provisional rule

All figures are NIFTY index points from an in-sample backtest; no option P&L exists.  Nothing
here is an approval: a strategy only becomes AUTHOR_APPROVED when the author signs the sheet
AND a human changes the configuration; this script changes no configuration.

Usage:  python scripts/generate_final_paper_decision_artifacts.py [--output-dir DIR]
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR / "src") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "src"))

logger = logging.getLogger("generate_artifacts")

DEFAULT_OUTPUT_DIR = Path("reports/paper_trading_live_session/regenerated")
RF_STATUS = (
    "PERMANENTLY DISABLED. The author's RF PDF gives no exit rules (no stop, target or "
    "trailing rule), so RF is never evaluated, even in shadow. It stays disabled until the "
    "author provides complete mechanical exit rules."
)
NO_APPROVAL_NOTE = (
    "No strategy is author-approved. Signing this sheet does not by itself enable anything: "
    "a human must still change the configuration. Figures are NIFTY index points from an "
    "in-sample backtest of the current engine (no option prices exist; nothing here validates "
    "option profitability)."
)
CONFIRMATION_DOC = "docs/AUTHOR_CONFIRMATION_REQUEST.md"


# --------------------------------------------------------------------------- pure helpers


def provisional_rules_by_strategy() -> dict[str, tuple[str, ...]]:
    """PROVISIONAL_RULES of the four authorized strategies, in portfolio priority order."""
    from trading_platform.backtesting.portfolio_runner import AUTHORIZED_STRATEGY_SPECS

    return {name: tuple(cls.PROVISIONAL_RULES) for name, cls, _cfg in AUTHORIZED_STRATEGY_SPECS}


def build_exposure_rows(
    signals: Sequence[Mapping[str, Any]],
    portfolio_trades: Sequence[Mapping[str, Any]],
    rules: Mapping[str, Sequence[str]],
) -> list[dict[str, Any]]:
    """One row per strategy (+ RF).  ``signals`` / ``portfolio_trades`` are in-window rows."""
    rows: list[dict[str, Any]] = []
    for name, strategy_rules in rules.items():
        n_signals = sum(1 for s in signals if s["strategy"] == name)
        trades = [t for t in portfolio_trades if t["strategy"] == name]
        rows.append(
            {
                "strategy_name": name,
                "engine_signals_in_window": n_signals,
                # Every signal depends on the provisional rules listed below: none of the four
                # strategies has an explicit-rules-only path.
                "signals_relying_on_provisional_rules": n_signals,
                "signals_eligible_under_pdf_rules_only": 0,
                "portfolio_trades_in_window": len(trades),
                "portfolio_points_in_window": round(sum(t["points_pnl"] for t in trades), 2),
                "provisional_rule_count": len(strategy_rules),
                "provisional_rules": " || ".join(strategy_rules),
                "author_action_required": (
                    f"Confirm or change each provisional rule on the sheet ({CONFIRMATION_DOC})"
                ),
            }
        )
    rows.append(
        {
            "strategy_name": "RF",
            "engine_signals_in_window": 0,
            "signals_relying_on_provisional_rules": 0,
            "signals_eligible_under_pdf_rules_only": 0,
            "portfolio_trades_in_window": 0,
            "portfolio_points_in_window": 0.0,
            "provisional_rule_count": 0,
            "provisional_rules": RF_STATUS,
            "author_action_required": "Provide complete mechanical exit rules (stop/target/trail)",
        }
    )
    return rows


def combined_trade_rows(result: Any) -> list[dict[str, Any]]:
    """Accepted portfolio trades followed by rejected signals of the current engine."""
    from trading_platform.backtesting.portfolio_runner import trade_to_row

    window = (result.params.window_start, result.params.window_end)
    rows: list[dict[str, Any]] = []
    for i, trade in enumerate(result.portfolio_trades, start=1):
        row = trade_to_row(trade, window)
        row.update(
            {
                "portfolio_trade_id": f"PORT_TR_{i:04d}",
                "portfolio_decision": "ACCEPTED",
                "rejection_reason": "",
                "blocking_trade_id": "",
            }
        )
        rows.append(row)
    for sig in result.rejected_signals:
        rows.append(
            {
                "portfolio_trade_id": "REJECTED_SIGNAL",
                "signal_id": sig["signal_id"],
                "strategy": sig["strategy"],
                "method": sig["method"],
                "direction": sig["direction"],
                "date": sig["date"],
                "signal_candle_open": sig["signal_candle_open"],
                "portfolio_decision": sig["portfolio_status"],
                "rejection_reason": sig["portfolio_reason"],
                "blocking_trade_id": sig["blocking_trade_id"],
                "in_window": sig["in_window"],
            }
        )
    return rows


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def safe_output_dir(output_dir: Path, base_dir: Path = BASE_DIR) -> Path:
    """Resolve ``output_dir`` and refuse the repo root or any pre-existing artifact folder."""
    out = (output_dir if output_dir.is_absolute() else base_dir / output_dir).resolve()
    root = base_dir.resolve()
    if out == root or out in root.parents:
        raise SystemExit(f"refusing to write generated artifacts into {out}")
    protected = (
        root / "reports" / "paper_trading_live_session",
        root / "archive",
        root / "docs",
    )
    if out in protected:
        raise SystemExit(f"refusing to overwrite a curated folder: {out}")
    out.mkdir(parents=True, exist_ok=True)
    return out


# --------------------------------------------------------------------------- PDF


def build_confirmation_pdf(
    pdf_path: Path,
    rules: Mapping[str, Sequence[str]],
    exposure: Sequence[Mapping[str, Any]],
    window_label: str,
    engine_label: str,
) -> None:
    """Confirm/change sheet: one checkbox line per PROVISIONAL rule of the current code."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "T", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=16, leading=20
    )
    h1 = ParagraphStyle(
        "H1", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11, leading=15,
        spaceBefore=10, spaceAfter=4, keepWithNext=True,
    )
    body = ParagraphStyle("B", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, leading=12)
    alert = ParagraphStyle(
        "A", parent=body, fontName="Helvetica-Bold", textColor=colors.HexColor("#842029")
    )

    def p(text: str, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(text, style)

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36
    )
    story: list[Any] = [
        p("Strategy author confirmation sheet (provisional rules)", title),
        p(f"Engine: {escape(engine_label)}. Window: {escape(window_label)}. "
          f"Generated from the strategies' PROVISIONAL_RULES.", body),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0B2545"), spaceAfter=6),
        p(escape(NO_APPROVAL_NOTE), alert),
        Spacer(1, 6),
        p(f"Full question list, code locations and evidence: {CONFIRMATION_DOC}", body),
    ]

    table_rows = [["Strategy", "Signals (window)", "Portfolio trades", "Points", "Provisional rules"]]
    for row in exposure:
        table_rows.append(
            [
                row["strategy_name"],
                str(row["engine_signals_in_window"]),
                str(row["portfolio_trades_in_window"]),
                f"{row['portfolio_points_in_window']:+.2f}",
                str(row["provisional_rule_count"]),
            ]
        )
    summary = Table(table_rows, colWidths=[70, 90, 100, 70, 100])
    summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B2545")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CED4DA")),
            ]
        )
    )
    story += [Spacer(1, 8), summary]

    for name, strategy_rules in rules.items():
        story.append(p(f"{escape(name)}: {len(strategy_rules)} provisional rules", h1))
        for i, rule in enumerate(strategy_rules, start=1):
            story.append(
                p(
                    f"<b>{escape(name)}-{i}.</b> {escape(rule)}<br/>"
                    "&nbsp;&nbsp;[ &nbsp; ] CONFIRM as implemented &nbsp;&nbsp; "
                    "[ &nbsp; ] CHANGE TO: ______________________________",
                )
            )
            story.append(Spacer(1, 3))

    story.append(p("RF: status", h1))
    story.append(p(escape(RF_STATUS)))
    story.append(Spacer(1, 10))

    sign = Table(
        [
            [p("<b>Strategy author name:</b> ____________________"), p("<b>Signature:</b> ____________________")],
            [p("<b>Role:</b> ____________________"), p("<b>Date:</b> ____________________")],
            [
                p("<b>Verdict:</b><br/>[ &nbsp; ] confirmed rules may be used for supervised "
                  "research paper trading<br/>[ &nbsp; ] keep shadow-only"),
                p("<b>Engineering note:</b> this sheet records the author's answers only. "
                  "It is not an engineering verification of any trading outcome."),
            ],
        ],
        colWidths=[270, 270],
    )
    sign.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#0B2545")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(sign)
    doc.build(story)


# --------------------------------------------------------------------------- main


def generate(output_dir: Path) -> Path:
    from trading_platform.backtesting.constants import ENGINE_VERSION_LABEL
    from trading_platform.backtesting.portfolio_runner import (
        BacktestParams,
        load_nifty_bars,
        run_portfolio_backtest,
        trade_to_row,
    )

    out = safe_output_dir(output_dir)
    logger.info("Loading NIFTY bars and running the %s engine ...", ENGINE_VERSION_LABEL)
    bars_1d, bars_5m = load_nifty_bars(BASE_DIR)
    params = BacktestParams()
    result = run_portfolio_backtest(bars_5m, bars_1d, params)
    window = (params.window_start, params.window_end)

    combined = combined_trade_rows(result)
    _write_csv(out / "COMBINED_PORTFOLIO_TRADES.csv", combined)

    in_window_signals = [s for s in result.signals if s["in_window"]]
    in_window_trades = [
        trade_to_row(t, window) for t in result.portfolio_trades if trade_to_row(t, window)["in_window"]
    ]
    rules = provisional_rules_by_strategy()
    exposure = build_exposure_rows(in_window_signals, in_window_trades, rules)
    _write_csv(out / "AMBIGUOUS_RULE_EXPOSURE.csv", exposure)

    window_label = f"{params.window_start} to {params.window_end} (in-sample)"
    build_confirmation_pdf(
        out / "STRATEGY_AUTHOR_CONFIRMATION.pdf", rules, exposure, window_label, ENGINE_VERSION_LABEL
    )
    logger.info("Wrote artifacts to %s", out)
    return out


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"where to write (default {DEFAULT_OUTPUT_DIR}; repo root and curated folders refused)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    generate(args.output_dir)


if __name__ == "__main__":
    main()
