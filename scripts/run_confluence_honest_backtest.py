"""
The one strategy in this project that has never been given the honest
treatment: the original Wyckoff accumulation/distribution + Fibonacci
retracement + Elliott Wave confluence rule (strategy/signals.py) -- the
strategy this whole project started with, before the trendline cascade,
the technical-strategy showdown, or crypto existed. Every number reported
for it anywhere else (README.md's "Current results", "Combined
confluence" section in backtest/README.md) had zero transaction costs,
implicit 1-contract-per-trade sizing, and no train/test split -- the same
gap this project already closed for the trendline cascade and the 8
technical strategies. This script closes it here too.

Same honest methodology as scripts/run_realistic_backtest.py and
scripts/run_100k_full_size_backtest.py: $100,000 account, full-size
contracts (mathematically identical to micro+$10k for these four
symbols -- verified earlier in this project), 1 tick slippage,
$2.50/contract commission, 1-2% risk per trade, 70/30 train/test split
by date. range_max_pct x retracement_zone x target_extension_ratio is
swept on TRAIN only (this strategy's three most consequential knobs --
how wide a "trading range" can be, where the pullback entry zone sits,
and how far the target reaches), the best-on-train combo is evaluated on
held-out test -- not "report the best cell," the same discipline used
for the trendline cascade's touch/break/target sweep.

Only GC, CL, ES, NQ have continuous-series strategy configs
(config/strategies/*_1day_continuous.yaml) -- SI and BZ were never given
one (this strategy predates SI/BZ being added to the project at all).

This strategy is known to be sparse -- far fewer signals than the
trendline cascade or the technical strategies, by design (it requires a
full range -> breakout -> impulse -> retracement -> confirming-swing
chain, not a single crossover/breakout condition). MIN_TRAIN_TRADES is
set lower here (3, not 5) to accommodate that, and the honest
consequence of a strategy this selective is reported directly rather
than worked around: if a symbol never reaches even 3 train trades across
the whole grid, that's the finding, not a bug.

Run from the project root with the venv active:
    python scripts/run_confluence_honest_backtest.py
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, PositionSizer, compute_metrics, simulate_trades
from data.cache import drop_untraded_bars, load_continuous
from data.contracts import load_instruments
from strategy.config import load_config
from strategy.signals import generate_signals

SYMBOLS = ["GC", "CL", "ES", "NQ"]
TIMEFRAMES = ["1 day", "1 hour"]  # daily turned out too sparse to evaluate at all (0-2 signals
# across a full 6-year window) -- 1 hour is the timeframe this strategy's
# earlier (cost-free, unsplit) sweeps actually found signal density on
# (see scripts/sweep_entry_exit.py's docstring), so it's included here too
# rather than concluding "no signal" from daily alone.
ACCOUNT_SIZE = 100_000.0
RISK_LEVELS = [0.01, 0.02]
COMMISSION_PER_CONTRACT = 2.50
SLIPPAGE_TICKS = 1.0
TRAIN_FRACTION = 0.70
MIN_TRAIN_TRADES = 3

RANGE_MAX_PCT_GRID = [0.03, 0.05, 0.08]
RETRACEMENT_ZONE_GRID = [(0.382, 0.618), (0.5, 0.618), (0.5, 0.786), (0.382, 0.786)]
TARGET_EXTENSION_RATIO_GRID = [1.272, 1.618, 2.0]


def _split_by_date(items: list, split_date) -> tuple[list, list]:
    before = [s for s in items if pd.Timestamp(s.entry_ts).date() < split_date]
    after = [s for s in items if pd.Timestamp(s.entry_ts).date() >= split_date]
    return before, after


def run_symbol(symbol: str, timeframe: str) -> list[dict]:
    timeframe_slug = timeframe.replace(" ", "")
    config_name = f"{symbol}_{timeframe_slug}_continuous"
    try:
        base_config = load_config(config_name)
    except FileNotFoundError:
        print(f"{symbol}/{timeframe}: no confluence strategy config ({config_name}.yaml) -- skipping")
        return []

    df = load_continuous(symbol, timeframe)
    if df is None:
        print(f"{symbol}/{timeframe}: no cached continuous data -- skipping")
        return []
    df = drop_untraded_bars(df)

    spec = load_instruments()[symbol]
    multiplier = float(spec["multiplier"])
    tick_size = float(spec["tick_size"])
    cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=tick_size, commission_per_contract=COMMISSION_PER_CONTRACT)

    split_idx = int(len(df) * TRAIN_FRACTION)
    split_date = pd.Timestamp(df["date"].iloc[split_idx]).date()
    print(f"\n=== {symbol} confluence strategy, {timeframe} ({len(df)} bars, split at {split_date}, full multiplier={multiplier}) ===")

    # Sizing check at the base (default) config -- informative regardless
    # of which grid combo the train sweep below ends up picking, since
    # stop distance is dominated by the confirming swing's own location,
    # not these three knobs.
    base_signals = generate_signals(df, base_config)
    base_risks = [abs(s.entry_price - s.stop_price) * multiplier for s in base_signals]
    if base_risks:
        min_risk_pct = min(base_risks) / ACCOUNT_SIZE
        print(f"  {len(base_signals)} raw signals at default params. sizing check: cheapest needs {min_risk_pct:.2%} of a ${ACCOUNT_SIZE:,.0f} account")
    else:
        print("  0 raw signals at default params")

    rows = []
    for risk_pct in RISK_LEVELS:
        sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)

        best = None  # (pf, config, train_sig, test_sig, train_metrics)
        for range_max_pct in RANGE_MAX_PCT_GRID:
            for retracement_zone in RETRACEMENT_ZONE_GRID:
                for target_ratio in TARGET_EXTENSION_RATIO_GRID:
                    combo = replace(
                        base_config, range_max_pct=range_max_pct,
                        retracement_zone=retracement_zone, target_extension_ratio=target_ratio,
                    )
                    all_signals = generate_signals(df, combo)
                    train_sig, test_sig = _split_by_date(all_signals, split_date)
                    train_trades = simulate_trades(df, train_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
                    if len(train_trades) < MIN_TRAIN_TRADES:
                        continue
                    train_metrics = compute_metrics(train_trades, account_size=ACCOUNT_SIZE)
                    pf = train_metrics["profit_factor"]
                    if pf is None:
                        continue
                    if best is None or pf > best[0]:
                        best = (pf, combo, train_sig, test_sig, train_metrics)

        if best is None:
            reason = f"no param combo reached {MIN_TRAIN_TRADES} train trades -- this strategy is too sparse on {symbol}/{timeframe} to evaluate honestly"
            rows.append({"symbol": symbol, "timeframe": timeframe, "risk_pct": risk_pct, "split": "train", "error": reason})
            print(f"  risk={risk_pct:.0%}: {reason}")
            continue

        _, chosen_combo, train_sig, test_sig, train_metrics = best
        test_trades = simulate_trades(df, test_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
        test_metrics = compute_metrics(test_trades, account_size=ACCOUNT_SIZE)

        for split_name, sig_list, metrics in [("train", train_sig, train_metrics), ("test", test_sig, test_metrics)]:
            rows.append({
                "symbol": symbol, "timeframe": timeframe, "risk_pct": risk_pct, "split": split_name,
                "range_max_pct": chosen_combo.range_max_pct, "retracement_zone": chosen_combo.retracement_zone,
                "target_extension_ratio": chosen_combo.target_extension_ratio,
                "num_raw_signals": len(sig_list), **metrics,
            })
        print(f"  risk={risk_pct:.0%}  train: {train_metrics['num_closed']} trades PF={train_metrics['profit_factor']}  |  "
              f"test: {test_metrics['num_closed']} trades PF={test_metrics['profit_factor']} pnl=${test_metrics['total_pnl_dollars']}")

    return rows


def main() -> None:
    all_rows = []
    for timeframe in TIMEFRAMES:
        for symbol in SYMBOLS:
            all_rows.extend(run_symbol(symbol, timeframe))

    df = pd.DataFrame(all_rows)
    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "confluence_honest_backtest.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
