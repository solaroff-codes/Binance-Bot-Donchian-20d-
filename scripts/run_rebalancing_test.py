"""
"Just for fun" follow-up: does periodically rebalancing capital across
the BTC+SOL+BNB sleeves (selling down whichever grew fastest, topping up
the laggards back to equal thirds) improve on letting each sleeve
compound independently forever, the way the validated configuration
currently works?

backtest/engine.py's simulate_portfolio_with_rebalancing() (new this
session) runs all three sleeves on one shared timeline so a rebalance
event can pool and redistribute equity across them at scheduled dates.
Tested at monthly, quarterly, semi-annual, and annual cadences against
the no-rebalancing baseline, same $1,000 total, 3% risk, full
compounding as everywhere else in this project's validated
configuration -- several cadences on purpose, specifically so the result
isn't "whichever one number looked best" but "does this hold up across
a range of reasonable choices," given how much of this project's
findings have depended on catching exactly that kind of cherry-picking.
No transaction costs are added
for the rebalancing trades themselves (a same-exchange USDT
transfer between sleeves is not a taxable/fee-bearing spot trade the way
selling one coin for another would be, so this only holds if the
sleeves' "equity" is tracked in a stable unit like USDT between trades,
which is how this project already treats it -- see data/binance_connector.py) --
a simplification worth knowing if this were ever actually implemented
with real coin holdings rather than cash sleeves.

Run from the project root with the venv active:
    python scripts/run_rebalancing_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from backtest.engine import CostModel, compute_metrics, simulate_portfolio_with_rebalancing
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import generate_donchian_breakout_signals

PORTFOLIO = ["BTC", "SOL", "BNB"]
ACCOUNT_SIZE = 1_000.0
PER_ASSET_CAPITAL = ACCOUNT_SIZE / len(PORTFOLIO)
RISK_PCT = 0.03
YEARS = (pd.Timestamp("2026-09-10") - pd.Timestamp("2020-09-10")).days / 365.25


def _load(symbol: str, specs: dict):
    df = load_continuous(symbol, "1 day")
    df = drop_untraded_bars(df)
    spec = specs[symbol]
    multiplier = float(spec["micro_multiplier"])
    cost_model = CostModel(slippage_ticks=1.0, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))
    return df, multiplier, cost_model


def run_scenario(label: str, rebalance_months: int | None, assets: dict, first_date: pd.Timestamp, cost_model: CostModel) -> None:
    if rebalance_months is None:
        rebalance_dates = []
    else:
        n_periods = int(YEARS * 12 / rebalance_months)
        rebalance_dates = [first_date + pd.DateOffset(months=rebalance_months * (i + 1)) for i in range(n_periods)]

    trades_by_symbol = simulate_portfolio_with_rebalancing(
        assets=assets, starting_capital_per_asset=PER_ASSET_CAPITAL,
        risk_pct_per_trade=RISK_PCT, rebalance_dates=rebalance_dates, cost_model=cost_model,
    )

    equity_series = {}
    combined_closed = []
    for symbol, trades in trades_by_symbol.items():
        closed = [t for t in trades if t.outcome != "open"]
        combined_closed.extend(closed)
        sorted_closed = sorted(closed, key=lambda t: t.exit_ts)
        cum = np.cumsum([t.pnl_dollars for t in sorted_closed]) if sorted_closed else np.array([])
        equity_series[symbol] = pd.Series(PER_ASSET_CAPITAL + cum, index=[pd.Timestamp(t.exit_ts) for t in sorted_closed])

    all_dates = sorted(set().union(*[s.index for s in equity_series.values()]))
    combined = pd.DataFrame(index=all_dates)
    for symbol, series in equity_series.items():
        combined[symbol] = series.reindex(all_dates).ffill().fillna(PER_ASSET_CAPITAL)
    combined["total"] = combined.sum(axis=1)
    final_total = combined["total"].iloc[-1]
    max_dd = (combined["total"].cummax() - combined["total"]).max()
    cagr = (final_total / ACCOUNT_SIZE) ** (1 / YEARS) - 1
    m = compute_metrics(combined_closed, account_size=ACCOUNT_SIZE)

    print(f"{label} ({len(rebalance_dates)} rebalances): ${ACCOUNT_SIZE:,.0f} -> ${final_total:,.2f} "
          f"({(final_total/ACCOUNT_SIZE-1):+.1%}, CAGR {cagr:.2%}), max_DD=${max_dd:,.2f} ({max_dd/ACCOUNT_SIZE:.2%}), "
          f"trades={m['num_closed']}, win_rate={m['win_rate']}, PF={m['profit_factor']}")


def main() -> None:
    specs = load_crypto_instruments()
    assets = {}
    first_date = None
    cost_model = None
    for symbol in PORTFOLIO:
        df, multiplier, symbol_cost_model = _load(symbol, specs)
        # BTC/SOL/BNB all share the same tick_size (0.01) and commission_pct
        # (0.1%) in config/crypto_instruments.yaml, so one shared CostModel
        # is exact here, not an approximation -- confirmed, not assumed.
        cost_model = symbol_cost_model
        signals = generate_donchian_breakout_signals(df)
        assets[symbol] = (df, multiplier, signals)
        if first_date is None:
            first_date = pd.Timestamp(df["date"].iloc[0])

    run_scenario("No rebalancing (current)", None, assets, first_date, cost_model)
    run_scenario("Monthly rebalancing", 1, assets, first_date, cost_model)
    run_scenario("Quarterly rebalancing", 3, assets, first_date, cost_model)
    run_scenario("Semi-annual rebalancing", 6, assets, first_date, cost_model)
    run_scenario("Annual rebalancing", 12, assets, first_date, cost_model)


if __name__ == "__main__":
    main()
