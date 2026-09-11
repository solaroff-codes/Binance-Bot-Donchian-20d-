"""
Two things in one script, both requested as a follow-up to the BTC/ETH/SOL
Donchian stress tests already run:

1. SOL gets the same full stress test BTC got (scripts/run_btc_donchian_stress_test.py):
   the 2022 bear-market window evaluated on its own, and a second,
   independent holdout distinct from the original 70/30 split. SOL's
   calendar-year check (scripts/run_eth_sol_donchian_stress_test.py) found
   it closer to BTC's picture than the original single-split test
   suggested -- this is the deeper follow-up that was flagged as not yet
   done.

2. A methodological fix applied to BTC, ETH, and SOL together: the
   calendar-year breakdowns run so far computed win rate / profit factor
   / drawdown SEPARATELY for each calendar year, resetting the running-
   equity baseline to $0 at every Dec 31 -> Jan 1 boundary. Win rate and
   profit factor are just counts/sums, unaffected by where the chopping
   happens. Drawdown is NOT unaffected: a real account carries equity
   across year boundaries, so a decline that straddles one (starts in
   November, bottoms in February) would show up as two smaller,
   understated drawdowns in a chopped-up view instead of the one real
   drawdown a continuously-held account would experience. This computes
   ONE continuous equity curve across the entire multi-year history per
   symbol (backtest.engine.compute_drawdown_curve, new this session) and
   reports the TRUE max drawdown -- how deep, when it started, when it
   bottomed, and whether/when it recovered -- plus year-end checkpoints
   on that SAME continuous curve (cumulative P&L and worst drawdown seen
   so far, as of each Dec 31), so year-by-year progression is still
   visible without ever resetting the baseline.

Same realistic-cost methodology as every other crypto test in this
project: $10k account, 1-2% risk per trade, Binance's ~0.1% taker fee,
1 tick slippage. No parameters are fit or swept anywhere in this script.

Run from the project root with the venv active:
    python scripts/run_crypto_donchian_full_analysis.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, PositionSizer, compute_drawdown_curve, compute_metrics, simulate_trades
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import generate_donchian_breakout_signals

SYMBOLS = ["BTC", "ETH", "SOL"]
ACCOUNT_SIZE = 10_000.0
RISK_LEVELS = [0.01, 0.02]
SLIPPAGE_TICKS = 1.0

BEAR_MARKET_START = pd.Timestamp("2021-11-10").date()
BEAR_MARKET_END = pd.Timestamp("2022-11-21").date()
ORIGINAL_TEST_SPLIT = pd.Timestamp("2024-11-22").date()


def _signals_in_window(signals: list, start, end) -> list:
    return [s for s in signals if start <= pd.Timestamp(s.entry_ts).date() <= end]


def _report_window(label: str, df: pd.DataFrame, signals: list, multiplier: float, cost_model: CostModel) -> None:
    print(f"\n--- {label} ({len(signals)} signals) ---")
    if not signals:
        print("  no signals in this window")
        return
    for risk_pct in RISK_LEVELS:
        sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)
        trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
        m = compute_metrics(trades, account_size=ACCOUNT_SIZE)
        print(f"  risk={risk_pct:.0%}  trades={m['num_closed']:>3}  win_rate={m['win_rate']}  PF={m['profit_factor']}  pnl=${m['total_pnl_dollars']}")


def sol_stress_test(df: pd.DataFrame, signals: list, multiplier: float, cost_model: CostModel) -> None:
    print("\n########## SOL: full stress test (same treatment BTC got) ##########")

    print(f"\n=== 2022 bear market window ({BEAR_MARKET_START} -> {BEAR_MARKET_END}) ===")
    bear_signals = _signals_in_window(signals, BEAR_MARKET_START, BEAR_MARKET_END)
    _report_window("Bear market", df, bear_signals, multiplier, cost_model)

    print(f"\n=== Second holdout: pre-{ORIGINAL_TEST_SPLIT} data split in half ===")
    pre_original = [s for s in signals if pd.Timestamp(s.entry_ts).date() < ORIGINAL_TEST_SPLIT]
    pre_original_dates = sorted({pd.Timestamp(s.entry_ts).date() for s in pre_original})
    if pre_original_dates:
        mid_date = pre_original_dates[len(pre_original_dates) // 2]
        earlier = [s for s in pre_original if pd.Timestamp(s.entry_ts).date() < mid_date]
        later = [s for s in pre_original if pd.Timestamp(s.entry_ts).date() >= mid_date]
        _report_window(f"Earlier half (before {mid_date})", df, earlier, multiplier, cost_model)
        _report_window(f"Later half ({mid_date} -> {ORIGINAL_TEST_SPLIT})", df, later, multiplier, cost_model)

    print(f"\n=== Reference: original test window ({ORIGINAL_TEST_SPLIT} -> {df['date'].max()}) ===")
    original_test = [s for s in signals if pd.Timestamp(s.entry_ts).date() >= ORIGINAL_TEST_SPLIT]
    _report_window("Original test window", df, original_test, multiplier, cost_model)


def continuous_drawdown_analysis(symbol: str, df: pd.DataFrame, signals: list, multiplier: float, cost_model: CostModel) -> None:
    print(f"\n########## {symbol}: continuous (non-chopped) equity curve ##########")

    for risk_pct in RISK_LEVELS:
        sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)
        # ONE simulate_trades call over the entire signal history -- this
        # is the whole point: no per-year resets anywhere in this call.
        all_trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
        metrics = compute_metrics(all_trades, account_size=ACCOUNT_SIZE)
        curve = compute_drawdown_curve(all_trades)

        print(f"\n--- risk={risk_pct:.0%} ---")
        print(f"  full period: {metrics['num_closed']} trades, win_rate={metrics['win_rate']}, "
              f"PF={metrics['profit_factor']}, total_pnl=${metrics['total_pnl_dollars']}")
        if curve["max_drawdown_dollars"] is not None:
            pct = curve["max_drawdown_dollars"] / ACCOUNT_SIZE
            recovered = curve["recovered_ts"] if curve["recovered_ts"] is not None else "NEVER (still underwater at end of data)"
            print(f"  TRUE continuous max drawdown: ${curve['max_drawdown_dollars']:,.2f} ({pct:.2%} of account)")
            print(f"    peak:  {curve['peak_ts']}")
            print(f"    trough:{curve['trough_ts']}")
            print(f"    recovered: {recovered}")

        # Year-end checkpoints on the SAME continuous curve: truncate to
        # trades closed by each year-end, and report cumulative P&L and
        # the worst drawdown seen up to that point -- year-by-year
        # progression without ever resetting the baseline.
        closed = sorted([t for t in all_trades if t.outcome != "open"], key=lambda t: t.exit_ts)
        if not closed:
            continue
        years = sorted(pd.to_datetime([t.exit_ts for t in closed]).year.unique())
        print("  year-end checkpoints (cumulative from day 1, not reset):")
        for year in years:
            year_end = pd.Timestamp(f"{year}-12-31").date()
            trades_so_far = [t for t in closed if pd.Timestamp(t.exit_ts).date() <= year_end]
            cum_pnl = sum(t.pnl_dollars for t in trades_so_far)
            dd_so_far = compute_drawdown_curve(trades_so_far)
            dd_dollars = dd_so_far["max_drawdown_dollars"] or 0.0
            print(f"    end of {year}: {len(trades_so_far)} trades so far, cumulative pnl=${cum_pnl:,.2f}, "
                  f"worst drawdown seen so far=${dd_dollars:,.2f} ({dd_dollars/ACCOUNT_SIZE:.2%})")


def main() -> None:
    crypto_specs = load_crypto_instruments()

    for symbol in SYMBOLS:
        df = load_continuous(symbol, "1 day")
        df = drop_untraded_bars(df)
        spec = crypto_specs[symbol]
        multiplier = float(spec["micro_multiplier"])
        cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))
        signals = generate_donchian_breakout_signals(df)
        print(f"\n{'='*70}\n{symbol}: {len(df)} daily bars ({df['date'].min()} -> {df['date'].max()}), {len(signals)} total signals\n{'='*70}")

        if symbol == "SOL":
            sol_stress_test(df, signals, multiplier, cost_model)

        continuous_drawdown_analysis(symbol, df, signals, multiplier, cost_model)


if __name__ == "__main__":
    main()
