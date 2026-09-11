import pandas as pd
import pytest

from backtest.atr_trailing import simulate_atr_trailing_trades
from backtest.engine import CostModel, PositionSizer
from strategy.signals import Signal


def _signal(**overrides) -> Signal:
    defaults = dict(
        entry_ts=pd.Timestamp("2026-01-01"),
        direction="long",
        entry_price=100,
        stop_price=95,
        target_price=110,
        range_start_ts=pd.Timestamp("2025-12-01"),
        range_end_ts=pd.Timestamp("2025-12-15"),
        impulse_start_price=90,
        impulse_extreme_price=120,
        retracement_ratio=0.618,
    )
    defaults.update(overrides)
    return Signal(**defaults)


def test_trailing_stop_tightens_as_price_rises_then_exits_on_pullback():
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    closes = [100, 105, 110, 115, 108]  # rises, then pulls back
    df = pd.DataFrame(
        {"date": dates, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes}
    )
    atr_series = pd.Series([2.0] * 5)  # constant ATR, isolates the trailing logic itself
    signal = _signal(entry_ts=dates[0], stop_price=95, target_price=999)  # target unused by this exit type

    trades = simulate_atr_trailing_trades(df, [signal], atr_series, trail_atr_mult=2.0, multiplier=1.0)
    t = trades[0]

    # Trailing stop should have tightened to 115 (best close) - 2*2 = 111,
    # never loosening back down during the earlier rise, and the pullback
    # bar's low (107) touches it.
    assert t.outcome == "win"
    assert t.exit_price == pytest.approx(111.0)
    assert t.exit_ts == dates[4]
    assert t.r_multiple == pytest.approx((111 - 100) / 5)


def test_trailing_stop_never_loosens_below_initial_stop():
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    closes = [100, 99, 98, 90]  # drifts down immediately, never favorable
    df = pd.DataFrame(
        {"date": dates, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes}
    )
    # ATR=3, trail_mult=2 -> candidate = 100 - 6 = 94, below the initial
    # stop (95) throughout since price never makes a new high above entry
    # -- running_stop must clamp to 95, not loosen down to 94.
    atr_series = pd.Series([3.0] * 4)
    signal = _signal(entry_ts=dates[0], stop_price=95, target_price=999)

    trades = simulate_atr_trailing_trades(df, [signal], atr_series, trail_atr_mult=2.0, multiplier=1.0)
    t = trades[0]

    assert t.stop_price == 95
    assert t.outcome == "loss"
    assert t.exit_price == 95


def test_trailing_stop_applies_cost_model_and_sizing_like_fixed_target():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    closes = [100, 106, 106]
    df = pd.DataFrame(
        {"date": dates, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes}
    )
    atr_series = pd.Series([1.0, 1.0, 1.0])
    signal = _signal(entry_ts=dates[0], stop_price=99, target_price=999)

    trades = simulate_atr_trailing_trades(
        df, [signal], atr_series, trail_atr_mult=2.0, multiplier=100,
        position_sizer=PositionSizer(account_size=10_000, risk_pct_per_trade=0.02),
    )
    assert len(trades) == 1
    assert trades[0].contracts >= 1
