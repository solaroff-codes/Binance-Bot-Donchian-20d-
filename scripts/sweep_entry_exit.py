"""
Sweep retracement_zone x target_extension_ratio around the range thresholds
that scripts/sweep_params.py already found to be productive, to see how
sensitive the edge is to entry/exit timing rather than to whether a range
is found at all.

Base cases (from the range-threshold sweep, both on 1-hour bars):
  - CL: range_max_pct=0.08 (7 signals, profit factor 7.3 at defaults)
  - GC: range_max_pct=0.03 (10 signals, profit factor 3.9 at defaults)

Everything else stays at each instrument's config default except
retracement_zone and target_extension_ratio, which this sweeps.

Run from the project root with the venv active:
    python scripts/sweep_entry_exit.py
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.sweep import run_param_sweep
from strategy.config import load_config

RETRACEMENT_ZONE_GRID = [
    (0.382, 0.618),
    (0.5, 0.618),
    (0.5, 0.786),
    (0.382, 0.786),
    (0.618, 0.786),
]
TARGET_EXTENSION_RATIO_GRID = [1.0, 1.272, 1.618, 2.0]

BASE_CASES = [
    ("CL_1hour", {"range_max_pct": 0.08}),
    ("GC_1hour", {"range_max_pct": 0.03}),
]

DISPLAY_COLUMNS = [
    "symbol",
    "timeframe",
    "retracement_zone",
    "target_extension_ratio",
    "num_signals",
    "num_closed",
    "win_rate",
    "profit_factor",
    "total_pnl_dollars",
    "max_drawdown_dollars",
]


def main() -> None:
    results = []
    for config_name, overrides in BASE_CASES:
        base_config = replace(load_config(config_name), **overrides)
        df = run_param_sweep(
            base_config,
            retracement_zone=RETRACEMENT_ZONE_GRID,
            target_extension_ratio=TARGET_EXTENSION_RATIO_GRID,
        )
        results.append(df)

    combined = pd.concat(results, ignore_index=True)

    with pd.option_context(
        "display.max_columns", None, "display.width", 200, "display.max_rows", None
    ):
        print(combined[DISPLAY_COLUMNS].to_string(index=False))

    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "entry_exit_sweep.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
