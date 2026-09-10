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
- `indicators/` — reusable indicator functions (Fibonacci levels, Wyckoff
  phase/range detection, swing high/low detection). Not yet built.
- `strategy/` — config-driven entry/exit signal logic combining indicators.
  Not yet built.
- `backtest/` — backtest engine + performance metrics, supports sweeping
  instruments/params. Not yet built.
- `paper/` — live paper-trading executor. Phase 3, not yet built.
- `scripts/` — one-off/manual scripts, e.g. `test_connector.py`
- `tests/` — automated tests (pytest)

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

## Status

Phase 1 (this pass): project scaffold + IBKR data connector with parquet
caching and front-month rollover. Indicators, strategy, and backtest logic
come next, one at a time. Paper trading is phase 3.
