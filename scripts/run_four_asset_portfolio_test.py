"""
Direct comparison: does adding DOGE as a 4th sleeve actually help the
validated BTC+SOL+BNB portfolio (backtest/README.md's "Increasing profit
on the 1:1.5 version" section), or does its long/overlapping drawdown
period cancel out the diversification benefit? Same 3% risk, full
compounding, $1,000 real account split evenly across sleeves in each
configuration (3-way vs 4-way).

Run from the project root with the venv active:
    python scripts/run_four_asset_portfolio_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from backtest.engine import CostModel, compute_metrics, simulate_trades_compounding
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import generate_donchian_breakout_signals

ACCOUNT_SIZE = 1_000.0
RISK_PCT = 0.03
YEARS = (pd.Timestamp("2026-09-10") - pd.Timestamp("2020-09-10")).days / 365.25


def _load(symbol: str, specs: dict):
    df = load_continuous(symbol, "1 day")
    df = drop_untraded_bars(df)
    spec = specs[symbol]
    multiplier = float(spec["micro_multiplier"])
    cost_model = CostModel(slippage_ticks=1.0, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))
    return df, multiplier, cost_model


def run_portfolio(symbols: list[str], specs: dict, label: str) -> None:
    per_asset = ACCOUNT_SIZE / len(symbols)
    equity_series = {}
    combined_closed = []

    for symbol in symbols:
        df, multiplier, cost_model = _load(symbol, specs)
        signals = generate_donchian_breakout_signals(df)
        trades = simulate_trades_compounding(
            df, signals, multiplier=multiplier, starting_capital=per_asset,
            risk_pct_per_trade=RISK_PCT, cost_model=cost_model, compounding_fraction=1.0,
        )
        closed = [t for t in trades if t.outcome != "open"]
        combined_closed.extend(closed)
        sorted_closed = sorted(closed, key=lambda t: t.exit_ts)
        cum = np.cumsum([t.pnl_dollars for t in sorted_closed]) if sorted_closed else np.array([])
        equity_series[symbol] = pd.Series(per_asset + cum, index=[pd.Timestamp(t.exit_ts) for t in sorted_closed])

    all_dates = sorted(set().union(*[s.index for s in equity_series.values()]))
    combined = pd.DataFrame(index=all_dates)
    for symbol, series in equity_series.items():
        combined[symbol] = series.reindex(all_dates).ffill().fillna(per_asset)
    combined["total"] = combined.sum(axis=1)
    final_total = combined["total"].iloc[-1]
    max_dd = (combined["total"].cummax() - combined["total"]).max()
    cagr = (final_total / ACCOUNT_SIZE) ** (1 / YEARS) - 1
    m = compute_metrics(combined_closed, account_size=ACCOUNT_SIZE)

    print(f"{label} ({', '.join(symbols)}, ${per_asset:.2f}/sleeve):")
    print(f"  ${ACCOUNT_SIZE:,.0f} -> ${final_total:,.2f} ({(final_total/ACCOUNT_SIZE-1):+.1%}, CAGR {cagr:.2%})")
    print(f"  max_DD=${max_dd:,.2f} ({max_dd/ACCOUNT_SIZE:.2%})  trades={m['num_closed']}  win_rate={m['win_rate']}  PF={m['profit_factor']}")


def main() -> None:
    specs = load_crypto_instruments()
    run_portfolio(["BTC", "SOL", "BNB"], specs, "3-asset (current)")
    print()
    run_portfolio(["BTC", "SOL", "BNB", "DOGE"], specs, "4-asset (+DOGE)")
    print()
    run_portfolio(["BTC"], specs, "BTC only, all capital")


if __name__ == "__main__":
    main()
