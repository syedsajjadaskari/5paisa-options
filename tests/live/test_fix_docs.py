"""Evidence-integrity and author-confirmation document tests (package F3).

They pin: (1) the archive of byte-exact originals, (2) the SUPERSEDED / SYNTHETIC labelling of
stale evidence, (3) that the author-confirmation documents list EVERY provisional rule of the
code and resolve their code locations, (4) that the sign-off generator can no longer emit the
retired engine's claims and never writes over curated folders.  No network, no market data, no
broker.
"""

from __future__ import annotations

import ast
import base64
import csv
import hashlib
import importlib.util
import re
import zlib
from pathlib import Path

import pytest
import yaml

from trading_platform.backtesting.portfolio_runner import AUTHORIZED_STRATEGY_SPECS
from trading_platform.dashboard import data as dash_data
from trading_platform.live.authorization import RESEARCH_PAPER_ACK

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports" / "paper_trading_live_session"
ARCHIVE = ROOT / "archive" / "evidence_originals_2026-10-29"
DOC_AUTHOR = ROOT / "docs" / "AUTHOR_CONFIRMATION_REQUEST.md"
DOC_STATUS = ROOT / "docs" / "STRATEGY_EXECUTION_STATUS.md"
GENERATOR = ROOT / "scripts" / "generate_final_paper_decision_artifacts.py"
MARK = "SUPERSEDED - DO NOT RELY ON"
V3 = "V3_PDF_CORRECTED_20261028T174123Z_523f2825"

BANNERED_MD = [
    "PAPER_TRADING_GO_NO_GO.md",
    "PREMARKET_CHECKLIST.md",
    "SAFETY_GATE_REPORT.md",
    "DAILY_OPERATIONAL_REPORT.md",
    "BACKTEST_VERSION_RECONCILIATION.md",
    "PORTFOLIO_PERFORMANCE.md",
    "PAPER_EXECUTION_BLOCKERS.md",
    "FORWARD_PAPER_TRADING_READINESS.md",
    "HISTORICAL_CASE_VALIDATION.md",
    "LIVE_SESSION_OPERATIONAL_CHECKLIST.md",
    "STRATEGY_AUTHORIZATION_MATRIX.md",
    "CORRECTED_STRATEGY_AUTHORIZATION_MATRIX.md",
]
SYNTHETIC_CSV = ["PAPER_TRADES.csv", "TRADE_EVENT_LOG.csv", "VIRTUAL_PORTFOLIO_SUMMARY.csv"]

needs_evidence = pytest.mark.skipif(
    not (ARCHIVE.is_dir() and REPORTS.is_dir()), reason="evidence folders not present"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rules() -> dict[str, tuple[str, ...]]:
    return {name: tuple(cls.PROVISIONAL_RULES) for name, cls, _ in AUTHORIZED_STRATEGY_SPECS}


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_final_artifacts", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ archive integrity


@needs_evidence
def test_archive_manifest_matches_archived_bytes() -> None:
    entries = [
        line.split(maxsplit=1)
        for line in (ARCHIVE / "MANIFEST.sha256").read_text().splitlines()
        if line.strip()
    ]
    assert len(entries) >= 25
    for digest, rel in entries:
        rel = rel.strip().lstrip("*")
        assert sha(ARCHIVE / rel) == digest, rel


@needs_evidence
@pytest.mark.parametrize(
    "rel",
    [
        "reports/paper_trading_live_session/STRATEGY_AUTHOR_FINAL_SIGNOFF.pdf",
        "reports/paper_trading_live_session/STRATEGY_SIGNAL_TIMELINE.csv",
        "COMBINED_PORTFOLIO_TRADES.csv",
    ],
)
def test_unedited_originals_are_byte_identical_in_place(rel: str) -> None:
    """PDF / timeline / dashboard-read legacy ledger are only labelled via companion notices."""
    assert sha(ROOT / rel) == sha(ARCHIVE / rel)


@needs_evidence
@pytest.mark.parametrize("name", BANNERED_MD)
def test_bannered_markdown_keeps_the_original_text_verbatim(name: str) -> None:
    rel = f"reports/paper_trading_live_session/{name}"
    current = (ROOT / rel).read_text(encoding="utf-8")
    original = (ARCHIVE / rel).read_text(encoding="utf-8")
    assert current.startswith("> [!CAUTION]")
    assert MARK in current.split("---", 1)[0]
    assert current.endswith(original), "body below the banner must equal the archived original"
    head = current[: len(current) - len(original)]
    assert V3 in head and "docs/STRATEGY_EXECUTION_STATUS.md" in head
    assert f"archive/evidence_originals_2026-10-29/{rel}" in head


@needs_evidence
@pytest.mark.parametrize("name", ["phase4_runbook.md", "phase4b_runbook.md"])
def test_phase4_runbooks_are_bannered_and_body_unchanged(name: str) -> None:
    current = (ROOT / "docs" / name).read_text(encoding="utf-8")
    original = (ARCHIVE / "docs" / name).read_text(encoding="utf-8")
    assert current.startswith("> [!CAUTION]") and MARK in current[:200]
    assert "README_PAPER_TRADING.md" in current[: len(current) - len(original)]
    assert current.endswith(original)


@needs_evidence
def test_baseline_manifest_is_not_bannered() -> None:
    text = (ROOT / "docs" / "baseline_v1_manifest.md").read_text(encoding="utf-8")
    assert MARK not in text


# ------------------------------------------------------------------ synthetic fixtures


@needs_evidence
@pytest.mark.parametrize("name", SYNTHETIC_CSV)
def test_synthetic_csvs_are_labelled_and_still_parse(name: str) -> None:
    path = REPORTS / name
    text = path.read_text(encoding="utf-8")
    for bad in (
        "AUTHENTIC_EXCHANGE_DATA",
        "VALID_TWO_SIDED_EXCHANGE_QUOTE",
        "Approved virtual equity ledger",
        "Net bottom-line virtual profit",
    ):
        assert bad not in text, (name, bad)
    rows = list(csv.DictReader(text.splitlines()))
    assert rows and all("SYNTHETIC_LIFECYCLE_TEST" in r["record_class"] for r in rows)
    assert (REPORTS / f"{name}.SYNTHETIC_NOTICE").read_text().startswith("SYNTHETIC FIXTURE")
    original = (ARCHIVE / "reports/paper_trading_live_session" / name).read_text(encoding="utf-8")
    assert len(rows) == len(list(csv.DictReader(original.splitlines())))


@needs_evidence
def test_synthetic_event_log_uses_fixture_tokens() -> None:
    rows = list(csv.DictReader((REPORTS / "TRADE_EVENT_LOG.csv").read_text().splitlines()))
    assert {r["source_data_type"] for r in rows} == {
        "SYNTHETIC_FIXTURE_NOT_MARKET_DATA",
        "SYNTHETIC_FIXTURE_ACCOUNTING",
    }


@needs_evidence
def test_virtual_summary_no_longer_claims_the_2pct_limit_as_enforced() -> None:
    text = (REPORTS / "VIRTUAL_PORTFOLIO_SUMMARY.csv").read_text(encoding="utf-8")
    assert "current live app limit is 10%" in text
    assert "SYNTHETIC_FIXTURE test figure" in text


@needs_evidence
def test_dashboard_legacy_reader_still_parses_the_untouched_root_ledger() -> None:
    legacy = dash_data.legacy_portfolio_csv_path(ROOT)
    trades, problems = dash_data.load_points_trades_csv(legacy)
    assert problems == [] and len(trades) > 0
    assert (ROOT / "COMBINED_PORTFOLIO_TRADES.csv.SUPERSEDED_NOTICE").read_text().startswith(
        "SUPERSEDED"
    )


@needs_evidence
def test_exposure_csv_marked_superseded_and_signoff_pdf_has_notice() -> None:
    rows = list(csv.DictReader((REPORTS / "AMBIGUOUS_RULE_EXPOSURE.csv").read_text().splitlines()))
    assert rows and all(r["record_status"].startswith("SUPERSEDED_DO_NOT_USE") for r in rows)
    notice = (REPORTS / "STRATEGY_AUTHOR_FINAL_SIGNOFF.pdf.SUPERSEDED_NOTICE").read_text()
    assert "DO NOT SIGN" in notice and "docs/AUTHOR_CONFIRMATION_REQUEST.md" in notice


@needs_evidence
def test_superseded_index_lists_every_stale_file_with_reason_and_replacement() -> None:
    text = (REPORTS / "SUPERSEDED_INDEX.md").read_text(encoding="utf-8")
    for name in [
        *BANNERED_MD,
        *SYNTHETIC_CSV,
        "STRATEGY_AUTHOR_FINAL_SIGNOFF.pdf",
        "AMBIGUOUS_RULE_EXPOSURE.csv",
        "STRATEGY_SIGNAL_TIMELINE.csv",
        "COMBINED_PORTFOLIO_TRADES.csv",
        "phase4_runbook.md",
    ]:
        assert name in text, name
    assert "184 trades" in text and "+813.60" in text and "no strategy is AUTHOR_APPROVED" in text


# ------------------------------------------------------------------ provisional rule text


def test_orb_text_no_longer_claims_official_highs_lows() -> None:
    rules = " ".join(_rules()["ORB"])
    assert "official previous-session high/low (wick extreme)" not in rules
    assert "regular 5-minute candles" in rules and "fallback" in rules
    assert "level_basis" in (ROOT / "configs/strategy_provisional/orb.yaml").read_text()
    why = yaml.safe_load((ROOT / "configs/strategy_provisional/orb.yaml").read_text())["level_basis"]
    assert "5-minute" in why["why"] and "OFFICIAL_DAILY_FALLBACK" in why["why"]


def test_irf_declares_equality_and_data_gap_rules() -> None:
    rules = " ".join(_rules()["IRF"])
    assert "EXACTLY at IRH" in rules and "close <= IRH" in rules
    assert "DATA_GAP" in rules and "disables IRF for the rest of the day" in rules


def test_pds_and_pdr_state_the_gates_that_cost_journal_matches() -> None:
    pds = " ".join(_rules()["PDS"])
    assert "2025-12-26" in pds and "first red candle" in pds and "consumes the approach" in pds
    assert "68 of the 116 gap-down" in pds
    assert "single dataset" in pds
    pdr = " ".join(_rules()["PDR"])
    assert "3 of 8" in pdr and "2025-10-07" in pdr and "roughly halves signals" in pdr
    assert "single dataset" in pdr


@needs_evidence
@pytest.mark.parametrize("name", ["pds", "orb", "irf", "pdr"])
def test_yaml_values_and_status_unchanged_only_why_text_edited(name: str) -> None:
    now = yaml.safe_load((ROOT / f"configs/strategy_provisional/{name}.yaml").read_text())
    old = yaml.safe_load(
        (ARCHIVE / f"strategy_provisional_{name}.yaml.orig").read_text(encoding="utf-8")
    )
    assert now.keys() == old.keys()
    for key in old:
        assert now[key]["value"] == old[key]["value"], key
        assert now[key]["status"] == old[key]["status"] == "PROVISIONAL", key


@needs_evidence
@pytest.mark.parametrize("name", ["pds", "orb", "irf", "pdr"])
def test_strategy_source_changed_only_inside_provisional_rules(name: str) -> None:
    """Code logic untouched: AST with the PROVISIONAL_RULES tuple (and docstrings) neutralised
    must be identical to the archived pre-edit module."""

    def neutral(src: str) -> str:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == (
                "PROVISIONAL_RULES"
            ):
                node.value = ast.Constant(value=None)
        return ast.dump(tree)

    now = (ROOT / f"src/trading_platform/strategies/{name}.py").read_text(encoding="utf-8")
    old = (ARCHIVE / f"strategy_{name}.py.orig").read_text(encoding="utf-8")
    assert neutral(now) == neutral(old)


# ------------------------------------------------------------------ author documents


def test_author_request_lists_every_provisional_rule_verbatim() -> None:
    text = DOC_AUTHOR.read_text(encoding="utf-8")
    for name, rules in _rules().items():
        for i, rule in enumerate(rules, 1):
            assert rule in text, f"{name} rule {i} missing from AUTHOR_CONFIRMATION_REQUEST.md"
            assert f"### {name}-{i}\n" in text
        assert f"### {name}-{len(rules) + 1}\n" not in text, f"{name} has extra rule sections"


def test_status_doc_lists_every_provisional_rule_and_the_tiers() -> None:
    text = DOC_STATUS.read_text(encoding="utf-8")
    for name, rules in _rules().items():
        for i, rule in enumerate(rules, 1):
            assert f"{i}. {rule}\n" in text, (name, i)
    for token in ("SHADOW_ONLY", "DISABLED", "RESEARCH_PAPER_PROVISIONAL", "--research-paper"):
        assert token in text
    assert RESEARCH_PAPER_ACK in text
    assert "No strategy is author-approved" in text
    assert "184 trades" in text and "+813.60" in text


def test_status_table_matches_live_config_tiers() -> None:
    cfg = yaml.safe_load((ROOT / "configs/live_paper.yaml").read_text())
    assert {k: v["tier"] for k, v in cfg["strategies"].items()} == {
        "PDS": "SHADOW_ONLY",
        "ORB": "SHADOW_ONLY",
        "IRF": "SHADOW_ONLY",
        "PDR": "SHADOW_ONLY",
        "RF": "DISABLED",
    }
    assert cfg["research_paper"]["enabled"] is False
    rows = [
        ln for ln in DOC_STATUS.read_text().splitlines() if ln.startswith("| ") and "SHADOW" in ln
    ]
    assert len(rows) == 4 and all("| No (none) |" in r for r in rows)
    assert any(ln.startswith("| RF") and "`DISABLED`" in ln for ln in DOC_STATUS.read_text().splitlines())


def test_author_request_says_rf_is_disabled_for_lack_of_exit_rules() -> None:
    text = DOC_AUTHOR.read_text(encoding="utf-8")
    assert "permanently disabled" in text and "no stop, target or trailing rule" in text


def test_author_request_effect_size_evidence_is_quoted() -> None:
    text = DOC_AUTHOR.read_text(encoding="utf-8")
    for token in (
        "37 signals and 5 of 8",
        "79 signals and 8 of 8",
        "2025-10-07",
        "2025-12-31",
        "2026-05-19",
        "matches 2 of 3",
        "matches 3 of 3",
        "184 trades",
        "+813.60",
        "-183.75",
        "In-sample",
    ):
        assert token in text, token
    assert "Antigravity" not in text


def test_code_locations_in_documents_resolve() -> None:
    pat = re.compile(r"`([\w/.\-]+\.(?:py|yaml)):(\d+)` \(([^)]+)\)")
    checked = 0
    for doc in (DOC_AUTHOR, DOC_STATUS):
        for rel, line, symbol in pat.findall(doc.read_text(encoding="utf-8")):
            path = ROOT / rel
            assert path.is_file(), rel
            lines = path.read_text(encoding="utf-8").splitlines()
            line_no = int(line)
            assert 1 <= line_no <= len(lines), (rel, line)
            sym = symbol.replace('\\"', '"')
            owned = rel.startswith(("src/trading_platform/strategies/", "configs/strategy_provisional"))
            if owned:  # edited only by string changes: must stay close to the cited line
                window = "\n".join(lines[max(0, line_no - 61) : line_no + 60])
                assert sym in window, (rel, line, symbol)
            else:  # other packages may move code: the symbol must still exist in the file
                assert sym in "\n".join(lines), (rel, symbol)
            checked += 1
    assert checked >= 60


# ------------------------------------------------------------------ generator


def test_generator_source_has_no_retired_engine_claims() -> None:
    text = GENERATOR.read_text(encoding="utf-8")
    for stale in (
        "swing pivot",
        "10.0 index points",
        "10pt zone",
        "360 verified",
        "288 FIFO",
        "Antigravity",
        "Verified No Real Broker Orders",
        "Current Engine Heuristic",
        "preceding 5 completed bars",
        "Lead Quant Engineer Verification",
    ):
        assert stale not in text, stale


def test_generator_refuses_repo_root_and_curated_folders(tmp_path: Path) -> None:
    gen = _load_generator()
    with pytest.raises(SystemExit):
        gen.safe_output_dir(Path("."), ROOT)
    with pytest.raises(SystemExit):
        gen.safe_output_dir(Path("reports/paper_trading_live_session"), ROOT)
    with pytest.raises(SystemExit):
        gen.safe_output_dir(Path("archive"), ROOT)
    assert gen.DEFAULT_OUTPUT_DIR != Path(".")
    assert gen.DEFAULT_OUTPUT_DIR.parts[:2] == ("reports", "paper_trading_live_session")
    out = gen.safe_output_dir(tmp_path / "scratch", ROOT)
    assert out.is_dir()


def test_generator_exposure_rows_come_from_current_provisional_rules() -> None:
    gen = _load_generator()
    rules = gen.provisional_rules_by_strategy()
    assert tuple(rules) == ("PDS", "ORB", "IRF", "PDR")
    assert rules == _rules()
    signals = [{"strategy": "PDS"}, {"strategy": "PDS"}, {"strategy": "ORB"}]
    trades = [{"strategy": "PDS", "points_pnl": 10.5}, {"strategy": "PDS", "points_pnl": -4.0}]
    rows = gen.build_exposure_rows(signals, trades, rules)
    by = {r["strategy_name"]: r for r in rows}
    assert by["PDS"]["engine_signals_in_window"] == 2
    assert by["PDS"]["portfolio_trades_in_window"] == 2
    assert by["PDS"]["portfolio_points_in_window"] == 6.5
    assert by["PDS"]["provisional_rule_count"] == len(rules["PDS"])
    assert all(r["signals_eligible_under_pdf_rules_only"] == 0 for r in rows)
    assert by["RF"]["provisional_rules"].startswith("PERMANENTLY DISABLED")
    assert "swing pivot" not in " ".join(r["provisional_rules"] for r in rows)


def _pdf_text(path: Path) -> str:
    """Decode ReportLab's ASCII85+Flate page streams and return the concatenated text ops."""
    data = path.read_bytes()
    out: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        body = m.group(1).strip()
        try:
            payload = body[:-2] if body.endswith(b"~>") else body  # strip the EOD marker once
            raw = zlib.decompress(base64.a85decode(payload, adobe=False))
        except Exception:
            try:
                raw = zlib.decompress(body)
            except Exception:
                continue
        out.extend(re.findall(rb"\((.*?)\) Tj", raw))
    return b"\n".join(out).decode("latin-1")


def test_generated_confirmation_pdf_has_one_line_per_rule_and_no_stale_footer(
    tmp_path: Path,
) -> None:
    gen = _load_generator()
    rules = gen.provisional_rules_by_strategy()
    exposure = gen.build_exposure_rows([], [], rules)
    pdf = tmp_path / "sheet.pdf"
    gen.build_confirmation_pdf(pdf, rules, exposure, "2025-09-01 to 2026-08-31", "V3_PDF_CORRECTED")
    assert pdf.read_bytes().startswith(b"%PDF")
    text = _pdf_text(pdf)
    assert "Antigravity" not in text and "Verified No Real Broker" not in text
    for strategy_rules in rules.values():
        assert f"{len(strategy_rules)} provisional rules" in text
    assert "PERMANENTLY DISABLED" in text
    assert "No strategy is author-approved" in text
    assert text.count("CONFIRM as implemented") == sum(len(r) for r in rules.values())
