"""
Run the confluence strategy (Wyckoff range + Fibonacci retracement + swing
confirmation) against cached historical data for a given strategy config,
and print any signals produced.

This only generates signals — it does not simulate fills, P&L, or
performance metrics; that's backtest/'s job, not yet built.

Run from the project root with the venv active:
    python scripts/run_strategy.py GC_1day
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.cache import drop_untraded_bars, load_continuous, load_latest
from strategy.config import load_config
from strategy.signals import generate_signals


def main() -> None:
    config_name = sys.argv[1] if len(sys.argv) > 1 else "GC_1day"
    config = load_config(config_name)

    df = load_continuous(config.symbol, config.timeframe) if config.use_continuous else None
    if df is None:
        df = load_latest(config.symbol, config.timeframe)
    if df is None:
        print(
            f"No cached data for {config.symbol} / {config.timeframe} — "
            "run scripts/fetch_all_instruments.py "
            f"{'or scripts/build_continuous_contracts.py ' if config.use_continuous else ''}first."
        )
        sys.exit(1)

    df = drop_untraded_bars(df)  # drop IBKR pre-listing placeholder bars — see data/cache.py

    print(f"Loaded {len(df)} {config.timeframe} bars for {config.symbol}")
    print(f"Config: {config}\n")

    signals = generate_signals(df, config)
    print(f"{len(signals)} signal(s) found:\n")
    for s in signals:
        print(
            f"  {s.entry_ts}  {s.direction.upper():<5}  entry={s.entry_price:.2f}  "
            f"stop={s.stop_price:.2f}  target={s.target_price:.2f}  "
            f"(range {s.range_start_ts} -> {s.range_end_ts}, "
            f"impulse {s.impulse_start_price:.2f} -> {s.impulse_extreme_price:.2f}, "
            f"retracement {s.retracement_ratio})"
        )


if __name__ == "__main__":
    main()
