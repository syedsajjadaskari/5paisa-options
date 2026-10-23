# Independent Codex Audit — Executive Summary

Audit date: 2026-10-22 (Asia/Kolkata)  
Repository: `/Users/syedaskari/project/5paisa-options`  
Audited commit: `72756466930511318319e8a25b3fe49e2a821577` (`main`, equal to `origin/main`)  
Working tree: materially dirty before the audit; no user changes were discarded, committed, or pushed.

## Verdict

**FAIL. Supervised paper trading is not safe to enable. Keep the platform in `LIVE_READ_ONLY`; do not use it for live orders.**

The audit found working components and a green 182-test suite after remediation, but tests do not close the evidence gaps. Canonical strategy semantics remain partly discretionary, the saved production paper account is internally inconsistent, the historical option archive contains fabricated provider timestamps, special sessions are not represented, and no genuine timestamp-valid option-fill lifecycle has been demonstrated. The active old paper engine was stopped flat. The production configurations and parser fallback now fail closed with `paper_execution_enabled: false`.

## Critical/high findings corrected

- Wrapped the exposed raw py5paisa client at the broker boundary and blocked transactional methods, including the SDK's generic `order_request`; transport tests cover order, modify, and cancel paths without sending an order.
- Rejected missing, malformed, stale, and chronologically invalid provider timestamps. `/Date(1729571156000+0530)/` is **2024-10-22 09:55:56 IST**, not an October 2026 quote.
- Stopped option archival from substituting receipt time for missing provider time. Existing archive records with equal provider/receipt timestamps are quarantined as unusable.
- Corrected paper-accounting double charging, per-leg rounding, entry-charge treatment, and broker/portfolio/equity reconciliation.
- Made restart recovery fail closed on mixed state generations, cash mismatch, or position mismatch; made JSON writes atomic.
- Added a separate paper-execution gate, made the default and malformed/missing-config fallback read-only, and redirected the public launcher from the legacy engine to the audited CLI.
- Rebased next-bar targets/stops from the actual fill, removed double slippage, enforced strictly future option bars, and enforced the 14:30 entry cutoff.
- Separated official daily PDH/PDL/PDC/DMA references from intraday bars and excluded post-close bars from body-zone construction.
- Removed fabricated live strategy levels and unsupported strategy fallbacks. Canonical RF now emits no trade because its exit remains unspecified; the 60-point form is labeled experimental.
- Corrected the 2026 F&O holiday list from official NSE circulars and disabled research-chart generation that used hard-coded trades, prices, and correlation values.

## Historical claims versus audit result

| Measure | Prior claim | Independently reconstructed legacy ledger | Corrected mechanical candidate (`CORRECTED_V2`) |
|---|---:|---:|---:|
| Unarbitrated trades | 353 | 353 | 307 |
| Unarbitrated net NIFTY points | not consistently distinguished | +1,693.65 | +1,993.10 |
| FIFO trades | 302 | 302 | 271 |
| FIFO net NIFTY points | about +1,608.90 | +1,608.90 | +2,016.20 |
| FIFO profit factor | about 1.27 | 1.2686 | 1.3812 |
| FIFO maximum drawdown | about 635.3 | 635.25 | 568.50 |

The legacy FIFO figures reproduce exactly from the saved 353-trade ledger; this verifies ledger arithmetic, not current-code correctness. The corrected result changed incidentally because correctness fixes changed signals/fills. It is **not** a profitability claim, option-return model, optimization result, or validated canonical baseline. `BASELINE_V1` remains preserved, but the corrected engine cannot reproduce it by design.

## Paper/runtime evidence

- A previously running `PAPER_TRADE` process (PID 62986) was found executing old code. It was flat at ₹250,000 with no open position and was stopped cleanly at approximately 10:24:59 IST.
- The dashboard process (PID 63501) remains running; no paper engine process remains.
- Persisted production state is inconsistent: portfolio cash/equity ₹250,000 versus broker cash ₹102,429.65 and an old report starting balance of ₹250,000 with latest equity ₹102,429.65. New recovery checks reject it.
- One stored synthetic trade is qty 65, 136.00 to 174.50: gross ₹2,502.50, costs ₹72.85, net ₹2,429.65. It is not evidence of a genuine market fill.
- Independent hand calculation for qty 65, buy 101.00/sell 109.50 in the April-2026 regime: buy costs ₹26.53, sell costs ₹37.23, total ₹63.76. The prior portfolio path overcharged the entry leg by ₹26.53.
- `doctor` authenticated and returned a read-only spot response; the SDK printed `Success`, which means an API response message, not fresh executable data. `observe` encountered an SSL handshake timeout and produced no accepted tick. `replay` passed. `start` is refused by the safety gate. `status` reports stopped. `report`/`stop` were not run against the corrupt production state because both mutate persisted output/state; their paths are covered by code review and synthetic tests.

## Validation gates

Executed with repository `.venv` (Python 3.11):

```text
.venv/bin/python -m compileall -q src tests scripts  PASS
.venv/bin/pytest -q                              182 passed
.venv/bin/ruff check .                           PASS
git diff --check                                 PASS
```

The system Python 3.12 was also intentionally not treated as the project environment: it lacked `optuna`, `python`, and `ruff` on the invoked path.

## Required decision

Do not enable supervised paper execution. First obtain a clean, timestamp-authentic option archive; define and approve unresolved strategy rules; model special sessions; discard or explicitly migrate the corrupt paper state; and complete a monitored, read-only live-data soak. Real broker order execution remains prohibited.

