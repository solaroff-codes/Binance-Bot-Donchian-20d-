"""
Two ways to increase total profit from the validated 1:1.5 BTC(+SOL)
Donchian strategy without touching its own parameters (which would
reopen the multiple-comparisons problem this project has repeatedly
found not worth the risk):

1. Add BNB as a third portfolio sleeve. Same fixed, unswept Donchian rule
   -- no per-coin tuning -- given the SAME full stress test BTC and SOL
   already passed (bear-market window, independent second holdout, plus
   the original-style test window) before being trusted, not just a
   promising calendar-year number. XRP was checked too (same criteria:
   an established, long-Binance-history large-cap coin, decided before
   looking at any result) and did NOT pass -- reported honestly rather
   than left out silently. See the printed section below for both.

2. Compounding position sizing. Every other test in this project sizes
   positions off a FIXED account_size, deliberately -- that isolates the
   strategy's own edge from the sizing mechanism, which is the right
   choice for validating whether an edge is real. This asks a different,
   equally honest question: what would an account that reinvests its
   gains (the way a real account behaves) actually have done? More
   total dollar profit is the expected result of compounding a positive
   edge -- the number that matters is whether drawdown, in percentage
   terms, gets meaningfully worse as a result, not just whether the
   dollar profit went up.

Same realistic-cost methodology throughout: 1% and 2% risk per trade,
Binance's ~0.1% taker fee, 1 tick slippage. No parameters are swept.

Run from the project root with the venv active:
    python scripts/run_profit_enhancements.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from backtest.engine import (
    CostModel,
    PositionSizer,
    compute_drawdown_curve,
    compute_metrics,
    simulate_trades,
    simulate_trades_compounding,
)
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import generate_donchian_breakout_signals

ACCOUNT_SIZE = 10_000.0
RISK_LEVELS = [0.01, 0.02]
SLIPPAGE_TICKS = 1.0
BEAR_MARKET_START = pd.Timestamp("2021-11-10").date()
BEAR_MARKET_END = pd.Timestamp("2022-11-21").date()
ORIGINAL_TEST_SPLIT = pd.Timestamp("2024-11-22").date()

PORTFOLIO = ["BTC", "SOL", "BNB"]  # passed the full stress test -- see part 0
CANDIDATE_NOT_INCLUDED = "XRP"  # checked, did not pass -- see part 0


def _load(symbol: str, specs: dict):
    df = load_continuous(symbol, "1 day")
    df = drop_untraded_bars(df)
    spec = specs[symbol]
    multiplier = float(spec["micro_multiplier"])
    cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))
    return df, multiplier, cost_model


def _window_report(label: str, df, signals, multiplier, cost_model) -> None:
    if not signals:
        print(f"  {label}: no signals")
        return
    sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=0.01)
    trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
    m = compute_metrics(trades, account_size=ACCOUNT_SIZE)
    print(f"  {label:38} trades={m['num_closed']:>3}  win_rate={m['win_rate']}  PF={m['profit_factor']}")


def part0_vet_new_candidates(specs: dict) -> None:
    print("\n" + "=" * 78)
    print("PART 0: vetting BNB and XRP with the SAME full stress test BTC/SOL passed")
    print("=" * 78)

    for symbol in ["BNB", "XRP"]:
        df, multiplier, cost_model = _load(symbol, specs)
        signals = generate_donchian_breakout_signals(df)
        print(f"\n{symbol}: {len(df)} bars, {len(signals)} signals")

        bear = [s for s in signals if BEAR_MARKET_START <= pd.Timestamp(s.entry_ts).date() <= BEAR_MARKET_END]
        _window_report("2022 bear market window", df, bear, multiplier, cost_model)

        pre = [s for s in signals if pd.Timestamp(s.entry_ts).date() < ORIGINAL_TEST_SPLIT]
        pre_dates = sorted({pd.Timestamp(s.entry_ts).date() for s in pre})
        if pre_dates:
            mid = pre_dates[len(pre_dates) // 2]
            earlier = [s for s in pre if pd.Timestamp(s.entry_ts).date() < mid]
            later = [s for s in pre if pd.Timestamp(s.entry_ts).date() >= mid]
            _window_report(f"Earlier half (before {mid})", df, earlier, multiplier, cost_model)
            _window_report(f"Later half ({mid}+)", df, later, multiplier, cost_model)

        test_window = [s for s in signals if pd.Timestamp(s.entry_ts).date() >= ORIGINAL_TEST_SPLIT]
        _window_report("Reference: original test window", df, test_window, multiplier, cost_model)

        curve = compute_drawdown_curve(
            simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model,
                             position_sizer=PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=0.01))
        )
        recovered = curve["recovered_ts"] if curve["recovered_ts"] else "NEVER"
        print(f"  full-period true drawdown: ${curve['max_drawdown_dollars']:.2f} "
              f"({curve['max_drawdown_dollars']/ACCOUNT_SIZE:.2%}), peak={curve['peak_ts']}, "
              f"trough={curve['trough_ts']}, recovered={recovered}")

    print(f"\nVerdict: BNB passes every window (see backtest/README.md for the full writeup) -> "
          f"added to the portfolio. XRP does not (weak/negative years, an unrecovered drawdown "
          f"like ETH's) -> not included.")


def part1_three_asset_portfolio(specs: dict) -> None:
    print("\n" + "=" * 78)
    print("PART 1: three-asset portfolio (BTC + SOL + BNB), original validated params")
    print("NOTE: each sleeve sized against its OWN full $10k risk-reference here")
    print("(the aggressive case -- effectively needs ~$30k total capital to run all")
    print("three at once). See Part 2 for the properly-divided single-$10k-account")
    print("version, which is the realistic one for an actual $10,000 account.")
    print("=" * 78)

    for risk_pct in RISK_LEVELS:
        print(f"\n--- risk={risk_pct:.0%} per trade, per asset (shared ${ACCOUNT_SIZE:,.0f} account) ---")
        combined_trades = []
        for symbol in PORTFOLIO:
            df, multiplier, cost_model = _load(symbol, specs)
            signals = generate_donchian_breakout_signals(df)
            sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)
            trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
            m = compute_metrics(trades, account_size=ACCOUNT_SIZE)
            curve = compute_drawdown_curve(trades)
            dd = curve["max_drawdown_dollars"] or 0.0
            print(f"  {symbol:5} alone   trades={m['num_closed']:>3}  PF={m['profit_factor']}  "
                  f"pnl=${m['total_pnl_dollars']}  max_DD=${dd:,.2f} ({dd/ACCOUNT_SIZE:.2%})")
            combined_trades.extend(trades)

        m = compute_metrics(combined_trades, account_size=ACCOUNT_SIZE)
        curve = compute_drawdown_curve(combined_trades)
        dd = curve["max_drawdown_dollars"] or 0.0
        print(f"  {'COMBINED':5}         trades={m['num_closed']:>3}  PF={m['profit_factor']}  "
              f"pnl=${m['total_pnl_dollars']}  max_DD=${dd:,.2f} ({dd/ACCOUNT_SIZE:.2%})")


def part2_compounding(specs: dict) -> None:
    print("\n" + "=" * 78)
    print("PART 2: compounding position sizing -- fixed vs. reinvested, per asset and combined")
    print("=" * 78)

    per_asset_capital = ACCOUNT_SIZE / len(PORTFOLIO)

    for risk_pct in RISK_LEVELS:
        print(f"\n--- risk={risk_pct:.0%} per trade (${ACCOUNT_SIZE:,.0f} total, split ${per_asset_capital:,.0f} per sleeve -- the realistic single-account case) ---")
        equity_series = {}  # symbol -> pd.Series indexed by exit_ts, cumulative equity
        fixed_finals = {}

        for symbol in PORTFOLIO:
            df, multiplier, cost_model = _load(symbol, specs)
            signals = generate_donchian_breakout_signals(df)

            fixed_sizer = PositionSizer(account_size=per_asset_capital, risk_pct_per_trade=risk_pct)
            fixed_trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=fixed_sizer)
            fixed_final = per_asset_capital + sum(t.pnl_dollars for t in fixed_trades if t.pnl_dollars)
            fixed_finals[symbol] = fixed_final

            comp_trades = simulate_trades_compounding(
                df, signals, multiplier=multiplier, starting_capital=per_asset_capital,
                risk_pct_per_trade=risk_pct, cost_model=cost_model,
            )
            comp_closed = [t for t in comp_trades if t.outcome != "open"]
            comp_final = per_asset_capital + sum(t.pnl_dollars for t in comp_closed)
            comp_curve = compute_drawdown_curve(comp_trades)
            comp_dd = comp_curve["max_drawdown_dollars"] or 0.0

            print(f"  {symbol:5} (${per_asset_capital:,.0f} starting): "
                  f"fixed-size final=${fixed_final:,.0f}  |  compounding final=${comp_final:,.0f}  "
                  f"(+{(comp_final/fixed_final - 1):.1%} vs fixed)  compounding max_DD=${comp_dd:,.2f} "
                  f"({comp_dd/per_asset_capital:.2%} of its own starting stake)")

            # Build this asset's cumulative-equity step series (forward-fillable).
            sorted_closed = sorted(comp_closed, key=lambda t: t.exit_ts)
            cum = np.cumsum([t.pnl_dollars for t in sorted_closed])
            equity_series[symbol] = pd.Series(
                per_asset_capital + cum, index=[pd.Timestamp(t.exit_ts) for t in sorted_closed]
            )

        # Combine: union of all exit dates, forward-fill each asset's own
        # last-known compounding equity, sum across the three sleeves.
        all_dates = sorted(set().union(*[s.index for s in equity_series.values()]))
        combined = pd.DataFrame(index=all_dates)
        for symbol, series in equity_series.items():
            combined[symbol] = series.reindex(all_dates).ffill().fillna(per_asset_capital)
        combined["total"] = combined.sum(axis=1)

        starting_total = ACCOUNT_SIZE
        final_total = combined["total"].iloc[-1]
        running_peak = combined["total"].cummax()
        drawdowns = running_peak - combined["total"]
        max_dd = drawdowns.max()
        max_dd_date = drawdowns.idxmax()

        fixed_total = sum(fixed_finals.values())
        print(f"  COMBINED portfolio, FIXED size (divided capital, no reinvestment): "
              f"${starting_total:,.0f} -> ${fixed_total:,.0f} ({(fixed_total/starting_total - 1):.1%} total return)")
        print(f"  COMBINED portfolio, COMPOUNDING: ${starting_total:,.0f} -> ${final_total:,.0f} "
              f"({(final_total/starting_total - 1):.1%} total return), "
              f"max drawdown ${max_dd:,.2f} ({max_dd/starting_total:.2%} of total) on {max_dd_date.date()}")


def main() -> None:
    specs = load_crypto_instruments()
    part0_vet_new_candidates(specs)
    part1_three_asset_portfolio(specs)
    part2_compounding(specs)


if __name__ == "__main__":
    main()
