"""
Stress-test the one strategy this project has found genuine train/test
evidence for -- BTC + Donchian channel breakout (see backtest/README.md's
"strategy showdown" section: train PF 1.53 / test PF 1.68, 94 trades) --
before it's treated as a candidate for anything beyond research.

The original honest test used a single 70/30 split by date. Its "test"
period turned out to run 2024-11-22 through 2026-09-10 -- almost entirely
BTC's 2024-2025 bull run. The entire 2022 bear market (BTC ~$69k top in
Nov 2021 to ~$15.5k bottom in Nov 2022) was inside the TRAINING period,
never evaluated out-of-sample at all. A strategy that only proved itself
in a bull market test window is not the same claim as "validated."

This script does NOT re-fit or sweep anything -- generate_donchian_breakout_signals
uses the same fixed, reasoned parameters as everywhere else in this
project (no parameter search ever touched this data, which is exactly why
segmenting it further here doesn't reopen the multiple-comparisons problem
that motivated the original train/test split). It just evaluates the
identical, already-fixed strategy across time windows chosen for their
market-regime meaning, decided before looking at any result:

1. Calendar-year breakdown, 2020-2026, to see the full consistency picture.
2. The 2021-11-10 -> 2022-11-21 window specifically (top to bottom of the
   2022 bear market), evaluated on its own.
3. A second, non-overlapping holdout distinct from the original: everything
   BEFORE the original test split's start (2020-09-10 -> 2024-11-21) is
   split again in half, so the "first half of training" acts as a fresh
   out-of-sample check unrelated to the original 70/30 cut.

Same realistic-cost methodology as scripts/run_strategy_showdown.py: $10k
account, 1-2% risk per trade, Binance's ~0.1% taker fee, 1 tick slippage.

Run from the project root with the venv active:
    python scripts/run_btc_donchian_stress_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, PositionSizer, compute_metrics, simulate_trades
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import generate_donchian_breakout_signals

SYMBOL = "BTC"
ACCOUNT_SIZE = 10_000.0
RISK_LEVELS = [0.01, 0.02]
SLIPPAGE_TICKS = 1.0

BEAR_MARKET_START = pd.Timestamp("2021-11-10").date()
BEAR_MARKET_END = pd.Timestamp("2022-11-21").date()
ORIGINAL_TEST_SPLIT = pd.Timestamp("2024-11-22").date()


def _signals_in_window(signals: list, start, end) -> list:
    return [s for s in signals if start <= pd.Timestamp(s.entry_ts).date() <= end]


def _report(label: str, df: pd.DataFrame, signals: list, multiplier: float, cost_model: CostModel) -> None:
    print(f"\n--- {label} ({len(signals)} signals) ---")
    if not signals:
        print("  no signals in this window")
        return
    for risk_pct in RISK_LEVELS:
        sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)
        trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
        m = compute_metrics(trades, account_size=ACCOUNT_SIZE)
        print(
            f"  risk={risk_pct:.0%}  trades={m['num_closed']:>3}  win_rate={m['win_rate']}  "
            f"PF={m['profit_factor']}  pnl=${m['total_pnl_dollars']}  max_DD=${m['max_drawdown_dollars']}"
        )


def main() -> None:
    df = load_continuous(SYMBOL, "1 day")
    df = drop_untraded_bars(df)
    spec = load_crypto_instruments()[SYMBOL]
    multiplier = float(spec["micro_multiplier"])
    cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))

    signals = generate_donchian_breakout_signals(df)
    print(f"BTC daily bars: {len(df)} ({df['date'].min()} -> {df['date'].max()}), {len(signals)} total signals (fixed params, no sweep)")

    # 1. Calendar-year breakdown.
    print("\n=== 1. Calendar-year breakdown ===")
    years = sorted(pd.to_datetime(df["date"]).dt.year.unique())
    for year in years:
        start = pd.Timestamp(f"{year}-01-01").date()
        end = pd.Timestamp(f"{year}-12-31").date()
        year_signals = _signals_in_window(signals, start, end)
        _report(f"{year}", df, year_signals, multiplier, cost_model)

    # 2. The 2022 bear market, top to bottom, as its own out-of-sample window.
    print("\n=== 2. 2022 bear market window (2021-11-10 -> 2022-11-21) ===")
    bear_signals = _signals_in_window(signals, BEAR_MARKET_START, BEAR_MARKET_END)
    _report("Bear market", df, bear_signals, multiplier, cost_model)

    # 3. A second, non-overlapping holdout: split everything before the
    # ORIGINAL test split's start date in half, so the earlier half acts
    # as a fresh out-of-sample check independent of the original 70/30 cut.
    print(f"\n=== 3. Second holdout: pre-{ORIGINAL_TEST_SPLIT} data split in half ===")
    pre_original = [s for s in signals if pd.Timestamp(s.entry_ts).date() < ORIGINAL_TEST_SPLIT]
    pre_original_dates = sorted({pd.Timestamp(s.entry_ts).date() for s in pre_original})
    if pre_original_dates:
        mid_date = pre_original_dates[len(pre_original_dates) // 2]
        earlier = [s for s in pre_original if pd.Timestamp(s.entry_ts).date() < mid_date]
        later = [s for s in pre_original if pd.Timestamp(s.entry_ts).date() >= mid_date]
        _report(f"Earlier half (before {mid_date})", df, earlier, multiplier, cost_model)
        _report(f"Later half ({mid_date} -> {ORIGINAL_TEST_SPLIT})", df, later, multiplier, cost_model)

    # For reference: the original test window's own numbers, recomputed
    # here on the same signal set for direct comparison.
    print(f"\n=== Reference: original test window ({ORIGINAL_TEST_SPLIT} -> {df['date'].max()}) ===")
    original_test = [s for s in signals if pd.Timestamp(s.entry_ts).date() >= ORIGINAL_TEST_SPLIT]
    _report("Original test window", df, original_test, multiplier, cost_model)


if __name__ == "__main__":
    main()
