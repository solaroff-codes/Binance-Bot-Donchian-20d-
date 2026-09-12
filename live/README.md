# live/

**Testnet only.** Every code path here talks to Binance's sandboxed
**Futures Testnet** (`https://testnet.binancefuture.com`) — fake funds,
real order mechanics, fully separate from any real Binance account.
Going live with real money is a separate, later, explicit decision (see
"Going live" at the bottom) — not a flag flip, and not something this
README walks through yet.

Same validated configuration as `paper/paper_trader.py`: BTC + SOL + BNB,
Donchian channel breakout, 3% risk per trade, full compounding, 3x
leverage (see `paper/README.md` and `backtest/README.md`'s "Trading this
on leveraged futures instead of spot" section for how this was chosen —
3x is effectively free capital efficiency, not something that changes the
strategy's own numbers). Signal generation is identical to the paper
trader (same public data, same fixed Donchian parameters) — only the
execution layer (placing real testnet orders) is new.

## What this actually does

`live_trader.py`, run once daily:
1. Fetches fresh **public** Binance market data per symbol (no auth
   needed — same as the paper trader) and regenerates signals.
2. Queries the **real testnet exchange** for each symbol's current
   position and open orders — the exchange is the source of truth for
   "are we in a trade," not a local file, so a missed run or a manual
   intervention can't leave the bot confused about its own state.
3. If a position closed since the last check (stop or target order shows
   `FILLED`), reconciles it: logs the trade, updates this sleeve's
   tracked equity, and cancels the now-orphaned other order (Binance
   Futures has no true OCO between `STOP_MARKET` and
   `TAKE_PROFIT_MARKET` reduce-only orders — one filling doesn't
   auto-cancel the other).
4. If flat and a fresh breakout signal fired today, sizes a position off
   this sleeve's tracked equity and places a market entry plus stop-loss
   and take-profit orders — or, under `DRY_RUN`, just logs what it would
   have done.

### DRY_RUN — read the real exchange, never write to it

`DRY_RUN = True` is the default in `live_trader.py` and should stay that
way until you've watched it run for a few days and are confident the
decisions it's making are sane. In dry-run mode, every *read* (position
and order queries) still happens against the real testnet API — the
reconciliation logic is genuinely exercised — but no order is ever
placed or cancelled; what would have happened is printed instead.
Compare its dry-run decisions against what `paper/paper_trader.py`
reports for the same day — they should always agree, since they share
the exact same signal-generation code.

Only flip `DRY_RUN = False` once you're ready to see real (testnet)
orders actually appear in your Binance Futures Testnet account.

### A real simplification worth understanding: "3 sleeves" is bookkeeping, not segregation

Binance Futures pools all USDT margin into **one account balance** —
there's no native way to wall off "BTC's $333" from "SOL's $333" the way
this project's backtests (and this bot's own tracked equity in
`live/state/live_trader_state.json`) model it. The three-sleeve
structure is this bot's own accounting convention, exactly like
`paper_trader.py`'s — not a real barrier preventing one sleeve's sizing
from drawing against capital "belonging" to another if the account's
real total balance is tighter than the sum of tracked sleeve equities.
On testnet this has no real consequence (the funds are fake); worth
re-reading before ever taking this live.

## Setup

### 1. Binance Futures Testnet account (you do this — not something Claude can do for you)

1. Go to testnet.binancefuture.com and register (separate from any real
   Binance account, separate from the Spot Testnet mentioned in
   `paper/README.md` — this is the derivatives/futures sandbox
   specifically).
2. Generate an API key/secret with **futures trading permission**.
3. **Never paste the key/secret into this chat.** They go into one of
   two places, both outside the conversation:
   - **Local testing**: a `.env` file in the project root (already
     gitignored) —
     ```
     BINANCE_TESTNET_API_KEY=...
     BINANCE_TESTNET_API_SECRET=...
     ```
     `live_trader.py` reads these from the environment
     (`os.environ`) — if you use a `.env` file, load it into your shell
     session before running (e.g. a small `Get-Content .env | ...`
     loader, or a package like `python-dotenv` if you'd rather not do it
     by hand — not currently wired in automatically).
   - **GitHub Actions (the scheduled run)**: the repo's
     **Settings → Secrets and variables → Actions → New repository
     secret**, named exactly `BINANCE_TESTNET_API_KEY` and
     `BINANCE_TESTNET_API_SECRET`. Paste the values directly into
     GitHub's UI there — never anywhere else.

### 2. Running it locally

```powershell
python live\live_trader.py
```

Prints what it found and did (or would do, under `DRY_RUN`) per symbol,
then a total tracked equity line. State persists to
`live/state/live_trader_state.json`, trade log to
`live/output/live_trades.csv` — **both are committed to the repo**
(unlike `paper/state/` and `paper/output/`, which are gitignored),
because GitHub Actions runners are ephemeral and need this history to
survive between scheduled runs.

### 3. The scheduled run (GitHub Actions)

`.github/workflows/live_trader.yml` runs daily at 00:05 UTC (and can be
triggered manually from the repo's Actions tab via "Run workflow", useful
for testing without waiting for the schedule). It installs
`requirements.txt`, runs `live/live_trader.py` with the two secrets
injected as environment variables, then commits any changed state/output
files back to the repo under a `live-trader-bot` commit identity — so
the repo will accumulate one automated commit per day this finds
something to record. Since this repo is public, that history is visible
to anyone; nothing sensitive is in it (the secrets never touch the repo
itself, only GitHub's encrypted secret store).

## Known limitations, stated plainly rather than glossed over

- **Approximate realized P&L.** Reconciliation computes P&L from
  entry/exit price times quantity — it doesn't query Binance's own
  income/trade history for the exact realized amount including fees.
  Close enough for this bot's own position-sizing bookkeeping; not a
  substitute for checking the testnet account's own P&L if precision
  matters.
- **One-way position mode assumed** — this bot doesn't manage Binance's
  hedge mode (separate long and short positions on the same symbol
  simultaneously); make sure the testnet account is in one-way mode.
- **Order precision** is rounded to the symbol's actual `LOT_SIZE`/
  `PRICE_FILTER` steps (queried from `futures_exchange_info()`), but this
  hasn't been exercised against a live order book yet — the first
  non-dry-run runs are exactly what would surface any remaining rounding
  or minimum-notional issue.

## Going live (not built)

Switching from testnet to real trading is a separate, explicit decision
this project hasn't made — it would mean new API keys (a real Binance
account, not testnet), removing the `FUTURES_TESTNET_URL` override in
`live/binance_futures_client.py`, and — more importantly — enough
confidence from testnet runs that the execution logic is solid before
anything real is at stake. Not a checklist to rush through once testnet
"looks fine" for a few days.
