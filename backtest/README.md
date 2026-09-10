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
    `enforce_single_position=True` (default): signals are processed in
    entry order and any signal that would open while a prior trade is
    still open is skipped — a real account can't take a second independent
    position on top of one it's already in. This was a real bug found
    while testing the trendline cascade strategy (see below): it fires
    often enough that overlapping "trades" (e.g. two same-direction
    entries an hour apart) were being counted as independent bets,
    inflating apparent trade count and profit factor. The sparser
    confluence strategy never had enough signal density to expose this.
    `signals: list[SignalLike]` — a `Protocol`, not a concrete import of
    `strategy.signals.Signal`, so both strategies' signal types work here
    without this module depending on either.
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

### Continuous contracts (more history) — supersedes the sections above

**Important:** everything above this point was measured *before* two real
fixes landed: (1) `data/cache.drop_untraded_bars()` — zero-volume
placeholder bars (IBKR reference data for periods before a contract was
actively traded, flat OHLC) were up to 45% of a single contract's raw
fetch and would look like trivial zero-volatility "trading ranges" to
`indicators.wyckoff`; and (2) continuous-contract stitching
(`data/continuous.py`), built via `scripts/build_continuous_contracts.py`.
Every number in the sections above should be treated as superseded by
this one — re-ran both sweeps (`scripts/sweep_params.py`,
`output/param_sweep.csv` now has a `source` column: `single` vs
`continuous`) with both fixes in place, across all 4 instruments x 2
timeframes x 2 sources = 16 configs.

**Continuous-series depth per instrument** (`scripts/build_continuous_contracts.py`,
stitching every expired contract IBKR still lists plus the current front
month, fixed 5-day pre-expiry roll, additive back-adjustment):

| Symbol | Daily bars (continuous) | Daily start | 1-hour bars (continuous) |
|---|---|---|---|
| GC | 398 | 2025-02-10 | 2,869 |
| CL | 737 | 2023-09-25 | 4,441 |
| ES | 739 | 2023-09-21 | 3,519 |
| NQ | 621 | 2024-03-18 | 3,318 |

IBKR's own expired-contract retention varies a lot by instrument — CL and
ES reach back 3 years, GC and NQ only about 1-1.5. Not something this
project controls; it's whatever contract records IBKR still has on file.

**What changed with more (and cleaner) data:**

- **More signals almost everywhere.** Max signal count found in the full
  grid, continuous vs single-contract: CL 1-hour 22 vs 11, ES 1-hour 7 vs
  4, NQ 1-hour 5 vs 3. GC 1-hour is the one exception (8 vs 9 — roughly a
  wash).
- **ES and NQ daily bars go from zero signals ever to actually finding
  some.** With single-contract daily data, ES and NQ found **0 signals
  across the entire grid** — full stop. With continuous daily data, both
  find 2 signals (at `range_max_pct=0.05`). Still a tiny sample, but a
  real qualitative change: it's no longer "this rule never fires on daily
  bars for these instruments."
- **CL is the most consistent result across both datasets.**
  `range_max_pct=0.08` with `range_min_bars` 8-15, 1-hour bars: profit
  factor ~14 and win rate 50-60% in *both* the single-contract and
  continuous data, with reasonable samples (5-6 trades each). Two
  different (overlapping but not identical) datasets landing on the same
  threshold with similar quality is the closest thing to a validated
  pattern found so far.
- **GC's standout single-contract result doesn't fully replicate.** The
  best single-contract cell (`range_max_pct=0.03`, `range_min_bars=15`:
  80% win rate, profit factor 24.5) is notably stronger than the best
  comparable continuous-data cell (`range_max_pct=0.03`,
  `range_min_bars=5`: 60% win rate, profit factor 9.4) — worth real
  skepticism that the single-contract result was a favorable quirk of
  that specific ~14-month window rather than a robust edge.
  `output/param_sweep.csv` has both in full for direct comparison.
  - **ES remains weak.** Even with continuous data, the best ES 1-hour
    result found in the min-5-trades cutoff is profit factor 1.88, 43% win
    rate — barely profitable, not a result worth trading.
  - **NQ 1-hour continuous** (`range_max_pct=0.03`): 5 signals, 60% win
    rate, profit factor 12.6, +$48,602. Promising number, still too few
    trades (5) to trust on its own.

### Conclusion

CL has the most robust edge found across everything tried in this
project so far — consistent across two different datasets at
`range_max_pct=0.08`, 1-hour bars, profit factor in the 9-14 range on 5-12
trades. GC looks promising but its best result is dataset-sensitive rather
than confirmed. ES has not produced a tradeable configuration in any test.
NQ is promising but under-sampled. Even the best sample sizes here (12-22
trades in the largest cells) are still small for a real go/no-go decision
— next steps worth considering: sweep `retracement_zone` /
`target_extension_ratio` against the continuous data specifically (the
existing entry/exit sweep above only used single-contract data), and/or
extend the confluence rule to more instruments' historical data as it
becomes available (e.g. CL/ES's 3-year depth vs GC/NQ's ~1.5 years).

## Trendline cascade strategy results

`scripts/run_trendline_backtest.py`, default configs (monthly/weekly/
daily/4h bias -> 1h trigger, single-contract data — see
`strategy/README.md` for the strategy itself). This strategy fires far
more often than the confluence one: 17-54 trades per instrument on the
same ~1-2 years of single-contract data that gave the confluence strategy
1-22.

That signal density is what surfaced the `enforce_single_position` bug in
`engine.py` (documented above) — the first run, before that fix, showed
32-203 "signals" per instrument, several of them literally overlapping
same-direction entries an hour or a day apart. Every number below is
*after* the fix.

| Symbol | Trades | Win rate | Profit factor | Total P&L | Max drawdown |
|---|---|---|---|---|---|
| CL | 54 | 43% | 1.69 | +$25,387 | $9,698 |
| ES | 17 | 53% | 2.57 | +$38,754 | $12,297 |
| GC | 19 | 47% | 0.98 | -$2,227 | $34,979 |
| NQ | 25 | 54% | 2.40 | +$90,455 | $18,919 |

Notably different picture from the confluence strategy: here **ES and NQ
look like the strongest instruments** (profit factor >2.4 each, GC and CL
were the confluence strategy's better performers), and **GC is
essentially breakeven** (profit factor 0.98 — a coin flip after costs,
not a result worth trading) despite GC being one of the confluence
strategy's two working instruments. Worth taking at face value as "these
are two different rules with different instrument fit," not as one
strategy being more correct than the other.

Sample sizes (17-54 trades) are larger than any single cell of the
confluence-strategy sweeps, but still not enough on their own for a real
capital-allocation decision — see below for a full parameter/structure
sweep, and `strategy/README.md` for the cascade design.

## Trendline strategy: parameter + structure sweep

`scripts/sweep_trendline_params.py` — crosses `touch_tolerance_pct` (3
values) x `break_buffer_pct` (3) x `target_r_multiple` (4) with 4 cascade
*structures*:

| Structure | Bias timeframes | Trigger |
|---|---|---|
| `default` | month, week, day, 4h | 1h |
| `no_month` | week, day, 4h | 1h |
| `reactive` | day, 4h | 1h |
| `4h_trigger` | month, week, day | 4h |

576 combinations total (4 instruments x 4 structures x 36 param combos).
Performance note: the expensive part (walk-forward trendline fitting)
depends only on the structure, not the touch/break/target params, so
`strategy/trendline_signals.py` was refactored to split
`compute_cascade_state()` (once per structure/instrument) from
`signals_from_state()` (cheap, called once per param combo) — otherwise
this sweep would have redone the expensive fit 576 times instead of 16.

Best result per instrument, requiring a real sample (>=15 closed trades):

| Symbol | Structure | touch/break/target_R | Trades | Win rate | Profit factor | Total P&L | Max DD |
|---|---|---|---|---|---|---|---|
| CL | `4h_trigger` | 0.003/0.001/1.0 | 37 | 73% | **4.17** | +$36,731 | $2,437 |
| NQ | `default` | 0.005/0.001/2.0 | 22 | 64% | 3.95 | +$126,787 | $18,919 |
| ES | `default` | 0.003/0.001/2.0 | 18 | 56% | 2.88 | +$45,682 | $12,297 |
| GC | `reactive` | 0.005/0.001/1.5 | 16 | 44% | 1.27 | +$16,668 | $22,880 |

Findings:
- **CL's best result switches the trigger timeframe itself to 4h**, not
  just the bias set — a genuinely different structure, not a parameter
  tweak, and it's a clear step up from the `default` structure's 1.69
  profit factor: 4.17, with a strikingly low $2,437 max drawdown against
  +$36,731 total profit. Worth taking seriously as a real structural
  finding, not just noise, given how much it stands out across all 576
  rows (CL's `4h_trigger` cells dominate the top of the full sweep — see
  `output/trendline_param_sweep.csv`).
- **NQ and ES both do best on the `default` structure**, just with
  `target_r_multiple` pulled in from 2.0 (already close to the original
  default) — the gains here are more modest tuning than CL's structural
  change.
- **GC never produces a good result anywhere in the entire 576-row grid.**
  Its single best profit factor at any sample size is 1.27 (16 trades,
  `reactive` structure) — every other combination is worse or has too few
  trades to mean anything. Consistent with GC being the weak instrument
  for this strategy in the original default-config test too. This isn't a
  parameter-tuning problem to keep searching for; GC and the trendline
  cascade as designed don't appear to fit each other.
- `break_buffer_pct` barely matters in any of the top results (identical
  profit factor across 0.001/0.003/0.005) — the productive cells are
  almost entirely bounce events, not breaks, so this knob rarely binds.

## Combined confluence: trendline strategy + widened Wyckoff filter

`scripts/run_combined_confluence.py` — tests whether requiring the
trendline strategy AND the Wyckoff/Fib/Elliott Wave confluence strategy to
agree (same direction, entries within 24 hours of each other) beats either
alone. The Wyckoff strategy's `range_max_pct` / `range_min_bars` are
widened from each instrument's 1h default (0.05 / 10 bars) by 30/40/50%
(`range_max_pct *= 1+X%`, `range_min_bars *= 1-X%`) — see the script
docstring for exactly why those two parameters and not others.
Confirmed trendline signals are simulated using the trendline's own
entry/stop/target (Wyckoff here is purely a confirming filter, not the
entry trigger).

| Symbol | trendline alone | Wyckoff widened (best of 30/40/50%) | Confluence (best of 30/40/50%) |
|---|---|---|---|
| CL | 53 trades, PF 1.69 | 6-11 trades, PF 17.4-45.3 | **4 trades, PF 10.5** |
| GC | 19 trades, PF 0.98 | 1-4 trades, PF 6.7-10.1 | **2 trades, PF undefined (2-0 record)** |
| ES | 17 trades, PF 2.57 | **0 signals at every widen level** | 0 trades |
| NQ | 24 trades, PF 2.40 | **0 signals at every widen level** | 0 trades |

**Read this table with real caution, not excitement** — the "confluence"
columns have 1-4 trades. A 2-0 win record or a profit factor of 10 on 4
trades is not evidence of anything; it's what small samples do. The
directionally interesting part is that filtering *down* to only the
trendline signals a Wyckoff range also agrees with produced a much higher
per-trade profit factor at drastically reduced volume for CL and GC — a
plausible "fewer, better trades" pattern — but this needs far more data
before it means anything, not a conclusion to act on.

**ES and NQ found zero Wyckoff signals at any widen level, and this is
explainable, not a bug:** widening moved `range_max_pct` *up* from the
0.05 default (to 0.065-0.075), but the earlier range-threshold sweep
(`sweep_params.py`) already showed ES and NQ's 1h data only produces
Wyckoff signals at `range_max_pct=0.03` — *below* the default, not above
it (recall the documented non-monotonic relationship between threshold
and signal count). Widening upward from 0.05, as literally requested,
moved in the wrong direction for these two instruments specifically. A
version of this experiment that widens from each instrument's own
known-productive threshold (0.03 for ES/NQ, not the generic 0.05 default)
would be a natural, low-effort follow-up if this combined-confluence idea
is worth pursuing further.
