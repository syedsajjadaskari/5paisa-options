"""CLI for the Phase 4E strictly read-only collector."""

from __future__ import annotations

import argparse
import json

from trading_platform.data.read_only_collector import ReadOnlyCollector


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or inspect the read-only market-data collector")
    sub = parser.add_subparsers(dest="command", required=True)
    observe = sub.add_parser("observe", help="Run bounded read-only collection")
    observe.add_argument("--cycles", type=int, default=None)
    sub.add_parser("status", help="Print persisted collector heartbeat")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(ReadOnlyCollector.inspect_state(), indent=2, default=str))
        return 0
    collector = ReadOnlyCollector()
    print(json.dumps(collector.run(max_cycles=args.cycles), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

