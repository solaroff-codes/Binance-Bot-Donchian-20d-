"""
Forward-tests BTC + Donchian channel breakout -- the one strategy this
project has validated with real train/test, bear-market, and second-
holdout evidence (see backtest/README.md's "Stress-testing BTC +
Donchian" section) -- against live Binance market data, with zero real
money or API credentials involved.

This is pure simulation ("Option A" -- see the project conversation this
was built from): no Binance account, no API key, no order ever placed
anywhere. Each run fetches fresh PUBLIC daily OHLCV data (the same public
endpoint data/binance_connector.py already uses for backtesting -- no
authentication required, this is market data, not account access),
regenerates Donchian breakout signals with the exact same fixed
parameters used throughout this project's validated backtest, and
re-simulates a paper account using backtest/engine.py's own
simulate_trades()/CostModel/PositionSizer -- the identical, already-
tested logic, not a reimplementation. That's deliberate: the paper
account's results are only meaningful as a live extension of the
backtest if they're produced by the same code path.

Design: stateless except for one persisted value (paper_start_date, set
on first run and never changed after). Every run recomputes the FULL
trade history since paper trading began from scratch, rather than
incrementally patching saved state -- simpler, and immune to state-
corruption from a missed run, a crash mid-write, or a clock glitch, since
every run is a deterministic function of (market data, paper_start_date).
Signals from before paper_start_date are excluded before simulating, so
the paper account always starts flat (no open position) on day one,
exactly like a real forward-testing account would.

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

from backtest.engine import CostModel, PositionSizer, Trade, compute_metrics, simulate_trades
from data.binance_connector import fetch_full_history, load_crypto_instruments
from data.cache import drop_untraded_bars
from strategy.technical_signals import generate_donchian_breakout_signals

SYMBOL = "BTC"
TIMEFRAME = "1 day"
ACCOUNT_SIZE = 10_000.0
# 1% rather than 2%: paper trading is the first live extension of the
# backtest, not a conviction bet -- start at the more conservative end of
# the 1-2% range this project has used throughout, tighten or widen later
# with real forward data in hand.
RISK_PCT = 0.01
SLIPPAGE_TICKS = 1.0
HISTORY_START_DATE = "2020-01-01"  # far enough back for Donchian(20)/ATR(14) warmup with room to spare

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_PATH = STATE_DIR / "btc_donchian_paper_state.json"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
TRADE_LOG_PATH = OUTPUT_DIR / "btc_donchian_paper_trades.csv"


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


def fetch_fresh_data(symbol: str = SYMBOL, timeframe: str = TIMEFRAME) -> pd.DataFrame:
    """Fresh public OHLCV from Binance, with today's still-forming bar
    dropped (only fully closed daily bars are ever evaluated)."""
    df = fetch_full_history(symbol, timeframe, HISTORY_START_DATE)
    today = _utc_today()
    df = df[df["date"] < today].reset_index(drop=True)
    return drop_untraded_bars(df)


def paper_trades_since(
    df: pd.DataFrame,
    paper_start_date: date,
    account_size: float = ACCOUNT_SIZE,
    risk_pct: float = RISK_PCT,
    multiplier: float = 1.0,
    cost_model: CostModel | None = None,
) -> list[Trade]:
    """The paper account's full trade history: Donchian breakout signals
    from paper_start_date onward, simulated exactly like the validated
    backtest (same simulate_trades/CostModel/PositionSizer), starting
    flat. Signals before paper_start_date are excluded rather than fed
    in, so the account never inherits a position from "history" that
    happened before paper trading began."""
    signals = generate_donchian_breakout_signals(df)
    live_signals = [s for s in signals if pd.Timestamp(s.entry_ts).date() >= paper_start_date]
    sizer = PositionSizer(account_size=account_size, risk_pct_per_trade=risk_pct)
    return simulate_trades(df, live_signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)


def _write_trade_log(trades: list[Trade], path: Path = TRADE_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "entry_ts": t.entry_ts, "exit_ts": t.exit_ts, "direction": t.direction,
            "entry_price": t.entry_price, "stop_price": t.stop_price, "target_price": t.target_price,
            "exit_price": t.exit_price, "outcome": t.outcome, "contracts": t.contracts,
            "pnl_dollars": t.pnl_dollars, "r_multiple": t.r_multiple,
        }
        for t in trades
    ]
    pd.DataFrame(rows).to_csv(path, index=False)


def run_once() -> None:
    state = load_state()
    is_first_run = "paper_start_date" not in state

    print("Fetching fresh BTC daily data from Binance (public market data, no account/API key used)...")
    try:
        df = fetch_fresh_data()
    except Exception as exc:
        print(f"Could not fetch market data: {exc}")
        print("No state changed. Re-run once network access to api.binance.com is available.")
        return

    if df.empty:
        print("No closed daily bars available yet -- nothing to evaluate.")
        return

    latest_closed_date = pd.Timestamp(df["date"].iloc[-1]).date()

    if is_first_run:
        paper_start_date = latest_closed_date
        state["paper_start_date"] = str(paper_start_date)
        print(f"First run: starting a fresh paper account as of {paper_start_date} "
              f"(${ACCOUNT_SIZE:,.0f} notional, {RISK_PCT:.0%} risk/trade, BTC + Donchian breakout).")
    else:
        paper_start_date = date.fromisoformat(state["paper_start_date"])

    spec = load_crypto_instruments()[SYMBOL]
    multiplier = float(spec["micro_multiplier"])
    cost_model = CostModel(
        slippage_ticks=SLIPPAGE_TICKS, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"])
    )

    trades = paper_trades_since(df, paper_start_date, ACCOUNT_SIZE, RISK_PCT, multiplier, cost_model)
    _write_trade_log(trades)

    last_seen_exit = state.get("last_seen_exit_ts")
    closed = [t for t in trades if t.outcome != "open"]
    newly_closed = [
        t for t in closed
        if last_seen_exit is None or pd.Timestamp(t.exit_ts) > pd.Timestamp(last_seen_exit)
    ]
    open_trade = next((t for t in trades if t.outcome == "open"), None)

    print(f"\nLatest closed daily bar: {latest_closed_date}   Paper account started: {paper_start_date}")

    if newly_closed and not is_first_run:
        print(f"\n{len(newly_closed)} trade(s) closed since last run:")
        for t in newly_closed:
            print(f"  {t.direction:5} entered {pd.Timestamp(t.entry_ts).date()} @ {t.entry_price:.2f} "
                  f"-> exited {pd.Timestamp(t.exit_ts).date()} @ {t.exit_price:.2f}  "
                  f"[{t.outcome.upper()}]  pnl=${t.pnl_dollars:,.2f} ({t.r_multiple:+.2f}R)")

    if open_trade is not None:
        print(f"\nCurrently open: {open_trade.direction} entered {pd.Timestamp(open_trade.entry_ts).date()} "
              f"@ {open_trade.entry_price:.2f}, stop {open_trade.stop_price:.2f}, target {open_trade.target_price:.2f} "
              f"({open_trade.contracts} units)")
    else:
        print("\nCurrently flat -- no open position.")

    metrics = compute_metrics(closed, account_size=ACCOUNT_SIZE)
    print(f"\nPaper account summary (since {paper_start_date}):")
    print(f"  closed trades: {metrics['num_closed']}   win rate: {metrics['win_rate']}   "
          f"profit factor: {metrics['profit_factor']}")
    print(f"  total pnl: ${metrics['total_pnl_dollars']}" if metrics["total_pnl_dollars"] is not None else "  total pnl: $0 (no closed trades yet)")
    print(f"  full trade log: {TRADE_LOG_PATH}")

    if closed:
        state["last_seen_exit_ts"] = str(max(pd.Timestamp(t.exit_ts) for t in closed))
    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(state)


if __name__ == "__main__":
    run_once()
