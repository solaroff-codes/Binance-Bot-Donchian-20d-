"""
Test whether requiring the trendline cascade strategy AND the Wyckoff/Fib/
Elliott Wave confluence strategy to agree (same direction, entries close
in time) beats either alone.

The Wyckoff strategy's range-detection parameters (range_max_pct,
range_min_bars) are widened relative to each instrument's default 1-hour
config (config/strategies/{symbol}_1hour.yaml) — a tighter default rarely
fires, and the interesting question here is whether a MORE PERMISSIVE
Wyckoff signal, used only as a confirming filter on top of the trendline
strategy's own precise entries, adds value. Widening by X%:
range_max_pct *= (1+X/100) [more permissive threshold],
range_min_bars *= (1-X/100), floor 2 [fewer bars required] — the same two
"how permissive is range detection" knobs identified as the main lever in
every earlier sweep this project has run. retracement_zone and
target_extension_ratio are left at each config's default; widening
everything at once would make it hard to attribute any effect.

Confluence definition: a trendline signal is "confirmed" if a Wyckoff
signal in the SAME direction has an entry_ts within max_gap_hours of it.
The trendline signal's own entry/stop/target is used for the resulting
trade — it's the more mechanically precise of the two — so Wyckoff here
acts purely as a structural confirming filter, not the entry trigger.
This is a specific, stated design choice, not the only reasonable one.

Run from the project root with the venv active:
    python scripts/run_combined_confluence.py
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import compute_metrics, simulate_trades
from data.cache import drop_untraded_bars, load_latest
from data.contracts import load_instruments
from strategy.config import StrategyConfig
from strategy.config import load_config as load_wyckoff_config
from strategy.signals import generate_signals
from strategy.trendline_config import load_config as load_trendline_config
from strategy.trendline_signals import generate_trendline_signals

WIDEN_LEVELS = [30, 40, 50]
MAX_GAP_HOURS = 24
SYMBOLS = ["GC", "CL", "ES", "NQ"]


def widen_wyckoff_config(base: StrategyConfig, pct: int) -> StrategyConfig:
    return replace(
        base,
        range_max_pct=round(base.range_max_pct * (1 + pct / 100), 6),
        range_min_bars=max(2, round(base.range_min_bars * (1 - pct / 100))),
    )


def find_confirmed(primary_signals, confirming_signals, max_gap_hours: float) -> list:
    by_direction = {"long": [], "short": []}
    for s in confirming_signals:
        by_direction[s.direction].append(pd.Timestamp(s.entry_ts))
    for d in by_direction:
        by_direction[d].sort()

    confirmed = []
    for s in primary_signals:
        ts = pd.Timestamp(s.entry_ts)
        candidates = by_direction[s.direction]
        if any(abs((c - ts).total_seconds()) <= max_gap_hours * 3600 for c in candidates):
            confirmed.append(s)
    return confirmed


def main() -> None:
    rows = []

    for symbol in SYMBOLS:
        multiplier = float(load_instruments()[symbol]["multiplier"])

        trendline_config = load_trendline_config(symbol)
        bias_dfs = {}
        ok = True
        for tf in trendline_config.bias_timeframes:
            df = load_latest(symbol, tf)
            if df is None:
                ok = False
                break
            bias_dfs[tf] = drop_untraded_bars(df)
        trigger_df = load_latest(symbol, trendline_config.trigger_timeframe)
        if not ok or trigger_df is None:
            print(f"{symbol}: missing data, skipping")
            continue
        trigger_df = drop_untraded_bars(trigger_df)

        trendline_signals = generate_trendline_signals(bias_dfs, trigger_df, trendline_config)
        trendline_trades = simulate_trades(trigger_df, trendline_signals, multiplier=multiplier)
        trendline_metrics = compute_metrics(trendline_trades)
        rows.append({"symbol": symbol, "variant": "trendline_alone", **trendline_metrics})
        print(f"{symbol}: trendline_alone -> {trendline_metrics['num_closed']} closed, "
              f"PF={trendline_metrics['profit_factor']}")

        wyckoff_base = load_wyckoff_config(f"{symbol}_1hour")

        for pct in WIDEN_LEVELS:
            widened = widen_wyckoff_config(wyckoff_base, pct)
            wyckoff_signals = generate_signals(trigger_df, widened)
            wyckoff_trades = simulate_trades(trigger_df, wyckoff_signals, multiplier=multiplier)
            wyckoff_metrics = compute_metrics(wyckoff_trades)
            rows.append({
                "symbol": symbol, "variant": f"wyckoff_widened_{pct}pct",
                "range_max_pct": widened.range_max_pct, "range_min_bars": widened.range_min_bars,
                **wyckoff_metrics,
            })
            print(f"{symbol}: wyckoff_widened_{pct}pct (max_pct={widened.range_max_pct}, "
                  f"min_bars={widened.range_min_bars}) -> {len(wyckoff_signals)} signals, "
                  f"PF={wyckoff_metrics['profit_factor']}")

            confirmed = find_confirmed(trendline_signals, wyckoff_signals, MAX_GAP_HOURS)
            confirmed_trades = simulate_trades(trigger_df, confirmed, multiplier=multiplier)
            confirmed_metrics = compute_metrics(confirmed_trades)
            rows.append({
                "symbol": symbol, "variant": f"confluence_{pct}pct",
                "num_trendline_signals": len(trendline_signals), "num_wyckoff_signals": len(wyckoff_signals),
                **confirmed_metrics,
            })
            print(f"{symbol}: confluence_{pct}pct -> {confirmed_metrics['num_closed']} closed, "
                  f"PF={confirmed_metrics['profit_factor']}")

    df = pd.DataFrame(rows)
    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "combined_confluence.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)

    with pd.option_context("display.max_columns", None, "display.width", 220):
        print("\n" + df.to_string(index=False))
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
