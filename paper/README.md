# paper/

Live paper-trading executor. Only one configuration has ever cleared the
bar to run here: **BTC + SOL + BNB, Donchian channel breakout, 3% risk
per trade, full compounding** — a three-asset portfolio, each sleeve
independently given the full bear-market/second-holdout stress test
(`backtest/README.md`'s "Stress-testing BTC + Donchian" and "Increasing
profit on the 1:1.5 version" sections), with the risk/compounding level
chosen after scaling through a conventional 1-5% band and weighing the
result against buy-and-hold's own return/drawdown profile (`backtest/
README.md`'s "Is 25-28% over 6 years actually good?" section). Backtested
~12.3% CAGR, ~15.6% max drawdown on the user's real $1,000 starting
capital. Nothing else — not the trendline cascade, not the original
Wyckoff/Fib/Elliott confluence strategy, not ETH or XRP (both checked,
both failed the same stress test BTC/SOL/BNB passed), not any futures
instrument — has been validated enough to belong here.

**Intended execution venue: leveraged futures at 3x, not spot** — chosen
after `backtest/README.md`'s "Trading this on leveraged futures instead
of spot" section found 3x leverage effectively free (near-zero
liquidation risk, performance indistinguishable from spot) while 5x/10x
introduce real, quantified degradation, worse on SOL than BTC/BNB
specifically. This simulation's own numbers don't change with leverage
(position sizing here is risk-based, not leverage-based — see that
section for why) — 3x is a statement about how much collateral to post
when executing this for real, not something this script models directly.
Do not use higher leverage than 3x on this configuration without
re-reading that section first.

This supersedes two earlier versions of this paper trader: a BTC-only,
1% risk, fixed-sizing version, and a brief $10,000-account version of the
current 3-asset/3%/compounding configuration (the $10,000 figure was this
project's backtesting reference account throughout, not the user's actual
capital — corrected to the real $1,000 once that came up). Both earlier
versions' state/output files (`btc_donchian_paper_state.json` /
`btc_donchian_paper_trades.csv`), if present from before, are left
untouched but no longer updated; the $10,000-account run's
`portfolio_paper_state.json` was reset (deleted, not archived — it was
one day old, zero trades logged, pure simulation state, nothing lost) so
the paper account restarts cleanly at the real $1,000 baseline rather
than carrying forward a fictional balance.

## `paper_trader.py` — simulated paper trading (no account, no API key)

Fetches fresh **public** Binance market data per symbol (no
authentication — the same endpoint `data/binance_connector.py` already
uses for backtesting), regenerates Donchian breakout signals with the
exact fixed parameters used throughout the validated backtest, and
re-simulates each sleeve using `backtest/engine.py`'s own
`simulate_trades_compounding()` / `CostModel` — the identical,
already-tested code, not a reimplementation. **No Binance account, no API
credentials, no order is ever placed anywhere.** This only tells you what
the strategy *would* have done; it does not test real order-execution
mechanics (see "Testnet" below for that).

Stateless except for one persisted date (`paper_start_date`, shared
across all three sleeves, set on first run and never changed) and a
per-symbol last-seen-exit marker used only for "what closed since last
run" reporting. Every run recomputes each sleeve's full trade history
since paper trading began from scratch — including its own compounding
equity curve — rather than incrementally patching saved state, so a
missed day, a crash, or a clock issue can't desync it.

### Running it

```powershell
python paper\paper_trader.py
```

Run this **once per day, any time after 00:00 UTC** (Binance's daily
candle close) — earlier and "today"'s bar hasn't closed yet on at least
one symbol, so there's nothing new to evaluate. Running it more than once
on the same day is harmless (it's a pure recomputation), it just won't
have anything new to report.

First run starts a fresh $1,000 total portfolio ($333.33 per sleeve),
flat on all three, as of the latest daily bar all three symbols have in
common, and records that date to
`paper/state/portfolio_paper_state.json` (gitignored — machine-local
state, not project history). Every run after that reports, per symbol:
- any trade(s) closed since the last run (win/loss, P&L, R-multiple)
- the currently open position, if any

— then a combined portfolio summary: each sleeve's current (compounded)
equity, total portfolio value and return, and combined win rate / profit
factor across all three sleeves' closed trades.

Full trade log (every paper trade across all three sleeves, tagged by
symbol, updated each run): `paper/output/portfolio_paper_trades.csv`
(gitignored).

Configuration — `paper/paper_trader.py`'s module constants:
`SYMBOLS = ["BTC", "SOL", "BNB"]`, `ACCOUNT_SIZE = 1_000` (the user's real
starting capital), `RISK_PCT = 0.03`, `COMPOUNDING_FRACTION = 1.0`
(0.0 = fixed sizing, 1.0 = full reinvestment, 0.5 = "half-Kelly" — see
`backtest/README.md`'s risk-scaling section for the alternatives this was
chosen over — at $1,000, 5%/full-compounding backtests to ~19.9% CAGR
with a much rougher ~36.1% max drawdown, 5%/half-Kelly to ~17.3% CAGR
with ~24.0% drawdown — before changing these).

### Automating it (Windows Task Scheduler)

1. Open Task Scheduler → Create Basic Task.
2. Trigger: Daily, some time after 00:05 UTC (convert to local time —
   e.g. if local time is UTC-5, that's 19:05 the previous day).
3. Action: Start a program. Task Scheduler doesn't capture a program's
   console output by itself — running `python.exe` directly means every
   printed status line just vanishes. To keep a persistent log, run it
   through `cmd.exe` with redirection instead:
   - Program/script: `cmd.exe`
   - Add arguments (one line, paths adjusted if this project ever moves):
     ```
     /c ""C:\Users\Privremeno-povremeni\Desktop\Claude Quant\.venv\Scripts\python.exe" "C:\Users\Privremeno-povremeni\Desktop\Claude Quant\paper\paper_trader.py" >> "C:\Users\Privremeno-povremeni\Desktop\Claude Quant\paper\output\paper_trader.log" 2>&1"
     ```
     The outer `"..."` around the whole argument string is required by
     `cmd /c` because the inner paths contain spaces ("Claude Quant") —
     without it cmd misparses where the command ends. `>>` appends rather
     than overwrites, so the log accumulates every run; `2>&1` folds
     errors into it too, so a crashed run is visible instead of silent.
   - Start in: the project root (`C:\Users\Privremeno-povremeni\Desktop\Claude Quant`)
4. Save. Check `paper/output/portfolio_paper_trades.csv` for the trade
   log, or `paper/output/paper_trader.log` for the full run history
   (both gitignored — machine-local, not project history).

## Testnet (real order placement, still no real money) — not built yet

A separate, later option if you want to test actual order mechanics
(latency, real fills, API auth) rather than pure simulation: a Binance
**Spot Testnet** account (testnet.binance.vision — free, fake funds, no
KYC, fully separate from any real Binance account) and a testnet API
key/secret, supplied via a local `.env` file (gitignored) rather than
ever pasted into a chat or committed. Not built until/unless you decide
Option A's simulation isn't enough on its own.
