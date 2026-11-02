# archive/evidence_originals_2026-10-29/

Byte-exact copies, taken on 2026-10-29 BEFORE any relabelling, of every evidence file that was
edited, bannered or labelled by the evidence-integrity fix. Nothing here is imported, linted or collected
by pytest. `MANIFEST.sha256` holds the SHA-256 of each file (`shasum -a 256 -c MANIFEST.sha256` from this folder);
`tests/live/test_fix_docs.py` re-checks it.

Why: the sign-off PDF, the authorization matrices and `AMBIGUOUS_RULE_EXPOSURE.csv` describe a retired engine
(5-bar swing pivot, 10 pt zone buffer, 360 signals / 288 FIFO trades) and the lifecycle-test CSVs were labelled as
authentic exchange data. The in-place files now carry SUPERSEDED / SYNTHETIC labels; the originals are kept here
unchanged so nothing is lost. See `reports/paper_trading_live_session/SUPERSEDED_INDEX.md` for every file, the reason
and its replacement.

Layout mirrors the repo for `reports/`, `docs/` and the root `COMBINED_PORTFOLIO_TRADES.csv`. `*.orig` files are the
pre-edit strategy modules, provisional YAMLs (before the rule-text corrections) and the old sign-off generator.

Restore one file: `cp -p archive/evidence_originals_2026-10-29/<path> <path>` (from the repo root).
