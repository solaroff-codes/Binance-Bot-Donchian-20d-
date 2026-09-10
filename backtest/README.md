# backtest/

Takes strategy signals (from `strategy/signals.py`) and simulates them
against cached historical data, producing a trade log and performance
metrics — win rate, profit factor, max drawdown, and a trade-level
Sharpe-like ratio. Supports sweeping multiple instruments/configs in one
run and writing results in a comparable format.

- [`engine.py`](engine.py):
  - `simulate_trades(df, signals, multiplier)` — walks each signal forward
    through subsequent bars to see whether its stop or target is hit
    first, producing a `Trade`. **Fill assumptions are documented in the
    module docstring** (entry at the signal's own best-case price, stop
    wins a same-bar tie with target, unresolved signals marked `'open'`) —
    this is a first-pass backtest, not a realistic fill/slippage model.
  - `compute_metrics(trades)` — aggregates a trade list into win rate,
    profit factor, avg R-multiple, `sharpe_r` (a trade-level Sharpe-like
    ratio on R-multiples — explicitly **not** an annualized time-series
    Sharpe), and max drawdown (measured from a $0 starting-equity
    baseline, not just from the first trade's own value).
- [`runner.py`](runner.py):
  - `run_backtest(config, start_date=None, end_date=None)` — one config,
    one instrument, returns `(trades, metrics)`.
  - `run_sweep(configs, ...)` — runs every config, writes
    `output/{config}_trades.csv` per config plus a single
    `output/summary.csv` / `summary.json` comparing all of them
    side by side. `output/` is gitignored (regenerable).

`scripts/run_backtest.py` is the CLI entry point:
```
python scripts/run_backtest.py                  # sweep every config in config/strategies/
python scripts/run_backtest.py GC_1day CL_1day   # run specific configs
```

Tests: `tests/test_backtest_engine.py` — covers win/loss/open outcomes,
the same-bar stop/target tie-break, and metric aggregation including a
regression test for the starting-equity drawdown fix (a loss-then-bigger-win
sequence must show the real intra-sequence dip, not $0).

## Current results

With the strategy modules' default (fairly tight) trading-range thresholds,
a sweep across GC/CL/ES/NQ daily bars currently finds 0 signals — none of
them have had a qualifying consolidation range recently (matches what
`indicators/wyckoff.py` already showed independently). This is the
correct, honest result given current market conditions, not a bug — the
full pipeline (range detection -> signal generation -> trade simulation ->
metrics) has been verified end-to-end against real data using loosened
range-detection thresholds, producing plausible trades with real P&L,
R-multiples, and drawdown.

Next step once you're ready to evaluate the strategy for real: sweep a
range of `range_max_pct` / `range_min_bars` / `retracement_zone` values per
instrument (not yet built — `run_sweep` takes an explicit list of configs,
so a parameter-grid generator is a natural next addition) rather than
relying on the single default config per instrument.
