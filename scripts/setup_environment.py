"""Environment bootstrap script to configure directories and verify requirements."""

import shutil
import sys
from pathlib import Path


def setup_directories() -> None:
    project_root = Path(__file__).resolve().parent.parent
    dirs = [
        "configs/strategies",
        "docs/strategy_specs",
        "docs/strategy_rulebooks",
        "data/raw",
        "data/processed",
        "data/reference",
        "data/cache",
        "data/samples",
        "reports/backtests",
        "reports/optimization",
        "reports/validation",
        "reports/paper_trading",
        "artifacts/experiments",
        "notebooks/research",
    ]
    for d in dirs:
        p = project_root / d
        p.mkdir(parents=True, exist_ok=True)
        gitkeep = p / ".gitkeep"
        if not gitkeep.exists() and not any(p.iterdir()):
            gitkeep.touch()

    # Ensure .env exists if .env.example exists
    env_example = project_root / ".env.example"
    env_file = project_root / ".env"
    if env_example.exists() and not env_file.exists():
        shutil.copy(env_example, env_file)
        print(f"Created default {env_file} from .env.example")

    print("Platform directory skeleton initialized successfully.")


if __name__ == "__main__":
    setup_directories()
    sys.exit(0)
