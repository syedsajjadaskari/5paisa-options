"""Build the Phase 4E.3 strategy-developer clarification PDF."""

from __future__ import annotations

import csv
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports/independent_codex_audit/phase4e3"
CHART_DIR = REPORT_DIR / "charts"
OUTPUT = REPORT_DIR / "STRATEGY_CLARIFICATION_PACKAGE.pdf"


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D9E2EC"))
    canvas.line(18 * mm, 13 * mm, 192 * mm, 13 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#52606D"))
    canvas.drawString(18 * mm, 8 * mm, "Phase 4E.3 | historical replay only | READ_ONLY")
    canvas.drawRightString(192 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("TitlePhase", parent=base["Title"], fontName="Helvetica-Bold", fontSize=23, leading=28, textColor=colors.HexColor("#172B4D"), alignment=TA_LEFT, spaceAfter=8),
        "subtitle": ParagraphStyle("SubtitlePhase", parent=base["Normal"], fontName="Helvetica", fontSize=11, leading=15, textColor=colors.HexColor("#52606D"), spaceAfter=16),
        "h1": ParagraphStyle("H1Phase", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=colors.HexColor("#172B4D"), spaceBefore=6, spaceAfter=8),
        "h2": ParagraphStyle("H2Phase", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=colors.HexColor("#243B53"), spaceBefore=7, spaceAfter=4),
        "body": ParagraphStyle("BodyPhase", parent=base["BodyText"], fontName="Helvetica", fontSize=9.3, leading=13, textColor=colors.HexColor("#243B53"), spaceAfter=6),
        "small": ParagraphStyle("SmallPhase", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=10.5, textColor=colors.HexColor("#52606D"), spaceAfter=4),
        "question": ParagraphStyle("QuestionPhase", parent=base["BodyText"], fontName="Helvetica", fontSize=9.2, leading=13, leftIndent=11, firstLineIndent=-11, textColor=colors.HexColor("#243B53"), spaceAfter=6),
        "callout": ParagraphStyle("CalloutPhase", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=11, leading=15, textColor=colors.HexColor("#0F5132"), alignment=TA_CENTER, spaceAfter=4),
    }


def _table(data: list[list[str]], widths: list[float]) -> Table:
    cell_style = ParagraphStyle("TableCellPhase", fontName="Helvetica", fontSize=7.5, leading=9, textColor=colors.HexColor("#243B53"))
    header_style = ParagraphStyle("TableHeaderPhase", fontName="Helvetica-Bold", fontSize=7.5, leading=9, textColor=colors.white)
    wrapped = [
        [Paragraph(escape(str(cell)), header_style if row_index == 0 else cell_style) for cell in row]
        for row_index, row in enumerate(data)
    ]
    table = Table(wrapped, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172B4D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("LEADING", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#BCCCDC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F4F8")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def main() -> int:
    styles = _styles()
    breakdown = _read_rows(REPORT_DIR / "SHADOW_REJECTION_BREAKDOWN.csv")
    comparison = _read_rows(REPORT_DIR / "DISCREPANCY_ANALYSIS.csv")
    story = []
    story.extend([
        Paragraph("Strategy clarification package", styles["title"]),
        Paragraph("Phase 4E.3 | independent offline audit and deterministic replay", styles["subtitle"]),
        Table([[Paragraph("FULL_LIVE_SESSION_VALIDATION = PENDING", styles["callout"])]], colWidths=[174 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E3FCEF")),
            ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#2F855A")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ])),
        Spacer(1, 10),
        Paragraph("Purpose", styles["h1"]),
        Paragraph("This package asks the original strategy author to resolve only those rule definitions that can materially change PDS, PDR, ORB, or IRF signal counts. It is based on genuine stored historical OHLC and the original strategy PDFs. It contains no live observations, simulated fills, paper positions, broker orders, optimization, or BASELINE_V1 changes.", styles["body"]),
        Paragraph("Evidence basis", styles["h2"]),
        Paragraph("Primary replay session: 2025-08-06, stored 5-minute provider OHLC, 75 completed bars from 09:15 through 15:25 IST. The 15:30 row is treated as a boundary record and excluded from the 75-bar completed-bar count. The replay uses point-in-time contexts and triggers evaluation only after each bar close.", styles["body"]),
        Paragraph("Original sources reviewed: IRF Strategy Guide Updated.pdf (17 pages), ORB Strategy Rulebook-2.pdf (13 pages), PDR Strategy Guide.pdf (4 pages), PDS strategy.pdf (7 pages), and RF strategy.pdf (9 pages). Page references below identify the relevant source guide; the author should confirm the final canonical interpretation.", styles["small"]),
        PageBreak(),
        Paragraph("Observed audit facts", styles["h1"]),
    ])

    totals = {}
    for row in breakdown:
        totals.setdefault(row["strategy"], {})[row["rejection_category"]] = row["rejected"]
    data = [["Strategy", "Evaluations", "Primary outcome", "Count", "Interpretation"]]
    for strategy in ("PDS", "PDR", "ORB", "IRF", "RF"):
        counts = totals[strategy]
        if strategy == "RF":
            outcome, count, note = "RULEBOOK_AMBIGUITY", counts["RULEBOOK_AMBIGUITY"], "Canonical exit/target remains unspecified."
        else:
            outcome, count, note = "STRATEGY_CONDITION_FALSE", counts["STRATEGY_CONDITION_FALSE"], "Inputs were available; no approved setup condition was met in this replay."
        data.append([strategy, "75", outcome, count, note])
    story.append(_table(data, [25 * mm, 24 * mm, 38 * mm, 18 * mm, 69 * mm]))
    story.extend([
        Spacer(1, 10),
        Paragraph("The earlier 375-row Phase 4E.1 audit had a different root cause: 300 rows were blocked before strategy evaluation because the caller supplied no prior-day context, and 75 RF rows were blocked by unresolved rulebook semantics. Phase 4E.2 corrected the context path. This package keeps missing evidence, false conditions, and rulebook ambiguity distinct.", styles["body"]),
        Paragraph("No row is promoted to a signal by this document. Any author answer must be incorporated as an approved rulebook revision and independently tested before it can affect signal eligibility.", styles["body"]),
        PageBreak(),
        Paragraph("Questions for the strategy author", styles["h1"]),
    ])

    questions = [
        "1. PDS/PDR zone (PDS strategy.pdf pp. 2-5; PDR Strategy Guide.pdf pp. 2-3): Is the previous-day zone the body of the candle containing PDH/PDL, the complete candle range, or a fixed point? What exact point or percentage tolerance constitutes a zone test?",
        "2. PDS/PDR gap (PDS strategy.pdf pp. 2-5; PDR Strategy Guide.pdf p. 2): Is the gap measured against PDC, PDO, or another level? Is any positive/negative gap sufficient, or is a minimum threshold required?",
        "3. PDS/PDR confirmation (PDS strategy.pdf pp. 4-6; PDR Strategy Guide.pdf pp. 2-3): Define good red, HPR, green, and small support using body/wick ratios, close location, and whether the confirmation candle must close beyond the zone.",
        "4. PDS/PDR timing: May a zone test and confirmation occur on the same completed candle? May the setup remain valid after a failed or repeated test, and for how many candles?",
        "5. ORB (ORB Strategy Rulebook-2.pdf pp. 2-7): Confirm that the opening range is exactly 09:15-09:20 and that entry can occur only after the next completed candle. Define established trend and required body/wick quality numerically.",
        "6. ORB levels (ORB Strategy Rulebook-2.pdf pp. 5-9): Which of PDH/PDL, prior-day body levels, EMA/DMA, or inner levels are mandatory? What precedence applies when levels overlap?",
        "7. IRF Methods 1-3 (IRF Strategy Guide Updated.pdf pp. 4-12): Define breakout close, immediate return, extension count, recent-support lookback, and red-candle quality. Confirm whether close or intrabar touch is required.",
        "8. RF (RF strategy.pdf pp. 3-8): Define the canonical entry trigger, stop, target, and exit. RF remains blocked because the current guide does not provide an executable canonical exit.",
    ]
    story.extend(Paragraph(q, styles["question"]) for q in questions)
    story.extend([
        Paragraph("Required answer format", styles["h2"]),
        Paragraph("For each answer, provide an exact formula or threshold, the permitted candle timestamp relationship, and one positive and one negative example. If a rule is intentionally discretionary, identify the approved operational category and keep it gated as RULEBOOK_AMBIGUITY until a deterministic version is approved.", styles["body"]),
        PageBreak(),
        Paragraph("Annotated historical examples", styles["h1"]),
        Paragraph("These charts use stored provider OHLC only. The annotations show audit evidence and a discrepancy location; they do not assert that a trade should have been taken.", styles["body"]),
    ])
    for image_name, caption in [
        ("2025-08-06_historical_session.png", "Figure 1. Complete historical replay session with the first and last completed bars annotated. No live collection claim."),
        ("2025-08-07_discrepancy_example.png", "Figure 2. Genuine historical comparison example: a baseline-only PDS timestamp at 09:40, while the conservative shadow path remained gated pending rulebook confirmation."),
    ]:
        image = Image(str(CHART_DIR / image_name), width=174 * mm, height=82 * mm)
        story.extend([image, Paragraph(caption, styles["small"]), Spacer(1, 5)])
    story.extend([
        PageBreak(),
        Paragraph("Discrepancy handling and release boundary", styles["h1"]),
        Paragraph("The multi-day historical comparison contains baseline-only timestamps where the conservative shadow path rejected an otherwise baseline-detected setup because the rulebook gate is not yet deterministic. These discrepancies are questions for the author, not permission to loosen safety checks.", styles["body"]),
    ])
    comp_data = [["Date", "Strategy", "Shadow", "Baseline", "Reason"]]
    for row in comparison:
        if row["agreement"].lower() == "false":
            comp_data.append([row["session_date"], row["strategy"], row["shadow_signal_count"], row["backtest_signal_count"], row["discrepancy_reason"]])
    story.append(_table(comp_data, [28 * mm, 25 * mm, 21 * mm, 25 * mm, 75 * mm]))
    story.extend([
        Spacer(1, 10),
        Paragraph("Release criteria", styles["h2"]),
        Paragraph("A future live-session acceptance requires one complete supported NSE session with actual exchange timestamps, receipt timestamps, NIFTY spot observations, near-ATM CE/PE two-sided quotes and contract metadata, explicit gap/reconnection accounting, completed-bar validation, and a persisted READ_ONLY heartbeat from 09:15 through 15:30 IST. Until then: FULL_LIVE_SESSION_VALIDATION = PENDING.", styles["body"]),
    ])

    doc = SimpleDocTemplate(str(OUTPUT), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm, title="Phase 4E.3 Strategy Clarification Package", author="Independent Codex Audit")
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    pages = len(PdfReader(str(OUTPUT)).pages)
    if pages < 4:
        raise RuntimeError(f"Expected a multi-page clarification package, got {pages} pages")
    print(f"created {OUTPUT} ({pages} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
