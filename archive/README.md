# archive/

Retired legacy code, tests, configs and duplicate generated reports, moved here on 2026-10-28 by the
repository cleanup. Nothing in this tree is imported, linted (`ruff extend-exclude`) or collected by
pytest (`norecursedirs`). It is kept so that nothing is lost and every move is reversible.

* `legacy_2026-10-28/` mirrors the original repo-relative layout (`src/...`, `scripts/...`, `tests/...`,
  `config/`, `configs/`, and root-level duplicate reports).
* The authoritative list, with reasons, replacements and a one-line restore command per item, is
  `docs/CLEANUP_MANIFEST.md`.
* Restore one item: `mkdir -p <parent> && mv archive/legacy_2026-10-28/<path> <path>` (from the repo root).
* Restore everything: `rsync -a archive/legacy_2026-10-28/ ./`
* A complete pre-cleanup copy of the working tree is also at
  `/Users/syedaskari/project/5paisa-options_SNAPSHOT_2026-10-28`.
* `legacy_2026-10-28/scripts/verify_installation.py` and `legacy_2026-10-28/tests/unit/test_phase4e2_context.py`
  are COPIES of the originals (the live versions were rewritten/trimmed); all other entries were moved.
