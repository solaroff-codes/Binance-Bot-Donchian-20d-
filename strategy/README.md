# strategy/

Combines `indicators/` outputs into entry/exit signals. Config-driven —
instrument, timeframe, and parameter set live in YAML, so the same
signal-generation code runs unchanged across GC/CL/ES/NQ and different
parameter sets. Three independent strategies live here — see each section
below.

## Confluence strategy (Wyckoff + Fibonacci + Elliott Wave)

Configs in `config/strategies/`.

- [`config.py`](config.py) — `StrategyConfig` dataclass + `load_config(name)`
  loader for `config/strategies/{name}.yaml`
- [`signals.py`](signals.py) — `generate_signals(df, config)`: the actual
  confluence rule combining all three indicator modules:
  1. A Wyckoff trading range forms, then price breaks out of it
     (`indicators.wyckoff`)
  2. Price impulses away from the broken boundary until the next
     opposite-direction swing point (`indicators.swings`) — treated as
     the first Elliott wave leg (wave 1 / wave A)
  3. Price retraces into a configured Fibonacci zone off that leg
     (`indicators.fibonacci`), without the range boundary being breached
     again (a re-entry there means the breakout failed)
  4. A new swing point in the breakout direction confirms the pullback
     held — that bar is the entry trigger, with a stop just beyond the
     confirming swing and a target at a Fibonacci extension of the impulse leg

This module only produces `Signal` objects (entry/stop/target) — it does
not simulate fills, P&L, or compute performance metrics. That's
`backtest/`'s job (`backtest/engine.py`).

`scripts/run_strategy.py <config_name>` loads cached data for the config's
instrument/timeframe and prints any signals found (defaults to `GC_1day`).
`scripts/run_backtest.py` runs the full backtest (see `backtest/README.md`
for results).

Tests: `tests/test_strategy_signals.py`, built against a synthetic dataset
constructed to trigger exactly one known long signal — verifies the full
confluence chain (range detection -> breakout -> impulse -> retracement
zone -> confirming swing) produces the expected entry/stop/target.

## Trendline cascade strategy

Configs in `config/trendline_strategies/`.

- [`trendline_config.py`](trendline_config.py) — `TrendlineStrategyConfig` +
  `load_config(name)` loader.
- [`trendline_signals.py`](trendline_signals.py) —
  `generate_trendline_signals(bias_dfs, trigger_df, config)`: a
  multi-timeframe cascade built on `indicators.trendlines`:
  1. On each of `config.bias_timeframes` (default monthly/weekly/daily/4h),
     compute a per-bar directional bias: bullish if a valid support
     trendline exists there and price is above it, bearish if a valid
     resistance line exists and price is below it, neutral otherwise.
  2. Combine across bias timeframes — every non-neutral one must agree, or
     there's no trade (one bullish + one bearish among them is a genuine
     conflict, not something to average out).
  3. On `config.trigger_timeframe` (default 1h), fire an entry when its own
     trendline produces a bounce or break event that agrees with the
     combined bias.
  4. Stop sits just beyond the trendline; target is a fixed R-multiple
     (`config.target_r_multiple`, default 2.0) — trendline trades don't
     have a natural Fibonacci-style measured target the way the confluence
     strategy does, so this is a simpler placeholder rule, not something
     derived from the trendline itself.

  Point-in-time correctness matters a lot for a walk-forward cascade like
  this — see `indicators.trendlines.walk_forward_trendlines`'s docstring.
  `TrendlineSignal` deliberately shares field names with `strategy.signals.
  Signal` (`entry_ts`, `direction`, `entry_price`, `stop_price`,
  `target_price`) so it's a drop-in input to `backtest.engine.
  simulate_trades()` — no duplicate simulation logic needed.

`scripts/run_trendline_backtest.py [names...]` loads every required
timeframe, generates signals, and runs the full backtest — see
`backtest/README.md` for results and important caveats (in particular:
this strategy generates far more signals than the confluence one, which is
what originally exposed a real bug in `backtest/engine.py` around
overlapping positions — see that file's docstring for
`enforce_single_position`).

Tests: `tests/test_trendline_signals.py` — bias point-in-time lookup, and
an end-to-end synthetic scenario verifying signals only fire in the
direction the bias agrees with, with the target R-multiple formula
verified algebraically rather than against brittle hardcoded floats.

## Technical strategies (EMA, RSI, MACD, Bollinger, Donchian, Supertrend, confluences)

- [`technical_signals.py`](technical_signals.py) — 8 popular technical
  strategies built on [`indicators/technical.py`](../indicators/technical.py),
  each producing `TechnicalSignal` objects (same drop-in field names as
  the other two strategies' signal types): `generate_ema_cross_signals`,
  `generate_rsi_reversion_signals`, `generate_macd_cross_signals`,
  `generate_bollinger_reversion_signals`, `generate_donchian_breakout_signals`,
  `generate_supertrend_signals`, and two confluence combos —
  `generate_trend_pullback_signals` (trend-filtered RSI pullback, the
  common "buy the dip in an uptrend" rule) and
  `generate_macd_rsi_confluence_signals` (MACD crossover filtered by RSI
  not already at an extreme). `STRATEGY_REGISTRY` maps names to functions
  for scripts that loop over all of them.

  All eight use ATR-based stop/target sizing (not a fixed percent or
  instrument-specific stop) — the standard, volatility-adjusted way to
  size risk in technical trading, which is why the same code applies
  sensibly across BTC/ETH/SOL's very different price scales without
  per-instrument tuning. RSI uses a 10-period window rather than the
  traditional 14 — commonly shortened for crypto's higher volatility.

  Unlike `indicators/trendlines.py`'s walk-forward trendline fitting,
  every indicator here is vectorized (single pandas pass, or a single
  O(n) loop for Supertrend's path-dependent bands) — fast even on tens of
  thousands of bars, which is what made testing these practical after the
  trendline cascade turned out to be computationally infeasible on
  crypto's hourly data.

`scripts/run_strategy_showdown.py` runs all 8 against BTC/ETH/SOL with a
genuine train/test split and realistic costs/sizing — see
`backtest/README.md`'s "strategy showdown" section for the full results.
Deliberately does not sweep parameters per strategy (one fixed, reasoned
parameter set each) — see that section for why.

Tests: `tests/test_technical_indicators.py` (each of the 7 indicator
functions, hand-verified where practical), `tests/test_technical_signals.py`
(4 of the 8 signal generators, covering the distinct mechanisms —
crossover, mean-reversion, breakout, confluence-gating — the rest share
the same tested wiring pattern).
