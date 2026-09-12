"""
Forward-tests the validated 3-asset Donchian breakout portfolio
(BTC + SOL + BNB, each independently given the full bear-market/second-
holdout stress test -- see backtest/README.md's "Stress-testing BTC +
Donchian" and "Increasing profit on the 1:1.5 version" sections) against
live Binance market data, with zero real money or API credentials
involved.

Configuration: $1,000 total (the user's real starting capital -- not the
$10,000 reference account used throughout most of this project's
backtesting, rescaled here since crypto's fractional position sizing
makes the two proportionally identical, confirmed directly rather than
assumed), split three ways ($333.33 per sleeve), 3% risk per trade, full
compounding (compounding_fraction=1.0) -- the "balanced" configuration
chosen after scaling risk through a conventional 1-5% band and comparing
against buy-and-hold's own return/drawdown profile (backtest/README.md's
"Is 25-28% over 6 years actually good?" section): ~12.3% backtested CAGR,
~15.6% max drawdown ($1,000 -> ~$2,006 over 6 years in the backtest), a
real risk-preference choice, not the only defensible one -- see that
section for the 5%/half-Kelly (~17.3% CAGR, ~24.0% drawdown) and
5%/full-compounding (~19.9% CAGR, ~36.1% drawdown) alternatives if a
different point on the tradeoff is wanted. Change RISK_PCT and
COMPOUNDING_FRACTION below to switch.

Intended execution venue: leveraged futures (not spot), at 3x leverage
-- chosen after backtest/README.md's "Trading this on leveraged futures
instead of spot" section found 3x leverage is effectively free (13
liquidation events out of ~675 signals across all three symbols,
performance indistinguishable from spot) and 5x/10x introduce real,
quantified degradation. This simulation's numbers do not change based on
leverage choice (position size here is risk-based, not leverage-based --
see that section for why), so no code here models leverage explicitly;
3x leverage is a statement about how much collateral to actually post on
the futures exchange when executing this for real, not something this
script computes. Do not use higher leverage than 3x on this configuration
without re-reading that section first -- SOL in particular is far more
liquidation-sensitive than BTC or BNB at higher leverage.

This is pure simulation ("Option A"): no Binance account, no API key, no
order ever placed anywhere. Each run fetches fresh PUBLIC daily OHLCV
data per symbol (the same public endpoint data/binance_connector.py
already uses for backtesting -- no authentication required, this is
market data, not account access), regenerates Donchian breakout signals
with the exact same fixed parameters used throughout this project's
validated backtest, and re-simulates each sleeve using
backtest/engine.py's own simulate_trades_compounding()/CostModel -- the
identical, already-tested logic, not a reimplementation. That's
deliberate: the paper account's results are only meaningful as a live
extension of the backtest if they're produced by the same code path.

Design: stateless except for one persisted date (paper_start_date, set on
first run and shared across all three sleeves) and a per-symbol
last-seen-exit marker (for "what closed since last run" reporting only,
not authoritative). Every run recomputes each sleeve's FULL trade history
since paper trading began from scratch, rather than incrementally
patching saved state -- simpler, and immune to state-corruption from a
missed run, a crash mid-write, or a clock glitch, since every run is a
deterministic function of (market data, paper_start_date). Signals before
paper_start_date are excluded before simulating, so each sleeve always
starts flat (no open position, no inherited compounding history) on day
one, exactly like a real forward-testing account would.

This supersedes the earlier single-asset (BTC only, 1% risk, fixed
sizing) paper trader -- state/output files are named "portfolio_*" (not
"btc_donchian_*") so the two don't collide; the old BTC-only state file,
if present from an earlier run, is left untouched but no longer updated.

Run once daily, any time after Binance's daily UTC candle close
(00:00 UTC) -- earlier than that and "today"'s bar hasn't closed yet, so
there's nothing new to evaluate. To automate: Windows Task Scheduler, a
daily trigger, action = run this script with the project's venv python.
See paper/README.md for the exact setup steps.

    python paper/paper_trader.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, Trade, compute_metrics, simulate_trades_compounding
from data.binance_connector import fetch_full_history, load_crypto_instruments
from data.cache import drop_untraded_bars
from strategy.technical_signals import generate_donchian_breakout_signals

SYMBOLS = ["BTC", "SOL", "BNB"]  # the fully stress-tested 3-asset portfolio
TIMEFRAME = "1 day"
ACCOUNT_SIZE = 1_000.0  # the user's real starting capital -- see module docstring
PER_ASSET_CAPITAL = ACCOUNT_SIZE / len(SYMBOLS)
# The chosen configuration -- see this file's module docstring for how it
# was picked and what the alternatives are.
RISK_PCT = 0.03
COMPOUNDING_FRACTION = 1.0
SLIPPAGE_TICKS = 1.0
HISTORY_START_DATE = "2020-01-01"  # far enough back for Donchian(20)/ATR(14) warmup with room to spare

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_PATH = STATE_DIR / "portfolio_paper_state.json"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
TRADE_LOG_PATH = OUTPUT_DIR / "portfolio_paper_trades.csv"


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def load_state(path: Path = STATE_PATH) -> dict:
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=str)


def fetch_fresh_data(symbol: str, timeframe: str = TIMEFRAME) -> pd.DataFrame:
    """Fresh public OHLCV from Binance, with today's still-forming bar
    dropped (only fully closed daily bars are ever evaluated)."""
    df = fetch_full_history(symbol, timeframe, HISTORY_START_DATE)
    today = _utc_today()
    df = df[df["date"] < today].reset_index(drop=True)
    return drop_untraded_bars(df)


def paper_trades_since(
    df: pd.DataFrame,
    paper_start_date: date,
    starting_capital: float = PER_ASSET_CAPITAL,
    risk_pct: float = RISK_PCT,
    multiplier: float = 1.0,
    cost_model: CostModel | None = None,
    compounding_fraction: float = COMPOUNDING_FRACTION,
) -> list[Trade]:
    """One sleeve's full trade history: Donchian breakout signals from
    paper_start_date onward, simulated exactly like the validated
    backtest (same simulate_trades_compounding/CostModel), starting flat
    and compounding its own gains. Signals before paper_start_date are
    excluded rather than fed in, so the sleeve never inherits a position
    -- or compounding history -- from before paper trading began."""
    signals = generate_donchian_breakout_signals(df)
    live_signals = [s for s in signals if pd.Timestamp(s.entry_ts).date() >= paper_start_date]
    return simulate_trades_compounding(
        df, live_signals, multiplier=multiplier, starting_capital=starting_capital,
        risk_pct_per_trade=risk_pct, cost_model=cost_model, compounding_fraction=compounding_fraction,
    )


def _write_trade_log(all_trades: dict[str, list[Trade]], path: Path = TRADE_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "symbol": symbol, "entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "direction": t.direction,
            "entry_price": t.entry_price, "stop_price": t.stop_price, "target_price": t.target_price,
            "exit_price": t.exit_price, "outcome": t.outcome, "contracts": t.contracts,
            "pnl_dollars": t.pnl_dollars, "r_multiple": t.r_multiple,
        }
        for symbol, trades in all_trades.items()
        for t in trades
    ]
    pd.DataFrame(rows).to_csv(path, index=False)


def run_once() -> None:
    state = load_state()
    is_first_run = "paper_start_date" not in state

    all_trades: dict[str, list[Trade]] = {}
    sleeve_equity: dict[str, float] = {}
    latest_closed_dates: list[date] = []
    crypto_specs = load_crypto_instruments()

    for symbol in SYMBOLS:
        print(f"Fetching fresh {symbol} daily data from Binance (public market data, no account/API key used)...")
        try:
            df = fetch_fresh_data(symbol)
        except Exception as exc:
            print(f"  Could not fetch {symbol} data: {exc}")
            print("  No state changed for this run. Re-run once network access to api.binance.com is available.")
            return
        if df.empty:
            print(f"  No closed daily bars available yet for {symbol} -- nothing to evaluate.")
            return

        latest_closed_dates.append(pd.Timestamp(df["date"].iloc[-1]).date())

        spec = crypto_specs[symbol]
        multiplier = float(spec["micro_multiplier"])
        cost_model = CostModel(
            slippage_ticks=SLIPPAGE_TICKS, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"])
        )

        # paper_start_date is set once, shared across all three sleeves,
        # below -- deferred until we know the earliest common closed bar.
        all_trades[symbol] = (df, multiplier, cost_model)  # placeholder; resolved after paper_start_date is known

    latest_closed_date = min(latest_closed_dates)  # the latest date ALL sleeves have a closed bar for

    if is_first_run:
        paper_start_date = latest_closed_date
        state["paper_start_date"] = str(paper_start_date)
        print(f"\nFirst run: starting a fresh 3-asset paper portfolio (BTC+SOL+BNB) as of {paper_start_date} "
              f"(${ACCOUNT_SIZE:,.0f} total, ${PER_ASSET_CAPITAL:,.2f}/sleeve, {RISK_PCT:.0%} risk/trade, "
              f"compounding_fraction={COMPOUNDING_FRACTION}).")
    else:
        paper_start_date = date.fromisoformat(state["paper_start_date"])

    last_seen = state.get("last_seen_exit_ts", {})
    new_last_seen = dict(last_seen)
    print(f"\nLatest common closed daily bar: {latest_closed_date}   Paper portfolio started: {paper_start_date}")

    for symbol, (df, multiplier, cost_model) in all_trades.items():
        trades = paper_trades_since(df, paper_start_date, PER_ASSET_CAPITAL, RISK_PCT, multiplier, cost_model, COMPOUNDING_FRACTION)
        all_trades[symbol] = trades

        closed = [t for t in trades if t.outcome != "open"]
        last_seen_symbol = last_seen.get(symbol)
        newly_closed = [
            t for t in closed
            if last_seen_symbol is None or pd.Timestamp(t.exit_ts) > pd.Timestamp(last_seen_symbol)
        ]
        open_trade = next((t for t in trades if t.outcome == "open"), None)

        print(f"\n--- {symbol} ---")
        if newly_closed and not is_first_run:
            print(f"  {len(newly_closed)} trade(s) closed since last run:")
            for t in newly_closed:
                print(f"    {t.direction:5} entered {pd.Timestamp(t.entry_ts).date()} @ {t.entry_price:.2f} "
                      f"-> exited {pd.Timestamp(t.exit_ts).date()} @ {t.exit_price:.2f}  "
                      f"[{t.outcome.upper()}]  pnl=${t.pnl_dollars:,.2f} ({t.r_multiple:+.2f}R)")
        if open_trade is not None:
            print(f"  Currently open: {open_trade.direction} entered {pd.Timestamp(open_trade.entry_ts).date()} "
                  f"@ {open_trade.entry_price:.2f}, stop {open_trade.stop_price:.2f}, target {open_trade.target_price:.2f} "
                  f"({open_trade.contracts} units)")
        else:
            print("  Currently flat -- no open position.")

        sleeve_equity[symbol] = PER_ASSET_CAPITAL + sum(t.pnl_dollars for t in closed)
        if closed:
            new_last_seen[symbol] = str(max(pd.Timestamp(t.exit_ts) for t in closed))

    _write_trade_log(all_trades)

    combined_closed = [t for symbol_trades in all_trades.values() for t in symbol_trades if t.outcome != "open"]
    metrics = compute_metrics(combined_closed, account_size=ACCOUNT_SIZE)
    total_equity = sum(sleeve_equity.values())

    print(f"\n=== Portfolio summary (since {paper_start_date}) ===")
    for symbol in SYMBOLS:
        print(f"  {symbol:5} sleeve equity: ${sleeve_equity[symbol]:,.2f} (started ${PER_ASSET_CAPITAL:,.2f})")
    print(f"  TOTAL: ${ACCOUNT_SIZE:,.2f} -> ${total_equity:,.2f} ({(total_equity/ACCOUNT_SIZE - 1):+.1%})")
    print(f"  combined closed trades: {metrics['num_closed']}   win rate: {metrics['win_rate']}   "
          f"profit factor: {metrics['profit_factor']}")
    print(f"  full trade log: {TRADE_LOG_PATH}")

    state["last_seen_exit_ts"] = new_last_seen
    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


if __name__ == "__main__":
    run_once()
