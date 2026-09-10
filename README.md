# Quant Research — Wyckoff / Fibonacci / Elliott Wave

Personal research project: a modular system for backtesting a
discretionary-turned-systematic strategy (Wyckoff accumulation/distribution +
Fibonacci retracements + Elliott Wave confluence) across gold futures (GC,
COMEX), crude oil futures (CL, NYMEX), and equity index futures (ES, NQ,
CME). Data and execution via Interactive Brokers. No crypto for now.

## Layout

- `config/` — instrument definitions (`instruments.yaml`)
- `data/` — IBKR connector (`ibkr_connector.py`), futures front-month
  resolution (`contracts.py`), and local parquet cache (`cache.py`, backed
  by `data/cache/`, which is gitignored)
- `indicators/` — reusable indicator functions: swing high/low detection
  (`swings.py`), Fibonacci retracement/extension levels (`fibonacci.py`),
  Wyckoff trading range detection (`wyckoff.py`)
- `strategy/` — config-driven entry/exit signal logic combining the three
  indicator modules into confluence signals (`signals.py`), configs in
  `config/strategies/*.yaml`
- `backtest/` — trade simulation + performance metrics (`engine.py`), and
  a runner that sweeps multiple instrument configs into one comparable
  summary (`runner.py`)
- `paper/` — live paper-trading executor. Phase 3, not yet built.
- `scripts/` — runnable entry points: `fetch_all_instruments.py`,
  `run_strategy.py`, `run_backtest.py`, plus smoke-test/sanity scripts
- `tests/` — automated tests (pytest) — one file per module

## Setup

1. Create and activate the virtual environment (already created as `.venv`
   with Python 3.14):
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
2. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
3. Start TWS or IB Gateway, log into your **paper trading** account, and
   enable API access:
   - File/Configure > Global Configuration > API > Settings
   - Check "Enable ActiveX and Socket Clients"
   - Note the socket port: **7497** for TWS paper (default used by this
     project), **4002** for IB Gateway paper — pass `port=4002` to
     `IBKRConnector` if you're using Gateway
   - Add `127.0.0.1` to trusted IPs, or you'll get a connection prompt in
     TWS that has to be accepted manually
4. Run the connector smoke test:
   ```powershell
   python scripts\test_connector.py
   ```
   This resolves the GC front-month contract, pulls daily + 1-hour bars,
   caches them under `data\cache\GC\`, and prints a summary.

## Data caching

Historical bars are cached as parquet under `data/cache/{symbol}/{timeframe}/
{contract_month}.parquet`. Each futures expiry gets its own file (keyed by
`lastTradeDateOrContractMonth`), which keeps the door open for building a
stitched continuous-contract series later without reshaping the cache.
Re-running a script that requests already-cached data reads from disk
instead of hitting the IBKR API again; pass `force_refresh=True` to
`data.cache.fetch_or_load` to bypass the cache.

## Running the pipeline end to end

```powershell
python scripts\fetch_all_instruments.py   # populate/refresh the parquet cache for GC/CL/ES/NQ
python scripts\run_strategy.py GC_1day    # generate signals for one config
python scripts\run_backtest.py            # simulate trades + metrics for every config, or pass names
```

## Status

Data connector, indicators, strategy signal generation, and the backtest
engine are all built and verified against real cached data (23 unit tests
passing; see each folder's README for details). With the strategy
modules' current default (fairly tight) range-detection thresholds, a
sweep across GC/CL/ES/NQ daily bars currently finds 0 signals — none of
them have had a qualifying consolidation recently, which is the correct
result given current market conditions, not a bug. The full pipeline has
been separately confirmed to produce real trades/P&L/drawdown using
loosened thresholds.

Not yet built: a parameter-grid sweep (currently `run_sweep` takes an
explicit list of configs, one set of params per instrument) and any
intraday/1-hour strategy configs (only daily configs exist so far). Paper
trading (`paper/`) is phase 3 and intentionally untouched until a config
is validated through backtesting.
