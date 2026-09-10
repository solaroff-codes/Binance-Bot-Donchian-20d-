"""
Run the multi-timeframe trendline cascade strategy (strategy/trendline_signals.py)
against cached data and print a full backtest (trade log + metrics), reusing
backtest/engine.py unchanged — TrendlineSignal deliberately shares field
names with strategy.signals.Signal so simulate_trades() works on it as-is.

Usage:
    python scripts/run_trendline_backtest.py                # every config in config/trendline_strategies/
    python scripts/run_trendline_backtest.py GC CL           # specific configs by name
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import asdict

import pandas as pd

from backtest.engine import compute_metrics, simulate_trades
from data.cache import drop_untraded_bars, load_continuous, load_latest
from data.contracts import load_instruments
from strategy.trendline_config import CONFIG_DIR, TrendlineStrategyConfig, load_config
from strategy.trendline_signals import generate_trendline_signals


def _load(symbol: str, timeframe: str, use_continuous: bool) -> pd.DataFrame | None:
    df = load_continuous(symbol, timeframe) if use_continuous else None
    if df is None:
        df = load_latest(symbol, timeframe)
    if df is None:
        return None
    return drop_untraded_bars(df)


def run_one(config: TrendlineStrategyConfig) -> tuple[list, dict] | None:
    bias_dfs = {}
    for tf in config.bias_timeframes:
        df = _load(config.symbol, tf, config.use_continuous)
        if df is None:
            print(f"  missing {config.symbol}/{tf} — run scripts/fetch_all_instruments.py first")
            return None
        bias_dfs[tf] = df

    trigger_df = _load(config.symbol, config.trigger_timeframe, config.use_continuous)
    if trigger_df is None:
        print(f"  missing {config.symbol}/{config.trigger_timeframe} — run scripts/fetch_all_instruments.py first")
        return None

    signals = generate_trendline_signals(bias_dfs, trigger_df, config)
    multiplier = float(load_instruments()[config.symbol]["multiplier"])
    trades = simulate_trades(trigger_df, signals, multiplier=multiplier)
    metrics = compute_metrics(trades)
    return trades, metrics


def main() -> None:
    names = sys.argv[1:] or sorted(p.stem for p in CONFIG_DIR.glob("*.yaml"))
    if not names:
        print(f"No trendline strategy configs found in {CONFIG_DIR}")
        sys.exit(1)

    output_dir = Path(__file__).resolve().parent.parent / "backtest" / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = []

    for name in names:
        config = load_config(name)
        print(f"\n=== {name} ({config.symbol}, bias={list(config.bias_timeframes)}, "
              f"trigger={config.trigger_timeframe}) ===")
        result = run_one(config)
        if result is None:
            summary_rows.append({"config": name, "error": "missing data"})
            continue
        trades, metrics = result

        if trades:
            trade_log_path = output_dir / f"trendline_{name}_trades.csv"
            pd.DataFrame([asdict(t) for t in trades]).to_csv(trade_log_path, index=False)
            print(f"  trade log: {trade_log_path}")

        for key in ["num_signals", "num_closed", "win_rate", "profit_factor", "total_pnl_dollars", "max_drawdown_dollars"]:
            print(f"  {key}: {metrics[key]}")

        summary_rows.append({"config": name, "error": None, **metrics})

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(output_dir / "trendline_summary.csv", index=False)
    print(f"\nSummary written to {output_dir / 'trendline_summary.csv'}")


if __name__ == "__main__":
    main()
