# paper/

**Phase 3 — not built yet.**

Will hold a live paper-trading executor that reads a finalized strategy
config and places paper orders via the IBKR connector in `data/`. Intended
sequence: data connector (done) -> indicators -> strategy -> backtest ->
this. Do not build against this folder until the backtest phase has
validated a strategy config worth trading.
