# backtest/

Takes a strategy config + date range, runs it against cached historical data
from `data/cache/`, and outputs performance metrics (win rate, profit
factor, max drawdown, Sharpe, trade log) to CSV/JSON. Should support
sweeping multiple instruments and parameter sets in one run, logging results
in a comparable format. Not yet built.
