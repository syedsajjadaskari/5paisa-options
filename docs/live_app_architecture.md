# Live paper-trading application — architecture & contract

Paper only. Read-only broker. No real orders, ever. This document describes the application as
built; shared types live in `src/trading_platform/live/models.py` and
`live/providers/base.py`. How to run and operate it: `README_PAPER_TRADING.md`.

## Flow

```
MANUAL START (scripts/run_paper_trading.py)
  -> startup sequence (config, lock, login, calendar, prev-day OHLC, warm-up, levels, contracts, quotes, account/recovery)
  -> poll loop (1 thread owns the SDK; serialized calls):  PollResult(spot, quotes)
  -> candle loop: provider-COMPLETED 5m candle -> strategies.on_bar -> SignalRecord
  -> authorization gate (tier) -> risk/entry checks -> contract -> fresh ask AFTER signal -> VIRTUAL fill (ask+slip)
  -> monitoring: spot vs underlying SL/TP, bid MTM, 15:15 square-off, daily-loss
  -> VIRTUAL exit (bid-slip) -> TradeRecord -> trades.csv + events.jsonl + checkpoint -> snapshot -> UI
```

Exit codes (`live/app.py`): `0` clean (session finished, holiday / nothing to do, operator stop —
a preserved virtual position is reported, not an error —, doctor all-green, recovery acknowledged) ·
`1` startup / doctor check failed (fail closed, no trading) or an acknowledgement was refused ·
`2` usage or configuration error · `3` another instance holds the single-instance lock ·
`5` unexpected error after startup (state was checkpointed, see `runtime/logs/errors.log`).

## Runtime files (all under `runtime/`, gitignored, tiny, NO market-data archive)

| file | writer | purpose |
|---|---|---|
| `runtime/journal/trades.csv` | engine (append, fsync) | one row per CLOSED virtual trade (`TradeRecord.csv_columns()`); authoritative P&L ledger; LIVE sessions only |
| `runtime/journal/events_YYYYMMDD.jsonl` | engine (append) | compact decision events only: signals, rejections, entries, exits, state changes, risk, recovery. NEVER ticks/quotes/candles |
| `runtime/checkpoint.json` | atomic tmp+fsync+rename | restart recovery: account, open position, executed signal ids, risk flags, session id |
| `runtime/checkpoint.recovered-<timestamp>[-n].json` | `--acknowledge-recovery` (rename) | an uncertain checkpoint set aside as evidence; never resumed |
| `runtime/recovery_ack.json` | `--acknowledge-recovery` | operator acknowledgement bound to today's ledger; lets a start with a lost checkpoint run as START_FRESH rebuilt from the ledger |
| `runtime/live_state.json` | atomic tmp+rename, ~1 Hz | snapshot for the dashboard & `status` (see schema) |
| `runtime/app.lock` | fcntl flock | single-instance lock (LIVE) |
| `runtime/logs/errors.log` | redacting logger | minimal operational errors |
| `runtime/replay/…` | same writers, different root | REPLAY (simulated) output with its **own** `app.lock`, `checkpoint.json`, `live_state.json`, `journal/` — never mixed with the LIVE ledger |

Outside `runtime/` (gitignored `data/cache/`): `data/cache/live_scrip_master/` (the live app's
daily-refreshed public Scrip Master) and `data/cache/.token_cache.json` (session token, a secret).
The frozen `data/reference/scrip_master.parquet` is **never written**; only the replay reads it.

Replay mode MUST use a separate root (`runtime/replay/`) and every record carries
`label=REPLAY_SIMULATED`. Replay never touches the live account/checkpoint/trades.csv.

## Config: `configs/live_paper.yaml` (single source of live config; `config/` dir is retired)

Loaded and strictly validated by `live/config.py`: unknown keys are errors, required keys must be
present, there are no silent defaults except the documented optional keys below.

```yaml
account: {starting_capital_inr: 250000, max_open_positions: 1, max_lots: 2,
          max_premium_allocation_pct: 20.0, max_daily_loss_pct: 10.0}   # of session-opening equity
session: {entry_cutoff_ist: "14:30", square_off_ist: "15:15", market_open_ist: "09:15", market_close_ist: "15:30"}
execution: {entry_slippage_points: 0.5, exit_slippage_points: 0.5,     # separately configured
            max_spread_points: 5.0, max_spread_pct: 2.0, max_quote_age_seconds: 5.0,
            expiry_policy: NEAREST,        # NEAREST | NEXT
            min_days_to_expiry: 0,
            allow_expiry_day: true,        # optional, default true; false == exclude contracts expiring today
            strike_selection: ATM, strike_step: 50}
strategies:            # tier per strategy; deny-by-default; fills are never granted from this block
  PDS: {enabled: true, method: METHOD_1, tier: SHADOW_ONLY}
  ORB: {enabled: true, method: METHOD_1, tier: SHADOW_ONLY}
  IRF: {enabled: true, method: METHOD_2, tier: SHADOW_ONLY}
  PDR: {enabled: true, method: RETEST_BOUNCE, tier: SHADOW_ONLY}
  RF:  {enabled: false, tier: DISABLED}
research_paper:        # explicit, separately-approved opt-in. OFF by default.
  enabled: false
  acknowledgement: ""  # must equal RESEARCH_PAPER_ACK (live/authorization.py) character for character
  strategies: []       # which provisional strategies may fill, e.g. [PDS, ORB]
candles: {source: PROVIDER, fail_closed: true, provider_poll_delay_seconds: 3, provider_wait_max_seconds: 90,
          spot_crosscheck_tolerance_points: 15.0,
          require_sample_crosscheck: true,        # optional, default true (fail closed)
          uncrosschecked_min_age_seconds: 60}     # optional; only used when the cross-check is off
polling: {interval_seconds: 1.0, option_depth_interval_seconds: 1.0}
paths: {runtime_dir: runtime, scrip_master: data/reference/scrip_master.parquet}   # scrip_master: REPLAY only
```

* `execution.allow_expiry_day: true` with `expiry_policy: NEAREST` means the app can open
  same-day-expiry (0-DTE) at-the-money contracts until the 14:30 entry cutoff. Set it to `false`
  (and/or raise `min_days_to_expiry`) to avoid that.
* `paths.scrip_master` is read only by the **replay** provider (read-only). The live path downloads
  and caches its own Scrip Master under `data/cache/live_scrip_master/`.
* `candles.require_sample_crosscheck: true` (default): a vendor candle is accepted only if a spot
  tick sampled by this process lies within 20 s of its close. After a restart the tick history is
  empty, so a start after ~09:20 (or a >20 s gap at a candle close) can leave strategies `BLOCKED`
  for the day. `false` accepts a vendor candle older than `uncrosschecked_min_age_seconds` without
  the cross-check (weaker; explicit operator decision).

Tier values (`ExecutionTier`): `DISABLED` (never evaluated, never fills — RF) · `SHADOW_ONLY`
(evaluated, signals journaled, **no fills**; the default for every enabled strategy) ·
`RESEARCH_PAPER` (simulated fills on PROVISIONAL rules; only through the opt-in below, label
`RESEARCH_PAPER_PROVISIONAL`) · `AUTHOR_APPROVED` (reserved, never granted by this build). The config
file itself may only say `SHADOW_ONLY` or `DISABLED`. A replay assigns simulated fills to the four
strategies for itself (`REPLAY_SIMULATED`); that is **not** the opt-in and is displayed as
`REPLAY-SIMULATED (not an opt-in)`.

Authorization rules (`live/authorization.py`, one function `authorize(strategy, cfg) -> AuthDecision`):
RF/disabled -> DISABLED; default every enabled strategy -> SHADOW_ONLY (signals journaled, **no fills**);
RESEARCH_PAPER only if `research_paper.enabled` AND exact acknowledgement phrase AND the CLI flag
`--research-paper` AND strategy listed. Every fill from that tier is labelled
`RESEARCH_PAPER_PROVISIONAL`. AUTHOR_APPROVED is never granted by this build.

There is **no kill switch**: the process is stopped with Ctrl+C / `kill -TERM` (state is
checkpointed, an open virtual position is preserved, never closed or invented). The inert
`OPERATIONAL_MODE` / `EMERGENCY_KILL_SWITCH` / risk variables in `.env.example` and the legacy
`configs/risk.yaml` / `live_trading.yaml` are not read by the app.

## Money rules (single implementation in `live/ledger.py` / `live/risk.py` / `live/costs.py`)

* Entry fill = observed ASK + entry slippage. Exit fill = observed BID − exit slippage (floor 0.05).
* Charges: `IndianOptionsCostCalculator.calculate_single_leg_costs(..., apply_slippage=False)` BUY at entry, SELL at exit — each applied exactly once. Slippage is in the fill price only.
* Premium budget: `(ask+entry_slip)*qty + entry_charges <= max_premium_allocation_pct * session_open_equity`; lots = min(max_lots, floor(budget/(lot cost))), ≥1 else reject `PREMIUM_BUDGET`. Cash must also cover it.
* Daily loss: breach when `equity(marked to bid) − session_open_equity <= −max_daily_loss_pct * session_open_equity` → state HALTED_RISK, close any open position with reason RISK (needs a valid bid → else EXIT_PENDING).
* Cutoff 14:30 and square-off 15:15 are applied on candle CLOSE / wall-clock decision time, not bar-open label. A candle that closes at 14:30:00 or later cannot open a trade; entry fill timestamp must be < 14:30.
* Unrealized (gross) = (bid − entry_premium)*qty; equity = cash + invested + unrealized. The position's `net_pnl_estimate` additionally applies exit slippage and both charges, so it differs from the account's gross unrealized by design.
* Quote used for an entry must have `received_at >= signal.decision_time` and age ≤ max_quote_age at the moment of use.
* One position max. Strategies in SHADOW_ONLY still evaluate and journal signals.
* EXIT_PENDING: set when a trigger fires but no valid fresh bid; keep position, block new entries, retry every poll; if still pending at the 15:30 close the state stays EXIT_PENDING with a loud banner and the position persists in the checkpoint (never invent a fill).

## Snapshot schema `runtime/live_state.json` (`schema_version: 1`)

```json
{
 "schema_version": 1, "written_at": "ISO-IST", "sim_time": null, "heartbeat_age_note": "reader computes age from written_at",
 "session": {"session_id": "...", "date": "YYYY-MM-DD", "mode": "LIVE|REPLAY", "label": "...", "pid": 123,
             "started_at": "...", "engine_state": "STARTING|READY|RUNNING|HALTED_RISK|RECOVERY_REQUIRED|BLOCKED|SHUTTING_DOWN|STOPPED",
             "state_reason": "...", "market_status": "OPEN|PRE_OPEN|CLOSED", "provider": {}},
 "connection": {"broker": "5paisa | SIMULATED (no broker)", "status": "CONNECTED|DEGRADED|DISCONNECTED|SIMULATED",
                "last_valid_quote_at": "ISO", "data_age_seconds": 0.7, "stale": false,
                "candle_source": "PROVIDER_COMPLETED", "candle_trusted": true, "notes": []},
 "spot": {"price": 0.0, "exchange_time": "ISO", "received_at": "ISO", "day_high": 0, "day_low": 0, "prev_close": 0},
 "current_candle": {"open_time": "ISO", "o":0,"h":0,"l":0,"c":0, "source": "SAMPLED_TICKS", "authoritative": false},
 "quotes": {"CE": {"symbol":"","scrip_code":0,"strike":0,"expiry":"","bid":0,"ask":0,"bid_qty":0,"ask_qty":0,"server_time":"ISO","received_at":"ISO"}, "PE": {...}},
 "levels": {"PDH":0,"PDL":0,"PDC":0,"support_zone":[lo,hi],"resistance_zone":[lo,hi],"IRH":null,"IRL":null,"EMA200":null,"levels_date":"","sources":{},"notes":"..."},
 "candles": [{"t":"ISO open","o":0,"h":0,"l":0,"c":0,"source":"PROVIDER_COMPLETED"}],   // current session, bounded (<=80)
 "markers": [{"kind":"SIGNAL|ENTRY|EXIT","t":"ISO","price":0.0,"strategy":"","trade_id":null,"text":"",
              "premium":0.0, "exit_reason":"TARGET (EXIT only)", "direction":"BULLISH|BEARISH (ENTRY only)"}],   // bounded (<=200)
 "account": {"starting_capital":0,"cash":0,"equity":0,"realized_pnl":0,"session_realized_pnl":0,"unrealized_pnl":0,"invested":0,
             "session_open_equity":0,"trades_today":0,"wins_today":0,"losses_today":0,"daily_loss_limit":0,"daily_loss_used":0},
 "risk": {"state":"OK|HALTED","reason":"","max_daily_loss_inr":0,"entry_cutoff":"14:30","square_off":"15:15","cutoff_passed":false},
 "strategies": [ StrategyMonitor as dict: name, label, method, tier, auth_label, state, last_evaluated_candle, levels,
                 conditions_satisfied, conditions_failed, last_signal, last_rejection, trades_today, pnl_today, note ],
 "position": null | {"trade_id","signal_id","strategy","strategy_label","method","direction","direction_label","option_type","symbol",
                     "strike","expiry","lots","quantity","lot_size","entry_time","entry_spot","entry_bid","entry_ask","entry_premium",
                     "entry_charges","premium_invested","current_bid","current_bid_age_seconds","bid_is_stale","stop_spot","target_spot",
                     "dist_to_stop","dist_to_target","gross_pnl","net_pnl_estimate","pnl_pct","holding_seconds","status",
                     "entry_reason","rule_id","reason_text","entry_warnings","label","exit_pending_reason","exit_pending_reason_text",
                     "exit_pending_since","exit_pending_detail","remaining_cash"},
 "pending_entry": null | {"signal_id","strategy","symbol","since","detail"},   // a signal waiting for a fill; NOT a position
 "recovery": null | {"reason","details","checkpoint_position"},                 // set when the start was RECOVERY_REQUIRED
 "last_event": {"kind":"","t":"ISO","text":""},
 "recent_events": [{"t":"ISO","kind":"","text":""}],   // last 50, bounded
 "last_closed_trade": null | TradeRecord-as-dict
}
```

Notes for readers:

* `written_at` is the **wall clock** of the writer in LIVE; in REPLAY it is also the wall clock and
  the simulated clock is carried by `sim_time` (null in LIVE). Readers must not compare a replay's
  simulated times with the wall clock. The dashboard additionally uses the snapshot file's
  modification time for a replay and treats `engine_state` `STOPPED` as "replay finished".
* In REPLAY `connection.broker` is `SIMULATED (no broker)` and `connection.status` is `SIMULATED`: no
  login, no network, no broker. Consumers must not present it as a live 5paisa connection.
* `bid_is_stale` is true when the held contract's last valid bid is older than
  `execution.max_quote_age_seconds` (or there is none); the P&L figures are then marked
  `(stale bid)` by the terminal and dashboard. `strategy_label`, `reason_text`, `direction_label`
  are the human wording (from `live/labels.py`); `rule_id` is the raw engine id, a secondary detail.
* Markers: the engine writes `exit_reason` on EXIT and `direction` on ENTRY; readers fall back to
  the leading token / CE-PE token of `text` and prefer the ledger marker when both exist.

`live/snapshot.py` owns read/write; readers tolerate a missing/partial/stale file and report
staleness from `written_at` (LIVE) or the file mtime (REPLAY). No other process writes it.

## Terminal and dashboard contract

* **Terminal** (`live/terminal.py`) renders only from the snapshot dict: header (with the
  reference-levels row), Active Position **directly under the header**, account + quotes, strategy
  table, events sized to the remaining terminal height (panels degrade to one-line summaries;
  the position is never cropped away). OPENED / CLOSED panels are printed after the event lines
  they belong to. Replay shows `Broker: none (SIMULATED)`.
* **Dashboard** (`dashboard/` + `scripts/paper_trading_dashboard.py`) is a read-only Streamlit page
  bound to localhost (`.streamlit/config.toml`, `--server.address 127.0.0.1`); it reads
  `$PAPER_RUNTIME_DIR` (default `runtime/`). A stale / stopped LIVE snapshot with an OPEN or
  EXIT_PENDING position shows a top-level error banner (the position is not monitored).

## Module ownership

| module | contents |
|---|---|
| `live/config.py`, `authorization.py`, `contracts.py`, `costs.py`, `ledger.py`, `risk.py` | config load/validate, tier gate, ContractResolver (ATM via `options.moneyness.find_atm_strike`, expiry via policy, lot size from master), fill/charge helpers, VirtualAccount, RiskGate |
| `live/journal.py`, `checkpoint.py`, `lock.py`, `snapshot.py` | trades CSV + events JSONL, atomic checkpoint + recovery decision (+ recovery acknowledgement), single-instance lock, snapshot read/write |
| `live/engine.py`, `levels.py`, `labels.py` | SessionEngine (event-driven, clock-injected), reference-context builder, shared human-readable labels |
| `live/providers/fivepaisa.py` (+ `brokers/fivepaisa.py`, `data/live_market_feed.py` parse helpers) | live read-only provider |
| `live/providers/replay.py`, `live/app.py` | simulated replay provider; top-level run loop/startup/shutdown |
| `live/terminal.py` | Rich live display |
| `trading_platform/dashboard/` + `scripts/paper_trading_dashboard.py` | Streamlit dashboard |
| `strategies/*.py`, `configs/strategy_provisional/*.yaml` | strategy rules |
| `backtesting/*`, `scripts/run_backtest.py` | backtest (research only) |
