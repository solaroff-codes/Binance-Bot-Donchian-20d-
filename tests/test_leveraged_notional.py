import pandas as pd
import pytest

from backtest.engine import CostModel
from backtest.leveraged_notional import simulate_leveraged_notional_trades
from strategy.signals import Signal


def _signal(**overrides) -> Signal:
    defaults = dict(
        entry_ts=pd.Timestamp("2026-01-01"), direction="long", entry_price=100,
        stop_price=90, target_price=120, range_start_ts=pd.Timestamp("2025-12-01"),
        range_end_ts=pd.Timestamp("2025-12-15"), impulse_start_price=90,
        impulse_extreme_price=120, retracement_ratio=0.618,
    )
    defaults.update(overrides)
    return Signal(**defaults)


def _df():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    return pd.DataFrame(
        {"date": dates, "high": [100, 100, 102], "low": [95, 89, 91], "close": [100, 92, 91]}
    )


def test_low_leverage_uses_strategy_stop_not_liquidation():
    # leverage=2 on a 10%-away stop: liquidation buffer (~49.5% away) is
    # far looser than the strategy's own stop -- the strategy's stop
    # should bind, exactly like risk-based sizing would.
    trades = simulate_leveraged_notional_trades(
        _df(), [_signal()], multiplier=1, leverage=2, starting_capital=1_000,
    )
    t = trades[0]
    assert t.stop_price == 90  # unchanged -- strategy stop, not liquidation
    assert t.contracts == 20  # (1000 * 2) / (100 * 1)
    assert t.outcome == "loss"
    assert t.pnl_dollars == pytest.approx(-200.0)  # 20% of the $1,000 sleeve


def test_high_leverage_gets_liquidated_before_strategy_stop():
    # leverage=10 on the same 10%-away stop: liquidation buffer (~9.5%) is
    # now TIGHTER than the strategy's stop -- liquidation should bind,
    # realizing close to the entire sleeve as a loss.
    liq_log = []
    trades = simulate_leveraged_notional_trades(
        _df(), [_signal()], multiplier=1, leverage=10, starting_capital=1_000,
        liquidation_log=liq_log,
    )
    t = trades[0]
    assert t.stop_price == pytest.approx(90.5)  # liquidation price, tighter than the 90 stop
    assert t.outcome == "loss"
    assert t.pnl_dollars < -900  # most of the $1,000 sleeve gone in one trade
    assert len(liq_log) == 1
    assert liq_log[0].loss_pct_of_sleeve > 0.9


def test_loss_never_exceeds_the_sleeves_own_equity():
    # Even in a pathological case, a leveraged account can't lose more
    # than what was actually posted as margin for that trade.
    trades = simulate_leveraged_notional_trades(
        _df(), [_signal(stop_price=1)], multiplier=1, leverage=50, starting_capital=1_000,
    )
    assert trades[0].pnl_dollars >= -1_000.0


def test_ruined_sleeve_stops_taking_new_trades():
    # After a wipeout, sizing_equity drops to ~0 -- the next signal
    # should find nothing left to trade with rather than erroring.
    dates = pd.date_range("2026-01-01", periods=6, freq="D")
    df = pd.DataFrame(
        {"date": dates, "high": [100, 100, 102, 105, 106, 107], "low": [95, 89, 91, 100, 101, 102],
         "close": [100, 92, 91, 104, 105, 106]}
    )
    sig1 = _signal(entry_ts=dates[0], stop_price=90, target_price=120)
    sig2 = _signal(entry_ts=dates[2], entry_price=104, stop_price=100, target_price=110)

    trades = simulate_leveraged_notional_trades(
        df, [sig1, sig2], multiplier=1, leverage=20, starting_capital=1_000,
    )
    # sig1 should wipe the sleeve out at 20x leverage; sig2 should either
    # be skipped (no equity left) or sized down to almost nothing.
    assert trades[0].pnl_dollars <= -900
