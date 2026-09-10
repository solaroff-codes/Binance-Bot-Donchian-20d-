"""
Run the backtest engine against one or more strategy configs and print/save
performance metrics.

Usage:
    python scripts/run_backtest.py                   # sweep every config in config/strategies/
    python scripts/run_backtest.py GC_1day CL_1day    # run specific configs

Results are written to backtest/output/: summary.csv and summary.json
comparing every config run, plus a {config}_trades.csv trade log per config.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.runner import OUTPUT_DIR, run_sweep
from strategy.config import CONFIG_DIR, load_config


def main() -> None:
    names = sys.argv[1:]
    if not names:
        names = sorted(p.stem for p in CONFIG_DIR.glob("*.yaml"))
    if not names:
        print(f"No strategy configs found in {CONFIG_DIR}")
        sys.exit(1)

    configs = [load_config(name) for name in names]
    summary = run_sweep(configs)

    with pd.option_context("display.max_columns", None, "display.width", 160):
        print(summary.to_string(index=False))

    print(f"\nWritten to {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
