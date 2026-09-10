"""
The same honest treatment run_realistic_backtest.py gives the futures
instruments (realistic costs, dollar-risk position sizing on a $10,000
account, a genuine train/test split, fixed-target vs trailing-stop
head-to-head), applied to BTC/ETH/SOL.

Two real differences from the futures version, both driven by the same
underlying fact — crypto's much higher volatility produces far more swing
pivots than futures data at the same bar count, and
indicators.trendlines.walk_forward_trendlines's refit cost blows up badly
as a result (measured directly: BTC's 6y of 1h data, ~52,600 bars, was
extrapolated at ~3.5 HOURS for a single support-line walk-forward pass;
even daily bars, ~2,200 of them, took 33s for one symbol/kind — 5-10x
slower than a comparable futures bar count):

1. No structure sweep. config/trendline_strategies/{BTC,ETH,SOL}.yaml
   hardcode a single structure (bias: month+week, trigger: daily) instead
   of searching 4 candidate structures like sweep_trendline_params.py does
   for futures — 3 of those 4 involve 4h or 1h data, which is not
   computationally tractable here. Sub-daily crypto timeframes are a real
   gap, not covered by this backtest.
2. Costs and sizing use config/crypto_instruments.yaml
   (commission_pct — a percentage of notional, matching Binance's actual
   fee structure — instead of futures' flat commission_per_contract; and
   micro_multiplier as a small fractional "lot size" for position sizing,
   since crypto trades in fractional units with no fixed contract size at
   all, unlike futures).

Train/test split, cost assumptions (1 tick slippage, Binance's standard
~0.1% taker fee), and $10k/1-2% position sizing otherwise match
run_realistic_backtest.py exactly — see that script's docstring.

Run from the project root with the venv active:
    python scripts/run_crypto_realistic_backtest.py
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, PositionSizer, compute_metrics, simulate_trades
from backtest.trailing import simulate_trailing_trades
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.trendline_config import load_config
from strategy.trendline_signals import compute_cascade_state, signals_from_state

ACCOUNT_SIZE = 10_000.0
RISK_LEVELS = [0.01, 0.02]
SLIPPAGE_TICKS = 1.0
TRAIN_FRACTION = 0.70
MIN_TRAIN_TRADES = 5

SYMBOLS = ["BTC", "ETH", "SOL"]

TOUCH_TOLERANCE_GRID = [0.001, 0.003, 0.005]
BREAK_BUFFER_GRID = [0.001, 0.003, 0.005]
TARGET_R_GRID = [1.0, 1.5, 2.0, 3.0]


def _load(symbol: str, timeframe: str) -> pd.DataFrame | None:
    df = load_continuous(symbol, timeframe)
    return drop_untraded_bars(df) if df is not None else None


def _split_by_date(items: list, split_date) -> tuple[list, list]:
    before = [s for s in items if pd.Timestamp(s.entry_ts).date() < split_date]
    after = [s for s in items if pd.Timestamp(s.entry_ts).date() >= split_date]
    return before, after


def run_symbol(symbol: str) -> list[dict]:
    base_config = load_config(symbol)

    bias_dfs = {}
    for tf in base_config.bias_timeframes:
        df = _load(symbol, tf)
        if df is None:
            print(f"{symbol}: missing {tf}, skipping")
            return []
        bias_dfs[tf] = df
    trigger_df = _load(symbol, base_config.trigger_timeframe)
    if trigger_df is None:
        print(f"{symbol}: missing {base_config.trigger_timeframe}, skipping")
        return []

    print(f"  fitting walk-forward trendlines ({len(trigger_df)} trigger bars — this is the slow part)...")
    state = compute_cascade_state(bias_dfs, trigger_df, base_config)
    split_idx = int(len(trigger_df) * TRAIN_FRACTION)
    split_date = pd.Timestamp(trigger_df["date"].iloc[split_idx]).date()

    instrument_spec = load_crypto_instruments()[symbol]
    multiplier = float(instrument_spec["micro_multiplier"])
    tick_size = float(instrument_spec["tick_size"])
    commission_pct = float(instrument_spec["commission_pct"])
    cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=tick_size, commission_pct=commission_pct)

    base_signals = signals_from_state(state, base_config)
    base_risks = [abs(s.entry_price - s.stop_price) * multiplier for s in base_signals]
    min_risk_dollars = min(base_risks) if base_risks else None
    min_risk_pct_needed = (min_risk_dollars / ACCOUNT_SIZE) if min_risk_dollars else None
    if min_risk_pct_needed is not None:
        print(
            f"  sizing check: cheapest signal needs ${min_risk_dollars:,.2f} risk for the smallest "
            f"lot size ({multiplier} units) = {min_risk_pct_needed:.2%} of a ${ACCOUNT_SIZE:,.0f} account"
        )

    rows = []

    for risk_pct in RISK_LEVELS:
        sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)

        best = None
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
            rows.append({"symbol": symbol, "risk_pct": risk_pct, "exit_type": "fixed_target",
                         "split": "train", "error": "no combo reached MIN_TRAIN_TRADES",
                         "min_risk_pct_needed": min_risk_pct_needed})
            continue

        _, chosen_combo, train_sig, test_sig, train_trades, train_metrics = best
        test_trades = simulate_trades(
            trigger_df, test_sig, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer,
        )
        test_metrics = compute_metrics(test_trades, account_size=ACCOUNT_SIZE)

        for split_name, sig_list, metrics in [("train", train_sig, train_metrics), ("test", test_sig, test_metrics)]:
            rows.append({
                "symbol": symbol, "risk_pct": risk_pct, "exit_type": "fixed_target", "split": split_name,
                "touch_tolerance_pct": chosen_combo.touch_tolerance_pct,
                "break_buffer_pct": chosen_combo.break_buffer_pct,
                "target_r_multiple": chosen_combo.target_r_multiple,
                "num_raw_signals": len(sig_list), **metrics,
            })

        all_signals = signals_from_state(state, chosen_combo)
        train_sig_tr, test_sig_tr = _split_by_date(all_signals, split_date)
        train_trades_tr = simulate_trailing_trades(
            state, train_sig_tr, chosen_combo, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer,
        )
        test_trades_tr = simulate_trailing_trades(
            state, test_sig_tr, chosen_combo, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer,
        )
        for split_name, sig_list, trades in [("train", train_sig_tr, train_trades_tr), ("test", test_sig_tr, test_trades_tr)]:
            rows.append({
                "symbol": symbol, "risk_pct": risk_pct, "exit_type": "trailing_stop", "split": split_name,
                "touch_tolerance_pct": chosen_combo.touch_tolerance_pct,
                "break_buffer_pct": chosen_combo.break_buffer_pct,
                "target_r_multiple": None,
                "num_raw_signals": len(sig_list), **compute_metrics(trades, account_size=ACCOUNT_SIZE),
            })

    return rows


def main() -> None:
    all_rows = []
    for symbol in SYMBOLS:
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
                    f"DD%acct={r.get('max_drawdown_pct_of_account')}"
                )

    df = pd.DataFrame(all_rows)
    out_path = Path(__file__).resolve().parent.parent / "backtest" / "output" / "crypto_realistic_backtest.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
