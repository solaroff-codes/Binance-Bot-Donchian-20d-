# Quant Research — Wyckoff / Fibonacci / Elliott Wave

Personal research project: a modular system for backtesting a
discretionary-turned-systematic strategy (Wyckoff accumulation/distribution +
Fibonacci retracements + Elliott Wave confluence) across gold futures (GC,
COMEX), crude oil futures (CL, NYMEX), and equity index futures (ES, NQ,
CME). Data and execution via Interactive Brokers. No crypto for now.

## Layout

- `config/` — instrument definitions (`instruments.yaml`), strategy configs
  (`strategies/*.yaml`, including `*_continuous.yaml` variants)
- `data/` — IBKR connector (`ibkr_connector.py`), futures front-month
  resolution (`contracts.py`), local parquet cache (`cache.py`, backed by
  `data/cache/`, gitignored), and back-adjusted continuous-contract
  stitching across multiple historical contract months (`continuous.py`)
- `indicators/` — reusable indicator functions: swing high/low detection
  (`swings.py`), Fibonacci retracement/extension levels (`fibonacci.py`),
  Wyckoff trading range detection (`wyckoff.py`)
- `strategy/` — config-driven entry/exit signal logic combining the three
  indicator modules into confluence signals (`signals.py`), configs in
  `config/strategies/*.yaml`
- `backtest/` — trade simulation + performance metrics (`engine.py`), a
  runner that sweeps multiple instrument configs into one comparable
  summary (`runner.py`), and a parameter-grid sweep utility (`sweep.py`)
- `paper/` — live paper-trading executor. Phase 3, not yet built.
- `scripts/` — runnable entry points: `fetch_all_instruments.py`,
  `build_continuous_contracts.py`, `run_strategy.py`, `run_backtest.py`,
  `sweep_params.py`, `sweep_entry_exit.py`, plus smoke-test/sanity scripts
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
`lastTradeDateOrContractMonth`). Re-running a script that requests
already-cached data reads from disk instead of hitting the IBKR API again;
pass `force_refresh=True` to `data.cache.fetch_or_load` (or `--force` to
`scripts/fetch_all_instruments.py`) to bypass the cache.

A single contract's own history is limited to however long it's actually
been listed (~1-3 years depending on instrument and IBKR's own
expired-contract retention — not something this project controls).
`data/continuous.py` builds a longer, back-adjusted series by stitching
every historical contract IBKR still has on file, cached separately to
`data/cache/{symbol}/{timeframe}/continuous.parquet` and loaded via
`data.cache.load_continuous()`. Zero-volume bars (IBKR placeholder data
from before a contract was actively traded — up to 45% of a raw
single-contract fetch, empirically) are dropped at usage time via
`data.cache.drop_untraded_bars()`, not in the cache itself.

## Running the pipeline end to end

```powershell
python scripts\fetch_all_instruments.py --force     # populate/refresh the parquet cache for GC/CL/ES/NQ
python scripts\build_continuous_contracts.py         # build back-adjusted continuous series (slower, many IBKR calls)
python scripts\run_strategy.py GC_1day               # generate signals for one config
python scripts\run_backtest.py                       # simulate trades + metrics for every config, or pass names
python scripts\sweep_params.py                       # search range-detection thresholds per instrument/timeframe/source
python scripts\sweep_entry_exit.py                   # search entry/exit params around a known-productive threshold
```

## Status

Data connector, indicators, strategy signal generation, the backtest
engine, parameter-grid sweeping, and continuous-contract stitching are all
built and verified against real cached data (33 unit tests passing; see
each folder's README for details, especially `backtest/README.md`'s
"Current results" for the full findings).

Headline finding so far: **CL shows the most consistent edge** of any
instrument tested — profit factor ~9-14 at `range_max_pct=0.08` on 1-hour
bars, confirmed across both single-contract and continuous-contract data
independently. GC looks promising but its best result doesn't fully
replicate across datasets (worth skepticism). ES has not produced a
tradeable configuration in any test run. NQ is promising but under-sampled
(low single digits of trades). None of these sample sizes are yet large
enough for a real capital-allocation decision.

Not yet built: a parameter-grid sweep of `retracement_zone` /
`target_extension_ratio` specifically against the continuous-contract data
(the existing one only used single-contract data), true historical
volume-crossover rolls for continuous stitching (currently a simpler fixed
days-before-expiry rule — documented tradeoff in `data/continuous.py`),
and literal-price (non-back-adjusted) continuous series for anything that
needs real historical price levels rather than point-difference-preserving
adjusted ones. Paper trading (`paper/`) is phase 3 and intentionally
untouched until a config is validated through backtesting.
