# Quant Research — Wyckoff / Fibonacci / Elliott Wave + Trendlines

Personal research project: a modular system for backtesting discretionary-
turned-systematic strategies across gold (GC, COMEX), silver (SI, COMEX),
WTI crude oil (CL, NYMEX), Brent crude oil (BZ, NYMEX), and equity index
futures (ES, NQ, CME) via Interactive Brokers, plus BTC/ETH/SOL via
Binance's public spot-market API. Two independent strategies live here —
a Wyckoff accumulation/distribution + Fibonacci retracement + Elliott Wave
confluence rule, and a multi-timeframe trendline bounce/break cascade
(monthly→weekly→daily→[4h→1h for futures]).

## Layout

- `config/` — IBKR instrument definitions (`instruments.yaml`), crypto
  instrument definitions (`crypto_instruments.yaml`), strategy configs
  (`strategies/*.yaml`, including `*_continuous.yaml` variants)
- `data/` — IBKR connector (`ibkr_connector.py`), futures front-month
  resolution (`contracts.py`), Binance public-API connector for crypto
  (`binance_connector.py`, no auth needed), local parquet cache
  (`cache.py`, backed by `data/cache/`, gitignored), and back-adjusted
  continuous-contract stitching across multiple historical contract
  months (`continuous.py`)
- `indicators/` — reusable indicator functions: swing high/low detection
  (`swings.py`), Fibonacci retracement/extension levels (`fibonacci.py`),
  Wyckoff trading range detection (`wyckoff.py`), trendline fitting +
  bounce/break events (`trendlines.py`)
- `strategy/` — config-driven entry/exit signal logic, two independent
  strategies: the Wyckoff/Fib/Elliott Wave confluence rule (`signals.py`,
  configs in `config/strategies/*.yaml`) and the multi-timeframe trendline
  cascade (`trendline_signals.py`, configs in
  `config/trendline_strategies/*.yaml`) — see `strategy/README.md`
- `backtest/` — trade simulation + performance metrics (`engine.py`,
  shared by both strategies, with realistic `CostModel`/`PositionSizer`
  support), a trailing-stop exit variant (`trailing.py`), a runner that
  sweeps multiple instrument configs into one comparable summary
  (`runner.py`), and a parameter-grid sweep utility (`sweep.py`)
- `paper/` — live paper-trading executor. Phase 3, not yet built.
- `scripts/` — runnable entry points: `fetch_all_instruments.py`,
  `fetch_crypto_data.py`, `build_continuous_contracts.py`,
  `run_strategy.py`, `run_backtest.py`, `run_trendline_backtest.py`,
  `run_realistic_backtest.py`, `run_crypto_realistic_backtest.py`,
  `sweep_params.py`, `sweep_entry_exit.py`, `sweep_trendline_params.py`,
  `run_combined_confluence.py`, plus smoke-test/sanity scripts
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
python scripts\run_trendline_backtest.py              # trendline cascade: every config, or pass names
python scripts\fetch_crypto_data.py                   # pull 6y of BTC/ETH/SOL from Binance (no auth needed)
python scripts\run_crypto_realistic_backtest.py       # same honest treatment, applied to crypto
```

## Status

Data connectors (IBKR + Binance), indicators, both strategies' signal
generation, the shared backtest engine (now with realistic transaction
costs and dollar-risk position sizing, including a percentage-of-notional
commission model for crypto), a trailing-stop exit variant, and
continuous-contract stitching are all built and verified against real
cached data (52 unit tests passing; see each folder's README for details).

**Read `backtest/README.md`'s "The honest backtest" section first** — it
supersedes every profit-factor/win-rate figure reported anywhere else in
this project. Every earlier sweep result (both strategies) had zero
transaction costs, implicit 1-contract-per-trade sizing regardless of
account size, and reported the best cell of a large parameter sweep
without ever validating it on held-out data. Once realistic costs,
$10,000-account dollar-risk position sizing (1-2% per trade), and a
genuine train/test split were added:

- **Four of six instruments (GC, NQ, SI, BZ) cannot be traded at all** at
  1-2% risk on a $10k account — even the cheapest signal each one ever
  produced needs 2.1-8.0% of the account for a single contract of the
  smallest available version (checked directly against IBKR: SI has no
  true micro contract, BZ has no smaller contract at all). This is a
  structural mismatch between the trendline strategy's stop placement and
  a small account, not a parameter-tuning gap.
- **CL is the only instrument with a real out-of-sample test, and it lost
  money in every configuration tried** (both risk levels, both exit
  types) — a sharp contrast to its healthy-looking training-period numbers
  (profit factor 1.5-3.2). 1-2 test trades is too small to call the edge
  fake, but it is conclusively not confirmed.
- **ES only sizes at 2% risk and is marginal-to-losing even in-sample**
  (profit factor 0.88-1.01).
- **A trailing stop did not outperform a fixed R-multiple target** in any
  head-to-head comparison run — it exited winners earlier without
  meaningfully reducing drawdown, and in one case was net losing where the
  fixed-target version was profitable.
- **BTC/ETH/SOL (6 years of Binance data) found zero signals of any kind
  since May 2022**, at every parameter combination tested. Every signal
  any of the three ever produced falls in a 20-month window from early
  2021 to mid-2022 — the COVID-era crypto boom and its crash — despite
  BTC alone rallying from ~$16-20k to $77k+ since. There is no
  out-of-sample data for crypto at all: the entire signal history
  predates the train/test split point by two and a half years. Not a
  bug — verified directly across all three symbols and every tested
  parameter combination.

Everything above this in the project's history — the "PF 9-45" sweep
results, the confluence-strategy "PF ~9-14" figures, the combined
Wyckoff-confluence "PF 10.5" cells — is real as far as the mechanics go,
but was never tested for costs, realistic sizing, or out-of-sample
survival. Treat those numbers as upper bounds under favorable conditions,
not expectations.

Not yet built: extending the realistic-cost/sizing/train-test treatment to
the confluence strategy (only the trendline cascade has been put through
it so far), building or sourcing a true micro Brent contract or accepting
BZ needs a larger account, continuous-contract data for Silver/Brent
(single-contract only so far), true historical volume-crossover rolls for
continuous stitching (currently a simpler fixed days-before-expiry rule —
documented tradeoff in `data/continuous.py`), a cascade-structure sweep
for crypto (skipped — sub-daily crypto data isn't computationally
tractable with the current walk-forward trendline fit; see
`backtest/README.md`'s crypto section), and a faster
`walk_forward_trendlines` refit strategy in general, which would unblock
both of those. Paper trading (`paper/`) is phase 3 and intentionally
untouched until a config is
validated through backtesting — which, per the above, none currently are.
