"""
Live (Binance Futures Testnet) execution of the validated BTC+SOL+BNB
Donchian breakout portfolio -- 3% risk per trade, full compounding, 3x
leverage -- see backtest/README.md's "Increasing profit on the 1:1.5
version" and "Trading this on leveraged futures instead of spot"
sections for how this configuration was chosen.

THIS IS TESTNET ONLY as built. live/binance_futures_client.py always
points at Binance's sandboxed Futures Testnet -- no code path here talks
to the live exchange. Going live with real money is a separate, later,
explicit decision (see live/README.md), not a flag flip.

DRY_RUN (default True): every read (positions, open orders) still
happens against the real testnet API -- reconciliation logic runs for
real -- but no order-placement or order-cancellation call is actually
made; what WOULD have been done is logged instead. Flip to False only
after reviewing dry-run output and being ready to see real (testnet)
orders appear.

Signal generation is identical to paper/paper_trader.py: fresh PUBLIC
daily OHLCV from data/binance_connector.py (no auth needed for market
data), the same generate_donchian_breakout_signals() with the same
fixed parameters. Only the execution layer -- placing/reconciling real
orders via live/binance_futures_client.py -- is new. This keeps the
signal identical across backtest, paper, and live, exactly as intended.

Position sizing/equity are tracked per-symbol in a local state file
(live/state/live_trader_state.json), the same "sleeve" bookkeeping
paper_trader.py already uses -- NOT a real segregation of funds on
Binance, which pools all USDM futures margin into one account balance.
See live/README.md for what that means in practice.

Run once daily, any time after Binance's daily UTC candle close
(00:00 UTC):
    python live/live_trader.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from data.binance_connector import fetch_full_history
from data.cache import drop_untraded_bars
from live.binance_futures_client import BinanceFuturesClient, round_step
from strategy.technical_signals import generate_donchian_breakout_signals

SYMBOLS = ["BTC", "SOL", "BNB"]
BINANCE_FUTURES_SYMBOL = {"BTC": "BTCUSDT", "SOL": "SOLUSDT", "BNB": "BNBUSDT"}
TIMEFRAME = "1 day"
ACCOUNT_SIZE = 1_000.0
PER_ASSET_CAPITAL = ACCOUNT_SIZE / len(SYMBOLS)
RISK_PCT = 0.03
LEVERAGE = 3
HISTORY_START_DATE = "2020-01-01"

DRY_RUN = True  # see module docstring -- flip only after reviewing dry-run output

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_PATH = STATE_DIR / "live_trader_state.json"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
TRADE_LOG_PATH = OUTPUT_DIR / "live_trades.csv"


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def load_state(path: Path = STATE_PATH) -> dict:
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return {"symbols": {s: {"equity": PER_ASSET_CAPITAL, "open_trade": None, "last_processed_date": None} for s in SYMBOLS}}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=str)


def append_trade_log(row: dict, path: Path = TRADE_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df_row = pd.DataFrame([row])
    if path.exists():
        df_row.to_csv(path, mode="a", header=False, index=False)
    else:
        df_row.to_csv(path, index=False)


def fetch_fresh_data(symbol: str) -> pd.DataFrame:
    df = fetch_full_history(symbol, TIMEFRAME, HISTORY_START_DATE)
    today = _utc_today()
    df = df[df["date"] < today].reset_index(drop=True)
    return drop_untraded_bars(df)


def compute_order_quantity(equity: float, risk_pct: float, entry_price: float, stop_price: float, step_size: float) -> float:
    """Pure sizing calculation -- risk_pct of equity, given the stop
    distance, rounded DOWN to the symbol's own lot step size (never
    rounds up past what was actually intended to be risked)."""
    risk_points = abs(entry_price - stop_price)
    if risk_points <= 0:
        return 0.0
    raw_qty = (equity * risk_pct) / risk_points
    return round_step(raw_qty, step_size)


def determine_daily_action(position: dict | None, latest_signal, symbol_state: dict) -> str:
    """
    Pure reconciliation decision -- given whether the exchange currently
    shows an open position, today's freshly generated signal (or None),
    and this symbol's locally tracked state, decide what today's run
    should do:
      "HOLD"             -- exchange shows an open position; nothing to do
      "RECONCILE_CLOSED" -- local state thought a trade was open, but the
                             exchange now shows flat -- it closed via
                             stop or target since the last check
      "OPEN_NEW"         -- flat, and a fresh signal fired today
      "STAY_FLAT"        -- flat, no new signal
    """
    if position is not None:
        return "HOLD"
    if symbol_state.get("open_trade") is not None:
        return "RECONCILE_CLOSED"
    if latest_signal is not None:
        return "OPEN_NEW"
    return "STAY_FLAT"


def _todays_signal(signals: list, latest_closed_date: date):
    """The signal (if any) whose entry_ts is the most recently closed
    daily bar -- what "a breakout happened today" means for a script
    that runs once a day."""
    for s in signals:
        if pd.Timestamp(s.entry_ts).date() == latest_closed_date:
            return s
    return None


def process_symbol(symbol: str, client: BinanceFuturesClient, state: dict) -> None:
    futures_symbol = BINANCE_FUTURES_SYMBOL[symbol]
    symbol_state = state["symbols"].setdefault(
        symbol, {"equity": PER_ASSET_CAPITAL, "open_trade": None, "last_processed_date": None}
    )

    df = fetch_fresh_data(symbol)
    if df.empty:
        print(f"  {symbol}: no closed daily bars yet -- skipping")
        return
    latest_closed_date = pd.Timestamp(df["date"].iloc[-1]).date()

    if symbol_state.get("last_processed_date") == str(latest_closed_date):
        print(f"  {symbol}: already processed {latest_closed_date} -- no-op")
        return

    signals = generate_donchian_breakout_signals(df)
    todays_signal = _todays_signal(signals, latest_closed_date)

    position = client.get_position(futures_symbol)
    action = determine_daily_action(position, todays_signal, symbol_state)
    print(f"  {symbol}: latest bar {latest_closed_date}, action={action}")

    if action == "HOLD":
        pass

    elif action == "RECONCILE_CLOSED":
        open_trade = symbol_state["open_trade"]
        exit_price = None
        outcome = None
        for order_key, label in [("stop_order_id", "loss"), ("target_order_id", "win")]:
            order_id = open_trade.get(order_key)
            if order_id is None:
                continue
            try:
                order = client.get_order(futures_symbol, order_id)
            except Exception as exc:
                print(f"    could not fetch order {order_id}: {exc}")
                continue
            if order.get("status") == "FILLED":
                exit_price = float(order.get("avgPrice") or order.get("stopPrice"))
                outcome = label
            elif order.get("status") in ("NEW", "PARTIALLY_FILLED"):
                if not DRY_RUN:
                    try:
                        client.cancel_order(futures_symbol, order_id)
                    except Exception as exc:
                        print(f"    could not cancel orphaned order {order_id}: {exc}")
                else:
                    print(f"    [DRY RUN] would cancel orphaned order {order_id}")

        if exit_price is not None:
            direction = open_trade["direction"]
            signed = 1 if direction == "long" else -1
            pnl_dollars = signed * (exit_price - open_trade["entry_price"]) * open_trade["quantity"]
            symbol_state["equity"] += pnl_dollars
            print(f"    closed {outcome}: entry {open_trade['entry_price']} -> exit {exit_price}, pnl=${pnl_dollars:,.2f}")
            append_trade_log({
                "symbol": symbol, "entry_ts": open_trade["entry_ts"], "exit_ts": datetime.now(timezone.utc).isoformat(),
                "direction": direction, "entry_price": open_trade["entry_price"], "exit_price": exit_price,
                "quantity": open_trade["quantity"], "outcome": outcome, "pnl_dollars": pnl_dollars,
            })
        else:
            print("    position closed but neither stop nor target order shows FILLED yet -- will recheck next run")
            return  # don't clear open_trade or advance last_processed_date until this resolves
        symbol_state["open_trade"] = None

    elif action == "OPEN_NEW":
        filters = client.get_symbol_filters(futures_symbol)
        quantity = compute_order_quantity(
            symbol_state["equity"], RISK_PCT, todays_signal.entry_price, todays_signal.stop_price, filters["step_size"]
        )
        stop_price = round_step(todays_signal.stop_price, filters["tick_size"])
        target_price = round_step(todays_signal.target_price, filters["tick_size"])

        if quantity <= 0:
            print(f"    signal fired ({todays_signal.direction}) but sizes to 0 at current equity -- skipping")
        elif DRY_RUN:
            print(f"    [DRY RUN] would open {todays_signal.direction} {quantity} {futures_symbol} "
                  f"@ ~{todays_signal.entry_price}, stop {stop_price}, target {target_price}")
        else:
            client.ensure_leverage_and_isolated_margin(futures_symbol, LEVERAGE)
            entry_order = client.place_market_entry(futures_symbol, todays_signal.direction, quantity)
            fill_price = float(entry_order.get("avgPrice") or todays_signal.entry_price)
            stop_order = client.place_stop_loss(futures_symbol, todays_signal.direction, quantity, stop_price)
            target_order = client.place_take_profit(futures_symbol, todays_signal.direction, quantity, target_price)
            symbol_state["open_trade"] = {
                "entry_ts": str(todays_signal.entry_ts), "direction": todays_signal.direction,
                "entry_price": fill_price, "quantity": quantity,
                "stop_order_id": stop_order["orderId"], "target_order_id": target_order["orderId"],
            }
            print(f"    opened {todays_signal.direction} {quantity} {futures_symbol} @ {fill_price}, "
                  f"stop {stop_price}, target {target_price}")

    symbol_state["last_processed_date"] = str(latest_closed_date)


def run_once() -> None:
    print(f"{'DRY RUN -- ' if DRY_RUN else ''}Binance Futures Testnet live trader")
    state = load_state()
    try:
        client = BinanceFuturesClient(testnet=True)
    except RuntimeError as exc:
        # Missing credentials -- our own check, already a clear message.
        print(str(exc))
        return
    except Exception as exc:
        # python-binance's Client pings the API on construction by
        # default -- this catches a bad/rejected key, a blocked network
        # path (some cloud/datacenter IP ranges, GitHub Actions runners
        # included, are sometimes blocked by Binance), or any other
        # startup failure, so one bad connection doesn't crash the whole
        # run with an unhandled traceback.
        print(f"Could not connect to Binance Futures Testnet: {type(exc).__name__}: {exc}")
        print("No state changed. This may be a blocked network path (some cloud IP "
              "ranges are blocked by Binance) rather than a code bug -- see live/README.md.")
        return

    for symbol in SYMBOLS:
        try:
            process_symbol(symbol, client, state)
        except Exception as exc:
            print(f"  {symbol}: ERROR -- {exc}")

    save_state(state)
    total_equity = sum(s["equity"] for s in state["symbols"].values())
    print(f"\nTotal tracked equity: ${total_equity:,.2f} (started ${ACCOUNT_SIZE:,.2f})")


if __name__ == "__main__":
    run_once()
