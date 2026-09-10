import pandas as pd
import pytest

from backtest.engine import Trade, compute_metrics, simulate_trades
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


def test_simulate_trades_detects_win_on_target_hit():
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    df = pd.DataFrame(
        {
            "date": dates,
            "high": [100, 105, 111, 112],
            "low": [95, 98, 99, 100],
            "close": [100, 103, 110, 111],
        }
    )
    signal = _signal(entry_ts=dates[0])

    trades = simulate_trades(df, [signal], multiplier=100)
    t = trades[0]

    assert t.outcome == "win"
    assert t.exit_ts == dates[2]
    assert t.exit_price == 110
    assert t.pnl_points == 10
    assert t.risk_points == 5
    assert t.r_multiple == 2.0
    assert t.pnl_dollars == 1000


def test_simulate_trades_detects_loss_on_stop_hit_short():
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    df = pd.DataFrame(
        {
            "date": dates,
            "high": [200, 205, 212, 215],
            "low": [190, 195, 200, 205],
            "close": [200, 202, 210, 212],
        }
    )
    signal = _signal(
        entry_ts=dates[0], direction="short", entry_price=200, stop_price=210, target_price=180
    )

    trades = simulate_trades(df, [signal], multiplier=100)
    t = trades[0]

    assert t.outcome == "loss"
    assert t.exit_ts == dates[2]
    assert t.exit_price == 210
    assert t.pnl_points == -10
    assert t.pnl_dollars == -1000


def test_simulate_trades_marks_open_when_neither_hit():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    df = pd.DataFrame(
        {
            "date": dates,
            "high": [100, 103, 104],
            "low": [95, 97, 98],
            "close": [100, 101, 102],
        }
    )
    signal = _signal(entry_ts=dates[0], stop_price=90, target_price=130)

    trades = simulate_trades(df, [signal])
    t = trades[0]

    assert t.outcome == "open"
    assert t.exit_ts is None
    assert t.pnl_points is None
    assert t.pnl_dollars is None


def test_simulate_trades_stop_takes_priority_when_both_hit_same_bar():
    dates = pd.date_range("2026-01-01", periods=2, freq="D")
    df = pd.DataFrame(
        {
            "date": dates,
            "high": [100, 200],  # wide bar: touches both stop (90) and target (110)
            "low": [95, 50],
            "close": [100, 100],
        }
    )
    signal = _signal(entry_ts=dates[0], stop_price=90, target_price=110)

    trades = simulate_trades(df, [signal])
    assert trades[0].outcome == "loss"


def test_simulate_trades_skips_signal_overlapping_an_open_position():
    dates = pd.date_range("2026-01-01", periods=6, freq="D")
    df = pd.DataFrame(
        {
            "date": dates,
            "high": [100, 105, 111, 116, 121, 125],
            "low": [95, 98, 99, 100, 101, 102],
            "close": [100, 103, 110, 115, 120, 124],
        }
    )
    # sig1: opens day0, resolves (win) on day2 -> blocks entries through day2.
    sig1 = _signal(entry_ts=dates[0], stop_price=95, target_price=110)
    # sig2: would open day1, while sig1 is still open (closes day2) -> skipped.
    sig2 = _signal(entry_ts=dates[1], stop_price=90, target_price=115)
    # sig3: opens day3, after sig1 has already closed -> kept.
    sig3 = _signal(entry_ts=dates[3], stop_price=95, target_price=120)

    trades = simulate_trades(df, [sig1, sig2, sig3], multiplier=1.0)
    assert len(trades) == 2
    assert {t.entry_ts for t in trades} == {dates[0], dates[3]}


def test_simulate_trades_can_disable_single_position_enforcement():
    dates = pd.date_range("2026-01-01", periods=6, freq="D")
    df = pd.DataFrame(
        {
            "date": dates,
            "high": [100, 105, 111, 116, 121, 125],
            "low": [95, 98, 99, 100, 101, 102],
            "close": [100, 103, 110, 115, 120, 124],
        }
    )
    sig1 = _signal(entry_ts=dates[0], stop_price=95, target_price=110)
    sig2 = _signal(entry_ts=dates[1], stop_price=90, target_price=115)

    trades = simulate_trades(df, [sig1, sig2], multiplier=1.0, enforce_single_position=False)
    assert len(trades) == 2


def test_compute_metrics_aggregates_win_loss_open():
    t0 = pd.Timestamp("2026-01-01")
    t2 = pd.Timestamp("2026-01-03")
    t3 = pd.Timestamp("2026-01-04")

    trades = [
        Trade(t0, t2, "long", 100, 95, 110, 110, "win", 5, 10, 2.0, 1000),
        Trade(t0, t3, "short", 200, 210, 180, 210, "loss", 10, -10, -1.0, -1000),
        Trade(t0, None, "long", 50, 45, 60, None, "open", 5, None, None, None),
    ]

    metrics = compute_metrics(trades)

    assert metrics["num_signals"] == 3
    assert metrics["num_closed"] == 2
    assert metrics["num_open"] == 1
    assert metrics["num_wins"] == 1
    assert metrics["num_losses"] == 1
    assert metrics["win_rate"] == 0.5
    assert metrics["total_pnl_dollars"] == 0
    assert metrics["profit_factor"] == 1.0
    assert metrics["avg_r_multiple"] == 0.5
    assert metrics["sharpe_r"] == pytest.approx(0.4714045207910317)
    assert metrics["max_drawdown_dollars"] == 1000


def test_compute_metrics_max_drawdown_measured_from_starting_equity():
    # A loss followed by a bigger win: cumulative P&L never dips below its
    # own post-first-trade value, but it does dip $500 below the $0
    # starting equity — that dip is the real max drawdown, not $0.
    t0 = pd.Timestamp("2026-01-01")
    t1 = pd.Timestamp("2026-01-02")
    t2 = pd.Timestamp("2026-01-03")

    trades = [
        Trade(t0, t1, "long", 100, 95, 110, 95, "loss", 5, -5, -1.0, -500),
        Trade(t0, t2, "long", 100, 95, 110, 110, "win", 5, 10, 2.0, 1000),
    ]

    metrics = compute_metrics(trades)
    assert metrics["max_drawdown_dollars"] == 500


def test_compute_metrics_handles_no_trades():
    metrics = compute_metrics([])
    assert metrics["num_signals"] == 0
    assert metrics["win_rate"] is None
    assert metrics["profit_factor"] is None
    assert metrics["max_drawdown_dollars"] is None
