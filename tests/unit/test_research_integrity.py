"""Regression checks preventing unsupported research claims from being regenerated."""

import pytest

from scripts.run_phase3c_research import generate_research_charts


def test_legacy_hard_coded_research_charts_fail_closed() -> None:
    """Hard-coded analytical values must never be exported as authentic evidence."""
    with pytest.raises(RuntimeError, match="hard-coded trade overlays"):
        generate_research_charts([], None, None, None, [], [])
