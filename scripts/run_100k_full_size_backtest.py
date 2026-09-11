"""
Re-run everything already tested on futures — the trendline cascade and
all 8 technical strategies (strategy/technical_signals.py) — on a
hypothetical $100,000 account using FULL-SIZE contracts (GC/CL/ES/NQ/SI/BZ,
not the micro versions), at standard 1-2% dollar risk per trade.

Why this adds real information instead of just repeating the last run:
GC/CL/ES/NQ's micro_multiplier is exactly 1/10 of their full multiplier,
and $100k is exactly 10x $10k, so for those four symbols full+$100k is
mathematically IDENTICAL to micro+$10k (verified directly, see
scripts/run_donchian_futures_backtest.py's docstring) — nothing new there,
they're included only so every symbol goes through the same script.
SI and BZ are different: SI's "micro" (QI, 2500oz) is half-size, not
tenth-size, and BZ has no smaller contract at all (its micro_multiplier
already equals the full one) — so SI and BZ at $100k/full-size are a
genuinely new test, not a repeat.

The other seven technical strategies (everything except Donchian breakout)
have never been run against futures at all before this — the earlier
futures work only tested Donchian (the crypto showdown's survivor) and the
trendline cascade. This script closes that gap.

Same realistic-cost methodology as every other honest backtest in this
project: 1 tick slippage, $2.50/contract round-turn commission (futures'
flat fee), 70/30 train/test split by date, no parameter sweep for the 8
technical strategies (one fixed, reasoned parameter set each — sweeping 8
strategies x N params would reproduce the same multiple-comparisons trap
documented in backtest/README.md), and the trendline cascade keeps its
existing best-known structure per instrument (BEST_STRUCTURES, from
scripts/sweep_trendline_params.py) with touch/break/target swept on train
only and evaluated on held-out test, exactly as in
scripts/run_realistic_backtest.py.

NOT included: the original Wyckoff/Fibonacci/Elliott Wave confluence
strategy (strategy/signals.py) — it has never been put through the
realistic-cost/sizing/train-test treatment at all, on any account size or
instrument (see README.md's "Not yet built" section); that's a separate,
larger gap than "wrong account size" and out of scope for this run.

Run from the project root with the venv active:
    python scripts/run_100k_full_size_backtest.py
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, PositionSizer, compute_metrics, simulate_trades
from backtest.trailing import simulate_trailing_trades
from data.cache import drop_untraded_bars, load_continuous, load_latest
from data.contracts import load_instruments
from strategy.technical_signals import STRATEGY_REGISTRY
from strategy.trendline_config import load_config
from strategy.trendline_signals import compute_cascade_state, signals_from_state

SYMBOLS = ["GC", "CL", "ES", "NQ", "SI", "BZ"]
ACCOUNT_SIZE = 100_000.0
RISK_LEVELS = [0.01, 0.02]
COMMISSION_PER_CONTRACT = 2.50
SLIPPAGE_TICKS = 1.0
TRAIN_FRACTION = 0.70
MIN_TEST_TRADES_TO_TRUST = 5
MIN_TRAIN_TRADES = 5

BEST_STRUCTURES = {
    "CL": (("1 month", "1 week", "1 day"), "4 hours"),
    "GC": (("1 day", "4 hours"), "1 hour"),
    "ES": (("1 month", "1 week", "1 day", "4 hours"), "1 hour"),
    "NQ": (("1 month", "1 week", "1 day", "4 hours"), "1 hour"),
    "SI": (("1 month", "1 week", "1 day", "4 hours"), "1 hour"),
    "BZ": (("1 month", "1 week", "1 day", "4 hours"), "1 hour"),
}
TOUCH_TOLERANCE_GRID = [0.001, 0.003, 0.005]
BREAK_BUFFER_GRID = [0.001, 0.003, 0.005]
TARGET_R_GRID = [1.0, 1.5, 2.0, 3.0]


def _split_by_date(items: list, split_date) -> tuple[list, list]:
    before = [s for s in items if pd.Timestamp(s.entry_ts).date() < split_date]
    after = [s for s in items if pd.Timestamp(s.entry_ts).date() >= split_date]
    return before, after


def run_technical_strategies() -> list[dict]:
    rows = []
    instruments = load_instruments()

    for symbol in SYMBOLS:
        df = load_continuous(symbol, "1 day")
        source = "continuous"
        if df is None:
            # SI/BZ have no stitched continuous series yet (single-contract
            # only, see README.md's "Not yet built" list) -- fall back to
            # the latest single contract's own history rather than skip
            # these two symbols entirely.
            df = load_latest(symbol, "1 day")
            source = "single-contract"
        if df is None:
            print(f"{symbol}: no cached data at all, skipping")
            continue
        df = drop_untraded_bars(df)

        spec = instruments[symbol]
        multiplier = float(spec["multiplier"])
        tick_size = float(spec["tick_size"])
        cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=tick_size, commission_per_contract=COMMISSION_PER_CONTRACT)

        split_idx = int(len(df) * TRAIN_FRACTION)
        split_date = pd.Timestamp(df["date"].iloc[split_idx]).date()
        print(f"\n=== {symbol} technical strategies ({len(df)} daily bars [{source}], split at {split_date}, full multiplier={multiplier}) ===")

        for strategy_name, generator in STRATEGY_REGISTRY.items():
            signals = generator(df)
            train_sig, test_sig = _split_by_date(signals, split_date)
            risks = [abs(s.entry_price - s.stop_price) * multiplier for s in signals]
            min_risk_pct = (min(risks) / ACCOUNT_SIZE) if risks else None

            for risk_pct in RISK_LEVELS:
                sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)
                train_trades = simulate_trades(df, train_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
                test_trades = simulate_trades(df, test_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
                train_metrics = compute_metrics(train_trades, account_size=ACCOUNT_SIZE)
                test_metrics = compute_metrics(test_trades, account_size=ACCOUNT_SIZE)

                rows.append({"symbol": symbol, "strategy": strategy_name, "risk_pct": risk_pct, "split": "train", "data_source": source,
                             "num_raw_signals": len(train_sig), "min_risk_pct_needed_for_1_contract": min_risk_pct, **train_metrics})
                rows.append({"symbol": symbol, "strategy": strategy_name, "risk_pct": risk_pct, "split": "test", "data_source": source,
                             "num_raw_signals": len(test_sig), "min_risk_pct_needed_for_1_contract": min_risk_pct, **test_metrics})

            min_risk_str = f"{min_risk_pct:.2%}" if min_risk_pct is not None else "n/a"
            print(f"  {strategy_name:22} train_sig={len(train_sig):3} test_sig={len(test_sig):3}  cheapest signal needs {min_risk_str} of account")

    return rows


def _load_single(symbol: str, timeframe: str) -> pd.DataFrame | None:
    df = load_latest(symbol, timeframe)
    return drop_untraded_bars(df) if df is not None else None


def run_trendline_cascade() -> list[dict]:
    rows = []
    instruments = load_instruments()

    for symbol in SYMBOLS:
        print(f"\n=== {symbol} trendline cascade ===")
        bias_timeframes, trigger_timeframe = BEST_STRUCTURES[symbol]
        base_config = replace(load_config(symbol), bias_timeframes=bias_timeframes, trigger_timeframe=trigger_timeframe)

        bias_dfs = {}
        missing = False
        for tf in bias_timeframes:
            bdf = _load_single(symbol, tf)
            if bdf is None:
                print(f"  missing {tf}, skipping")
                missing = True
                break
            bias_dfs[tf] = bdf
        if missing:
            continue
        trigger_df = _load_single(symbol, trigger_timeframe)
        if trigger_df is None:
            print(f"  missing {trigger_timeframe}, skipping")
            continue

        state = compute_cascade_state(bias_dfs, trigger_df, base_config)
        split_idx = int(len(trigger_df) * TRAIN_FRACTION)
        split_date = pd.Timestamp(trigger_df["date"].iloc[split_idx]).date()

        spec = instruments[symbol]
        multiplier = float(spec["multiplier"])
        tick_size = float(spec["tick_size"])
        cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=tick_size, commission_per_contract=COMMISSION_PER_CONTRACT)

        base_signals = signals_from_state(state, base_config)
        base_risks = [abs(s.entry_price - s.stop_price) * multiplier for s in base_signals]
        min_risk_dollars = min(base_risks) if base_risks else None
        min_risk_pct_needed = (min_risk_dollars / ACCOUNT_SIZE) if min_risk_dollars else None
        if min_risk_pct_needed is not None:
            print(f"  sizing check: cheapest signal needs ${min_risk_dollars:,.0f} = {min_risk_pct_needed:.2%} of a ${ACCOUNT_SIZE:,.0f} account (full multiplier={multiplier})")

        for risk_pct in RISK_LEVELS:
            sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)

            best = None
            for touch in TOUCH_TOLERANCE_GRID:
                for buffer in BREAK_BUFFER_GRID:
                    for target_r in TARGET_R_GRID:
                        combo = replace(base_config, touch_tolerance_pct=touch, break_buffer_pct=buffer, target_r_multiple=target_r)
                        all_signals = signals_from_state(state, combo)
                        train_sig, test_sig = _split_by_date(all_signals, split_date)
                        train_trades = simulate_trades(trigger_df, train_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
                        if len(train_trades) < MIN_TRAIN_TRADES:
                            continue
                        train_metrics = compute_metrics(train_trades, account_size=ACCOUNT_SIZE)
                        pf = train_metrics["profit_factor"]
                        if pf is None:
                            continue
                        if best is None or pf > best[0]:
                            best = (pf, combo, train_sig, test_sig)

            if best is None:
                reason = "no combo reached MIN_TRAIN_TRADES"
                if min_risk_pct_needed is not None and min_risk_pct_needed > risk_pct:
                    reason += f" (cheapest signal needs {min_risk_pct_needed:.2%}, above the {risk_pct:.0%} budget — every trade skipped as undersized)"
                rows.append({"symbol": symbol, "strategy": "trendline_cascade", "risk_pct": risk_pct, "exit_type": "fixed_target",
                             "split": "train", "error": reason, "min_risk_pct_needed_for_1_contract": min_risk_pct_needed})
                print(f"  risk={risk_pct:.0%} fixed_target: {reason}")
                continue

            _, chosen_combo, train_sig, test_sig = best
            train_trades = simulate_trades(trigger_df, train_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
            test_trades = simulate_trades(trigger_df, test_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
            train_metrics = compute_metrics(train_trades, account_size=ACCOUNT_SIZE)
            test_metrics = compute_metrics(test_trades, account_size=ACCOUNT_SIZE)

            for split_name, sig_list, metrics in [("train", train_sig, train_metrics), ("test", test_sig, test_metrics)]:
                rows.append({
                    "symbol": symbol, "strategy": "trendline_cascade", "risk_pct": risk_pct, "exit_type": "fixed_target",
                    "split": split_name, "touch_tolerance_pct": chosen_combo.touch_tolerance_pct,
                    "break_buffer_pct": chosen_combo.break_buffer_pct, "target_r_multiple": chosen_combo.target_r_multiple,
                    "num_raw_signals": len(sig_list), "min_risk_pct_needed_for_1_contract": min_risk_pct_needed, **metrics,
                })
            print(f"  risk={risk_pct:.0%} fixed_target  train: {train_metrics['num_closed']} trades PF={train_metrics['profit_factor']}  |  test: {test_metrics['num_closed']} trades PF={test_metrics['profit_factor']} pnl=${test_metrics['total_pnl_dollars']}")

            # Trailing-stop variant, same chosen entry params
            train_sig_tr, test_sig_tr = _split_by_date(signals_from_state(state, chosen_combo), split_date)
            train_trades_tr = simulate_trailing_trades(state, train_sig_tr, chosen_combo, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
            test_trades_tr = simulate_trailing_trades(state, test_sig_tr, chosen_combo, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
            train_metrics_tr = compute_metrics(train_trades_tr, account_size=ACCOUNT_SIZE)
            test_metrics_tr = compute_metrics(test_trades_tr, account_size=ACCOUNT_SIZE)

            for split_name, sig_list, metrics in [("train", train_sig_tr, train_metrics_tr), ("test", test_sig_tr, test_metrics_tr)]:
                rows.append({
                    "symbol": symbol, "strategy": "trendline_cascade", "risk_pct": risk_pct, "exit_type": "trailing_stop",
                    "split": split_name, "touch_tolerance_pct": chosen_combo.touch_tolerance_pct,
                    "break_buffer_pct": chosen_combo.break_buffer_pct, "target_r_multiple": None,
                    "num_raw_signals": len(sig_list), "min_risk_pct_needed_for_1_contract": min_risk_pct_needed, **metrics,
                })
            print(f"  risk={risk_pct:.0%} trailing_stop  train: {train_metrics_tr['num_closed']} trades PF={train_metrics_tr['profit_factor']}  |  test: {test_metrics_tr['num_closed']} trades PF={test_metrics_tr['profit_factor']} pnl=${test_metrics_tr['total_pnl_dollars']}")

    return rows


def main() -> None:
    tech_rows = run_technical_strategies()
    trend_rows = run_trendline_cascade()
    all_rows = tech_rows + trend_rows

    result = pd.DataFrame(all_rows)
    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "full_100k_backtest.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")

    # Survivors: profitable on both train and test, with a real test sample.
    scored = result[result["split"].isin(["train", "test"]) & result["num_closed"].notna()]
    pivot_rows = []
    group_cols = [c for c in ["symbol", "strategy", "risk_pct", "exit_type"] if c in scored.columns]
    for keys, group in scored.groupby(group_cols):
        train = group[group["split"] == "train"]
        test = group[group["split"] == "test"]
        if train.empty or test.empty:
            continue
        train, test = train.iloc[0], test.iloc[0]
        row = dict(zip(group_cols, keys if isinstance(keys, tuple) else (keys,)))
        row.update({
            "train_trades": train["num_closed"], "train_pf": train["profit_factor"],
            "test_trades": test["num_closed"], "test_pf": test["profit_factor"], "test_pnl": test["total_pnl_dollars"],
        })
        pivot_rows.append(row)
    pivot = pd.DataFrame(pivot_rows)

    print(f"\n=== Combos profitable on BOTH train and test, with >= {MIN_TEST_TRADES_TO_TRUST} test trades ===")
    if pivot.empty:
        print("  none")
    else:
        survivors = pivot[
            (pivot["test_trades"] >= MIN_TEST_TRADES_TO_TRUST)
            & (pivot["train_pf"].fillna(0) > 1.0)
            & (pivot["test_pf"].fillna(0) > 1.0)
        ].sort_values("test_pf", ascending=False)
        if survivors.empty:
            print("  none")
        else:
            with pd.option_context("display.max_columns", None, "display.width", 200):
                print(survivors.to_string(index=False))


if __name__ == "__main__":
    main()
