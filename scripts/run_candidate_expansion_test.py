"""
Extends the coin universe beyond BTC/SOL/BNB, using the exact same bar
BNB and XRP were held to (backtest/README.md's "A third sleeve" section)
-- not a promising calendar-year number, the full bear-market/second-
holdout/continuous-drawdown treatment. Five candidates: LTC, ADA, DOGE,
LINK, TRX -- all established, long-Binance-history large/mid-caps,
chosen for that reason before looking at any result, matching how
BNB/XRP were chosen. No per-coin tuning anywhere -- same fixed Donchian
breakout rule, same costs, same $10k/1% reference sizing used throughout
this project's stress-test sections (results are pure percentages, so
they translate directly to the real $1,000 account).

Pass bar (identical to BNB's): the 4 core stress-test windows -- 2022
bear market, both halves of a second holdout, the original-style 70/30
test window -- all profitable (no sign flips), plus the true continuous
drawdown (not chopped by calendar year) actually recovering within the
available data. Calendar years are printed for context/consistency color
only, NOT part of the pass bar -- BNB itself has a losing calendar year
(2026, PF 0.57) and still passed on this same bar.

Run from the project root with the venv active:
    python scripts/run_candidate_expansion_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from backtest.engine import CostModel, PositionSizer, compute_drawdown_curve, compute_metrics, simulate_trades
from data.binance_connector import load_crypto_instruments
from data.cache import drop_untraded_bars, load_continuous
from strategy.technical_signals import generate_donchian_breakout_signals

CANDIDATES = ["LTC", "ADA", "DOGE", "LINK", "TRX"]
ACCOUNT_SIZE = 10_000.0
BEAR_MARKET_START = pd.Timestamp("2021-11-10").date()
BEAR_MARKET_END = pd.Timestamp("2022-11-21").date()
ORIGINAL_TEST_SPLIT = pd.Timestamp("2024-11-22").date()
YEARS = (pd.Timestamp("2026-09-10") - pd.Timestamp("2020-09-10")).days / 365.25


def _window_report(label: str, df, signals, multiplier, cost_model) -> dict:
    if not signals:
        print(f"  {label}: no signals")
        return {"label": label, "trades": 0, "pf": None}
    sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=0.01)
    trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
    m = compute_metrics(trades, account_size=ACCOUNT_SIZE)
    print(f"  {label:30} trades={m['num_closed']:>3}  win_rate={m['win_rate']}  PF={m['profit_factor']}")
    return {"label": label, "trades": m["num_closed"], "pf": m["profit_factor"]}


def main() -> None:
    specs = load_crypto_instruments()
    verdicts = {}

    for symbol in CANDIDATES:
        df = load_continuous(symbol, "1 day")
        df = drop_untraded_bars(df)
        spec = specs[symbol]
        multiplier = float(spec["micro_multiplier"])
        cost_model = CostModel(slippage_ticks=1.0, tick_size=float(spec["tick_size"]), commission_pct=float(spec["commission_pct"]))
        signals = generate_donchian_breakout_signals(df)

        print(f"\n{'='*70}\n{symbol}: {len(df)} daily bars ({df['date'].min()} -> {df['date'].max()}), {len(signals)} signals\n{'='*70}")

        # Calendar years are context/consistency color, NOT the pass bar --
        # BNB itself has a losing calendar year (2026, PF 0.57) and still
        # passed, because the pass bar that was actually applied is the 4
        # core stress-test windows below. An earlier version of this script
        # wrongly required every calendar year to pass too, which no coin
        # in this project -- including the already-validated ones -- would
        # have cleared; that bug is fixed here rather than left in.
        print("Calendar years (context only, not the pass bar):")
        years = sorted(pd.to_datetime(df["date"]).dt.year.unique())
        for year in years:
            start = pd.Timestamp(f"{year}-01-01").date()
            end = pd.Timestamp(f"{year}-12-31").date()
            year_signals = [s for s in signals if start <= pd.Timestamp(s.entry_ts).date() <= end]
            _window_report(str(year), df, year_signals, multiplier, cost_model)

        print("Stress test windows (this is the actual pass bar):")
        core_windows = []
        bear = [s for s in signals if BEAR_MARKET_START <= pd.Timestamp(s.entry_ts).date() <= BEAR_MARKET_END]
        core_windows.append(_window_report("2022 bear market", df, bear, multiplier, cost_model))

        pre = [s for s in signals if pd.Timestamp(s.entry_ts).date() < ORIGINAL_TEST_SPLIT]
        pre_dates = sorted({pd.Timestamp(s.entry_ts).date() for s in pre})
        if pre_dates:
            mid = pre_dates[len(pre_dates) // 2]
            earlier = [s for s in pre if pd.Timestamp(s.entry_ts).date() < mid]
            later = [s for s in pre if pd.Timestamp(s.entry_ts).date() >= mid]
            core_windows.append(_window_report(f"2nd holdout, earlier half", df, earlier, multiplier, cost_model))
            core_windows.append(_window_report(f"2nd holdout, later half", df, later, multiplier, cost_model))

        test_window = [s for s in signals if pd.Timestamp(s.entry_ts).date() >= ORIGINAL_TEST_SPLIT]
        core_windows.append(_window_report("original test window", df, test_window, multiplier, cost_model))

        # Full-period continuous drawdown (the fix from earlier in this project).
        sizer = PositionSizer(account_size=ACCOUNT_SIZE, risk_pct_per_trade=0.01)
        full_trades = simulate_trades(df, signals, multiplier=multiplier, cost_model=cost_model, position_sizer=sizer)
        full_m = compute_metrics(full_trades, account_size=ACCOUNT_SIZE)
        curve = compute_drawdown_curve(full_trades)
        dd = curve["max_drawdown_dollars"] or 0.0
        recovered = curve["recovered_ts"] if curve["recovered_ts"] else "NEVER"
        final = ACCOUNT_SIZE + (full_m["total_pnl_dollars"] or 0.0)
        cagr = (final / ACCOUNT_SIZE) ** (1 / YEARS) - 1
        print(f"Full period: {full_m['num_closed']} trades, win_rate={full_m['win_rate']}, PF={full_m['profit_factor']}, "
              f"total_return={(final/ACCOUNT_SIZE-1):+.1%}, CAGR={cagr:.2%}, "
              f"true max_DD=${dd:,.2f} ({dd/ACCOUNT_SIZE:.2%}), peak={curve['peak_ts']}, trough={curve['trough_ts']}, recovered={recovered}")

        # Pass/fail verdict: the 4 core stress-test windows (bear market,
        # both holdout halves, original test window) must ALL be
        # profitable, and the true continuous drawdown must have
        # recovered -- the same bar BNB was actually held to. Calendar
        # years are context, not part of this gate (see comment above).
        core_with_trades = [r for r in core_windows if r["trades"] and r["trades"] > 0]
        all_profitable = len(core_with_trades) == len(core_windows) and all((r["pf"] or 0) > 1.0 for r in core_with_trades)
        recovered_ok = recovered != "NEVER"
        passed = all_profitable and recovered_ok and full_m["profit_factor"] and full_m["profit_factor"] > 1.0
        verdicts[symbol] = {
            "passed": passed, "full_pf": full_m["profit_factor"], "full_trades": full_m["num_closed"],
            "cagr": cagr, "max_dd_pct": dd / ACCOUNT_SIZE, "recovered": recovered_ok,
            "all_core_windows_profitable": all_profitable,
        }
        print(f"VERDICT: {'PASS' if passed else 'FAIL'} -- all 4 core windows profitable: {all_profitable}, drawdown recovered: {recovered_ok}")

    print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
    for symbol, v in verdicts.items():
        print(f"  {symbol:5} {'PASS' if v['passed'] else 'FAIL':5}  full PF={v['full_pf']}  CAGR={v['cagr']:.2%}  trades={v['full_trades']}  "
              f"max_DD={v['max_dd_pct']:.1%}  all_core_windows_profitable={v['all_core_windows_profitable']}  recovered={v['recovered']}")


if __name__ == "__main__":
    main()
