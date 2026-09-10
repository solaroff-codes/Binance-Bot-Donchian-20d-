# indicators/

Reusable, independently testable indicator functions — no strategy or
backtest logic here. Each function takes OHLCV data (and parameters) in and
returns levels/labels out: no IBKR calls, no config-file reads, no side
effects.

- [`swings.py`](swings.py) — N-bar fractal swing high/low detection
  (`detect_swing_points`, `get_swing_points`), plus `alternate_swings()` to
  collapse raw fractals into a strictly alternating high/low sequence —
  the input Elliott Wave counting needs.
- [`fibonacci.py`](fibonacci.py) — retracement levels (0.236–1.0) and
  extension levels (1.272/1.618/2.0) between two swing prices, direction-
  agnostic (works for both up- and down-swings).
- [`wyckoff.py`](wyckoff.py) — trading range detection (`detect_trading_ranges`):
  identifies periods of volatility contraction (sideways consolidation) and
  flags each bar as inside a range, breaking out, or breaking down
  (`label_bar_state`). **Scope note:** this covers range detection only —
  spring/upthrust signals and full A–E phase labeling are intentionally
  deferred as a more subjective layer to build later.

Tests live in `tests/test_swings.py`, `tests/test_fibonacci.py`,
`tests/test_wyckoff.py` — run with `pytest tests/`.

`scripts/sanity_check_indicators.py` runs all three modules against real
cached GC data (not synthetic fixtures) and prints what they find — useful
for eyeballing behavior on actual market data after a change.

## A data-shape note learned while testing

The `date` column in **daily** cached bars holds plain `datetime.date`
objects (from IBKR/`ib_async`), while **intraday** bars (e.g. 1-hour) carry
full timezone-aware `Timestamp`s. The indicator functions here don't call
date-specific methods on timestamps (just comparison/sorting), so this
doesn't currently cause problems — but keep it in mind if you add logic
that assumes one type or the other.
