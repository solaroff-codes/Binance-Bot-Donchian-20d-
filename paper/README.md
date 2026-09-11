# paper/

Live paper-trading executor. Only one strategy has ever cleared the bar
to be run here: **BTC + Donchian channel breakout**, the sole result in
this project's history with real train/test, bear-market, and
second-holdout evidence behind it (`backtest/README.md`'s "Stress-testing
BTC + Donchian" section). Nothing else — not the trendline cascade, not
the original Wyckoff/Fib/Elliott confluence strategy, not any futures
instrument — has been validated enough to belong here.

## `paper_trader.py` — simulated paper trading (no account, no API key)

Fetches fresh **public** Binance market data (no authentication — the
same endpoint `data/binance_connector.py` already uses for backtesting),
regenerates Donchian breakout signals with the exact fixed parameters
used throughout the validated backtest, and re-simulates a paper account
using `backtest/engine.py`'s own `simulate_trades()` / `CostModel` /
`PositionSizer` — the identical, already-tested code, not a
reimplementation. **No Binance account, no API credentials, no order is
ever placed anywhere.** This only tells you what the strategy *would*
have done; it does not test real order-execution mechanics (see "Testnet"
below for that).

Stateless except for one persisted date (`paper_start_date`, set on first
run and never changed) — every run recomputes the full trade history
since paper trading began from scratch, so a missed day, a crash, or a
clock issue can't desync it.

### Running it

```powershell
python paper\paper_trader.py
```

Run this **once per day, any time after 00:00 UTC** (Binance's daily
candle close) — earlier and "today"'s bar hasn't closed yet, so there's
nothing new to evaluate. Running it more than once on the same day is
harmless (it's a pure recomputation), it just won't have anything new to
report.

First run starts a fresh $10,000 (notional) account, flat, as of the
latest closed daily bar, and records that date to
`paper/state/btc_donchian_paper_state.json` (gitignored — machine-local
state, not project history). Every run after that reports:
- any trade(s) closed since the last run (win/loss, P&L, R-multiple)
- the currently open position, if any
- running win rate / profit factor / total P&L for the whole paper period

Full trade log (every paper trade, updated each run): `paper/output/btc_donchian_paper_trades.csv` (gitignored).

Risk settings — `paper/paper_trader.py`'s module constants:
`ACCOUNT_SIZE = 10_000`, `RISK_PCT = 0.01` (the conservative end of the
1-2% range used throughout this project's backtests; change the constant
directly to adjust).

### Automating it (Windows Task Scheduler)

1. Open Task Scheduler → Create Basic Task.
2. Trigger: Daily, some time after 00:05 UTC (convert to local time —
   e.g. if local time is UTC-5, that's 19:05 the previous day).
3. Action: Start a program —
   - Program/script: full path to `.venv\Scripts\python.exe`
   - Arguments: full path to `paper\paper_trader.py`
   - Start in: the project root (`C:\Users\Privremeno-povremeni\Desktop\Claude Quant`)
4. Save. Check `paper/output/btc_donchian_paper_trades.csv` periodically,
   or redirect the task's output to a log file if you want a persistent
   run history beyond what's printed to console.

## Testnet (real order placement, still no real money) — not built yet

A separate, later option if you want to test actual order mechanics
(latency, real fills, API auth) rather than pure simulation: a Binance
**Spot Testnet** account (testnet.binance.vision — free, fake funds, no
KYC, fully separate from any real Binance account) and a testnet API
key/secret, supplied via a local `.env` file (gitignored) rather than
ever pasted into a chat or committed. Not built until/unless you decide
Option A's simulation isn't enough on its own.
