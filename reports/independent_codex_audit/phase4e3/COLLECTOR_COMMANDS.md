# Read-only collector commands for the next supported NSE session

Run from the repository root:

```bash
cd /Users/syedaskari/project/5paisa-options
.venv/bin/python scripts/run_phase4e_collector.py observe
```

Inspect the persisted heartbeat from another terminal:

```bash
cd /Users/syedaskari/project/5paisa-options
.venv/bin/python scripts/run_phase4e_collector.py status
```

Stop safely with `Ctrl-C` in the collector terminal. The collector handles
SIGINT/KeyboardInterrupt by persisting `GRACEFUL_SHUTDOWN_REQUESTED`, writing a
`STOPPED` heartbeat, releasing the process lock, and creating no orders or
fills. Do not use a forced kill unless the process is unresponsive and the
heartbeat has been reviewed.

Offline deterministic validation command used for this phase:

```bash
.venv/bin/python scripts/run_phase4e3_offline_validation.py
```

This offline command must not be used as evidence of live collection.
