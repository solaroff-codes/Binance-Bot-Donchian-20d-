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

- [`sweep.py`](sweep.py):
  - `generate_grid(base_config, **param_grids)` — Cartesian product of a
    base `StrategyConfig` over `{field_name: [values...]}`, via
    `dataclasses.replace`.
  - `run_param_sweep(base_config, **param_grids)` — runs every combination
    through `run_backtest` and returns one comparable row per combination
    (no trade-log CSVs written — this is for scanning many combinations
    quickly, not archiving every run).

`scripts/run_backtest.py` is the CLI entry point for a fixed set of configs:
```
python scripts/run_backtest.py                  # sweep every config in config/strategies/
python scripts/run_backtest.py GC_1day CL_1day   # run specific configs
```

`scripts/sweep_params.py` searches `range_max_pct` x `range_min_bars` per
instrument (the two parameters that gate whether `indicators.wyckoff` finds
a trading range at all) and writes `output/param_sweep.csv`.

Tests: `tests/test_backtest_engine.py` — covers win/loss/open outcomes,
the same-bar stop/target tie-break, and metric aggregation including a
regression test for the starting-equity drawdown fix (a loss-then-bigger-win
sequence must show the real intra-sequence dip, not $0).
`tests/test_backtest_sweep.py` — covers grid generation.

## Current results

Ran `scripts/sweep_params.py` across GC/CL/ES/NQ daily bars
(`range_max_pct` in 0.03-0.15, `range_min_bars` in 5-15). Findings, all
confirmed against real data rather than assumed:

- **CL**: starts producing signals at `range_max_pct=0.12` (1 signal) and
  `0.15` (2 signals) — both losses so far in this window.
- **GC**: exactly one combination (`range_max_pct=0.08`) produces a signal
  (a win, ~$21.5k on 1 contract) — `0.05` and everything above `0.08`
  produce none.
- **ES / NQ**: zero signals across the *entire* grid. Diagnosed directly
  (not just assumed): `indicators.wyckoff.detect_trading_ranges` **does**
  find ranges for both (1-7 of them depending on threshold) — the
  confluence chain in `strategy/signals.py` (breakout -> impulse leg ->
  pullback into the Fib zone -> confirming swing without re-entering the
  range) just never completes for any of them in this dataset. That's the
  strategy rule being strict, not a bug in range detection.
- **Why signal counts aren't monotonic in `range_max_pct`** (e.g. GC finds
  a signal at 0.08 but none at 0.12/0.15, even though *more* ranges exist
  at those looser thresholds): loosening the threshold changes which
  specific contiguous runs of bars qualify as "contracted" — previously
  separate short ranges can merge into fewer, longer ones as the bar-level
  condition relaxes. More/fewer ranges, and *different* ranges entirely,
  is expected behavior for a threshold-based contiguous-run detector, not
  a defect.

Conclusion so far: the confluence rule as written is quite selective (1-2
signals per instrument across ~250 daily bars, all within a narrow slice
of thresholds) and needs either looser confluence conditions or a longer
lookback / more instruments to evaluate meaningfully — one or two trades
per instrument is not enough to trust any win rate or profit factor
number yet.
