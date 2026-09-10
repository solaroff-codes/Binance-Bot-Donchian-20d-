"""
The honest version of the trendline strategy backtest: realistic costs,
dollar-risk position sizing on a $10,000 account, a genuine train/test
split (parameters chosen on train only, evaluated on held-out test data —
not "best cell of a sweep run over everything"), and a head-to-head of
fixed-target vs trailing-stop exits.

For each instrument, uses the cascade STRUCTURE already found to be best
for it (scripts/sweep_trendline_params.py): CL -> 4h_trigger, GC ->
reactive, ES/NQ/SI/BZ -> default. Re-optimizing structure under the new
cost model is out of scope here — this reuses that earlier finding and
focuses the new work on validating it honestly.

SI and BZ (silver, Brent crude) added alongside the original four. Neither
has a true 1/10th "micro" contract available on this account (checked
directly): SI uses "QI" (mini silver, half-size — the smallest available,
not a real micro) and BZ has no smaller contract at all, so its "micro"
multiplier is just the full contract — see config/instruments.yaml.

Costs: 1 tick slippage per fill (both entry and exit), $2.50/contract
round-turn commission — reasonable retail futures assumptions, not
measured from this account. Position sizing: $10,000 account,
risk_pct_per_trade swept at 1% and 2% (a trade that can't even size 1
contract within the risk budget is skipped, not floored up to 1 — see
backtest/engine.py's CostModel/PositionSizer docstring).

Uses MICRO futures multipliers (MGC/MCL/MES/MNQ — config/instruments.yaml's
micro_multiplier), not the full-size GC/CL/ES/NQ ones: at full size, every
single signal across all four instruments needs $400-$19,500+ of risk for
just 1 contract, which blows through a 1-2% budget on a $10k account
universally (verified directly before adding this — see backtest/README.md).
That's what a real trader on an account this size would actually use.
Price data is the full contract's cached series (micros settle against the
same underlying, so this is a standard, reasonable approximation — no
separate micro tick data is fetched or modeled).

Train/test: first 70% of each instrument's available bars by date is
train, the rest is test. touch_tolerance_pct x break_buffer_pct x
target_r_multiple (36 combos, same grid as sweep_trendline_params.py) is
swept using ONLY train-period signals, under the realistic cost/sizing
model, requiring >=5 closed train trades to be eligible. The best-on-train
combo (by profit factor) is then evaluated on the untouched test period —
that test-period result is the number to trust, not the train one.

The same chosen touch/break parameters (target_r_multiple doesn't apply)
are also run through backtest/trailing.py's trailing-stop exit, on the
same train/test split, for a direct fixed-target vs trailing comparison.

Run from the project root with the venv active:
    python scripts/run_realistic_backtest.py
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, PositionSizer, compute_metrics, simulate_trades
from backtest.trailing import simulate_trailing_trades
from data.cache import drop_untraded_bars, load_latest
from data.contracts import load_instruments
from strategy.trendline_config import TrendlineStrategyConfig, load_config
from strategy.trendline_signals import compute_cascade_state, signals_from_state

ACCOUNT_SIZE = 10_000.0
RISK_LEVELS = [0.01, 0.02]
COMMISSION_PER_CONTRACT = 2.50
SLIPPAGE_TICKS = 1.0
TRAIN_FRACTION = 0.70
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


def _load(symbol: str, timeframe: str) -> pd.DataFrame | None:
    df = load_latest(symbol, timeframe)
    return drop_untraded_bars(df) if df is not None else None


def _split_by_date(items: list, split_date) -> tuple[list, list]:
    before = [s for s in items if pd.Timestamp(s.entry_ts).date() < split_date]
    after = [s for s in items if pd.Timestamp(s.entry_ts).date() >= split_date]
    return before, after


def run_symbol(symbol: str) -> list[dict]:
    bias_timeframes, trigger_timeframe = BEST_STRUCTURES[symbol]
    base_config = replace(
        load_config(symbol), bias_timeframes=bias_timeframes, trigger_timeframe=trigger_timeframe
    )

    bias_dfs = {}
    for tf in bias_timeframes:
        df = _load(symbol, tf)
        if df is None:
            print(f"{symbol}: missing {tf}, skipping")
            return []
        bias_dfs[tf] = df
    trigger_df = _load(symbol, trigger_timeframe)
    if trigger_df is None:
        print(f"{symbol}: missing {trigger_timeframe}, skipping")
        return []

    state = compute_cascade_state(bias_dfs, trigger_df, base_config)
    split_idx = int(len(trigger_df) * TRAIN_FRACTION)
    split_date = pd.Timestamp(trigger_df["date"].iloc[split_idx]).date()

    instrument_spec = load_instruments()[symbol]
    multiplier = float(instrument_spec["micro_multiplier"])
    tick_size = float(instrument_spec["tick_size"])
    cost_model = CostModel(
        slippage_ticks=SLIPPAGE_TICKS, tick_size=tick_size, commission_per_contract=COMMISSION_PER_CONTRACT
    )

    # Upfront sizing feasibility check at the base config's own stop
    # distances (informative regardless of which touch/break combo the
    # sweep below picks — stop distance is dominated by the trendline's
    # own recent range, not these two knobs).
    base_signals = signals_from_state(state, base_config)
    base_risks = [abs(s.entry_price - s.stop_price) * multiplier for s in base_signals]
    min_risk_dollars = min(base_risks) if base_risks else None
    min_risk_pct_needed = (min_risk_dollars / ACCOUNT_SIZE) if min_risk_dollars else None
    if min_risk_pct_needed is not None:
        print(
            f"  sizing check: cheapest signal needs ${min_risk_dollars:,.0f} risk for 1 contract "
            f"= {min_risk_pct_needed:.1%} of a ${ACCOUNT_SIZE:,.0f} account "
            f"(requested risk budget: {RISK_LEVELS[0]:.0%}-{RISK_LEVELS[-1]:.0%})"
        )

    rows = []

    for risk_pct in RISK_LEVELS:
        sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)

        # --- Fixed-target: sweep on train, pick best, evaluate on test ---
        best = None  # (profit_factor, combo_config, train_signals, test_signals)
        for touch in TOUCH_TOLERANCE_GRID:
            for buffer in BREAK_BUFFER_GRID:
                for target_r in TARGET_R_GRID:
                    combo = replace(
                        base_config, touch_tolerance_pct=touch, break_buffer_pct=buffer,
                        target_r_multiple=target_r,
                    )
                    all_signals = signals_from_state(state, combo)
                    train_sig, test_sig = _split_by_date(all_signals, split_date)

                    train_trades = simulate_trades(
                        trigger_df, train_sig, multiplier=multiplier,
                        cost_model=cost_model, position_sizer=sizer,
                    )
                    if len(train_trades) < MIN_TRAIN_TRADES:
                        continue
                    train_metrics = compute_metrics(train_trades, account_size=ACCOUNT_SIZE)
                    pf = train_metrics["profit_factor"]
                    if pf is None:
                        continue
                    if best is None or pf > best[0]:
                        best = (pf, combo, train_sig, test_sig, train_trades, train_metrics)

        if best is None:
            reason = "no combo reached MIN_TRAIN_TRADES"
            if min_risk_pct_needed is not None and min_risk_pct_needed > risk_pct:
                reason += (
                    f" (likely cause: even the cheapest signal needs {min_risk_pct_needed:.1%} "
                    f"risk for 1 contract, above the {risk_pct:.0%} budget — every trade gets "
                    "skipped as undersized, not a signal-scarcity problem)"
                )
            rows.append({"symbol": symbol, "risk_pct": risk_pct, "exit_type": "fixed_target",
                         "split": "train", "error": reason,
                         "min_risk_pct_needed_for_1_contract": min_risk_pct_needed})
            continue

        _, chosen_combo, train_sig, test_sig, train_trades, train_metrics = best
        test_skip_log: list = []
        test_trades = simulate_trades(
            trigger_df, test_sig, multiplier=multiplier,
            cost_model=cost_model, position_sizer=sizer, skip_log=test_skip_log,
        )
        test_metrics = compute_metrics(test_trades, account_size=ACCOUNT_SIZE)

        for split_name, sig_list, trades, metrics in [
            ("train", train_sig, train_trades, train_metrics),
            ("test", test_sig, test_trades, test_metrics),
        ]:
            rows.append({
                "symbol": symbol, "risk_pct": risk_pct, "exit_type": "fixed_target", "split": split_name,
                "touch_tolerance_pct": chosen_combo.touch_tolerance_pct,
                "break_buffer_pct": chosen_combo.break_buffer_pct,
                "target_r_multiple": chosen_combo.target_r_multiple,
                "num_raw_signals": len(sig_list), **metrics,
            })

        # --- Trailing stop: same entry params (touch/break), same split ---
        trailing_combo = chosen_combo
        all_signals = signals_from_state(state, trailing_combo)
        train_sig_tr, test_sig_tr = _split_by_date(all_signals, split_date)

        train_trades_tr = simulate_trailing_trades(
            state, train_sig_tr, trailing_combo, multiplier=multiplier,
            cost_model=cost_model, position_sizer=sizer,
        )
        train_metrics_tr = compute_metrics(train_trades_tr, account_size=ACCOUNT_SIZE)
        test_trades_tr = simulate_trailing_trades(
            state, test_sig_tr, trailing_combo, multiplier=multiplier,
            cost_model=cost_model, position_sizer=sizer,
        )
        test_metrics_tr = compute_metrics(test_trades_tr, account_size=ACCOUNT_SIZE)

        for split_name, sig_list, metrics in [
            ("train", train_sig_tr, train_metrics_tr),
            ("test", test_sig_tr, test_metrics_tr),
        ]:
            rows.append({
                "symbol": symbol, "risk_pct": risk_pct, "exit_type": "trailing_stop", "split": split_name,
                "touch_tolerance_pct": trailing_combo.touch_tolerance_pct,
                "break_buffer_pct": trailing_combo.break_buffer_pct,
                "target_r_multiple": None,
                "num_raw_signals": len(sig_list), **metrics,
            })

    return rows


def main() -> None:
    all_rows = []
    for symbol in BEST_STRUCTURES:
        print(f"\n=== {symbol} ===")
        rows = run_symbol(symbol)
        all_rows.extend(rows)
        for r in rows:
            if "error" in r:
                print(f"  {r}")
            else:
                print(
                    f"  risk={r['risk_pct']:.0%} {r['exit_type']:13} {r['split']:5} "
                    f"trades={r.get('num_closed')} win_rate={r.get('win_rate')} "
                    f"PF={r.get('profit_factor')} pnl=${r.get('total_pnl_dollars')} "
                    f"DD=${r.get('max_drawdown_dollars')} ({r.get('max_drawdown_pct_of_account')})"
                )

    df = pd.DataFrame(all_rows)
    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "realistic_backtest.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
