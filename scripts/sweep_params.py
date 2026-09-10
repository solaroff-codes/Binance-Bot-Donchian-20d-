"""
Search a parameter grid per instrument to find trading-range thresholds
that actually produce signals, rather than relying on one hand-picked
default per instrument.

Sweeps range_max_pct x range_min_bars — the two parameters that gate
whether indicators.wyckoff finds a trading range at all, and therefore
whether any signal can exist — for every instrument config under
config/strategies/, holding every other parameter (retracement_zone,
target_extension_ratio, fractal_n, range_window) at that config's default.
Other parameters are deliberately not swept in this first pass: they only
matter once a range is found at all, and crossing all of them at once
would produce hundreds of hard-to-read rows per instrument for no benefit
until we know where the range-detection threshold actually is.

Run from the project root with the venv active:
    python scripts/sweep_params.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.sweep import run_param_sweep
from strategy.config import CONFIG_DIR, load_config

RANGE_MAX_PCT_GRID = [0.03, 0.05, 0.08, 0.12, 0.15]
RANGE_MIN_BARS_GRID = [5, 8, 10, 15]

DISPLAY_COLUMNS = [
    "symbol",
    "timeframe",
    "range_max_pct",
    "range_min_bars",
    "num_signals",
    "num_closed",
    "win_rate",
    "profit_factor",
    "total_pnl_dollars",
    "max_drawdown_dollars",
]


def main() -> None:
    config_names = sorted(p.stem for p in CONFIG_DIR.glob("*.yaml"))
    if not config_names:
        print(f"No strategy configs found in {CONFIG_DIR}")
        sys.exit(1)

    results = [
        run_param_sweep(
            load_config(name),
            range_max_pct=RANGE_MAX_PCT_GRID,
            range_min_bars=RANGE_MIN_BARS_GRID,
        )
        for name in config_names
    ]
    combined = pd.concat(results, ignore_index=True)

    with pd.option_context(
        "display.max_columns", None, "display.width", 200, "display.max_rows", None
    ):
        print(combined[DISPLAY_COLUMNS].to_string(index=False))

    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "param_sweep.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
