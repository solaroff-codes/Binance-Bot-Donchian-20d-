# indicators/

Reusable, independently testable indicator functions — no strategy or
backtest logic here. Planned modules (not yet built):

- `swings.py` — swing high/low detection (basis for Elliott Wave counting)
- `fibonacci.py` — Fibonacci retracement/extension level calculations
- `wyckoff.py` — Wyckoff phase/range detection helpers

Each function should take OHLCV data (and parameters) in, and return levels/
labels out — no IBKR calls, no config-file reads, no side effects.
