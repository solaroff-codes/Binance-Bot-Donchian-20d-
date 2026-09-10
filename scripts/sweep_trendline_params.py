"""
Sweep the trendline cascade strategy's own knobs: touch_tolerance_pct x
break_buffer_pct x target_r_multiple (cheap — reuses one walk-forward
CascadeState per structure/instrument, see strategy/trendline_signals.py's
module docstring for why that matters), crossed with a few candidate
cascade *structures* (which timeframes set bias vs trigger — this part IS
expensive, one CascadeState per structure/instrument).

Structures tried, all starting from config/trendline_strategies/{symbol}.yaml:
  - default : bias=(month,week,day,4h) -> trigger=1h
  - no_month: bias=(week,day,4h)       -> trigger=1h   (monthly data is thin for GC/NQ)
  - reactive: bias=(day,4h)            -> trigger=1h   (least filtered)
  - 4h_trigger: bias=(month,week,day)  -> trigger=4h   (coarser, fewer bars)

Run from the project root with the venv active:
    python scripts/sweep_trendline_params.py                # every config
    python scripts/sweep_trendline_params.py GC CL           # specific symbols
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import compute_metrics, simulate_trades
from data.cache import drop_untraded_bars, load_continuous, load_latest
from data.contracts import load_instruments
from strategy.trendline_config import CONFIG_DIR, TrendlineStrategyConfig, load_config
from strategy.trendline_signals import compute_cascade_state, signals_from_state

STRUCTURES = {
    "default": (("1 month", "1 week", "1 day", "4 hours"), "1 hour"),
    "no_month": (("1 week", "1 day", "4 hours"), "1 hour"),
    "reactive": (("1 day", "4 hours"), "1 hour"),
    "4h_trigger": (("1 month", "1 week", "1 day"), "4 hours"),
}

TOUCH_TOLERANCE_GRID = [0.001, 0.003, 0.005]
BREAK_BUFFER_GRID = [0.001, 0.003, 0.005]
TARGET_R_GRID = [1.0, 1.5, 2.0, 3.0]

DISPLAY_COLUMNS = [
    "symbol", "structure", "touch_tolerance_pct", "break_buffer_pct", "target_r_multiple",
    "num_signals", "num_closed", "win_rate", "profit_factor", "total_pnl_dollars", "max_drawdown_dollars",
]


def _load(symbol: str, timeframe: str, use_continuous: bool) -> pd.DataFrame | None:
    df = load_continuous(symbol, timeframe) if use_continuous else None
    if df is None:
        df = load_latest(symbol, timeframe)
    return drop_untraded_bars(df) if df is not None else None


def sweep_one_symbol(base_config: TrendlineStrategyConfig) -> pd.DataFrame:
    rows = []
    multiplier = float(load_instruments()[base_config.symbol]["multiplier"])

    for structure_name, (bias_timeframes, trigger_timeframe) in STRUCTURES.items():
        structure_config = replace(
            base_config, bias_timeframes=bias_timeframes, trigger_timeframe=trigger_timeframe
        )

        bias_dfs = {}
        missing = False
        for tf in bias_timeframes:
            df = _load(base_config.symbol, tf, base_config.use_continuous)
            if df is None:
                missing = True
                break
            bias_dfs[tf] = df
        trigger_df = _load(base_config.symbol, trigger_timeframe, base_config.use_continuous)
        if missing or trigger_df is None:
            rows.append({"symbol": base_config.symbol, "structure": structure_name, "error": "missing data"})
            continue

        state = compute_cascade_state(bias_dfs, trigger_df, structure_config)

        for touch in TOUCH_TOLERANCE_GRID:
            for buffer in BREAK_BUFFER_GRID:
                for target_r in TARGET_R_GRID:
                    combo_config = replace(
                        structure_config,
                        touch_tolerance_pct=touch,
                        break_buffer_pct=buffer,
                        target_r_multiple=target_r,
                    )
                    signals = signals_from_state(state, combo_config)
                    trades = simulate_trades(state.trigger, signals, multiplier=multiplier)
                    metrics = compute_metrics(trades)
                    rows.append(
                        {
                            "symbol": base_config.symbol,
                            "structure": structure_name,
                            "touch_tolerance_pct": touch,
                            "break_buffer_pct": buffer,
                            "target_r_multiple": target_r,
                            **metrics,
                        }
                    )

    return pd.DataFrame(rows)


def main() -> None:
    names = sys.argv[1:] or sorted(p.stem for p in CONFIG_DIR.glob("*.yaml"))
    if not names:
        print(f"No trendline strategy configs found in {CONFIG_DIR}")
        sys.exit(1)

    results = [sweep_one_symbol(load_config(name)) for name in names]
    combined = pd.concat(results, ignore_index=True)

    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "trendline_param_sweep.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_path, index=False)
    print(f"Written {len(combined)} rows to {out_path}")

    # Print just the top-5-by-profit-factor cells with a meaningful sample.
    meaningful = combined[combined["num_closed"] >= 10].dropna(subset=["profit_factor"])
    top = meaningful.sort_values("profit_factor", ascending=False).head(20)
    with pd.option_context("display.max_columns", None, "display.width", 220):
        print("\nTop 20 by profit factor (num_closed >= 10):")
        print(top[DISPLAY_COLUMNS].to_string(index=False))


if __name__ == "__main__":
    main()
