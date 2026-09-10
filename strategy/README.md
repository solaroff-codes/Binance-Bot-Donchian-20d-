# strategy/

Combines `indicators/` outputs into entry/exit signals. Config-driven —
instrument, timeframe, and parameter set live in YAML under
`config/strategies/`, so the same signal-generation code runs unchanged
across GC/CL/ES/NQ and different parameter sets.

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
not simulate fills, P&L, or compute performance metrics. That's `backtest/`'s
job, not yet built.

`scripts/run_strategy.py <config_name>` loads cached data for the config's
instrument/timeframe and prints any signals found (defaults to `GC_1day`).

Tests: `tests/test_strategy_signals.py`, built against a synthetic dataset
constructed to trigger exactly one known long signal — verifies the full
confluence chain (range detection -> breakout -> impulse -> retracement
zone -> confirming swing) produces the expected entry/stop/target.
