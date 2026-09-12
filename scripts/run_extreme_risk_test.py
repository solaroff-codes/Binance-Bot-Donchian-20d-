"""
Two things, both explicitly requested as "how far can this actually go":

1. Extends the risk-managed scaling already done (1-5% risk per trade,
   backtest/README.md's "Is 25-28% over 6 years actually good?" section)
   to 7% and 10% -- still fully risk-managed (same 2x-ATR stop, same
   compounding options), just a bigger slice of equity risked per trade.
   No leverage involved in this part.

2. The genuinely different, dangerous thing: backtest/leveraged_notional.py's
   simulate_leveraged_notional_trades() -- position size driven by
   NOTIONAL exposure (current_equity * leverage, using the whole sleeve
   as margin) instead of risk. This is what "trading with Nx leverage"
   means in the common aggressive retail sense, not the capital-
   efficiency sense tested in backtest/README.md's leveraged-futures
   section (where position size stayed risk-based). Tested at 3x, 5x,
   10x, and 20x, reporting not just final return but liquidation
   frequency and the realistic risk of a sleeve being wiped out --
   the number that actually matters for deciding whether to do this.

Same $1,000 total account (the user's real capital), split three ways,
throughout. Same realistic costs (1 tick slippage, ~0.1% taker fee) as
everywhere else in this project.

Run from the project root with the venv active:
    python scripts/run_extreme_risk_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from backtest.engine import CostModel, compute_metrics, simulate_trades_compounding
from backtest.leveraged_notional import simulate_leveraged_notional_trades
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import generate_donchian_breakout_signals

PORTFOLIO = ["BTC", "SOL", "BNB"]
ACCOUNT_SIZE = 1_000.0
PER_ASSET_CAPITAL = ACCOUNT_SIZE / len(PORTFOLIO)
YEARS = (pd.Timestamp("2026-09-10") - pd.Timestamp("2020-09-10")).days / 365.25


def _load(symbol: str, specs: dict):
    df = load_continuous(symbol, "1 day")
    df = drop_untraded_bars(df)
    spec = specs[symbol]
    multiplier = float(spec["micro_multiplier"])
    cost_model = CostModel(slippage_ticks=1.0, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))
    return df, multiplier, cost_model


def _combine_equity(equity_series: dict, starting_each: float) -> pd.Series:
    all_dates = sorted(set().union(*[s.index for s in equity_series.values()]))
    combined = pd.DataFrame(index=all_dates)
    for symbol, series in equity_series.items():
        combined[symbol] = series.reindex(all_dates).ffill().fillna(starting_each)
    combined["total"] = combined.sum(axis=1)
    return combined["total"]


def part1_extended_risk_scaling(specs: dict) -> None:
    print("\n" + "=" * 78)
    print("PART 1: risk-managed scaling extended to 7% and 10% (still bounded by the")
    print("strategy's own 2x-ATR stop -- no leverage involved)")
    print("=" * 78)

    for risk_pct in [0.07, 0.10]:
        for fraction, label in [(1.0, "full compounding"), (0.5, "half-Kelly")]:
            equity_series = {}
            combined_closed = []
            for symbol in PORTFOLIO:
                df, multiplier, cost_model = _load(symbol, specs)
                signals = generate_donchian_breakout_signals(df)
                trades = simulate_trades_compounding(
                    df, signals, multiplier=multiplier, starting_capital=PER_ASSET_CAPITAL,
                    risk_pct_per_trade=risk_pct, cost_model=cost_model, compounding_fraction=fraction,
                )
                closed = [t for t in trades if t.outcome != "open"]
                combined_closed.extend(closed)
                sorted_closed = sorted(closed, key=lambda t: t.exit_ts)
                cum = np.cumsum([t.pnl_dollars for t in sorted_closed]) if sorted_closed else np.array([])
                equity_series[symbol] = pd.Series(PER_ASSET_CAPITAL + cum, index=[pd.Timestamp(t.exit_ts) for t in sorted_closed])

            total = _combine_equity(equity_series, PER_ASSET_CAPITAL)
            final_total = total.iloc[-1]
            max_dd = (total.cummax() - total).max()
            cagr = (final_total / ACCOUNT_SIZE) ** (1 / YEARS) - 1
            m = compute_metrics(combined_closed, account_size=ACCOUNT_SIZE)

            print(f"  {risk_pct:.0%} risk, {label:18} final=${final_total:,.2f}  total_return={(final_total/ACCOUNT_SIZE-1):+.1%}  "
                  f"CAGR={cagr:.2%}  max_DD=${max_dd:,.2f} ({max_dd/ACCOUNT_SIZE:.2%})  win_rate={m['win_rate']}  PF={m['profit_factor']}")


def part2_leveraged_notional(specs: dict) -> None:
    print("\n" + "=" * 78)
    print("PART 2: leveraged NOTIONAL sizing (full sleeve equity as margin,")
    print("position = equity x leverage) -- the aggressive, common-retail-mistake case")
    print("=" * 78)

    for leverage in [3, 5, 10, 20]:
        print(f"\n--- {leverage}x notional leverage ---")
        equity_series = {}
        all_liquidations = {}
        combined_closed = []

        for symbol in PORTFOLIO:
            df, multiplier, cost_model = _load(symbol, specs)
            signals = generate_donchian_breakout_signals(df)
            liq_log = []
            trades = simulate_leveraged_notional_trades(
                df, signals, multiplier=multiplier, leverage=leverage, starting_capital=PER_ASSET_CAPITAL,
                cost_model=cost_model, compounding_fraction=1.0, liquidation_log=liq_log,
            )
            closed = [t for t in trades if t.outcome != "open"]
            combined_closed.extend(closed)
            all_liquidations[symbol] = liq_log

            sorted_closed = sorted(closed, key=lambda t: t.exit_ts)
            cum = np.cumsum([t.pnl_dollars for t in sorted_closed]) if sorted_closed else np.array([])
            equity_series[symbol] = pd.Series(PER_ASSET_CAPITAL + cum, index=[pd.Timestamp(t.exit_ts) for t in sorted_closed])

            final_sleeve = PER_ASSET_CAPITAL + sum(t.pnl_dollars for t in closed)
            # % lost relative to THAT trade's own equity right before it --
            # not the original stake, which understates how violent a loss
            # is after a winning streak has grown the sleeve well past its
            # starting size.
            running = PER_ASSET_CAPITAL
            worst_single_loss_pct = 0.0
            for t in sorted_closed:
                if t.pnl_dollars < 0 and running > 0:
                    worst_single_loss_pct = max(worst_single_loss_pct, -t.pnl_dollars / running)
                running += t.pnl_dollars
            print(f"  {symbol:5} sleeve: ${PER_ASSET_CAPITAL:.2f} -> ${max(final_sleeve, 0):.2f}  "
                  f"({len(liq_log)} liquidation events)  worst single trade lost "
                  f"{worst_single_loss_pct:.0%} of that sleeve's then-current equity")
            if liq_log:
                first = liq_log[0]
                print(f"    first liquidation: {pd.Timestamp(first.entry_ts).date()}, "
                      f"sleeve equity ${first.equity_before:.2f} -> ${first.equity_after:.2f} "
                      f"({first.loss_pct_of_sleeve:.0%} lost in that one trade)")

        total = _combine_equity(equity_series, PER_ASSET_CAPITAL)
        final_total = max(total.iloc[-1], 0.0)
        max_dd = (total.cummax() - total).max()
        total_liquidations = sum(len(v) for v in all_liquidations.values())

        print(f"  COMBINED: ${ACCOUNT_SIZE:,.2f} -> ${final_total:,.2f} ({(final_total/ACCOUNT_SIZE-1):+.1%}), "
              f"max_DD=${max_dd:,.2f} ({max_dd/ACCOUNT_SIZE:.2%}), total liquidation events across all 3 sleeves: {total_liquidations}")


def main() -> None:
    specs = load_crypto_instruments()
    part1_extended_risk_scaling(specs)
    part2_leveraged_notional(specs)


if __name__ == "__main__":
    main()
