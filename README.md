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
  bounce/break events (`trendlines.py`), and standard technical indicators
  — SMA/EMA, RSI, MACD, Bollinger Bands, ATR, Donchian channels,
  Supertrend (`technical.py`)
- `strategy/` — config-driven entry/exit signal logic: the Wyckoff/Fib/
  Elliott Wave confluence rule (`signals.py`, configs in
  `config/strategies/*.yaml`), the multi-timeframe trendline cascade
  (`trendline_signals.py`, configs in `config/trendline_strategies/*.yaml`),
  and 8 popular technical strategies built on `indicators/technical.py`
  (`technical_signals.py`) — see `strategy/README.md`
- `backtest/` — trade simulation + performance metrics (`engine.py`,
  shared by both strategies, with realistic `CostModel`/`PositionSizer`
  support), a trailing-stop exit variant (`trailing.py`), a runner that
  sweeps multiple instrument configs into one comparable summary
  (`runner.py`), and a parameter-grid sweep utility (`sweep.py`)
- `paper/` — live paper-trading executor. `paper_trader.py` forward-tests
  BTC + Donchian breakout (the one validated strategy — see Status below)
  against live public Binance data with no account or API key; see
  `paper/README.md`.
- `scripts/` — runnable entry points: `fetch_all_instruments.py`,
  `fetch_crypto_data.py`, `build_continuous_contracts.py`,
  `run_strategy.py`, `run_backtest.py`, `run_trendline_backtest.py`,
  `run_realistic_backtest.py`, `run_crypto_realistic_backtest.py`,
  `run_strategy_showdown.py` (8 technical strategies x BTC/ETH/SOL),
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
- **The trendline cascade found zero signals in BTC/ETH/SOL since May
  2022**, at every parameter combination tested (6 years of Binance data).
  Every signal any of the three ever produced falls in a 20-month window
  from early 2021 to mid-2022 — the COVID-era crypto boom and its crash —
  despite BTC alone rallying from ~$16-20k to $77k+ since. No
  out-of-sample data for crypto exists for this strategy at all. Not a
  bug — verified directly across all three symbols and every parameter
  combination.
- **This project's best evidence of a real edge, found anywhere:** 8
  popular technical strategies (EMA cross, RSI, MACD, Bollinger, Donchian
  breakout, Supertrend, and two confluence combos —
  `strategy/technical_signals.py`) tested against the same 6 years of
  crypto data, one fixed reasoned parameter set per strategy (no sweep —
  see `backtest/README.md`'s "strategy showdown" section for why). **BTC +
  Donchian channel breakout** is the one combination that held up on
  genuinely held-out data: 70 training trades (51% win rate, profit
  factor 1.53) and 24 test trades (54% win rate, profit factor 1.68) — win
  rate, profit factor, *and* drawdown all stable or improved out of
  sample, a believable ~15 trades/year frequency, and a plausible-sized
  edge (1.5-1.7 PF) rather than a sweep's inflated best cell. **Correction:**
  this used to say BTC-Donchian was the *only* combination that cleared
  train PF>1 and test PF>1 — re-checking the saved results directly found
  that's wrong; 4 of the 24 combinations pass that filter (ETH+EMA
  crossover and SOL+MACD crossover also do, on thinner samples never
  separately stress-tested). BTC-Donchian is still the strongest by a
  clear margin — see `backtest/README.md` for the corrected accounting.
- **Taking Donchian breakout to futures (GC/CL/ES/NQ) surfaced the same
  risk-sizing wall the trendline cascade hit, now confirmed with a
  completely different strategy.** At 1-2% risk, GC/ES/NQ produce *zero*
  tradeable signals — every signal needs 4.9-8.7% of even a $100k-
  equivalent account (micro+$10k and full+$100k are mathematically
  identical here, verified directly) for just 1 contract. Removing that
  constraint to check the strategy itself: NQ echoes BTC-Donchian's shape
  (train PF 1.85, test PF 1.28, both profitable, no sign flip) on a much
  smaller sample (23 trades vs. 94); GC looks good in training but decays
  to breakeven in test; CL and ES show the noise-shaped sign-flipping
  pattern most of the crypto showdown's non-survivors did. None of this is
  actually tradeable within standard risk management at these account
  sizes — see `backtest/README.md` for what account size or stop redesign
  would be needed.
- **Re-tested everything (all 8 technical strategies + the trendline
  cascade) on all six futures instruments with a $100,000 account and
  full-size contracts — a 10x larger account than the original baseline —
  and nothing new survives.** Not one of the 96 technical-strategy test
  combinations produced a single closed trade in the held-out test period,
  on any instrument; checked directly that this is a genuine volatility/
  stop-width effect (confirmed on CL) and not a bug, and not something a
  bigger account fixes, since risk-as-a-percent-of-account doesn't change
  with account size. The trendline cascade's only profitable-both-sides
  result (Brent/BZ) rests on just 132 daily bars of single-contract
  history — too thin to trust. See `backtest/README.md`'s "Everything,
  re-tested on a $100,000 account" section.
- **Stress-tested BTC + Donchian against the exact gap the section above
  called out — a real bear market and a second holdout — and it survived
  both.** Every calendar year from 2020 through 2026 is individually
  profitable (profit factor 1.10-3.36, no sign flips), the 2022 bear
  market evaluated on its own (2021-11-10 top to 2022-11-21 bottom, never
  previously tested out-of-sample — it was inside the original training
  window) returned a profit factor of 2.19, and an independent second
  holdout split lands in the same 1.5-1.7 range as everything else. No
  parameters were fit or swept to get this — same fixed rule as always,
  just evaluated on time windows it had never individually been judged
  against. This is now the strongest evidence this project has produced;
  it is still short of live paper-trading validation. A follow-up
  calendar-year check found ETH-Donchian is a clean, consistent negative
  (full-period PF 0.98) but **SOL-Donchian is closer to BTC's picture than
  the original single-split test suggested** — full-period PF 1.51 (vs.
  BTC's 1.56) and every calendar year individually profitable, including
  2022 — though SOL hasn't had the full bear-market/second-holdout
  treatment yet, only this calendar-year check. See
  `backtest/README.md`'s "Stress-testing BTC + Donchian" section.
- **The original Wyckoff/Fibonacci/Elliott Wave confluence strategy —
  what this project started with — has now been given the same honest
  treatment, and does not clear the bar anywhere.** On daily bars it's too
  sparse to evaluate at all (0-2 signals across a multi-year history, on
  any of GC/CL/ES/NQ). On 1-hour bars (the timeframe its earlier, cost-free
  sweeps were actually run on) it produces small samples (3-17 trades)
  with suspiciously extreme numbers (PF 3.7-16.8) — checked directly why:
  a fixed, tight 0.5% stop paired with a variable, often much larger
  Fibonacci-extension target mechanically inflates R-multiples on
  whichever few trades happen to work, independent of any real edge. Not
  reported as a finding to act on; reported so it doesn't quietly become
  a "PF 16" headline the way earlier cost-free sweeps did before this
  project learned better. Closes the last strategy that hadn't been put
  through this project's honest methodology. See `backtest/README.md`'s
  "The original confluence strategy, finally given the honest treatment"
  section.
- **Paper trading is live.** `paper/paper_trader.py` forward-tests BTC +
  Donchian breakout against real public Binance market data — no
  account, no API key, no order ever placed, pure simulation reusing the
  same validated `simulate_trades`/`CostModel`/`PositionSizer` code the
  backtest used. Run once daily after 00:00 UTC; see `paper/README.md`
  for the exact setup and how to automate it. Nothing else in this
  project has reached this stage.

Everything above this in the project's history — the "PF 9-45" sweep
results, the confluence-strategy "PF ~9-14" figures, the combined
Wyckoff-confluence "PF 10.5" cells — is real as far as the mechanics go,
but was never tested for costs, realistic sizing, or out-of-sample
survival. Treat those numbers as upper bounds under favorable conditions,
not expectations.

Not yet built: building or sourcing a true micro Brent contract or
accepting BZ needs a larger account, continuous-contract data for
Silver/Brent (single-contract only so far), true historical
volume-crossover rolls for continuous stitching (currently a simpler
fixed days-before-expiry rule — documented tradeoff in
`data/continuous.py`), a cascade-structure sweep for crypto (skipped —
sub-daily crypto data isn't computationally tractable with the current
walk-forward trendline fit; see `backtest/README.md`'s crypto section), a
faster `walk_forward_trendlines` refit strategy in general (which would
unblock that), the full bear-market/second-holdout stress test applied to
SOL-Donchian (only the calendar-year check has been run there so far),
and a Binance Testnet paper-trading path for real order-placement
mechanics (`paper/README.md`'s "Testnet" section — the current paper
trader is pure simulation, no orders ever placed). Every strategy this
project has built has now been through the honest realistic-cost/sizing/
train-test treatment; BTC + Donchian breakout is the only one that
passed, and is the only one live in `paper/`.
