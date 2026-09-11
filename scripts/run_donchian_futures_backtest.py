"""
Test the one strategy that held up in the crypto showdown (Donchian
channel breakout — see backtest/README.md's "strategy showdown" section)
against gold (GC), crude oil (CL), and stock index futures (ES, NQ).

Contract size / account size: micro contracts (MGC/MCL/MES/MNQ) on a
$10,000 account, or full-size contracts on a $100,000 account — these are
NOT two things to test separately. Every micro_multiplier in
config/instruments.yaml is exactly 1/10 of the corresponding full
multiplier, and $100k is exactly 10x $10k, so risk-as-a-percentage-of-
account (and therefore the number of contracts PositionSizer computes) is
mathematically identical between the two. Verified directly before
writing this script rather than assumed. One run covers both; this uses
micro+$10k since it's simpler to reason about, and the results apply
identically to full+$100k.

Same realistic-cost methodology as everywhere else in this project:
1 tick slippage, $2.50/contract round-turn commission (futures' flat fee,
not crypto's percentage-of-notional), 1-2% dollar risk per trade, 70/30
train/test split by date, using each instrument's continuous
(back-adjusted, multi-contract) daily series for the longest available
history.

Also checks — before assuming 1-2% risk is achievable at all — whether
this account/contract combination can even size the cheapest signal each
instrument ever produces. This matters: ATR-based Donchian stops on
gold/index futures turned out to still be wide relative to a $10k (or
equivalently $100k-full-size) account even after switching to the
smallest available contract — checked directly, not assumed; see the
printed sizing check for each instrument.

Run from the project root with the venv active:
    python scripts/run_donchian_futures_backtest.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, PositionSizer, compute_metrics, simulate_trades
from data.cache import drop_untraded_bars, load_continuous
from data.contracts import load_instruments
from strategy.technical_signals import generate_donchian_breakout_signals

SYMBOLS = ["GC", "CL", "ES", "NQ"]
TIMEFRAME = "1 day"
ACCOUNT_SIZE = 10_000.0  # equivalent to $100k + full-size contracts, see module docstring
RISK_LEVELS = [0.01, 0.02]
COMMISSION_PER_CONTRACT = 2.50
SLIPPAGE_TICKS = 1.0
TRAIN_FRACTION = 0.70


def _split_by_date(items: list, split_date) -> tuple[list, list]:
    before = [s for s in items if pd.Timestamp(s.entry_ts).date() < split_date]
    after = [s for s in items if pd.Timestamp(s.entry_ts).date() >= split_date]
    return before, after


def main() -> None:
    rows = []
    instruments = load_instruments()

    for symbol in SYMBOLS:
        df = load_continuous(symbol, TIMEFRAME)
        if df is None:
            print(f"{symbol}: no cached continuous data, skipping")
            continue
        df = drop_untraded_bars(df)

        spec = instruments[symbol]
        multiplier = float(spec["micro_multiplier"])
        tick_size = float(spec["tick_size"])
        cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=tick_size, commission_per_contract=COMMISSION_PER_CONTRACT)

        signals = generate_donchian_breakout_signals(df)
        risks = [abs(s.entry_price - s.stop_price) * multiplier for s in signals]
        print(f"\n=== {symbol} ({len(df)} daily bars, {len(signals)} Donchian signals) ===")
        if risks:
            min_risk_pct = min(risks) / ACCOUNT_SIZE
            print(
                f"  sizing check: cheapest signal needs ${min(risks):,.0f} risk for the smallest "
                f"contract = {min_risk_pct:.2%} of a ${ACCOUNT_SIZE:,.0f}-equivalent account "
                f"(requested budget: {RISK_LEVELS[0]:.0%}-{RISK_LEVELS[-1]:.0%})"
            )

        split_idx = int(len(df) * TRAIN_FRACTION)
        split_date = pd.Timestamp(df["date"].iloc[split_idx]).date()
        train_sig, test_sig = _split_by_date(signals, split_date)
        print(f"  split at {split_date}: {len(train_sig)} train signals, {len(test_sig)} test signals")

        for risk_pct in RISK_LEVELS:
            sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)

            train_skip, test_skip = [], []
            train_trades = simulate_trades(df, train_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer, skip_log=train_skip)
            test_trades = simulate_trades(df, test_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer, skip_log=test_skip)
            train_metrics = compute_metrics(train_trades, account_size=ACCOUNT_SIZE)
            test_metrics = compute_metrics(test_trades, account_size=ACCOUNT_SIZE)

            rows.append({"symbol": symbol, "risk_pct": risk_pct, "split": "train",
                         "num_raw_signals": len(train_sig), "num_skipped_undersized": len(train_skip), **train_metrics})
            rows.append({"symbol": symbol, "risk_pct": risk_pct, "split": "test",
                         "num_raw_signals": len(test_sig), "num_skipped_undersized": len(test_skip), **test_metrics})

            print(
                f"  risk={risk_pct:.0%}  train: {train_metrics['num_closed']} trades "
                f"({len(train_skip)} skipped), PF={train_metrics['profit_factor']}, "
                f"pnl=${train_metrics['total_pnl_dollars']}  |  "
                f"test: {test_metrics['num_closed']} trades ({len(test_skip)} skipped), "
                f"PF={test_metrics['profit_factor']}, pnl=${test_metrics['total_pnl_dollars']}"
            )

    result = pd.DataFrame(rows)
    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "donchian_futures_backtest.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
