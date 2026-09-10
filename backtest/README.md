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

### Getting more data first

Single-contract history is capped by how long the current front-month
contract has actually been listed — asking for more `duration` doesn't
conjure more real bars (IBKR silently truncates rather than erroring).
Verified directly (`scripts/fetch_all_instruments.py --force`, now
requesting `"2 Y"` for both timeframes): daily bars nearly doubled (GC/CL/ES
~450-500 bars, up from ~250; NQ's current contract still caps at ~250 — it
simply hasn't been listed longer), and 1-hour bars grew ~8-10x (1270-2333
bars, up from ~238). Getting meaningfully more than that requires stitching
multiple historical contract-months into a continuous series, which is
still intentionally not built — `data/cache.py`'s contract-month keying
was designed to support adding that later, but it's a real chunk of new
work (roll-date detection across contracts, back-adjustment), not a
parameter tweak.

Added `config/strategies/{GC,CL,ES,NQ}_1hour.yaml` (same starting
parameters as the daily configs) so `scripts/sweep_params.py` now searches
both timeframes — it already picks up every `*.yaml` under
`config/strategies/` automatically.

### Sweep findings (`range_max_pct` 0.03-0.15, `range_min_bars` 5-15)

1-hour bars give a much larger, more meaningful sample than daily did:

- **CL 1-hour**, `range_max_pct=0.08`: 7 signals, 43% win rate, profit
  factor 7.3, +$9,276 total, max drawdown $762. Consistent across
  `range_min_bars` 5/8/10 (identical results — the qualifying runs are all
  longer than 10 bars already); `range_min_bars=15` narrows to 6 signals
  with an even better profit factor (10.0). The most promising combination
  found so far, though 6-7 trades is still a small sample.
- **GC 1-hour**, `range_max_pct=0.03`: 9-12 signals depending on
  `range_min_bars`, ~40-44% win rate, profit factor ~4-5, +$41k-55k total —
  the largest sample of any combination, and still solidly positive.
  `range_max_pct=0.05` (6-7 signals) is markedly worse (profit factor
  ~1.2-1.5), so GC's edge looks threshold-sensitive rather than robust
  across nearby settings — worth treating with some skepticism until
  tested on more history.
- **ES 1-hour**: only found at `range_max_pct=0.03` (3-4 signals), and
  every result there has a profit factor **below 1** (net losing) — no
  configuration found for ES looks tradeable yet.
- **NQ 1-hour**, `range_max_pct=0.03`: 2-3 signals, 100% win rate — take
  this with real skepticism, not confidence: a 100% win rate on 2-3 trades
  is not a meaningful sample, it's as likely to be luck as edge.
- Daily-bar results are still in the sweep for comparison but are weaker
  signal on their own now that hourly gives more trades to look at — see
  `output/param_sweep.csv` for the full table (`timeframe` column
  distinguishes them).
- The non-monotonic signal counts as `range_max_pct` loosens (e.g. GC
  1-hour: 12 signals at 0.03, 0 at 0.08) are still explained the same way
  as before: loosening the threshold changes *which* contiguous bar-runs
  qualify as contracted — it merges some previously-separate ranges rather
  than strictly finding more of them. Expected behavior for a threshold
  based contiguous-run detector, not a defect.

### Entry/exit sensitivity (`scripts/sweep_entry_exit.py`)

Held `range_max_pct` at each instrument's productive threshold (CL 0.08,
GC 0.03, both 1-hour) and swept `retracement_zone` x
`target_extension_ratio` around them. Full table in
`output/entry_exit_sweep.csv`. Clear, consistent pattern across both
instruments:

- **Wider retracement zones find more setups.** `(0.382, 0.786)` — the
  widest zone tested — gave the largest sample for both instruments (CL: 7
  signals, GC: 12 signals), vs. 5-7 for narrower zones like `(0.5, 0.618)`.
  Makes sense: a wider acceptable pullback range simply matches more real
  retracements.
- **Lower target ratios raise win rate; higher ones raise total profit
  (as long as win rate doesn't collapse).** For CL `(0.382, 0.786)`: win
  rate is fixed at 57% regardless of target (target only changes where you
  exit on a win, not whether you win) while profit factor climbs from 6.2
  at `target=1.0` to 17.2 at `target=2.0` (+$17,644 total). Same pattern
  for GC.
- **Standout combinations found:**
  - CL `(0.382, 0.786)` zone, `target_extension_ratio=2.0`: 7 signals, 57%
    win rate, profit factor **17.2**, +$17,644, $407 max drawdown —
    the best risk-adjusted result found for CL so far.
  - GC `(0.382, 0.618)` zone, `target_extension_ratio=1.0`: 9 signals,
    **78% win rate**, profit factor 7.8, +$34,318 — by far the highest win
    rate found for GC, at the more conservative (1:1) target.
  - GC `(0.382, 0.786)` zone, `target_extension_ratio=1.0`: 12 signals (the
    largest GC sample found anywhere in either sweep), 67% win rate,
    profit factor 5.0, +$37,528 — best balance of sample size and quality.
- Deep, narrow zones like `(0.618, 0.786)` (the closest thing to a
  "textbook" golden-ratio-only zone) underperformed the wider zones on
  both instruments — worth noting since `(0.5, 0.786)` was the arbitrary
  starting default in every config, and it is *not* the best zone found
  for either instrument.

None of this should be read as "GC `(0.382,0.618)`/`target=1.0` is the
strategy" — it's one grid search on ~1 year of 1-hour data for two
instruments. The pattern (wider zone finds more setups; target ratio
trades off win rate against payoff) is more trustworthy than any single
cell's exact numbers.

### Continuous contracts (more history)

See [`data/continuous.py`](../data/continuous.py) — front-month-only
history is capped at ~1-1.5 years of 1-hour data regardless of requested
duration (a single contract's real trading life). Building a back-adjusted
continuous series across multiple historical contract months is what
actually gets more history; see that module and its README section in the
root README for what's implemented and its own documented limitations.

### Conclusion so far

CL and GC both show a real, if modest, edge on 1-hour bars, and it
strengthens further once the entry/exit parameters are tuned around a
wider retracement zone (CL profit factor up to 17.2, GC win rate up to
78%). ES is consistently losing across every configuration tried. NQ's
sample is too small to conclude anything. Sample sizes (single digits to
low teens per cell) are still small for any of this — the honest next
step, now that continuous-contract history is available, is re-running
both sweeps against the longer series to see whether these patterns hold
up or were an artifact of a short, possibly unrepresentative window.
