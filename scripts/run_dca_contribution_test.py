"""
The validated "safe" configuration (BTC+SOL+BNB, Donchian breakout, 3%
risk per trade, full compounding -- backtest/README.md's "Increasing
profit on the 1:1.5 version" and "Expanding the coin universe" sections),
with regular cash contributions added on top: $300/month total, split
$100/month per coin, for 72 consecutive months (6 years) -- on top of
the $1,000 starting capital ($333.33/coin).

Uses backtest/engine.py's simulate_trades_with_contributions() (new this
session): each trade sizes off starting_capital + every contribution
made on or before that trade's entry + compounding_fraction * realized
P&L so far -- a deposit grows the next trade's position size exactly the
way a real account depositing more cash would, without being "at risk"
beyond what the 3% risk formula already implies for whatever the balance
is at the time.

Reports the account's real, literal running balance (contributions
included) alongside how much of the final number is just deposits vs.
actual trading profit, since those are very different things to look at
together -- a $22,600 balance after contributing $22,600 is a wash, not
a return.

Run from the project root with the venv active:
    python scripts/run_dca_contribution_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from backtest.engine import CostModel, compute_metrics, simulate_trades_with_contributions
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import generate_donchian_breakout_signals

PORTFOLIO = ["BTC", "SOL", "BNB"]
INITIAL_ACCOUNT_SIZE = 1_000.0
INITIAL_PER_ASSET = INITIAL_ACCOUNT_SIZE / len(PORTFOLIO)
MONTHLY_CONTRIBUTION_PER_ASSET = 100.0
CONTRIBUTION_MONTHS = 72  # 6 years
RISK_PCT = 0.03
COMPOUNDING_FRACTION = 1.0


def _load(symbol: str, specs: dict):
    df = load_continuous(symbol, "1 day")
    df = drop_untraded_bars(df)
    spec = specs[symbol]
    multiplier = float(spec["micro_multiplier"])
    cost_model = CostModel(slippage_ticks=1.0, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))
    return df, multiplier, cost_model


def main() -> None:
    specs = load_crypto_instruments()

    balance_series = {}
    contributed_series = {}
    combined_closed = []
    total_contributed_per_asset = MONTHLY_CONTRIBUTION_PER_ASSET * CONTRIBUTION_MONTHS

    for symbol in PORTFOLIO:
        df, multiplier, cost_model = _load(symbol, specs)
        signals = generate_donchian_breakout_signals(df)

        first_date = pd.Timestamp(df["date"].iloc[0])
        contributions = [
            (first_date + pd.DateOffset(months=i + 1), MONTHLY_CONTRIBUTION_PER_ASSET)
            for i in range(CONTRIBUTION_MONTHS)
        ]

        trades = simulate_trades_with_contributions(
            df, signals, multiplier=multiplier, starting_capital=INITIAL_PER_ASSET,
            risk_pct_per_trade=RISK_PCT, cost_model=cost_model, compounding_fraction=COMPOUNDING_FRACTION,
            contributions=contributions,
        )
        closed = [t for t in trades if t.outcome != "open"]
        combined_closed.extend(closed)

        # Build this sleeve's real running balance: initial capital, plus
        # every contribution (step up on its date), plus every realized
        # trade P&L (step up/down on its exit date) -- all summed on one
        # combined timeline. A contribution and a trade exit can land on
        # the exact same calendar date, so same-date deltas are summed
        # (via groupby) before cumulating, rather than left as duplicate
        # index entries a plain Series/reindex can't handle.
        pnl_events = [(pd.Timestamp(t.exit_ts).normalize(), t.pnl_dollars) for t in closed]
        contrib_events = [(pd.Timestamp(d).normalize(), amt) for d, amt in contributions]
        all_events = pd.DataFrame(pnl_events + contrib_events, columns=["date", "delta"])
        daily_deltas = all_events.groupby("date")["delta"].sum().sort_index()
        balance_series[symbol] = INITIAL_PER_ASSET + daily_deltas.cumsum()

        contrib_cum = np.cumsum([amt for _, amt in contrib_events])
        contributed_series[symbol] = pd.Series(contrib_cum, index=[c[0] for c in contrib_events])

        final_balance = balance_series[symbol].iloc[-1]
        net_trading_profit = sum(t.pnl_dollars for t in closed)
        m = compute_metrics(closed, account_size=INITIAL_PER_ASSET)
        print(f"{symbol}: started ${INITIAL_PER_ASSET:.2f} + ${total_contributed_per_asset:,.2f} contributed "
              f"-> final ${final_balance:,.2f}  (trading profit alone: ${net_trading_profit:,.2f})")
        print(f"  trades={m['num_closed']}  win_rate={m['win_rate']}  PF={m['profit_factor']}")

    # Combine into the portfolio total.
    all_dates = sorted(set().union(*[s.index for s in balance_series.values()]))
    combined = pd.DataFrame(index=all_dates)
    for symbol, series in balance_series.items():
        combined[symbol] = series.reindex(all_dates).ffill().fillna(INITIAL_PER_ASSET)
    combined["total"] = combined.sum(axis=1)

    # Cost basis over time: initial capital plus every contribution made
    # by that date (across all 3 sleeves) -- what was actually put in so
    # far, as of each point on the timeline.
    contributed_total_series = sum(
        contributed_series[s].reindex(all_dates).ffill().fillna(0) for s in PORTFOLIO
    )
    total_initial = INITIAL_ACCOUNT_SIZE
    cost_basis_series = INITIAL_ACCOUNT_SIZE + contributed_total_series

    total_contributed_all = contributed_total_series.iloc[-1]
    total_put_in = total_initial + total_contributed_all
    final_total = combined["total"].iloc[-1]
    net_profit = final_total - total_put_in

    running_peak = combined["total"].cummax()
    max_dd = (running_peak - combined["total"]).max()
    max_dd_date = (running_peak - combined["total"]).idxmax()

    # Raw dollar drawdown above is understated by design in a DCA
    # scenario: a monthly deposit arriving mid-dip props the balance back
    # up, masking how much of the ALREADY-INVESTED money actually fell.
    # This normalizes by cost basis instead -- balance / money-put-in-so-far
    # -- so a deposit doesn't get counted as "recovery."
    funded_ratio = combined["total"] / cost_basis_series
    funded_peak = funded_ratio.cummax()
    funded_dd = (funded_peak - funded_ratio).max()
    funded_dd_date = (funded_peak - funded_ratio).idxmax()

    m_combined = compute_metrics(combined_closed, account_size=total_initial)

    print("\n" + "=" * 70)
    print("COMBINED PORTFOLIO (BTC + SOL + BNB, $300/month total, 6 years)")
    print("=" * 70)
    print(f"  Initial capital:        ${total_initial:,.2f}")
    print(f"  Total contributed:      ${total_contributed_all:,.2f}  (${MONTHLY_CONTRIBUTION_PER_ASSET*3:,.0f}/month x {CONTRIBUTION_MONTHS} months)")
    print(f"  Total capital put in:   ${total_put_in:,.2f}")
    print(f"  Final balance:          ${final_total:,.2f}")
    print(f"  Net trading profit:     ${net_profit:,.2f}  (final balance minus everything put in)")
    print(f"  Return on capital put in: {(net_profit/total_put_in):+.1%}")
    print(f"  Raw $ drawdown (understated -- deposits mask real dips): ${max_dd:,.2f} on {max_dd_date.date()}")
    print(f"  Cost-basis-adjusted drawdown (the honest number): {funded_dd:.1%} of money-invested-so-far, "
          f"worst on {funded_dd_date.date()}")
    print(f"  Combined trades={m_combined['num_closed']}  win_rate={m_combined['win_rate']}  PF={m_combined['profit_factor']}")

    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "dca_contribution_test.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(out_path)
    print(f"\nFull balance curve written to {out_path}")


if __name__ == "__main__":
    main()
