"""
Tests the exact validated strategy (Donchian breakout, 2x-ATR stop,
3x-ATR target, 3% risk per trade, full compounding, BTC+SOL+BNB) as it
would run on leveraged perpetual futures instead of spot, at 3x, 5x, and
10x leverage.

IMPORTANT MODELING POINT, worth understanding before reading the results:
leverage does NOT, by itself, change a risk-based-sized position's P&L.
Position size here is already computed from "risk 3% of equity, given
the stop distance" -- leverage only changes how much margin/collateral is
required to hold that already-determined position, and WHERE the
exchange's forced-liquidation price sits. The real question leverage
introduces is: can the exchange force-close the position (at whatever
price consumes the posted margin) BEFORE the strategy's own stop-loss
order would have executed? If the strategy's stop is tighter than the
liquidation buffer, leverage changes nothing about performance -- it's
purely a capital-efficiency question (how much collateral sits locked
up). If the strategy's stop is WIDER than the liquidation buffer, some
trades get force-closed early, at a price the strategy itself never
chose, missing whatever the stop/target would have produced.

liquidation buffer approximation (standard retail-tier formula, ISOLATED
margin, ignoring funding/precise Binance bracket math):
    long:  liquidation_price = entry * (1 - 1/leverage + maintenance_margin_rate)
    short: liquidation_price = entry * (1 + 1/leverage - maintenance_margin_rate)
maintenance_margin_rate is assumed at 0.5% (a commonly-cited retail-bracket
figure for major pairs) for all three symbols -- an approximation, not
fetched from Binance's live bracket table, and clearly labeled as one.

For each trade, the EFFECTIVE stop is whichever of (the strategy's own
2x-ATR stop, the leverage-driven liquidation price) is closer to entry --
whichever would actually trigger first. Position sizing is NOT changed by
leverage (see above) -- only which stop level binds. This reuses
backtest/engine.py's already-tested simulate_trades_compounding
unchanged; only the signals fed into it are adjusted per leverage level.

NOT modeled here, and worth knowing before trading this for real: funding
rate (perpetual futures' periodic long/short payment, paid every 8 hours
-- a real, recurring cost this backtest has no historical data for, since
data/binance_connector.py only pulls SPOT klines; building a futures
funding-rate connector is a natural next step, not done here) and
Binance's exact tiered maintenance-margin brackets (which increase for
larger position sizes -- irrelevant at this account size, but worth
knowing this isn't literally Binance's own formula).

Run from the project root with the venv active:
    python scripts/run_leverage_liquidation_test.py
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from backtest.engine import CostModel, compute_drawdown_curve, compute_metrics, simulate_trades_compounding
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import generate_donchian_breakout_signals

PORTFOLIO = ["BTC", "SOL", "BNB"]
ACCOUNT_SIZE = 10_000.0
PER_ASSET_CAPITAL = ACCOUNT_SIZE / len(PORTFOLIO)
RISK_PCT = 0.03
COMPOUNDING_FRACTION = 1.0
LEVERAGE_LEVELS = [3, 5, 10]
MAINTENANCE_MARGIN_RATE = 0.005  # 0.5% -- approximation, see module docstring
YEARS = (pd.Timestamp("2026-09-10") - pd.Timestamp("2020-09-10")).days / 365.25


def _load(symbol: str, specs: dict):
    df = load_continuous(symbol, "1 day")
    df = drop_untraded_bars(df)
    spec = specs[symbol]
    multiplier = float(spec["micro_multiplier"])
    cost_model = CostModel(slippage_ticks=1.0, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))
    return df, multiplier, cost_model


def leverage_adjust_signals(signals: list, leverage: int, maintenance_margin_rate: float = MAINTENANCE_MARGIN_RATE):
    """Swap in the liquidation price as the effective stop wherever it's
    tighter (closer to entry) than the strategy's own 2x-ATR stop.
    Returns (adjusted_signals, liquidation_bound_count, stop_pct_stats)."""
    adjusted = []
    liquidation_bound = 0
    stop_pcts = []
    for s in signals:
        stop_pct = abs(s.entry_price - s.stop_price) / s.entry_price
        stop_pcts.append(stop_pct)
        if s.direction == "long":
            liq_price = s.entry_price * (1 - 1 / leverage + maintenance_margin_rate)
            effective_stop = max(s.stop_price, liq_price)
        else:
            liq_price = s.entry_price * (1 + 1 / leverage - maintenance_margin_rate)
            effective_stop = min(s.stop_price, liq_price)

        if effective_stop != s.stop_price:
            liquidation_bound += 1
            adjusted.append(replace(s, stop_price=effective_stop))
        else:
            adjusted.append(s)
    return adjusted, liquidation_bound, stop_pcts


def main() -> None:
    specs = load_crypto_instruments()

    print("=" * 78)
    print("Stop distance (2x ATR) as % of price -- the number that determines")
    print("whether leverage's liquidation buffer ever binds tighter than the")
    print("strategy's own stop, per symbol")
    print("=" * 78)

    all_signals = {}
    for symbol in PORTFOLIO:
        df, multiplier, cost_model = _load(symbol, specs)
        signals = generate_donchian_breakout_signals(df)
        all_signals[symbol] = (df, multiplier, cost_model, signals)
        stop_pcts = np.array([abs(s.entry_price - s.stop_price) / s.entry_price for s in signals])
        print(f"\n{symbol}: {len(signals)} signals, stop distance as % of price -- "
              f"min={stop_pcts.min():.1%}  median={np.median(stop_pcts):.1%}  "
              f"mean={stop_pcts.mean():.1%}  max={stop_pcts.max():.1%}")
        for leverage in LEVERAGE_LEVELS:
            liq_buffer = 1 / leverage - MAINTENANCE_MARGIN_RATE
            pct_wider_than_buffer = (stop_pcts > liq_buffer).mean()
            print(f"  {leverage}x leverage: liquidation buffer ~{liq_buffer:.1%} of price -- "
                  f"{pct_wider_than_buffer:.1%} of signals have a WIDER stop than this buffer "
                  f"(would be liquidation-bound at entry)")

    print("\n" + "=" * 78)
    print("Full portfolio backtest at each leverage level (liquidation-adjusted exits)")
    print("=" * 78)

    baseline_final = None
    for leverage in [None] + LEVERAGE_LEVELS:
        label = "SPOT (no leverage, baseline)" if leverage is None else f"{leverage}x leverage"
        equity_series = {}
        total_liquidations = 0
        combined_closed = []

        for symbol in PORTFOLIO:
            df, multiplier, cost_model, signals = all_signals[symbol]
            if leverage is None:
                use_signals = signals
                liq_count = 0
            else:
                use_signals, liq_count, _ = leverage_adjust_signals(signals, leverage)
            total_liquidations += liq_count

            trades = simulate_trades_compounding(
                df, use_signals, multiplier=multiplier, starting_capital=PER_ASSET_CAPITAL,
                risk_pct_per_trade=RISK_PCT, cost_model=cost_model, compounding_fraction=COMPOUNDING_FRACTION,
            )
            closed = [t for t in trades if t.outcome != "open"]
            combined_closed.extend(closed)
            sorted_closed = sorted(closed, key=lambda t: t.exit_ts)
            cum = np.cumsum([t.pnl_dollars for t in sorted_closed]) if sorted_closed else np.array([])
            equity_series[symbol] = pd.Series(
                PER_ASSET_CAPITAL + cum, index=[pd.Timestamp(t.exit_ts) for t in sorted_closed]
            )

        all_dates = sorted(set().union(*[s.index for s in equity_series.values()]))
        combined = pd.DataFrame(index=all_dates)
        for symbol, series in equity_series.items():
            combined[symbol] = series.reindex(all_dates).ffill().fillna(PER_ASSET_CAPITAL)
        combined["total"] = combined.sum(axis=1)
        final_total = combined["total"].iloc[-1]
        running_peak = combined["total"].cummax()
        max_dd = (running_peak - combined["total"]).max()

        m = compute_metrics(combined_closed, account_size=ACCOUNT_SIZE)
        cagr = (final_total / ACCOUNT_SIZE) ** (1 / YEARS) - 1

        print(f"\n{label}:")
        print(f"  final=${final_total:,.2f}  total_return={(final_total/ACCOUNT_SIZE - 1):+.1%}  CAGR={cagr:.2%}")
        print(f"  trades={m['num_closed']}  win_rate={m['win_rate']}  PF={m['profit_factor']}  "
              f"max_DD=${max_dd:,.2f} ({max_dd/ACCOUNT_SIZE:.2%})")
        if leverage is not None:
            print(f"  signals liquidation-bound (tighter than intended stop): {total_liquidations}")

        if leverage is None:
            baseline_final = final_total


if __name__ == "__main__":
    main()
