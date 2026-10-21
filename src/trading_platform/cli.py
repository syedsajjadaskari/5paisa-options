"""Command Line Interface for the Trading Platform."""

import argparse

from trading_platform import __version__
from trading_platform.core.config import ConfigManager
from trading_platform.strategies.registry import registry


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Multi-Strategy Algorithmic Trading Platform (NSE / 5paisa)"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: status
    subparsers.add_parser("status", help="Show system status and configuration")

    # Command: strategies
    subparsers.add_parser("strategies", help="List registered strategies")

    args = parser.parse_args()

    if args.command == "status":
        cfg = ConfigManager()
        app_cfg = cfg.get_app_config()
        print(f"Trading Platform v{__version__}")
        print(f"Environment: {app_cfg.get('environment')}")
        print(f"Timezone:    {app_cfg.get('timezone')}")
        print(f"Default Broker: {app_cfg.get('broker', {}).get('default_provider')}")
    elif args.command == "strategies":
        strategies = registry.list_strategies()
        print("Registered Strategies:")
        for s in strategies:
            print(f" - {s}")
        if not strategies:
            print(" (No strategies registered yet)")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
