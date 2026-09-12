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

## The honest backtest: realistic costs, $10k account, train/test split

Every result above was produced *before* this section's fixes, and should
be read with that in mind — see "How does a sweep produce PF 4-45 and 70%+
win rates?" below for why those numbers were misleading, not just optimistic.

`scripts/run_realistic_backtest.py` adds four things at once, all real
gaps in every prior result in this file:

1. **Transaction costs** (`backtest.engine.CostModel`): 1 tick of slippage
   per fill (entry and exit, both unfavorable) plus $2.50/contract
   round-turn commission. Zero costs were modeled anywhere before this.
2. **Dollar-risk position sizing** (`backtest.engine.PositionSizer`): a
   $10,000 account, risk_pct_per_trade swept at 1% and 2% — contracts are
   computed from each trade's own stop distance, not assumed to be 1.
   A trade that can't size even 1 contract within budget is skipped, not
   floored up to 1.
3. **A genuine train/test split**: the touch/break/target_r grid is swept
   using *only* the first 70% of each instrument's data (by date); the
   best-on-train combination is then evaluated on the untouched final 30%.
   That held-out result — not "best cell of a sweep run over everything"
   — is the number to trust.
4. **Trailing stop vs fixed target, head-to-head**: the same entries,
   simulated two ways — `backtest/trailing.py`'s trailing-stop exit
   (stop ratchets along the live trendline, no fixed target) against the
   original fixed R-multiple target — under identical costs and sizing.

### Sizing reality check, before any trades were even simulated

Every instrument was checked against 1-2% risk on $10k *before* running
anything else, using the cheapest (tightest-stop) signal each one ever
produced:

| Symbol | Contract used | Cheapest signal's risk (1 contract) | % of $10k account needed |
|---|---|---|---|
| CL | MCL (micro, 1/10 size) | $41 | 0.4% |
| ES | MES (micro, 1/10 size) | $182 | 1.8% |
| GC | MGC (micro, 1/10 size) | $212 | 2.1% |
| BZ | full size (no smaller contract exists) | $445 | 4.5% |
| NQ | MNQ (micro, 1/10 size) | $249 | 2.5% |
| SI | QI (mini, half size — no true micro exists) | $799 | 8.0% |

Checked directly against IBKR before assuming any of this — SI has no
micro contract on this account (`SIL` returns 0 results on COMEX, NYMEX,
and CME), so it uses `QI` (mini silver, half-size, not a real micro).
BZ (Brent) has no smaller contract at all (`BZ`/`MBZ` checked on NYMEX and
ICEEU) — it's traded at full size or not at all. See
`config/instruments.yaml` for the `micro_multiplier` used per instrument
and why.

**GC, NQ, SI, and BZ never clear the 1-2% budget at all** — every single
signal each one ever produced needs more than 2% of a $10k account for
just one contract of the smallest available version, even at the "cheap"
end of their own range. This isn't a parameter-tuning problem to search
around; it's the strategy's stop placement (tied to the trendline's own
distance from price, not a fixed small amount) being fundamentally
sized for a bigger account than $10k, regardless of instrument, for four
of six instruments tested. Their rows in `output/realistic_backtest.csv`
are all `error: no combo reached MIN_TRAIN_TRADES` — every signal skipped
as undersized, not a signal-scarcity problem.

### The two instruments that could actually be tested

**CL** is the only instrument with a real train *and* test result:

| Risk | Exit | Split | Trades | Win rate | Profit factor | Total P&L | Max DD (% acct) |
|---|---|---|---|---|---|---|---|
| 1% | fixed target | train | 15 | 53% | 3.19 | +$1,303 | 2.0% |
| 1% | fixed target | **test** | **1** | **0%** | **0.0** | **-$61** | 0.6% |
| 1% | trailing stop | train | 14 | 50% | 2.40 | +$584 | 1.5% |
| 1% | trailing stop | **test** | **1** | **0%** | **0.0** | **-$61** | 0.6% |
| 2% | fixed target | train | 24 | 58% | 2.64 | +$2,438 | 3.4% |
| 2% | fixed target | **test** | **2** | **0%** | **0.0** | **-$297** | 3.0% |
| 2% | trailing stop | train | 24 | 38% | 1.54 | +$858 | 7.1% |
| 2% | trailing stop | **test** | **2** | **0%** | **0.0** | **-$297** | 3.0% |

**The out-of-sample test period lost money in every single configuration
tried, at both risk levels, with both exit types.** The training-period
numbers (profit factor 1.5-3.2, positive P&L every time) looked
reasonable — that's exactly the number a less careful backtest would have
reported and stopped there. The held-out period it was never allowed to
see tells a different story: 0% win rate on its only 1-2 trades. To be
fair in the other direction: 1-2 trades is much too small a sample to
conclude the edge is *fake* either — but it is conclusively *not
confirmed*, which is the entire point of holding out test data in the
first place.

**ES** only sizes at 2% risk, and only marginally — 8-11 train trades,
profit factor 0.88-1.01 (essentially breakeven to slightly losing) even
*before* looking at test data, of which there was none (0 signals survived
into the test period at any sizeable combination). Not a result worth
pursuing further as-is.

### Trailing stop vs fixed target: the trailing stop did not help

Every apples-to-apples comparison available (same instrument, same risk
level, train period) favored the fixed R-multiple target:

- CL @ 1%: fixed PF 3.19 vs trailing PF 2.40
- CL @ 2%: fixed PF 2.64 vs trailing PF 1.54 (and trailing's drawdown was
  more than double: 7.1% of account vs 3.4%)
- ES @ 2%: fixed PF 1.01 vs trailing PF 0.88 (trailing was net losing)

The intuition behind a trailing stop is "let winners run past a fixed
target" — but ratcheting the stop up to the live trendline here meant
exiting *earlier* on trades that the fixed target would have let continue
to a larger predefined win, while not adding meaningfully more protection
on the losers (both exit rules use the same initial stop). On this
evidence — small samples, one real instrument — the trailing stop is not
an improvement for this strategy as built; if anything it's a downgrade.

## How does a sweep produce PF 4-45 and 70%+ win rates? (why the sections above are misleading)

Every profit-factor figure reported in this file above the "honest
backtest" section shares three inflating factors, discovered by directly
inspecting trade logs rather than trusting the summary numbers:

1. **No transaction costs.** Commission and slippage were zero. Several
   CL trades in the original sweep risked under 1 point — on a $1,000/point
   full-size contract, a couple ticks of real slippage is a meaningful
   fraction of that edge, and it cost nothing in the backtest.
2. **576+ parameter combinations tested, best-of reported.** That's a
   textbook multiple-comparisons setup: finding *something* with PF > 4 by
   chance alone across that many combinations on a small dataset is close
   to guaranteed even with zero real edge.
3. **The "best" trade logs are dominated by one sustained trend, not
   repeated skill.** CL's headline 37-trade run: entries climb 66.97 →
   68.48 → 70.50 → 72.67 → 75.53 → 78.11 → 80.59 → 82.90 → 86.99 → 90.06,
   almost entirely long, March through May. NQ's does the same: 25,121 →
   25,954 → 27,106 → 28,897 → 30,474, February through August. A
   trendline-bounce strategy looks spectacular riding one persistent
   multi-month rally — that's the easy case for this kind of rule, not
   evidence it works across regimes the ~1.5-2 years of cached data never
   tested (a chop or decline would hit the same rule very differently).

None of this makes the earlier sections wrong to have written down — the
mechanics all check out, and the *patterns* found (wider retracement zones
find more setups, CL's 4h-trigger structure being a real structural
finding, etc.) are still informative. But the specific profit-factor and
win-rate numbers in every section above this one should be read as upper
bounds under favorable, cost-free, best-of-many conditions — not as
realistic expectations. The "honest backtest" section above is the one
that actually tries to answer "would this have made money."

## Crypto: BTC, ETH, SOL (Binance, 6 years, same honest treatment)

`scripts/run_crypto_realistic_backtest.py` — same realistic costs, $10k
account with 1-2% dollar-risk position sizing, and train/test split as the
futures "honest backtest" above, applied to 6 years of Binance spot data
(`data/binance_connector.py`, `config/crypto_instruments.yaml`). One
structural difference, forced by performance: crypto's trigger timeframe
is daily, not hourly — see `config/trendline_strategies/BTC.yaml`'s
comment; `walk_forward_trendlines`'s refit cost blew up badly on crypto's
much higher volatility (far more swing pivots per bar than futures data
has at the same bar count), extrapolating to ~3.5 hours for BTC's 6y of 1h
data versus 33s for its 2,192 daily bars. Sub-daily crypto isn't tested.

### Sizing was never the constraint here — something else was

Unlike the futures instruments, crypto's fractional position sizing
(0.0001 BTC/ETH/SOL "lots") makes every signal trivially affordable — the
most expensive signal in this whole run needed **$0.07** of a $10,000
account. That's not the finding. The finding is this:

**Every signal any of the three symbols produced, at every parameter
combination tested (across 6 years of data), falls inside a 20-month
window from early 2021 to May 2022 — the COVID-era crypto boom and its
subsequent crash.** Verified directly, not assumed: at the loosest tested
parameters, BTC produced 39 signals (33 in 2021, 6 in 2022, none after
2022-05-24), ETH produced 34 (same pattern, last one 2022-05-27), SOL
produced 61 (last one 2022-05-31). **Zero signals in over four years
since** — despite BTC alone rallying from ~$16-20k at the 2022 low to
over $77k by the time this was tested, one of the largest sustained bull
markets in the entire dataset.

This means the train/test split couldn't test anything for crypto: the
split point (70% through 6 years) falls in November 2024 — two and a half
years *after* the last signal any configuration ever produced. Every
single trade in every crypto result is a training-period trade by
construction, and every "test" row in `output/crypto_realistic_backtest.csv`
shows 0 trades, for all three symbols, both risk levels, both exit types,
without exception.

| Symbol | Exit | Train trades | Win rate | Profit factor | Test trades |
|---|---|---|---|---|---|
| BTC | fixed target | 19 | 53% | 1.53 | **0** |
| BTC | trailing stop | 25 | 28% | 0.56 | **0** |
| ETH | fixed target | 16 | 69% | 4.14 | **0** |
| ETH | trailing stop | 18 | 39% | 3.45 | **0** |
| SOL | fixed target | 15 | 53% | 3.28 | **0** |
| SOL | trailing stop | 42 | 36% | 2.18 | **0** |

Read the profit-factor column with the same skepticism as everything in
the "why the sections above are misleading" section — except more so
here: these aren't even "best of a sweep on a favorable window," they're
the *entire signal history* of one specific, unrepeated market event (a
speculative mania and its crash). There is no out-of-sample evidence for
crypto at all, in either direction — not "it failed out of sample" like
CL, but "there was no out-of-sample period with any signal in it to
evaluate." Whatever this strategy configuration is capturing, it hasn't
been relevant to BTC/ETH/SOL since mid-2022, through a bear market bottom,
a multi-year recovery, and a fresh all-time high — none of which produced
a single bounce or break event this rule recognizes, at any parameter
setting tried.

## Strategy showdown: 8 popular technical strategies x BTC/ETH/SOL

`scripts/run_strategy_showdown.py` — since the trendline cascade found
nothing in crypto since 2022, tried a genuinely different approach: 8
well-known technical strategies (`strategy/technical_signals.py`,
`indicators/technical.py`) — EMA crossover, RSI mean-reversion, MACD
crossover, Bollinger Band reversion, Donchian channel breakout,
Supertrend, and two confluence combos (trend-filtered RSI pullback,
MACD+RSI) — against 6 years of daily BTC/ETH/SOL data. Same realistic
costs and $10k/1-2% position sizing as everywhere else in this file.

**Deliberately did not sweep parameters this time.** Each strategy uses
one fixed, reasoned parameter set (standard textbook defaults — ATR-based
stops/targets at roughly 1.5:1-1.7:1 reward:risk, RSI shortened to 10
periods for crypto's volatility per current trading commentary — see the
module docstring) rather than searching a grid and reporting the best
cell. Given what every other sweep in this file already demonstrated
about the multiple-comparisons trap, testing 8 strategies x a parameter
grid each would have reproduced the same problem at a larger scale. One
principled choice per strategy, then real train/test validation, is the
honest way to search across *strategies* instead of *parameters*.

### Correction: this section originally said "exactly one" survivor — that was wrong

An earlier version of this section claimed BTC + Donchian was the only
(symbol, strategy) combination with profit factor above 1.0 on both train
and test. Re-checking the saved `output/strategy_showdown.csv` directly
found that's false: **four combinations pass that same filter**, not one —
BTC-Donchian, but also ETH + EMA crossover (train PF 1.09, test PF 1.45,
8 test trades), SOL + MACD crossover (train PF 1.14, test PF 1.17, 18 test
trades), and SOL + Donchian breakout (train PF 1.66, test PF 1.12, 23 test
trades — already mentioned further down as a "weaker secondary case," so
this wasn't fully hidden, just not counted in the headline claim). Left
as-is below with this correction rather than silently edited, since
getting this wrong once is itself worth a visible record — see
`backtest/README.md`'s change history via git if you want the original
wording.

None of the other three come close to BTC-Donchian's combination of
sample size, stability, and now (see the stress-test section above) an
entire additional round of bear-market/second-holdout validation — ETH-EMA
and SOL-MACD both clear PF 1.0 on paper but on thin test samples (8 and 18
trades) that have never been stress-tested the way BTC-Donchian has, and
SOL-Donchian's own follow-up check (below) shows real but weaker
consistency. The corrected, honest count is **4 out of 24, not 1** — still
a minority, but the original "exactly one" framing overstated how rare a
train/test-consistent result was here, which is worth knowing since it
changes how surprised anyone should be that BTC-Donchian, specifically,
worked.

### The headline result: BTC + Donchian channel breakout

BTC + Donchian remains the strongest of the four survivors by a clear
margin — matching win rate and drawdown behavior, not just profit factor,
on the largest sample of the four, and the only one put through the
additional bear-market/second-holdout stress test above:

| Split | Trades | Win rate | Profit factor | Total P&L (2% risk) | Max DD (% acct) |
|---|---|---|---|---|---|
| Train (2020-2024) | 70 | 51.4% | 1.53 | +$3,648 | 10.4% |
| Test (2024-2026) | 24 | 54.2% | 1.68 | +$1,546 | 6.2% |

Win rate, profit factor, *and* drawdown all held up or improved out of
sample — about as good as out-of-sample validation gets for a simple,
unoptimized rule. 94 total trades over ~6 years (~15-16/year) is a
believable frequency for a 20-day Donchian breakout on daily bars, not a
suspiciously dense signal count. The profit factor (1.5-1.7) is also
*plausible* in a way the project's earlier PF 10-45 sweep results were
not — a real, modest edge looks like this, not like a sweep's best cell.

SOL's Donchian breakout is a weaker secondary case: strong in training
(77 trades, PF 1.66) but decaying on test (23 trades, PF 1.12) — still
positive, but a real falloff rather than BTC's stability. A follow-up
calendar-year check (see below) found this holds up reasonably well across
most individual years too, though not as cleanly as BTC. ETH's Donchian
result doesn't clear the bar at all (train PF 0.95, essentially breakeven).

The other two combinations that technically pass the same train>1/test>1
filter (see the correction above) are weaker still and have not been
stress-tested further: **ETH + EMA crossover** (train PF 1.09 on 21
trades — barely above 1.0 — test PF 1.45 but only 8 trades) and **SOL +
MACD crossover** (train PF 1.14 on 57 trades, test PF 1.17 on 18 trades —
modest but consistent). Neither is dismissed as noise below, but neither
has earned the confidence BTC-Donchian has either; they're closer to
"passed the same bar, not separately investigated" than "worth acting on."

### Everything else: mostly noise, and that's the honest, expected result

Every other combination that showed a "profitable" train *or* test split
individually failed to show both — the standard signature of noise rather
than edge:

- **SOL RSI mean-reversion**: train PF 0.78 (losing) -> test PF 1.78 (very
  good). A real edge doesn't flip this hard; more likely one favorable
  stretch in the test window.
- **ETH Bollinger reversion**: train PF 1.16 (profitable) -> test PF 0.62
  (losing) — the reverse pattern, equally not to be trusted.
- **BTC EMA crossover**: train PF 0.96 (losing, 20 trades) -> test PF 1.97
  (great, but only 12 trades) — too small a sample either direction to
  mean anything, textbook overfitting-shaped noise if taken at face value.
- **Supertrend** was the most consistently *weak* strategy across all
  three symbols and both splits (profit factor 0.50-1.08 everywhere,
  never convincingly above 1.0) — the one strategy tested that looks
  closer to "doesn't work here" than "inconclusive."
- MACD crossover, the two confluence combos, and trend-pullback were all
  similarly mixed-to-negative across symbols — no consistent winner among
  them.

Full results for all 24 combinations (both risk levels) in
`output/strategy_showdown.csv`.

### Honest caveats

One standout out of 24 tried is not overwhelming evidence on its own —
with that many independent tries, finding one combination that happens to
look consistent is not wildly improbable even without a real underlying
edge, though BTC-Donchian's stability (matching win rate, matching
drawdown behavior, not just matching profit factor) is meaningfully
stronger evidence than a single number holding up would be. This has not
been tested against a second, later holdout period, against transaction
cost assumptions other than the ones used throughout this file, or with
the position-sizing/stop-placement logic stress-tested further (e.g. does
it survive a genuine bear market the current 6-year window under-samples,
since BTC/ETH/SOL have mostly trended up over this span). Worth taking
seriously as this project's best evidence of a real, if modest, edge
found anywhere across futures, crypto, both strategies, and every sweep
run — but "best evidence found so far" and "validated" are not the same
claim.

## Stress-testing BTC + Donchian: does it survive the 2022 bear market?

`scripts/run_btc_donchian_stress_test.py` — directly answers the caveat
above. The original test window (2024-11-22 onward, from the 70/30 split)
turned out to be almost entirely BTC's 2024-2025 bull run; the whole 2022
crash (BTC ~$69k top in Nov 2021 to ~$15.5k bottom in Nov 2022) was inside
*training*, never evaluated out-of-sample. No parameters are fit or swept
anywhere in this script — `generate_donchian_breakout_signals` uses the
same fixed, reasoned parameters as every other use in this project, so
slicing the already-fixed signal set into more time windows doesn't
reopen the multiple-comparisons problem the original train/test split
exists to guard against. It just asks: does this one fixed rule keep
working across periods it's never been individually judged against
before?

**Calendar-year breakdown, every year with data (2020 partial - 2026
partial), profit factor at 1% risk:**

| Year | Trades | Win rate | Profit factor |
|---|---|---|---|
| 2020 (partial) | 10 | 70% | 3.36 |
| 2021 | 14 | 43% | 1.10 |
| 2022 | 10 | 50% | 1.45 |
| 2023 | 15 | 67% | 2.84 |
| 2024 | 23 | 43% | 1.10 |
| 2025 | 12 | 50% | 1.42 |
| 2026 (partial) | 11 | 64% | 2.51 |

**Every single calendar year is profitable — no sign flips anywhere in
six years of data.** That's a materially different picture than "one
70/30 split happened to work."

**The 2022 bear-market window specifically (2021-11-10 top to 2022-11-21
bottom, evaluated as its own segment):** 10 trades, 60% win rate, **profit
factor 2.19**, positive P&L. The strategy did not merely survive the
crash, it profited from it — Donchian breakout trades both directions, so
a sustained downtrend produces short breakout signals same as an uptrend
produces long ones. This directly closes the "under-sampled bear market"
caveat from the strategy-showdown section above.

**A second, independent holdout** (splitting everything before the
original test split's start date, 2020-09-10 to 2024-11-21, into two
non-overlapping halves — a check unrelated to the original 70/30 cut):
earlier half PF 1.55 (31 trades), later half PF 1.50 (41 trades). Both
sides, plus the original test window's own PF 1.68, land in the same
1.1-3.4 range every other segmentation does.

### ETH and SOL Donchian, the same calendar-year check

`scripts/run_eth_sol_donchian_stress_test.py` runs the identical
calendar-year breakdown against ETH and SOL, to see *how* they fail —
uniformly (a clean negative), or only on the specific original 70/30
split (which would mean that split undersold them, the same concern
raised about BTC before the section above).

**ETH is a clean, consistent negative** — full-period PF 0.98 (104
trades, effectively breakeven-to-losing), and the 2022 bear market and
2024 are its two worst years (PF 0.67 and 0.62). This matches the
original showdown finding and adds nothing to doubt it.

**SOL is more interesting than the original single-split result
suggested.** Full-period profit factor **1.51** across 100 trades — very
close to BTC's own full-period 1.56 — and every single calendar year from
2020 through 2026 is individually profitable (PF 1.02-2.75), including
the 2022 bear market (PF 1.47, 10 trades). That's a materially better
picture than the original showdown section's "decaying on test" framing
implied. It doesn't change SOL-Donchian's status to "as validated as
BTC" — the original 70/30 test split (train PF 1.66, test PF 1.12) is
still the only holdout actually checked with position sizing and full
metrics, and 1.12 is a real, if smaller, edge than 1.02-2.75 makes it
look at a glance — but it does mean SOL deserves the same full stress
test BTC got (dedicated bear-market window, independent second holdout)
before being written off as "didn't replicate," which is not what this
project's own numbers show.

### SOL's full stress test, and a drawdown methodology fix that changes the ETH picture

`scripts/run_crypto_donchian_full_analysis.py` does two things.

**First, SOL gets the exact stress test BTC got.** 2022 bear-market window
alone: 10 trades, profit factor **1.47**. Second, independent holdout
(pre-original-split data cut in half): earlier half PF 2.14 (37 trades),
later half PF 1.32 (42 trades) — both sides positive. Reference, the
original 70/30 test window itself: PF 1.12 (23 trades). Every window
tried is profitable, no sign flips anywhere — the same pattern BTC showed,
just with somewhat more variance between windows (2.14 down to 1.12) than
BTC's tighter 1.5-1.7 band.

**Second — and this is the more important fix — a real methodological gap
in every calendar-year table in this file up to this point.** Computing
win rate/profit factor/drawdown separately per calendar year resets the
running-equity baseline to $0 at every Dec 31 -> Jan 1 boundary. Win rate
and profit factor are just sums/counts, unaffected by where the chopping
happens — but drawdown is measured as a peak-to-trough decline, and a real
account carries equity continuously across a year boundary rather than
resetting it. A decline that straddles one gets recorded as two smaller,
understated drawdowns in a chopped view instead of the one real one a
continuously-held position experiences. `backtest/engine.py` now has
`compute_drawdown_curve()`, which walks the ENTIRE trade history as a
single equity curve and reports not just how deep the worst drawdown was,
but exactly when it started, when it bottomed, and whether/when it
recovered:

| Symbol | Max drawdown (1% risk) | Peak | Trough | Recovered |
|---|---|---|---|---|
| BTC | $521 (5.2% of account) | 2024-03-05 | 2024-09-13 | 2024-11-21 (~8.5 months later) |
| SOL | $918 (9.2% of account) | 2022-11-09 | 2023-06-05 | 2023-11-10 (~1 year later) |
| ETH | $1,554 (15.5% of account) | 2022-08-11 | 2026-05-18 | **never — still underwater at the end of all available data** |

BTC's true drawdown matches what full-period reporting already showed
elsewhere in this file — that number was never actually distorted by
chopping, since it was always computed on the full trade list. SOL's
drawdown is real (9.2%) but fully recovered within about a year, which is
a normal, tradeable drawdown profile.

**ETH is the one this fix actually changes the picture on, and makes the
case against it stronger, not weaker.** Its equity curve peaked in August
2022, and by the time this project's cached data ends (September 2026) it
still hasn't gotten back there — a **~4-year underwater period**, worse at
2% risk (31% of account, still unrecovered). None of the calendar-year
tables run so far could have shown this, because each year was judged in
isolation against its own $0 baseline — a strategy can look like it has
merely a "bad year" here and there (PF 0.62-0.67 in 2022 and 2024) while
actually never making a new equity high across four consecutive years.
This is a materially worse picture of ETH-Donchian than "breakeven,
inconsistent" — it's "breakeven, and if you'd started at the wrong moment
in mid-2022 you would still be underwater four years later," which is a
real difference for anyone actually deciding whether to trade it.

Year-end checkpoints on the same continuous curves (full detail via the
script) make the contrast visible directly: BTC's worst-drawdown-so-far
figure stabilizes at $521 by end of 2024 and never gets worse again; SOL's
stabilizes at $918 by end of 2023; ETH's keeps climbing every single year
through 2026 ($204 -> $711 -> $905 -> $1,258 -> $1,443 -> $1,554) — an
equity curve that has not yet found its bottom within the available data,
not one that troughed and moved on.

### What this does and doesn't establish

This is a meaningfully stronger result than before: seven consecutive
calendar-year windows, a dedicated bear-market window, and an independent
second holdout, all profitable, with no signal reversal anywhere in six
years of BTC daily data, using parameters that were never fit to this (or
any) data. That combination of evidence is hard to explain as a pure
train/test-split artifact.

What it still doesn't establish: ETH is a clean negative and, per the
continuous-drawdown fix above, a considerably worse one than it first
looked (a ~4-year unrecovered drawdown, not just a mediocre PF) — that
part only gets stronger with more scrutiny. **SOL, after its own full
stress test, is now genuinely the second-best-evidenced result in this
project** — every independent window tried (bear market, both halves of a
second holdout, the original test) came back profitable, and its true
continuous drawdown (9.2% of account) fully recovered within about a
year, a normal profile rather than ETH's unbounded one. It is still not
quite BTC's equal: BTC's window-to-window range is tighter (roughly
1.5-1.7 PF everywhere vs. SOL's 1.1-2.1), BTC's sample is somewhat larger
(94 vs. 100 trades is close, but BTC's original test alone had 24 trades
vs. SOL's 23 — comparable, not a clear edge either way), and BTC's
drawdown recovered faster (~8.5 months vs. ~1 year). Two instruments with
real, differently-shaped supporting evidence is a stronger place for this
project to be than one, but it's also a reminder that a within-project
"BTC and SOL, not ETH" split needs to be held loosely — it could reflect
something structural about these three assets, or it could just be three
data points, not enough to generalize "Donchian works on large-cap coins
but not ETH specifically" from. One exchange's spot data throughout, and
real trading adds execution risk (slippage beyond the flat 1-tick
assumption here, API/exchange outages, funding/borrow costs if ever run
short via margin/futures rather than spot) that a backtest can't fully
capture. "Survived every out-of-sample window tried, including the bear
market" is the strongest claim this project can honestly make, and now
applies to two instruments, not one — it is still not the same claim as
"ready to risk real money on," which would need live paper-trading
validation first (see `paper/`, currently running for BTC only —
extending it to SOL is a reasonable next step, not yet done).

## Donchian breakout on futures: GC, CL, ES, NQ

`scripts/run_donchian_futures_backtest.py` — took the one strategy that
held up in the crypto showdown and tested it against gold, crude oil, and
both stock index futures, using each instrument's continuous daily series.

**Contract size / account size, clarified up front:** micro contracts
(MGC/MCL/MES/MNQ) on a $10,000 account and full-size contracts on a
$100,000 account are not two separate things tested here — every
`micro_multiplier` in `config/instruments.yaml` is exactly 1/10 of the
corresponding full multiplier, and $100k is exactly 10x $10k, so
`PositionSizer`'s contracts-per-trade calculation is mathematically
identical either way. Verified directly, not assumed, before running
anything. One run covers both.

### Blocked by risk sizing, not by the strategy itself

At the standard 1-2% risk budget used throughout this project, **GC, ES,
and NQ produce zero tradeable signals — every single signal any of them
ever generated needs more risk than the budget allows for even 1
contract**, regardless of micro-vs-full contract size:

| Symbol | Cheapest signal's risk (smallest contract) | % of $10k-equivalent account |
|---|---|---|
| CL | $135 | 1.35% |
| ES | $489 | 4.89% |
| GC | $633 | 6.33% |
| NQ | $873 | 8.73% |

CL is the only one where any signals clear the bar at all — 7 trades at
2% risk in training, and they collectively **lose** money (profit factor
0.62). This is the same structural finding the trendline cascade hit on
GC/NQ/SI/BZ earlier in this file, now confirmed independently with a
completely different strategy (ATR-based stops instead of trendline-
distance stops): the stop distances these instruments' daily volatility
produces are wide relative to account size in a way contract size alone
doesn't fix, since risk-as-a-percentage-of-account is scale-invariant to
the micro/full choice.

### Does the strategy have real signal here at all? Checked directly

Rather than stop at "not tradeable within 1-2% risk," re-ran without any
position-sizing constraint (1 contract flat, ignoring account size) to see
whether Donchian breakout has *any* underlying edge on these instruments,
independent of whether a modest account could afford to trade it:

| Symbol | Train (trades, win rate, PF) | Test (trades, win rate, PF) |
|---|---|---|
| **NQ** | 15, 53%, **1.85** | 8, 50%, **1.28** |
| GC | 19, 58%, 1.60 | 7, 43%, 0.94 |
| CL | 20, 25%, 0.51 | 13, 62%, 1.41 |
| ES | 14, 36%, 0.99 | 7, 43%, 1.23 |

**NQ is the one futures instrument whose result echoes BTC-Donchian's
shape** — profitable on both sides, no sign flip, though on a much
smaller sample (23 total trades vs. BTC's 94, so considerably less
confidence). GC looks good in training and decays to roughly breakeven in
test — a real result, but not a confirmed one. CL and ES show the same
sign-flipping, noise-shaped pattern as most of the crypto showdown's
non-survivors.

### The practical conclusion

Even NQ's decent-looking numbers can't actually be traded at standard
1-2% risk management on a $10k (or equivalent $100k-full-size) account —
its cheapest signal alone needs 8.73%. Getting NQ's Donchian breakout
into a tradeable state would need roughly a $44k+ account just to size
the *cheapest* signal at 2% risk (most signals, being pricier than the
minimum, would need considerably more) — or a redesigned, tighter stop
than the standard 2x-ATR used here, which is a strategy change, not an
account-size fix. Neither has been tested; this section reports the
finding, not a fix for it.

## Everything, re-tested on a $100,000 account with full-size contracts

`scripts/run_100k_full_size_backtest.py` — widens the question from "does
Donchian work on futures" to "does *anything* work on futures at this
account size": all 8 technical strategies from `strategy/technical_signals.py`
(not just Donchian) plus the trendline cascade, across all six futures
instruments (GC, CL, ES, NQ, SI, BZ), on a **$100,000 account with
full-size contracts** — no micro contracts this time. Same realistic-cost
methodology throughout: 1 tick slippage, $2.50/contract commission, 70/30
train/test split by date, 1-2% risk per trade, no parameter sweep for the
technical strategies (one fixed reasoned set each, same as the crypto
showdown), touch/break/target swept on train only for the trendline
cascade (same as "the honest backtest" section above).

**Why this isn't just repeating the last run under a new name:** for
GC/CL/ES/NQ, full-size+$100k is mathematically identical to
micro+$10k (verified directly — see the section above), so those four
contribute no new information for strategies already tested that way.
What *is* new: SI and BZ, whose "micro" contracts aren't true tenth-size
(SI's is half-size, BZ has no smaller contract at all — see
`config/instruments.yaml`), so $100k/full-size is a genuinely different,
easier test for them than anything run before. And the other seven
technical strategies (everything except Donchian) had never been run
against any futures instrument at all until this script — only crypto.
SI and BZ also don't have a stitched continuous contract series yet (see
"Not yet built" in the root README), so their technical-strategy legs use
the latest single contract's own history instead (251 daily bars for SI,
just 132 for BZ — a few months, not years; flagged explicitly below).

### Zero tradeable signals, for every technical strategy, on every futures instrument

Across all 8 technical strategies x all 6 instruments x both risk levels
(96 test-period combinations), **not one produced a single closed trade in
the held-out test period.** This isn't a sizing edge case on one or two
symbols — it's universal. Checked directly that this isn't a bug: for CL
(the cheapest instrument to trade, needing as little as 0.99% of the
account for its overall cheapest signal), the training period does size
trades fine at 2% risk, but every individual signal *in the test period*
turned out to need 2.5%-11.2% of the account — oil's realized volatility
(and therefore its ATR-based stop width) was simply wider in the test
window than in training. The account-size fix from the section above
(micro→full, $10k→$100k) doesn't help here because risk-as-a-percentage-
of-account doesn't change with account size — it's determined entirely by
how wide the stop is relative to how much of the account you're willing
to risk, and that's a property of the instrument's recent volatility, not
the account.

### Trendline cascade: same wall for GC/NQ/SI, one exception

| Symbol | Cheapest signal needs (% of $100k) | Test result |
|---|---|---|
| GC | 2.12% | no combo sizes at 1-2% risk — 0 trades |
| NQ | 2.49% | no combo sizes at 1-2% risk — 0 trades |
| SI | 1.60% | no combo sizes at 1-2% risk — 0 trades |
| ES | 1.82% | sizes at 2% risk only, 0 test trades either way |
| CL | 0.41% | sizes fine, but **loses money in test** (PF 0.0, 1-2 losing trades) — consistent with the CL finding in "the honest backtest" section above |
| BZ | 0.45% | the only survivor — see below |

**BZ (Brent crude) is the single combination, out of 106 tested in this
run, that was profitable on both train and test with a real number of
test trades** — profit factor 2.1-6.9 in test, 5-7 train trades, 8-15
test trades, all four risk/exit-type variants profitable both sides.
**Treat this with real suspicion, not excitement:** BZ has no continuous
contract series, so this result comes from just 132 daily bars of a
single contract's own history — under six months of data. A "test period"
built from 8-15 trades inside a few months of one instrument's history is
exactly the kind of small sample this project has repeatedly found
produces impressive-looking but fragile numbers (see "why the sections
above are misleading" earlier in this file). This is not validated
evidence of an edge; it's a result that would need a real continuous
multi-year BZ series — which doesn't exist yet — before it's worth
trusting at all.

### The honest summary

Testing everything this project has built, on every futures instrument
covered, with a full order of magnitude more account size than the
original $10k baseline: **nothing new survives.** The technical
strategies that showed real signal on crypto (Donchian breakout
especially) produce no test-period trades at all on any futures
instrument under standard risk management, regardless of account size —
this is a volatility/stop-width problem, not an undersized-account
problem, and a bigger account doesn't fix it. The trendline cascade's one
apparent survivor (BZ) rests on a sample too thin to trust. Ten times the
account size did not unlock a new edge anywhere in this project's futures
coverage; it mainly confirmed that GC/CL/ES/NQ's earlier micro-contract
results already told the whole story (being mathematically identical),
and that SI/BZ need real historical depth (a continuous series) before
any result on them means much.

## The original confluence strategy, finally given the honest treatment

`scripts/run_confluence_honest_backtest.py` — the Wyckoff accumulation/
distribution + Fibonacci retracement + Elliott Wave confluence rule
(`strategy/signals.py`) is what this project started with, before the
trendline cascade, the technical-strategy showdown, or crypto existed.
Every number reported for it anywhere in this project's early history
(README.md's original "Current results", this file's "Combined
confluence" section) had zero transaction costs, 1-contract sizing, and
no train/test split — exactly the gap already closed for every other
strategy. This is that treatment, finally applied here.

Same methodology as the confluence rule's own earlier parameter sweeps
(`scripts/sweep_params.py`, `scripts/sweep_entry_exit.py`): $100,000
account, full-size contracts, 1 tick slippage, $2.50/contract commission,
1-2% risk, 70/30 train/test split, `range_max_pct` x `retracement_zone` x
`target_extension_ratio` swept on train only (36 combos), best-on-train
evaluated on held-out test. Only GC, CL, ES, NQ have a confluence config
at all (SI/BZ were added to the project after this strategy was mostly
set aside in favor of the trendline cascade).

### Daily bars: too sparse to evaluate at all

On daily continuous data, **all four instruments produced 0-2 raw
signals across their entire multi-year history** — never enough to reach
even 3 train trades at any of the 36 grid combinations tried. This
strategy's four-stage chain (a valid trading range forms, price breaks
out, price impulses away and retraces into a specific Fibonacci zone, a
confirming swing lands inside that zone) is simply too selective to fire
more than a couple of times a year on daily bars. Not a bug, not a
sizing problem — a genuine, honest "not enough signal to say anything"
result.

### 1 hour: some signal, but treat every number here with real suspicion

The original (cost-free, unsplit) sweeps that first found this strategy's
best-looking numbers were run on 1-hour bars, not daily — so this script
also tried that timeframe rather than concluding "no signal" from daily
alone:

| Symbol | Signals | Train | Test |
|---|---|---|---|
| GC | 5 | 4 trades, PF 11.9 (risk 2% only) | 0 trades |
| CL | 15 | 11 trades, PF 16.8 | 6 trades, PF 12.3 |
| ES | 0-4 | 4 trades, PF 3.7 (risk 2% only) | 3 trades, PF 1.2 |
| NQ | 3 | never reached 3 train trades | — |

**CL's number in particular (PF 16.8 train, PF 12.3 test) is exactly the
shape this project has learned to distrust on sight** — checked directly
rather than reported at face value. The individual trades explain why:
several show R-multiples of 8-13 (e.g. one trade risked ~$0.29/barrel and
made ~$3/barrel). That's not a data glitch — it's this strategy's
construction: the stop sits a fixed 0.5% (`stop_buffer_pct`) beyond the
confirming swing, while the target is a Fibonacci extension of the
impulse leg, which can be many times larger on a volatile instrument at
hourly resolution. Tight, fixed-percent stop paired with a variable, often
much larger target mechanically produces large R-multiples on the trades
that *do* work, regardless of whether there's a real edge — and with only
11 training and 6 test trades total, a couple of large winners dominate
the whole result. This is a small-sample/structural-mechanics artifact
explanation, not a confirmed data bug, but either way: **11-17 trades
total, chosen as the best of a 36-combo train sweep, is not evidence this
project is willing to act on** — it's reported here specifically so it
doesn't quietly become a "PF 16" headline number the way earlier
cost-free sweeps did before this project learned better.

GC and ES's 1-hour numbers are even smaller-sample (3-4 trades) and not
worth more than a footnote. NQ never reached a usable sample at all.

### The honest conclusion

The strategy this project started with has now been given the same
honest treatment as everything else, and **it does not clear the bar
anywhere** — daily bars starve it of signals entirely, and 1-hour bars
produce samples too small (3-17 trades) and numbers too extreme (PF
3.7-16.8) to trust, with a specific, identified mechanical reason (a
fixed tight stop against a variable, often much larger target) for why
the R-multiples look inflated even before considering sample size. This
closes the last major "not yet built" gap in this project's honest-
methodology coverage — every strategy that predates BTC + Donchian has
now been tested the same way, and none of them beat it.

## Enhancing BTC (+ SOL) Donchian: portfolio, trailing stop, a stricter reward:risk floor

`scripts/run_btc_sol_enhancements.py` — three deliberate, named
comparisons (not a parameter search) against the validated BTC/SOL +
Donchian breakout result, all at $10k account / 1% and 2% risk.

### 1. A two-asset BTC+SOL portfolio — the one that actually helps

Combining both symbols' trades into one shared-account equity curve, no
parameters changed on either side:

| | Trades | Win rate | PF | Total P&L (1% risk) | Max drawdown |
|---|---|---|---|---|---|
| BTC alone | 94 | 52.1% | 1.56 | +$2,595 | 5.21% |
| SOL alone | 100 | 51.0% | 1.51 | +$2,559 | 9.18% |
| **BTC+SOL combined** | 194 | 51.5% | 1.54 | +$5,154 | **7.32%** |

Total P&L is exactly additive (as it has to be), and profit factor barely
moves — no surprise, since nothing about either strategy changed. The
real result is **drawdown: 7.32% combined vs. 5.21%/9.18% individually**.
If the two assets' bad periods were the same period, combined drawdown
would sit at or above the worse of the two (9.18%+); if they were
perfectly offsetting, it would fall well below the better one. Landing
between the two, closer to the average than either extreme, confirms what
the peak/trough dates already suggested (BTC's worst stretch was
2024-03 to 2024-09; SOL's was 2022-11 to 2023-06 — different windows):
real, if partial, diversification. This is the one enhancement here that
improves the risk picture without touching either strategy's own edge or
introducing any new parameter to second-guess.

### 2. ATR trailing stop vs. fixed target — worse on both, consistent with the earlier trendline-cascade finding

`backtest/atr_trailing.py` (new this session): a generic ATR "chandelier
exit" — trails at the same 2x-ATR distance the strategy's own initial
stop already uses, not a separately chosen number — tested head-to-head
against the fixed 3x-ATR target:

| Symbol | Exit | PF | Win rate | Max DD (1% risk) | Avg win |
|---|---|---|---|---|---|
| BTC | fixed target | 1.56 | 52.1% | 5.21% | 1.50R |
| BTC | trailing stop | **0.95 (losing)** | 46.5% | 10.96% | 0.87R |
| SOL | fixed target | 1.51 | 51.0% | 9.18% | 1.50R |
| SOL | trailing stop | 1.38 | 40.0% | 6.62% | 1.42R |

**Worse on both symbols, badly enough on BTC to flip it into a losing
strategy.** Same conclusion this project already reached testing a
trailing stop against the trendline cascade ("did not outperform a fixed
R-multiple target in any head-to-head comparison run") — now confirmed a
second time, on a completely different strategy. The mechanism: Donchian
breakout's edge depends on capturing a specific, sized move (the 3x-ATR
target), and a trail that lets winners "run" instead just gives back gains
on the pullback before the wider trailing stop catches it — average win
size actually shrinks (0.87-1.42R vs. the fixed target's 1.50R), the
opposite of what a trend-following trailing exit is supposed to achieve.
**Not adopted — reported because it was tested, not because it worked.**

### 3. The reward:risk floor: original is 1:1.5, a stated minimum is 1:2

The strategy's validated default (stop=2x ATR, target=3x ATR) is a 1:1.5
reward:risk ratio. Tested a stricter 1:2 variant (target=4x ATR) as a
single, one-time comparison — not a search for a better number, a direct
answer to whether the edge survives a stricter floor:

| Symbol | Variant | PF | Win rate | Max DD (1% risk) |
|---|---|---|---|---|
| BTC | original (1:1.5) | 1.56 | 52.1% | 5.21% |
| BTC | strict 1:2 | 1.23 | 39.0% | 9.91% |
| SOL | original (1:1.5) | 1.51 | 51.0% | 9.18% |
| SOL | strict 1:2 | 1.29 | 39.8% | 12.53% |

**Both symbols stay profitable at the stricter 1:2 floor — this is a real
option, not a broken one — but it's a meaningfully weaker version of the
same edge**, not an improvement: profit factor drops ~20-25%, win rate
drops from ~51% to ~39% (a wider target takes longer to reach, so more
trades reverse before getting there), and max drawdown roughly doubles on
both symbols. This is a genuine risk-preference tradeoff, not something
this project can resolve on its own: the validated 1:1.5 version performs
better on every metric measured here, but a 1:2-or-better rule is a
defensible, common risk-management floor many traders hold to regardless
of backtested performance. Both are reported; which one (if either) to
paper-trade is a call the strategy's own numbers can't make.

## Increasing profit on the 1:1.5 version: a third portfolio sleeve, plus compounding

`scripts/run_profit_enhancements.py` — moving forward with the validated
1:1.5 reward:risk version (not the stricter 1:2 variant above), two ways
to increase total profit without touching the strategy's own parameters
at all, which is deliberate: any new parameter reopens the multiple-
comparisons risk this project has repeatedly found not worth it. Both are
mechanical/structural changes to how capital is deployed around an
already-fixed, already-validated signal rule.

### A third sleeve: BNB passes the same bar BTC and SOL did; XRP doesn't

Before adding anything to the portfolio, it gets the identical full
stress test BTC and SOL already passed — not just a good-looking
calendar-year number. Two coins were checked (both established, long-
Binance-history large caps, chosen for that reason before looking at any
result — not picked after the fact for looking good):

| | Bear market PF | 2nd holdout (both halves) | Original test PF | True drawdown | Recovered? |
|---|---|---|---|---|---|
| **BNB** | 1.45 | 1.77 / 1.43 | 1.43 | 4.11% | Yes, ~10 months |
| XRP | 2.04 | 2.05 / **0.90** | 1.24 | 5.87% | **Never** |

**BNB passes cleanly** — every window in the 1.4-1.8 range, no sign
flips, the tightest, most consistent spread of any of the three coins
tested so far (tighter than SOL's 1.1-2.1). Added to the portfolio.

**XRP does not** — its second holdout flips sign between halves (2.05
then 0.90, the losing kind of inconsistency this project's methodology
exists to catch), and its true continuous drawdown never recovered by the
end of available data, the same red flag ETH showed. Checked and reported
honestly rather than quietly left out; not added.

### Part 1: BTC + SOL + BNB, three-asset portfolio

Same fixed, unswept Donchian rule on all three, no parameters changed.
Two ways to read this, both shown — the honest, capital-constrained
version is the one that matters for an actual $10,000 account:

**If each sleeve gets its own full $10k risk-reference** (the aggressive
reading — effectively needs ~$30k total capital to run all three
simultaneously): combined 289 trades, PF 1.54, pnl +$7,667 (1% risk),
max drawdown 10.04% of the $10k reference.

**If one real $10,000 account is split three ways** ($3,333/sleeve — see
Part 2 below, which computes this properly): this is the number that
actually answers "what does $10,000 do."

### Part 2: compounding — and the properly-divided $10k comparison that matters

| | $10,000 account, 1% risk | $10,000 account, 2% risk |
|---|---|---|
| BTC alone (full $10k, no split) | $12,595 (+25.9%), DD 5.21% | $15,194 (+51.9%), DD 10.43% |
| 3-asset, fixed size, $10k split 3 ways | $12,555 (+25.5%) | $15,113 (+51.1%) |
| **3-asset, compounding, $10k split 3 ways** | **$12,811 (+28.1%), DD 3.64%** | **$16,159 (+61.6%), DD 8.79%** |

Splitting one real $10,000 account across three sleeves with no
reinvestment (row 2) performs almost identically to holding BTC alone
(row 1) — diversification alone, without compounding, mostly just
trades concentration risk for smoother returns at roughly the same total
number, which is the expected, unglamorous result of dividing capital
three ways with a similar edge on each piece.

**Compounding is what actually moves the total-return number** (row 3):
letting each sleeve reinvest its own gains takes the 1%-risk case from
+25.5% (fixed) to +28.1%, and the 2%-risk case from +51.1% to +61.6% —
and the combined portfolio's drawdown (3.64% / 8.79%) comes in *lower*
than BTC alone's own drawdown (5.21% / 10.43%), not higher. More profit
and less drawdown than the single-asset baseline, from the same
$10,000, by combining diversification (Part 1) with reinvestment
(this part) — neither alone gets there; the combination does.

Compounding's mechanical honesty check, per sleeve (why the boost is
modest, not dramatic, at 1% risk): BTC's own $3,333 sleeve compounding
vs. fixed sizing is +2.1% better in final balance, SOL +2.0%, BNB +2.0%
— compounding return being small on a $3,333 starting stake over 94-100
trades is expected arithmetic (each individual trade is a small fraction
of the account, so reinvesting its gain barely changes the next trade's
size) and gets more pronounced at 2% risk (+6.7% to +7.2% per sleeve) and
would keep growing with more capital or more trades, not evidence
against a real, if modest, compounding effect. Per-sleeve compounding
drawdown (as % of that sleeve's *own* starting stake) is naturally larger
than the combined-portfolio percentage — BTC's sleeve alone can see 6-14%
drawdown on its own $3,333, same diversification-smoothing effect as
Part 1, now visible at the sleeve level too.

### The honest summary

Two changes, neither touching the validated signal rule: adding a third,
independently-stress-tested asset (BNB), and letting gains compound
instead of sizing off a fixed balance. Together, on the same $10,000
this whole project has used as its reference account throughout, they
outperform holding BTC alone on both total return and drawdown. Neither
change alone (diversification without compounding, or compounding a
single asset) gets there by itself — it's the combination that does.

## Is 25-28% over 6 years actually good? Buy-and-hold context, and scaling risk further

A fair challenge to the section above: +28.1% over 6 years is a ~4.2%
CAGR — and buying and holding BTC over the same window returned +644.6%
(39.8% CAGR). That comparison is real, but it isn't apples to apples:
buy-and-hold is 100% exposure 100% of the time; this strategy is flat
roughly a third of the time and risks only 1-2% of the account per
trade, by design. `scripts/run_portfolio_risk_scaling.py` puts numbers on
both halves of that tradeoff: how much of the gap a conventional (up to
5%) risk increase actually closes, and what buy-and-hold's own risk
profile looks like for honest context.

### Buy-and-hold's own numbers, for context — not a strategy, the benchmark

| | Total return | CAGR | Max drawdown |
|---|---|---|---|
| BTC | +644.6% | 39.8% | 76.6% |
| SOL | +2,764.7% | 74.9% | 96.3% |
| BNB | +2,767.2% | 75.0% | 70.9% |
| Equal-weight 3-asset | +2,058.8% | 66.9% | 86.7% |

Buy-and-hold's returns are enormous over this particular 6-year window —
and so are its drawdowns. An 86.7% peak-to-trough decline on the
equal-weight buy-and-hold portfolio means losing $8,670 of every $10,000
at the worst point, before it recovered. Very few investors hold through
an 87% drawdown without capitulating at the bottom, which is exactly the
risk this strategy exists to avoid — the comparison isn't "this strategy
failed to beat buy-and-hold," it's "this strategy trades a large amount
of buy-and-hold's return for a large reduction in its drawdown," and the
question is whether a conventional risk increase can close some of that
gap without reopening the drawdown problem.

### Risk-per-trade x diversification (fixed sizing, no compounding yet)

| Risk | 3-asset (split $10k) | BTC alone (full $10k) |
|---|---|---|
| 1% | +25.5%, DD 3.3% | +25.9%, DD 5.2% |
| 2% | +51.1%, DD 6.7% | +51.9%, DD 10.4% |
| 3% | +76.7%, DD 10.0% | +77.9%, DD 15.7% |
| 5% | +127.8%, DD 16.7% | +129.9%, DD 26.1% |

Diversification barely changes total return at any risk level (as
expected — it doesn't touch the edge) but **the drawdown gap widens as
risk increases**: at 5% risk, the diversified portfolio's drawdown (16.7%)
is nearly 10 points lower than BTC alone's (26.1%). Diversification
doesn't just smooth the ride at 1-2% risk — it's what makes a higher risk
level survivable at all.

### Risk-per-trade x compounding fraction, 3-asset portfolio

`compounding_fraction` is a new parameter on `simulate_trades_compounding`
(0.0 = fixed sizing, 1.0 = full reinvestment, 0.5 = "half-Kelly" — a
standard, named risk-management compromise, not an arbitrary number):

| Risk | Fixed (0.0) | Half-Kelly (0.5) | Full compounding (1.0) |
|---|---|---|---|
| 1% | +25.5%, DD 3.0% | +26.8%, DD 3.3% | +28.1%, DD 3.6% |
| 2% | +51.1%, DD 6.0% | +56.2%, DD 7.3% | +61.6%, DD 8.8% |
| 3% | +76.7%, DD 9.0% | +88.4%, DD 12.0% | +100.9%, DD 15.7% |
| 5% | +127.8%, DD 15.0% | +161.1%, DD 24.0% | +196.5%, DD 36.2% |

**Full compounding's drawdown grows faster than its return as risk
increases** — at 5% risk it reaches 36.2%, worse than half of
buy-and-hold's own 76.6% BTC drawdown, on a strategy whose whole appeal
was avoiding exactly that. Half-Kelly (0.5) captures most of full
compounding's return benefit at 3% risk (11.1% CAGR vs. 12.3% — about
90% of the upside) for meaningfully less drawdown (12.0% vs. 15.7%) —
the better risk-adjusted choice at that level. At 5% risk, even
half-Kelly's drawdown (24.0%) is getting large; full compounding's
(36.2%) is the point where this stops looking like the same
conservative strategy validated earlier in this file.

### Where this leaves the decision

None of this is free — every row above that beats the original 1%/fixed
baseline's return also has a bigger drawdown than its 3.0-3.6% starting
point. Within the conventional band (up to 5%, per the brief this
section was scoped to — 10-20% risk per trade was explicitly ruled out
as not a serious proposal for a systematic strategy, backtest numbers
notwithstanding), the honest shortlist:

| Configuration | CAGR | Max drawdown | Character |
|---|---|---|---|
| 1% risk, fixed (original) | 3.9% | 3.0% | Very conservative, already validated |
| 2% risk, full compounding | 8.3% | 8.8% | Moderate, still tight |
| **3% risk, full compounding** | **12.3%** | **15.7%** | Balanced — best return-per-unit-drawdown of the growth options |
| 5% risk, half-Kelly | 17.3% | 24.0% | Aggressive but bounded |
| 5% risk, full compounding | 19.9% | 36.2% | Most return, but drawdown starts eroding the strategy's own reason for existing |

This isn't a decision the backtest can make on its own — it's a real
risk-preference tradeoff, same as the 1:1.5-vs-1:2 reward:risk choice
earlier in this file. 3% risk with full compounding is the balanced
recommendation (meaningfully higher CAGR than the original 1% baseline,
15.7% drawdown well inside what most systematic strategies target); 5%
with half-Kelly is the honest answer if more absolute growth matters more
than the smoothest ride. All of the above is on the same three-asset,
fully-stress-tested portfolio — the strategy itself hasn't changed, only
how much of it is deployed.

## Trading this on leveraged futures instead of spot

The chosen configuration (3% risk, full compounding) was originally
tested as spot trading. `scripts/run_leverage_liquidation_test.py` tests
the identical strategy on leveraged perpetual futures at 3x, 5x, and 10x.

**The modeling point that matters before reading any number below:**
leverage does not, by itself, change a risk-based-sized position's P&L.
Position size here is already computed from "risk 3% of equity, given the
stop distance" — leverage only changes how much collateral (margin) is
required to hold that already-determined position, and where the
exchange's forced-liquidation price sits. The real question leverage
introduces is whether the exchange can force-close a position, at
whatever price consumes the posted margin, *before* the strategy's own
2x-ATR stop-loss would have executed. If the strategy's stop is tighter
than the leverage-driven liquidation buffer, leverage changes nothing
about performance — it's purely a capital-efficiency question. If the
stop is wider, some trades get cut off early at a price the strategy
never chose.

Liquidation buffer approximation used (standard retail-tier formula,
isolated margin, 0.5% maintenance margin rate — a commonly-cited retail
bracket figure, not fetched from Binance's live bracket table, so treat
as directionally right rather than exact): roughly 33% of price at 3x,
20% at 5x, 10% at 10x.

### How often the strategy's own stop is wider than the buffer

| Symbol | Stop distance (median) | 3x liquidation-bound | 5x liquidation-bound | 10x liquidation-bound |
|---|---|---|---|---|
| BTC | 7.6% of price | 0.0% | 0.9% | 25.4% |
| SOL | 14.6% of price | 4.2% | 21.0% | 90.8% |
| BNB | 9.2% of price | 1.4% | 2.9% | 48.8% |

**SOL is the outlier — its stops are typically twice as wide as BTC's**
(higher volatility means a bigger ATR relative to price), so at 10x
leverage over 90% of its signals would have their exit forced by
liquidation rather than the strategy's own logic. This is a coin-specific
risk, not a portfolio-wide constant — a leverage level safe for BTC is
not automatically safe for SOL.

### Full portfolio result at each leverage level

| | Final ($10k start) | CAGR | Max drawdown | Win rate | Liquidation-bound trades |
|---|---|---|---|---|---|
| Spot (baseline) | $20,086 | 12.33% | 15.73% | 51.6% | — |
| 3x leverage | $20,173 | 12.41% | 15.78% | 51.5% | 13 |
| 5x leverage | $18,865 | 11.16% | 15.28% | 50.0% | 58 |
| 10x leverage | $19,042 | 11.33% | 17.32% | **43.6%** | 376 |

**3x leverage is effectively free** — 13 liquidation events out of
roughly 675 combined signals, performance indistinguishable from spot.
The practical benefit is capital efficiency, not return: the same
backtested result requires only a third of the collateral locked up as
margin compared to spot.

**5x leverage introduces a real but modest drag** — CAGR drops about a
point (12.33% → 11.16%) as some trades that would have recovered under
the strategy's own wider stop get closed early instead.

**10x leverage changes the character of the strategy, even though the
final CAGR looks deceptively close to spot's.** Win rate drops sharply
(51.6% → 43.6%) — a large fraction of would-be winners get forced closed
before recovering. The reason CAGR doesn't collapse along with win rate:
because position size is fixed by the *original* risk calculation, a
liquidation-forced exit (which triggers *closer* to entry than the
intended stop, by construction of the buffer math) realizes a **smaller**
dollar loss than the strategy's own planned 3% risk would have — so each
individual liquidation event is capped, but there are far more of them,
and many are trades that the strategy itself would have turned into
winners. Lower win rate and lower profit factor (1.46 → 1.32) is a
materially worse-quality strategy than what was validated, even though
total return happens to land in a similar place over this particular
6-year window — that similarity is not something to rely on going
forward.

### What isn't modeled here

**Funding rate** — perpetual futures' periodic payment between long and
short holders, exchanged every 8 hours, is a real, recurring cost this
analysis has no historical data for (`data/binance_connector.py` only
pulls spot klines; a futures funding-rate connector doesn't exist yet).
Given this strategy's ~15-day average holding period and roughly 64%
long / 36% short signal mix, funding could plausibly add a real, if
modest, drag over time — not estimated here rather than guessed at.
**Binance's exact tiered maintenance-margin brackets** (which increase
for larger position sizes) aren't modeled either — irrelevant at this
account size, but this isn't literally Binance's own formula.

### The practical takeaway

**3x leverage is a reasonable, low-risk way to free up capital
efficiency on this exact strategy — the backtest shows no meaningful
downside.** 5x is a real, quantified tradeoff (roughly a point of CAGR)
rather than a red flag. 10x is where this stops being "the same
strategy" — not because it blows up outright in this particular 6-year
window, but because it silently degrades win rate and profit factor by
cutting winners short, and does so worse on SOL than BTC specifically. A
middle path worth knowing about: the leverage *tier* an exchange offers
doesn't have to equal the leverage actually *used* — posting more margin
than the required minimum (e.g. choosing 10x tier access but funding the
position at a 3-5x margin ratio) removes the liquidation risk shown here
entirely, at the cost of tying up more collateral, which is a real,
practical way to get some of leverage's capital-efficiency benefit
without its risk — not tested here, but the mechanism this section's own
numbers point toward.
