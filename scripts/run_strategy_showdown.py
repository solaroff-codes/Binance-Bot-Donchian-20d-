"""
Backtest 8 popular technical strategies (strategy/technical_signals.py)
against BTC/ETH/SOL, looking for anything that holds up across both a
training period and genuinely held-out test data.

Deliberate methodology choice: this does NOT sweep parameters per
strategy. Each strategy uses one fixed, reasoned parameter set (standard
textbook defaults, with RSI shortened for crypto's volatility — see
strategy/technical_signals.py's module docstring) rather than searching a
grid and reporting the best cell. The rest of this project's history
already found that approach produces impressive-looking but unreliable
numbers (see backtest/README.md's "why the sections above are misleading"
section) — sweeping 8 strategies x however many parameter combinations
each would reproduce the same multiple-comparisons trap at a larger scale.
One principled choice per strategy, then genuine train/test validation,
is the honest way to search across strategies instead.

Same realistic-cost and position-sizing model as
scripts/run_crypto_realistic_backtest.py: $10k account, 1-2% risk per
trade sized in fractional lots, Binance's ~0.1% taker fee, 1 tick
slippage. Same 70/30 train/test split by date. Daily bars only (the
technical indicators here are cheap, vectorized, single-pass — unlike
walk_forward_trendlines, they would work fine at hourly resolution too,
but daily keeps this run directly comparable to the crypto trendline
result and fast to iterate on).

Run from the project root with the venv active:
    python scripts/run_strategy_showdown.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, PositionSizer, compute_metrics, simulate_trades
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import STRATEGY_REGISTRY

SYMBOLS = ["BTC", "ETH", "SOL"]
TIMEFRAME = "1 day"
ACCOUNT_SIZE = 10_000.0
RISK_LEVELS = [0.01, 0.02]
SLIPPAGE_TICKS = 1.0
TRAIN_FRACTION = 0.70
MIN_TEST_TRADES_TO_TRUST = 5  # below this, "it worked on test" isn't meaningful either


def _split_by_date(items: list, split_date) -> tuple[list, list]:
    before = [s for s in items if pd.Timestamp(s.entry_ts).date() < split_date]
    after = [s for s in items if pd.Timestamp(s.entry_ts).date() >= split_date]
    return before, after


def main() -> None:
    rows = []
    crypto_specs = load_crypto_instruments()

    for symbol in SYMBOLS:
        df = load_continuous(symbol, TIMEFRAME)
        if df is None:
            print(f"{symbol}: no cached data, skipping")
            continue
        df = drop_untraded_bars(df)

        spec = crypto_specs[symbol]
        multiplier = float(spec["micro_multiplier"])
        cost_model = CostModel(
            slippage_ticks=SLIPPAGE_TICKS, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"])
        )

        split_idx = int(len(df) * TRAIN_FRACTION)
        split_date = pd.Timestamp(df["date"].iloc[split_idx]).date()

        print(f"\n=== {symbol} ({len(df)} daily bars, split at {split_date}) ===")

        for strategy_name, generator in STRATEGY_REGISTRY.items():
            signals = generator(df)
            train_sig, test_sig = _split_by_date(signals, split_date)

            for risk_pct in RISK_LEVELS:
                sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)

                train_trades = simulate_trades(df, train_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
                test_trades = simulate_trades(df, test_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
                train_metrics = compute_metrics(train_trades, account_size=ACCOUNT_SIZE)
                test_metrics = compute_metrics(test_trades, account_size=ACCOUNT_SIZE)

                rows.append({"symbol": symbol, "strategy": strategy_name, "risk_pct": risk_pct, "split": "train",
                             "num_raw_signals": len(train_sig), **train_metrics})
                rows.append({"symbol": symbol, "strategy": strategy_name, "risk_pct": risk_pct, "split": "test",
                             "num_raw_signals": len(test_sig), **test_metrics})

            print(f"  {strategy_name:22} train_signals={len(train_sig):3} test_signals={len(test_sig):3}")

    result = pd.DataFrame(rows)
    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "strategy_showdown.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")

    # Highlight combos that are profitable on BOTH train and test, with a
    # real sample on test — the only honest "this might generalize" signal.
    pivot_rows = []
    for (symbol, strategy, risk_pct), group in result.groupby(["symbol", "strategy", "risk_pct"]):
        train = group[group["split"] == "train"].iloc[0]
        test = group[group["split"] == "test"].iloc[0]
        pivot_rows.append({
            "symbol": symbol, "strategy": strategy, "risk_pct": risk_pct,
            "train_trades": train["num_closed"], "train_pf": train["profit_factor"],
            "test_trades": test["num_closed"], "test_pf": test["profit_factor"], "test_pnl": test["total_pnl_dollars"],
        })
    pivot = pd.DataFrame(pivot_rows)
    survivors = pivot[
        (pivot["test_trades"] >= MIN_TEST_TRADES_TO_TRUST)
        & (pivot["train_pf"].fillna(0) > 1.0)
        & (pivot["test_pf"].fillna(0) > 1.0)
    ].sort_values("test_pf", ascending=False)

    print(f"\n=== Combos profitable on BOTH train and test, with >= {MIN_TEST_TRADES_TO_TRUST} test trades ===")
    if survivors.empty:
        print("  none")
    else:
        with pd.option_context("display.max_columns", None, "display.width", 200):
            print(survivors.to_string(index=False))


if __name__ == "__main__":
    main()
