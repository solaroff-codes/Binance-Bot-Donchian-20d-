"""
Direct follow-up to a fair question: the validated 3-asset (BTC+SOL+BNB)
portfolio returned +28.1% over 6 years at 1% risk (CAGR ~4.3%) -- badly
underperforming just buying and holding BTC (+644.6%, CAGR 39.8%) over
the same window. That comparison is real, but it's not apples to apples:
buy-and-hold is 100% exposure, 100% of the time, with a 76.6% max
drawdown BTC actually had in this window (and SOL/BNB's own buy-and-hold
drawdowns are even worse, 71-96%) -- this strategy is flat ~35% of the
time and risks only 1-2% of the account per trade, by design, which is
exactly why its return AND its drawdown are both a fraction of
buy-and-hold's.

This asks the honest next question: within a CONVENTIONAL risk band (up
to 5% per trade -- above that isn't a serious proposal for a systematic
strategy regardless of backtested numbers), how much of that gap can
real risk-scaling and diversification close, and what does it cost in
drawdown to get there? Three levers, all mechanical (no new signal
parameters, so no new multiple-comparisons risk):

1. Risk per trade: 1%, 2%, 3%, 5% -- already the known lever, scaled
   further than previously shown.
2. The 3-asset portfolio (BTC+SOL+BNB) instead of BTC alone, at each
   risk level -- diversification's effect on drawdown at higher risk,
   not just at 1-2%.
3. Fractional compounding (backtest/engine.py's new compounding_fraction
   parameter): 1.0 (full reinvestment, already tested) vs 0.5 ("half-Kelly"
   -- a standard risk-management compromise, not an arbitrary number) vs
   0.0 (fixed sizing, the baseline). Full compounding's drawdown grows
   faster than proportionally as risk rises (already seen: BTC alone at
   5% risk hit 54.9% drawdown, worse than buy-and-hold's own 76.6% isn't
   far off) -- this checks whether damping it recovers most of the growth
   with a lot less of the drawdown.

Buy-and-hold benchmarks (both single-asset and the equal-weight 3-asset
portfolio) are computed for honest context throughout, not to be beaten
by construction -- reported alongside, not hidden.

Run from the project root with the venv active:
    python scripts/run_portfolio_risk_scaling.py
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

PORTFOLIO = ["BTC", "SOL", "BNB"]
ACCOUNT_SIZE = 10_000.0
RISK_LEVELS = [0.01, 0.02, 0.03, 0.05]
COMPOUNDING_FRACTIONS = [0.0, 0.5, 1.0]
SLIPPAGE_TICKS = 1.0
YEARS = (pd.Timestamp("2026-09-10") - pd.Timestamp("2020-09-10")).days / 365.25


def _load(symbol: str, specs: dict):
    df = load_continuous(symbol, "1 day")
    df = drop_untraded_bars(df)
    spec = specs[symbol]
    multiplier = float(spec["micro_multiplier"])
    cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))
    return df, multiplier, cost_model


def _cagr(final: float, starting: float) -> float:
    return (final / starting) ** (1 / YEARS) - 1


def part0_buy_and_hold_context(specs: dict) -> dict:
    print("\n" + "=" * 78)
    print("PART 0: buy-and-hold context (not a strategy -- the benchmark being compared against)")
    print("=" * 78)
    per_asset = ACCOUNT_SIZE / len(PORTFOLIO)
    bh_final_total = 0.0
    bh_series = {}
    for symbol in PORTFOLIO:
        df, _, _ = _load(symbol, specs)
        prices = df["close"].to_numpy()
        start_price, end_price = prices[0], prices[-1]
        total_return = end_price / start_price - 1
        running_peak = np.maximum.accumulate(prices)
        max_dd_pct = ((running_peak - prices) / running_peak).max()
        final = per_asset * (end_price / start_price)
        bh_final_total += final
        bh_series[symbol] = pd.Series(per_asset * (prices / start_price), index=pd.to_datetime(df["date"]))
        print(f"  {symbol}: buy-and-hold total_return={total_return:.1%}  CAGR={_cagr(end_price, start_price):.1%}  max_DD={max_dd_pct:.1%}")

    all_dates = sorted(set().union(*[s.index for s in bh_series.values()]))
    combined = pd.DataFrame({s: series.reindex(all_dates).ffill() for s, series in bh_series.items()})
    combined["total"] = combined.sum(axis=1)
    running_peak = combined["total"].cummax()
    combined_dd = ((running_peak - combined["total"]) / running_peak).max()
    print(f"  EQUAL-WEIGHT 3-asset buy-and-hold: ${ACCOUNT_SIZE:,.0f} -> ${bh_final_total:,.0f} "
          f"({(bh_final_total/ACCOUNT_SIZE - 1):.1%}, CAGR {_cagr(bh_final_total, ACCOUNT_SIZE):.1%}), max_DD={combined_dd:.1%}")
    return {"total_return": bh_final_total / ACCOUNT_SIZE - 1, "cagr": _cagr(bh_final_total, ACCOUNT_SIZE), "max_dd": combined_dd}


def part1_risk_and_diversification(specs: dict) -> None:
    print("\n" + "=" * 78)
    print("PART 1: risk-per-trade x portfolio size (fixed sizing, no compounding yet)")
    print("=" * 78)

    for risk_pct in RISK_LEVELS:
        print(f"\n--- risk={risk_pct:.0%} per trade ---")
        per_asset = ACCOUNT_SIZE / len(PORTFOLIO)
        combined_trades = []
        for symbol in PORTFOLIO:
            df, multiplier, cost_model = _load(symbol, specs)
            signals = generate_donchian_breakout_signals(df)
            sizer = PositionSizer(account_size=per_asset, risk_pct_per_trade=risk_pct)
            trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
            combined_trades.extend(trades)

        m = compute_metrics(combined_trades, account_size=ACCOUNT_SIZE)
        curve = compute_drawdown_curve(combined_trades)
        dd = curve["max_drawdown_dollars"] or 0.0
        final = ACCOUNT_SIZE + (m["total_pnl_dollars"] or 0.0)
        print(f"  3-asset portfolio, fixed size, $10k split 3 ways: final=${final:,.0f} "
              f"total_return={(final/ACCOUNT_SIZE - 1):.1%}  CAGR={_cagr(final, ACCOUNT_SIZE):.1%}  "
              f"max_DD=${dd:,.0f} ({dd/ACCOUNT_SIZE:.1%})")

        # Single BTC alone at the same risk level, full $10k, for reference.
        df, multiplier, cost_model = _load("BTC", specs)
        signals = generate_donchian_breakout_signals(df)
        sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)
        btc_trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
        m2 = compute_metrics(btc_trades, account_size=ACCOUNT_SIZE)
        curve2 = compute_drawdown_curve(btc_trades)
        dd2 = curve2["max_drawdown_dollars"] or 0.0
        final2 = ACCOUNT_SIZE + (m2["total_pnl_dollars"] or 0.0)
        print(f"  BTC alone, fixed size, full $10k:              final=${final2:,.0f} "
              f"total_return={(final2/ACCOUNT_SIZE - 1):.1%}  CAGR={_cagr(final2, ACCOUNT_SIZE):.1%}  "
              f"max_DD=${dd2:,.0f} ({dd2/ACCOUNT_SIZE:.1%})")


def part2_compounding_fractions() -> None:
    specs = load_crypto_instruments()
    print("\n" + "=" * 78)
    print("PART 2: risk-per-trade x compounding fraction, 3-asset portfolio")
    print("=" * 78)
    per_asset_capital = ACCOUNT_SIZE / len(PORTFOLIO)

    rows = []
    for risk_pct in RISK_LEVELS:
        print(f"\n--- risk={risk_pct:.0%} per trade ---")
        for fraction in COMPOUNDING_FRACTIONS:
            equity_series = {}
            for symbol in PORTFOLIO:
                df, multiplier, cost_model = _load(symbol, specs)
                signals = generate_donchian_breakout_signals(df)
                trades = simulate_trades_compounding(
                    df, signals, multiplier=multiplier, starting_capital=per_asset_capital,
                    risk_pct_per_trade=risk_pct, cost_model=cost_model, compounding_fraction=fraction,
                )
                closed = sorted([t for t in trades if t.outcome != "open"], key=lambda t: t.exit_ts)
                cum = np.cumsum([t.pnl_dollars for t in closed]) if closed else np.array([])
                equity_series[symbol] = pd.Series(
                    per_asset_capital + cum, index=[pd.Timestamp(t.exit_ts) for t in closed]
                )

            all_dates = sorted(set().union(*[s.index for s in equity_series.values()]))
            combined = pd.DataFrame(index=all_dates)
            for symbol, series in equity_series.items():
                combined[symbol] = series.reindex(all_dates).ffill().fillna(per_asset_capital)
            combined["total"] = combined.sum(axis=1)

            final_total = combined["total"].iloc[-1]
            running_peak = combined["total"].cummax()
            max_dd = (running_peak - combined["total"]).max()
            total_return = final_total / ACCOUNT_SIZE - 1
            cagr = _cagr(final_total, ACCOUNT_SIZE)

            label = {0.0: "fixed (0.0)", 0.5: "half-Kelly (0.5)", 1.0: "full compounding (1.0)"}[fraction]
            print(f"  {label:24} final=${final_total:,.0f}  total_return={total_return:.1%}  "
                  f"CAGR={cagr:.1%}  max_DD=${max_dd:,.0f} ({max_dd/ACCOUNT_SIZE:.1%})")
            rows.append({"risk_pct": risk_pct, "compounding_fraction": fraction, "final": final_total,
                         "total_return": total_return, "cagr": cagr, "max_dd_pct": max_dd / ACCOUNT_SIZE})

    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "portfolio_risk_scaling.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")


def main() -> None:
    specs = load_crypto_instruments()
    part0_buy_and_hold_context(specs)
    part1_risk_and_diversification(specs)
    part2_compounding_fractions()


if __name__ == "__main__":
    main()
