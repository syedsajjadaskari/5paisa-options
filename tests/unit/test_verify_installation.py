"""scripts/verify_installation.py is an offline, network-free check of the live app."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_installation.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_installation_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verification_passes_offline(capsys) -> None:
    module = _load()
    assert module.run_verification() is True
    out = capsys.readouterr().out
    assert "RESULT: PASS" in out
    assert "correctly absent (retired)" in out


def test_retired_modules_are_not_importable() -> None:
    module = _load()
    for name in module.RETIRED_MODULES:
        try:
            present = importlib.util.find_spec(name) is not None
        except ModuleNotFoundError:
            present = False
        assert not present, f"{name} should live under archive/, not src/"
