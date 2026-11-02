# NIFTY options paper trading (VIRTUAL fills, read-only data)

A manually launched, fully automated NIFTY options **paper**-trading app. It reads live 5paisa
market data (read-only), runs the corrected strategies PDS M1, ORB M1, IRF M2 and PDR retest, and
books **virtual** fills in memory. Nothing is ever sent to a broker: there is no order, modify or
cancel call anywhere in the live path, and the 5paisa client is wrapped in a read-only allowlist.
There is no market-data database; only tiny journals under `runtime/` (gitignored).

**Important: all four strategies are `SHADOW_ONLY` by default** (some author rules are unresolved).
Signals are evaluated and journaled, but **no virtual fill is created** unless you explicitly opt
in to the research-paper mode (below). RF is permanently disabled.

Per-strategy status, every provisional (not author-approved) rule and the questions the author must
answer are in [docs/STRATEGY_EXECUTION_STATUS.md](docs/STRATEGY_EXECUTION_STATUS.md) and
[docs/AUTHOR_CONFIRMATION_REQUEST.md](docs/AUTHOR_CONFIRMATION_REQUEST.md). Older reports under
`reports/paper_trading_live_session/` describe superseded engine versions; see their
[SUPERSEDED_INDEX.md](reports/paper_trading_live_session/SUPERSEDED_INDEX.md).

## Setup

```bash
cd /Users/syedaskari/project/5paisa-options     # Python 3.11 venv in ./.venv
cp .env.example .env                              # then fill in your 5paisa credentials
```

`.env` is read by the broker layer only (never printed). Only the `FIVEPAISA_*` credential lines
matter to this app; the risk / `OPERATIONAL_MODE` / `EMERGENCY_KILL_SWITCH` lines in `.env.example`
are legacy and are **not read** (see "No kill switch"). The app configuration is
`configs/live_paper.yaml` (strict: unknown or missing keys are errors).

### What `--doctor` really does

```bash
./.venv/bin/python scripts/run_paper_trading.py --doctor        # exit 0 = all checks green, 1 = not
```

`--doctor` runs the whole startup sequence and stops before the trading loop. For a LIVE doctor
that is **not offline and not side-effect free**:

* it **logs in to 5paisa** (TOTP) and calls the historical-candle and market-feed REST APIs
  (read-only calls: calendar, previous-day OHLC, warm-up history, spot, option depth);
* it **downloads the public Scrip Master** into `data/cache/live_scrip_master/` (refreshed once
  per IST day; this cache is separate from the frozen `data/reference/scrip_master.parquet`);
* it **writes the session token cache** `data/cache/.token_cache.json` (a secret; gitignored,
  never printed);
* it takes the single-instance lock (`runtime/app.lock`) and may write `runtime/logs/errors.log`.

It writes no journal, checkpoint or snapshot. Run it before market hours to check credentials,
calendar, history and contracts; the spot/quote freshness checks are only meaningful while the
market is open (pre-open they only warn). `--doctor --replay auto` checks the replay instead and
makes no network call and no login.

## Commands

| command | what it does |
|---|---|
| `./.venv/bin/python scripts/run_paper_trading.py` | LIVE: read-only 5paisa data, tiers from config (default: all SHADOW_ONLY, no fills) |
| `... --research-paper` | the CLI half of the research opt-in (see below) |
| `... --replay auto --speed 0` | SIMULATED replay of the first historical demo day, instant |
| `... --replay 2025-08-18 --speed 60` | replay a date at 60 simulated minutes per real second |
| `... --doctor` | startup checks only (see above; add `--replay auto` to check the replay) |
| `... --acknowledge-recovery` | operator action after an uncertain restart (see Recovery) |
| `--config PATH`, `--runtime-dir PATH`, `--no-ui` | alternative config / runtime dir / plain text output |

### Dashboard (separate terminal, read-only)

```bash
streamlit run scripts/paper_trading_dashboard.py --server.address 127.0.0.1
```

Run it from the repository root. `.streamlit/config.toml` also pins `address = "127.0.0.1"` and
`headless = true`, so the page is reachable only from this machine and no browser is opened for you:
open the printed `http://127.0.0.1:8501` yourself. Do not expose it on a network (it shows your
virtual account).

The dashboard reads `$PAPER_RUNTIME_DIR` (default `<repo>/runtime`). **If you started the app with a
custom `--runtime-dir` (or `paths.runtime_dir`), start the dashboard with the same directory**,
otherwise it shows "No active session" or an old folder while the app runs fine:

```bash
PAPER_RUNTIME_DIR=/path/to/dir streamlit run scripts/paper_trading_dashboard.py --server.address 127.0.0.1
```

The app prints the plain `streamlit run ...` hint and does not know about `PAPER_RUNTIME_DIR`; a
non-default runtime dir is your responsibility. The sidebar shows the directory in use. After a
replay (`runtime/replay/`) the dashboard opens on the replay when no LIVE snapshot exists; otherwise
choose Source "Replay (simulated)" in the sidebar. A finished replay is shown as `REPLAY FINISHED`
(not as stale) and its connection as `SIMULATED (no broker)`.

If the trading process stops while a LIVE virtual position is open, the dashboard shows an error
banner at the top: `Engine not updating - virtual position <id> is OPEN and NOT monitored (no
stop/target). Restart the app to recover.`

### Research-paper opt-in (simulated fills on PROVISIONAL rules)

Fills are granted to a strategy only if **all four** hold. Exact steps:

1. Edit `configs/live_paper.yaml`:
   ```yaml
   research_paper:
     enabled: true
     acknowledgement: "I UNDERSTAND THESE RULES ARE PROVISIONAL AND NOT AUTHOR-APPROVED; SIMULATED FILLS ONLY"
     strategies: [ORB]          # only the strategies you want to allow to fill
   ```
   The acknowledgement must equal the constant `RESEARCH_PAPER_ACK` in
   `src/trading_platform/live/authorization.py` character for character (copy it from there if
   in doubt).
2. Start with the flag: `./.venv/bin/python scripts/run_paper_trading.py --research-paper`.

If any of the four (enabled, exact phrase, strategy listed, CLI flag) is missing, the tiers stay
`SHADOW_ONLY` and startup step 2 prints exactly which condition failed. Granted fills are labelled
`RESEARCH_PAPER_PROVISIONAL` in every record. The full text of the provisional rules is printed
at start **only** in that case (long rules may be shortened on screen); the dashboard lists them
per strategy at all times. AUTHOR_APPROVED is never granted by this build. A **replay** is not this
opt-in: it always simulates fills for the four strategies and says so (`REPLAY-SIMULATED`).

## Startup sequence (each step shown with a check or cross; secrets are never printed)

1 app + trading date (IST) · 2 config + tier/authorization summary · 3 single-instance lock
(before login) · 4 broker login · 5 exchange calendar (holiday: says so, exit 0) · 6 previous
trading-day OHLC · 7 warm-up history (insufficient: fail closed, exit 1) · 8 reference levels
(problems/warnings verbatim) · 9 contracts (count, expiry, real lot size) · 10 spot exchange time
and quote freshness (stale spot in market hours blocks; pre-open only warns; a non-blocking
warning is shown with a `!`) · 11 virtual account and recovery decision · 12 stream loop. Any
blocking failure stops with the reason and trades nothing.

## What the terminal shows

A live Rich dashboard (about 2 Hz; plain status lines with `--no-ui` or when piped), top to bottom:

1. **Header**: date and time, `Broker`, connection, mode, market, engine state, NIFTY and data age,
   candle trust, and a one-line **reference levels** row (PDH, PDL, PDC, EMA200, IRH, IRL; `n/a` for
   a level that is not available).
2. **Active Position**, directly under the header so it stays visible on a 24-row terminal: strategy
   label (for example `ORB M1`), contract, direction, stop / target distance, the option BID, the
   **P&L (net est.)** (exit at bid minus slippage, both charges included) and the reason as a sentence
   (`NIFTY completed a breakout confirmation above the previous-day high (PDH)`) with the raw rule id
   as a secondary detail. The account panel's **Unrealized (gross)** is the plain bid mark without
   charges, so the two numbers differ by design. If the bid is old the panel says
   `Option BID 87.90 (age 42s STALE)` and flags the P&L `(stale bid)`; with no valid bid it says
   `n/a - no valid bid`. `EXIT PENDING` is shown loudly if an exit trigger fires without a valid bid.
3. Account + the two option books, then the strategy table (tier, state, last signal or rejection).
4. Recent events, sized to the rows that are left (nothing is shown when the terminal is too short;
   panels degrade to one-line summaries rather than hiding the position).

A `VIRTUAL TRADE OPENED` and `VIRTUAL TRADE CLOSED` panel is printed for each fill after the event
lines it belongs to (entry = ask + slippage, exit = bid - slippage, charges applied once). If the
provider never returns a verifiable completed candle the strategies show `BLOCKED`; sampled ticks
are never substituted. After 15:35 the app exits when flat; with an unresolved position it keeps
running and shows it.

In a **replay** the header reads `Broker: none (SIMULATED)` and the connection `SIMULATED (no
broker)`: no login or network call exists, so the terminal never claims `CONNECTED` and the
displayed time is the simulated clock.

## No kill switch; how to stop; where the limits are

There is **no kill switch** and no remote stop. `OPERATIONAL_MODE`, `EMERGENCY_KILL_SWITCH`,
`MAX_DAILY_LOSS_INR`, `MAX_OPEN_POSITIONS` in `.env.example` and `configs/risk.yaml` /
`configs/live_trading.yaml` belong to the legacy backtest/config layer and are **not read by the
paper app**. Stop the app with `Ctrl+C` or `kill -TERM <pid>` (see Safe stop). The real limits are
in `configs/live_paper.yaml`: virtual capital, `max_open_positions: 1`, `max_lots`, premium
allocation, daily loss limit (`max_daily_loss_pct: 10` of the session-opening equity), entry cutoff
14:30 and square-off 15:15. Breaching the daily limit puts the engine in `HALTED_RISK`
(no new entries; an open position is closed with reason RISK when a valid bid exists).

## Candle trust (fail closed) and what it means for a late start

A vendor 5-minute candle is accepted only if it matches spot ticks that **this process** sampled
within 20 s of the candle close. Consequences you must know:

* If you **start (or restart) after about 09:20**, or a data gap of more than 20 s spans a candle
  close, the earlier candles cannot be cross-checked, because those ticks can never be recreated.
  The strategies then show `BLOCKED` and may stay `BLOCKED` for the rest of the day (the startup
  catch-up cannot complete). Start the app before 09:15 to avoid this.
* The weaker alternative is an explicit operator decision in `configs/live_paper.yaml`:
  `candles.require_sample_crosscheck: false` accepts a vendor candle older than
  `candles.uncrosschecked_min_age_seconds` (default 60) **without** a tick cross-check. It is off by
  default; do not enable it unless you accept unverified candles.
* After an authentication problem the broker layer re-logs in on its own: five quick attempts with
  backoff (5 s, 15 s, 45 s, 2 min, 5 min), then one attempt every 5 minutes for the rest of the
  session. Until the login succeeds the connection reads `DISCONNECTED`/`DEGRADED`; an open virtual
  position stays `EXIT_PENDING` if its exit triggers while there is no valid bid, and it is never
  closed on a made-up price. A transient network blip may still trigger a full TOTP login (a throttling risk on the broker side).

## 0-DTE / expiry day

The default config has `execution.allow_expiry_day: true`, `min_days_to_expiry: 0` and
`expiry_policy: NEAREST`. On the weekly expiry day the app **can therefore buy same-day-expiry
at-the-money options until 14:30**, which move very fast. To avoid that set in
`configs/live_paper.yaml`:

```yaml
execution:
  allow_expiry_day: false      # exclude contracts expiring today -> the next expiry is used
  min_days_to_expiry: 1        # optional: require at least N days to expiry
```

## Safe stop

`Ctrl+C` (or `kill -TERM`): the loop stops, the engine writes `runtime/checkpoint.json`, the
terminal prints what is unresolved (`virtual position preserved, NOT closed`), the candle worker
and provider are closed and the lock is released. Pressing `Ctrl+C` again during shutdown does not
skip the checkpoint. An open position is **never** closed or invented on exit; restarting the app
the same day resumes it (no duplicate entry), subject to the candle-trust limitation above. A
network call already in flight finishes first (bounded by SDK timeouts, up to about a minute in the
worst case, during which the terminal may look stuck). Even `kill -9` is safe: the checkpoint is
rewritten at every state change.

Exit codes: `0` clean (finished, holiday, operator stop, doctor green, recovery acknowledged) ·
`1` startup/doctor check failed or acknowledgement refused · `2` usage/config error · `3` another
instance holds the lock · `5` unexpected error after startup (state was checkpointed; see
`runtime/logs/errors.log`).

## Recovery

An uncertain restart (overnight position, corrupt checkpoint, ledger/checkpoint mismatch) gives
`RECOVERY_REQUIRED`: no new entries, nothing auto-resumed, startup stops with the reason.
Procedure:

1. Stop everything (`Ctrl+C`); make sure no second instance runs (exit code 3 means the lock is held).
2. Read `runtime/journal/trades.csv`, `runtime/journal/events_*.jsonl` and the reason printed at
   startup. If a virtual position is recorded in the checkpoint, decide by hand what it was worth;
   the app will **not** close it or book its P&L.
3. Run `./.venv/bin/python scripts/run_paper_trading.py --acknowledge-recovery`. It prints the
   details, renames `runtime/checkpoint.json` to `runtime/checkpoint.recovered-<timestamp>.json`,
   writes `runtime/recovery_ack.json` (bound to today's ledger) and journals the event. It refuses a
   healthy resumable checkpoint.
4. Start normally. If today's closed trades are already in `trades.csv` and the checkpoint is
   gone, the acknowledgement lets the next start run as a fresh session rebuilt from the ledger
   (the executed-signal memory is not recoverable). If the ledger changes afterwards, acknowledge
   again. A set-aside checkpoint is evidence only; it is never resumed.

## Replay (SIMULATED, NOT MARKET DATA)

`--replay` drives the real engine, levels and strategies with real historical NIFTY 5-minute bars,
**simulated ticks and simulated option quotes** from a simple price model. The banner `SIMULATED —
NOT MARKET DATA` is printed at start and end, every record carries `label=REPLAY_SIMULATED`, and
tiers are simulated (`REPLAY-SIMULATED`, not an opt-in) for the four strategies (RF off). It uses
its own root `runtime/replay/` (lock, `journal/trades.csv`, events, `checkpoint.json`,
`live_state.json`) and starts fresh on every run (only the known replay files are deleted). It
never touches the live ledger or checkpoint, makes no login and no network call, and reads
`data/reference/scrip_master.parquet` **read-only** (the live cache under `data/cache/` is separate;
`paths.scrip_master` in the config is used only by the replay). Replay details are approximations: lot size
comes from today's Scrip Master, not the lot size valid on the replayed date, and the contract
symbol is invented (`SIMULATED NIFTY ...`). `--replay auto` picks the first demo day with signals,
so its headline P&L is plumbing output, **not a trading result**.

## Runtime files (`runtime/`, gitignored)

`journal/trades.csv` (closed virtual trades, authoritative P&L) · `journal/events_YYYYMMDD.jsonl`
(decision events only, no market data) · `checkpoint.json` · `recovery_ack.json` ·
`live_state.json` (dashboard snapshot) · `app.lock` · `logs/errors.log` (redacted) ·
`replay/` (same layout for the replay). Outside `runtime/`: `data/cache/live_scrip_master/` and
`data/cache/.token_cache.json` (see `--doctor`).

## Blocked / not supported

Real orders of any kind; modify/cancel; AUTHOR_APPROVED tier; RF; more than one open position;
option-chain or tick archives; multi-day carry of an open position; auto-recovery of an uncertain
state; a kill switch.

## Limitations (honest)

* The live 5paisa path has been tested only against fakes in this repository (no network was
  used). Same-day vendor candle behaviour is unverified: the provider fails closed (strategies
  `BLOCKED`) when it cannot prove a candle complete (see "Candle trust").
* Quote freshness uses the local receive time of REST depth snapshots, not exchange time.
* A pending entry is not checkpointed; a crash between signal and fill loses that entry (its
  signal id is consumed, so there is no automatic re-entry).
* The engine state name can still read `RUNNING` during the short post-close grace; the terminal
  then says `market closed` instead of `market open`.
* Replay option prices come from a simple model; the replay shows plumbing, not P&L expectations.
* Charge rates come from the repository's cost calculator and could not be verified offline.
* Evidence files under `reports/paper_trading_live_session/` describe earlier engine versions; use
  this README, `configs/live_paper.yaml` and `docs/live_app_architecture.md` as the current truth.
