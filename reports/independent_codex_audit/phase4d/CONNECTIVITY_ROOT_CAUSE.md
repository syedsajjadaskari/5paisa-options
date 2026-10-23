# Phase 4D Connectivity Root Cause

Direct library callers previously did not load `.env`; the CLI did. This made the same cached-token path report `connected=False` outside the CLI. `FivePaisaBroker` now loads dotenv consistently without logging secret values.

Successful capture: `PHASE4D_20261022_122136`. Installed SDK: `py5paisa==0.7.21.2`. The adapter uses `FivePaisaClient(cred=...)`, cached `set_access_token`, `fetch_market_feed_scrip`, and `fetch_market_depth`; direct transactional methods remain blocked.

Read-only API validation succeeded for the persisted capture: the spot request was accepted by the parser and both depth envelopes persisted `Status=0`. A later 15:27 boundary retry timed out and remained rejected; no unbounded retry was performed.
