"""
Same calendar-year consistency check run against BTC in
scripts/run_btc_donchian_stress_test.py (see backtest/README.md's
"Stress-testing BTC + Donchian" section), applied to ETH and SOL instead.

Context: the original strategy-showdown train/test split found Donchian
breakout survived on BTC but did NOT replicate on ETH or SOL (see
backtest/README.md's "strategy showdown" section) -- this asks a
different question than "does the strategy also work on ETH/SOL" (already
answered: no). It asks *how* it fails: does it fail everywhere
consistently (a clean, expected negative result), or did it happen to
fail on the original 70/30 split specifically while still working in
other periods (which would suggest the single-split test undersold it)?
Same fixed, unswept parameters throughout -- no fitting happens here
either.

Same realistic-cost methodology as the BTC version: $10k account, 1-2%
risk per trade, Binance's ~0.1% taker fee, 1 tick slippage.

Run from the project root with the venv active:
    python scripts/run_eth_sol_donchian_stress_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, PositionSizer, compute_metrics, simulate_trades
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import generate_donchian_breakout_signals

SYMBOLS = ["ETH", "SOL"]
ACCOUNT_SIZE = 10_000.0
RISK_LEVELS = [0.01, 0.02]
SLIPPAGE_TICKS = 1.0


def _report(label: str, df: pd.DataFrame, signals: list, multiplier: float, cost_model: CostModel) -> dict:
    if not signals:
        print(f"  {label}: no signals in this window")
        return {"label": label, "num_signals": 0}
    row = {"label": label, "num_signals": len(signals)}
    for risk_pct in RISK_LEVELS:
        sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)
        trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
        m = compute_metrics(trades, account_size=ACCOUNT_SIZE)
        print(
            f"  {label:28} risk={risk_pct:.0%}  trades={m['num_closed']:>3}  win_rate={m['win_rate']}  "
            f"PF={m['profit_factor']}  pnl=${m['total_pnl_dollars']}"
        )
        row[f"risk_{risk_pct}_trades"] = m["num_closed"]
        row[f"risk_{risk_pct}_pf"] = m["profit_factor"]
        row[f"risk_{risk_pct}_pnl"] = m["total_pnl_dollars"]
    return row


def main() -> None:
    all_rows = []
    crypto_specs = load_crypto_instruments()

    for symbol in SYMBOLS:
        df = load_continuous(symbol, "1 day")
        if df is None:
            print(f"{symbol}: no cached data, skipping")
            continue
        df = drop_untraded_bars(df)
        spec = crypto_specs[symbol]
        multiplier = float(spec["micro_multiplier"])
        cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))

        signals = generate_donchian_breakout_signals(df)
        print(f"\n=== {symbol}: {len(df)} daily bars ({df['date'].min()} -> {df['date'].max()}), {len(signals)} total signals ===")

        years = sorted(pd.to_datetime(df["date"]).dt.year.unique())
        for year in years:
            start = pd.Timestamp(f"{year}-01-01").date()
            end = pd.Timestamp(f"{year}-12-31").date()
            year_signals = [s for s in signals if start <= pd.Timestamp(s.entry_ts).date() <= end]
            row = _report(str(year), df, year_signals, multiplier, cost_model)
            row["symbol"] = symbol
            all_rows.append(row)

        # Full-period aggregate, for direct comparison against BTC's own
        # full-period numbers (94 trades, PF 1.56 at 1% risk).
        row = _report("Full period", df, signals, multiplier, cost_model)
        row["symbol"] = symbol
        all_rows.append(row)

    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "eth_sol_donchian_stress_test.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_rows).to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
