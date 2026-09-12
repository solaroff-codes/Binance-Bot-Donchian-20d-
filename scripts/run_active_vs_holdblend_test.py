"""
Direct test of a different portfolio construction than "trade all three
actively": what if only the strongest, most-consistent candidate (BTC)
is actively traded (3% risk, compounding), with the other two sleeves
(SOL, BNB) held as buy-and-hold instead of run through the strategy?
Buy-and-hold's own return/drawdown profile was already characterized in
backtest/README.md's "Is 25-28% over 6 years actually good?" section
(SOL +2,764.7%/96.3% drawdown, BNB +2,767.2%/70.9% drawdown over the same
6 years) -- this asks whether blending some of that raw upside in, at
the cost of its own raw drawdown, beats trading all three the same way.

Run from the project root with the venv active:
    python scripts/run_active_vs_holdblend_test.py
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


def _active_equity(symbol: str, per_asset: float, specs: dict) -> pd.Series:
    df, multiplier, cost_model = _load(symbol, specs)
    signals = generate_donchian_breakout_signals(df)
    trades = simulate_trades_compounding(
        df, signals, multiplier=multiplier, starting_capital=per_asset,
        risk_pct_per_trade=RISK_PCT, cost_model=cost_model, compounding_fraction=1.0,
    )
    closed = sorted([t for t in trades if t.outcome != "open"], key=lambda t: t.exit_ts)
    cum = np.cumsum([t.pnl_dollars for t in closed]) if closed else np.array([])
    return pd.Series(per_asset + cum, index=[pd.Timestamp(t.exit_ts) for t in closed])


def _buyhold_equity(symbol: str, per_asset: float, specs: dict) -> pd.Series:
    df, _, _ = _load(symbol, specs)
    prices = df["close"].to_numpy()
    return pd.Series(per_asset * (prices / prices[0]), index=pd.to_datetime(df["date"]))


def _combine_and_report(label: str, series: dict, per_asset: float) -> None:
    all_dates = sorted(set().union(*[s.index for s in series.values()]))
    combined = pd.DataFrame(index=all_dates)
    for symbol, s in series.items():
        combined[symbol] = s.reindex(all_dates).ffill().fillna(per_asset)
    combined["total"] = combined.sum(axis=1)
    final_total = combined["total"].iloc[-1]
    max_dd = (combined["total"].cummax() - combined["total"]).max()
    cagr = (final_total / ACCOUNT_SIZE) ** (1 / YEARS) - 1
    print(f"{label}: ${ACCOUNT_SIZE:,.0f} -> ${final_total:,.2f} ({(final_total/ACCOUNT_SIZE-1):+.1%}, CAGR {cagr:.2%}), "
          f"max_DD=${max_dd:,.2f} ({max_dd/ACCOUNT_SIZE:.2%})")


def main() -> None:
    specs = load_crypto_instruments()
    per_asset = ACCOUNT_SIZE / 3

    # Baseline: all three actively traded (already validated).
    active_all = {s: _active_equity(s, per_asset, specs) for s in ["BTC", "SOL", "BNB"]}
    _combine_and_report("All 3 actively traded (current)", active_all, per_asset)

    # Blend: BTC actively traded, SOL and BNB held as buy-and-hold.
    blend = {
        "BTC": _active_equity("BTC", per_asset, specs),
        "SOL": _buyhold_equity("SOL", per_asset, specs),
        "BNB": _buyhold_equity("BNB", per_asset, specs),
    }
    _combine_and_report("BTC active, SOL+BNB buy-and-hold", blend, per_asset)

    # All buy-and-hold, for reference (the other extreme).
    all_hold = {s: _buyhold_equity(s, per_asset, specs) for s in ["BTC", "SOL", "BNB"]}
    _combine_and_report("All 3 buy-and-hold (reference, not a strategy)", all_hold, per_asset)


if __name__ == "__main__":
    main()
