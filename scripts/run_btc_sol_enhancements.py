"""
Two enhancements to BTC + Donchian (this project's best-evidenced
strategy, now joined by SOL — see backtest/README.md's stress-test
sections), requested as a direct follow-up rather than a parameter
search:

1. A two-asset BTC+SOL portfolio using the strategy exactly as already
   validated (no parameter changes) — both assets' trades merged into one
   shared-account equity curve, to see whether diversification (their
   worst individual drawdowns barely overlap in time) smooths the combined
   curve the way it should if the two edges are genuinely semi-independent.

2. A trailing-stop exit (backtest/atr_trailing.py, new this session — a
   generic ATR "chandelier exit," trailing at the same 2x-ATR distance the
   strategy's own initial stop already uses, not a newly chosen number),
   head-to-head against the fixed 3x-ATR target, on both BTC and SOL.

Also addresses a stated constraint directly: the strategy's own default
stop=2x ATR / target=3x ATR is a 1:1.5 reward:risk ratio, BELOW a stated
1:2 minimum. This is flagged, not silently worked around -- a 1:2 variant
(target=4x ATR) is tested alongside the original as a single, principled,
one-time comparison (not a grid search), on both symbols and both risk
levels, to see whether the edge survives a stricter target. The trailing
stop's own REALIZED average win size (in R) is also reported, since a
trailing exit doesn't have a fixed target to compare against a ratio
directly -- what matters there is what it actually captured.

Same realistic-cost methodology as every other crypto test in this
project: $10k account, 1% and 2% risk per trade, Binance's ~0.1% taker
fee, 1 tick slippage. No parameters are swept -- three deliberate,
named variants (original 1:1.5, stricter 1:2, and trailing) are compared,
not searched.

Run from the project root with the venv active:
    python scripts/run_btc_sol_enhancements.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.atr_trailing import simulate_atr_trailing_trades
from backtest.engine import CostModel, PositionSizer, compute_drawdown_curve, compute_metrics, simulate_trades
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from indicators.technical import atr
from strategy.technical_signals import generate_donchian_breakout_signals

SYMBOLS = ["BTC", "SOL"]
ACCOUNT_SIZE = 10_000.0
RISK_LEVELS = [0.01, 0.02]
SLIPPAGE_TICKS = 1.0
ATR_WINDOW = 14
STOP_ATR_MULT = 2.0
ORIGINAL_TARGET_ATR_MULT = 3.0  # this project's validated default -- reward:risk = 1.5
STRICT_TARGET_ATR_MULT = 4.0  # reward:risk = 2.0, the stated minimum


def _load(symbol: str, specs: dict):
    df = load_continuous(symbol, "1 day")
    df = drop_untraded_bars(df)
    spec = specs[symbol]
    multiplier = float(spec["micro_multiplier"])
    cost_model = CostModel(slippage_ticks=SLIPPAGE_TICKS, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))
    return df, multiplier, cost_model


def _print_result(label: str, trades: list, account_size: float) -> None:
    m = compute_metrics(trades, account_size=account_size)
    curve = compute_drawdown_curve(trades)
    dd = curve["max_drawdown_dollars"]
    dd_str = f"${dd:,.2f} ({dd/account_size:.2%})" if dd is not None else "n/a"
    wins = [t for t in trades if t.outcome == "win"]
    avg_win_r = sum(t.r_multiple for t in wins) / len(wins) if wins else None
    avg_win_r_str = f"{avg_win_r:.2f}R" if avg_win_r is not None else "n/a"
    print(f"  {label:38} trades={m['num_closed']:>3}  win_rate={m['win_rate']}  PF={m['profit_factor']}  "
          f"pnl=${m['total_pnl_dollars']}  max_DD={dd_str}  avg_win={avg_win_r_str}")


def part1_portfolio(specs: dict) -> None:
    print("\n" + "=" * 78)
    print("PART 1: BTC + SOL as one two-asset portfolio (original, validated params)")
    print("=" * 78)

    for risk_pct in RISK_LEVELS:
        print(f"\n--- risk={risk_pct:.0%} per trade, per asset (shared ${ACCOUNT_SIZE:,.0f} account) ---")
        combined_trades = []
        for symbol in SYMBOLS:
            df, multiplier, cost_model = _load(symbol, specs)
            signals = generate_donchian_breakout_signals(df, atr_window=ATR_WINDOW, stop_atr_mult=STOP_ATR_MULT, target_atr_mult=ORIGINAL_TARGET_ATR_MULT)
            sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)
            trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
            _print_result(f"{symbol} alone", trades, ACCOUNT_SIZE)
            combined_trades.extend(trades)
        _print_result("BTC+SOL combined portfolio", combined_trades, ACCOUNT_SIZE)


def part2_trailing_vs_fixed(specs: dict) -> None:
    print("\n" + "=" * 78)
    print("PART 2: ATR trailing stop vs fixed target, head-to-head")
    print("=" * 78)

    for symbol in SYMBOLS:
        df, multiplier, cost_model = _load(symbol, specs)
        atr_series = atr(df, window=ATR_WINDOW)
        signals = generate_donchian_breakout_signals(df, atr_window=ATR_WINDOW, stop_atr_mult=STOP_ATR_MULT, target_atr_mult=ORIGINAL_TARGET_ATR_MULT)
        print(f"\n{symbol}:")
        for risk_pct in RISK_LEVELS:
            sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)
            fixed_trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
            trailing_trades = simulate_atr_trailing_trades(
                df, signals, atr_series, trail_atr_mult=STOP_ATR_MULT, multiplier=multiplier,
                cost_model=cost_model, position_sizer=sizer,
            )
            print(f"  risk={risk_pct:.0%}:")
            _print_result("fixed target (3x ATR)", fixed_trades, ACCOUNT_SIZE)
            _print_result("ATR trailing stop (2x ATR)", trailing_trades, ACCOUNT_SIZE)


def part3_reward_risk_floor(specs: dict) -> None:
    print("\n" + "=" * 78)
    print(f"PART 3: reward:risk floor check -- original is 1:{ORIGINAL_TARGET_ATR_MULT/STOP_ATR_MULT:g}, "
          f"stated minimum is 1:2 -- testing a 1:2 variant (target={STRICT_TARGET_ATR_MULT}x ATR)")
    print("=" * 78)

    for symbol in SYMBOLS:
        df, multiplier, cost_model = _load(symbol, specs)
        print(f"\n{symbol}:")
        for risk_pct in RISK_LEVELS:
            sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=risk_pct)
            original_signals = generate_donchian_breakout_signals(df, atr_window=ATR_WINDOW, stop_atr_mult=STOP_ATR_MULT, target_atr_mult=ORIGINAL_TARGET_ATR_MULT)
            strict_signals = generate_donchian_breakout_signals(df, atr_window=ATR_WINDOW, stop_atr_mult=STOP_ATR_MULT, target_atr_mult=STRICT_TARGET_ATR_MULT)
            original_trades = simulate_trades(df, original_signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
            strict_trades = simulate_trades(df, strict_signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
            print(f"  risk={risk_pct:.0%}:")
            _print_result(f"original (1:{ORIGINAL_TARGET_ATR_MULT/STOP_ATR_MULT:g})", original_trades, ACCOUNT_SIZE)
            _print_result(f"strict 1:2 (target={STRICT_TARGET_ATR_MULT}x ATR)", strict_trades, ACCOUNT_SIZE)


def main() -> None:
    specs = load_crypto_instruments()
    part1_portfolio(specs)
    part2_trailing_vs_fixed(specs)
    part3_reward_risk_floor(specs)


if __name__ == "__main__":
    main()
