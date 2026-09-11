import pandas as pd
import pytest

from backtest.engine import (
    CostModel,
    PositionSizer,
    Trade,
    compute_drawdown_curve,
    compute_metrics,
    simulate_trades,
    simulate_trades_compounding,
)
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


def test_simulate_trades_applies_slippage_and_commission():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    df = pd.DataFrame(
        {"date": dates, "high": [100, 111, 111], "low": [95, 99, 99], "close": [100, 110, 110]}
    )
    signal = _signal(entry_ts=dates[0], stop_price=95, target_price=110)

    baseline = simulate_trades(df, [signal], multiplier=100)
    assert baseline[0].pnl_dollars == 1000  # (110-100)*100, no costs

    costed = simulate_trades(
        df, [signal], multiplier=100,
        cost_model=CostModel(slippage_ticks=1, tick_size=0.1, commission_per_contract=5.0),
    )
    t = costed[0]
    # fill_entry = 100 + 0.1 = 100.1 (pay more to buy); fill_exit = 110 - 0.1 = 109.9 (receive less)
    assert t.pnl_dollars == pytest.approx((109.9 - 100.1) * 100 - 5.0)
    assert t.contracts == 1


def test_simulate_trades_applies_percentage_commission():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    df = pd.DataFrame(
        {"date": dates, "high": [100, 111, 111], "low": [95, 99, 99], "close": [100, 110, 110]}
    )
    signal = _signal(entry_ts=dates[0], stop_price=95, target_price=110)

    costed = simulate_trades(
        df, [signal], multiplier=1, cost_model=CostModel(commission_pct=0.01),
    )
    t = costed[0]
    # no slippage: fill_entry=100, fill_exit=110. commission = 1% * (100+110) = 2.10
    assert t.pnl_dollars == pytest.approx((110 - 100) * 1 - 2.10)


def test_simulate_trades_position_sizer_computes_contracts():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    df = pd.DataFrame(
        {"date": dates, "high": [100, 106, 106], "low": [98, 100, 100], "close": [100, 105, 105]}
    )
    # risk_points=1, multiplier=100 -> $100 risk per contract. $10k account,
    # 2% risk -> $200 budget -> 2 contracts.
    signal = _signal(entry_ts=dates[0], stop_price=99, target_price=105)

    trades = simulate_trades(
        df, [signal], multiplier=100,
        position_sizer=PositionSizer(account_size=10_000, risk_pct_per_trade=0.02),
    )
    assert len(trades) == 1
    assert trades[0].contracts == 2
    assert trades[0].pnl_dollars == pytest.approx((105 - 100) * 100 * 2)


def test_simulate_trades_skips_signal_that_cannot_size_even_one_contract():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    df = pd.DataFrame(
        {"date": dates, "high": [100, 106, 106], "low": [89, 99, 99], "close": [100, 105, 105]}
    )
    # risk_points=10, multiplier=1000 -> $10,000 risk for just 1 contract,
    # but the budget is only $100 (1% of $10k) -> can't take this trade at all.
    signal = _signal(entry_ts=dates[0], stop_price=90, target_price=105)

    skip_log = []
    trades = simulate_trades(
        df, [signal], multiplier=1000,
        position_sizer=PositionSizer(account_size=10_000, risk_pct_per_trade=0.01),
        skip_log=skip_log,
    )
    assert trades == []
    assert len(skip_log) == 1
    assert skip_log[0]["reason"] == "undersized"


def test_compute_metrics_account_pct_drawdown():
    t0 = pd.Timestamp("2026-01-01")
    t1 = pd.Timestamp("2026-01-02")
    trades = [Trade(t0, t1, "long", 100, 95, 110, 95, "loss", 5, -500, -1.0, -500)]
    metrics = compute_metrics(trades, account_size=10_000)
    assert metrics["max_drawdown_pct_of_account"] == pytest.approx(0.05)


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


def test_compute_drawdown_curve_reports_peak_trough_and_recovery():
    t0 = pd.Timestamp("2026-01-01")
    t1 = pd.Timestamp("2026-01-02")
    t2 = pd.Timestamp("2026-01-03")
    # Loss then a bigger win: equity dips from its $0 starting peak down to
    # -500 (the trough), then a new high of +500 recovers it.
    trades = [
        Trade(t0, t1, "long", 100, 95, 110, 95, "loss", 5, -5, -1.0, -500),
        Trade(t0, t2, "long", 100, 95, 110, 110, "win", 5, 10, 2.0, 1000),
    ]
    curve = compute_drawdown_curve(trades)
    assert curve["max_drawdown_dollars"] == 500
    assert curve["peak_ts"] == t0  # the $0 baseline, before any trade closed
    assert curve["trough_ts"] == t1
    assert curve["recovered_ts"] == t2


def test_compute_drawdown_curve_reports_no_recovery_if_still_underwater():
    t0 = pd.Timestamp("2026-01-01")
    t1 = pd.Timestamp("2026-01-02")
    t2 = pd.Timestamp("2026-01-03")
    # Win then a bigger loss: ends below its own peak, with no later trade
    # to recover it within this history.
    trades = [
        Trade(t0, t1, "long", 100, 110, 120, 110, "win", 10, 10, 1.0, 1000),
        Trade(t0, t2, "long", 100, 85, 120, 85, "loss", 15, -15, -1.0, -1500),
    ]
    curve = compute_drawdown_curve(trades)
    assert curve["max_drawdown_dollars"] == 1500
    assert curve["peak_ts"] == t1
    assert curve["trough_ts"] == t2
    assert curve["recovered_ts"] is None


def test_compute_drawdown_curve_handles_no_trades():
    curve = compute_drawdown_curve([])
    assert curve["max_drawdown_dollars"] is None
    assert curve["peak_ts"] is None


def test_simulate_trades_compounding_sizes_next_trade_off_grown_equity():
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    df = pd.DataFrame(
        {
            "date": dates,
            "high": [100, 108, 111, 113],
            "low": [95, 97, 99, 104],
            "close": [100, 103, 110, 109],
        }
    )
    sig1 = _signal(entry_ts=dates[0], entry_price=100, stop_price=95, target_price=110)
    sig2 = _signal(entry_ts=dates[2], entry_price=110, stop_price=105, target_price=120)

    trades = simulate_trades_compounding(
        df, [sig1, sig2], multiplier=1, starting_capital=10_000, risk_pct_per_trade=0.02,
    )
    assert len(trades) == 2

    t1, t2 = trades
    # Trade 1: $10,000 * 2% = $200 budget / $5 risk-per-unit = 40 units.
    assert t1.outcome == "win"
    assert t1.contracts == 40
    assert t1.pnl_dollars == pytest.approx(400.0)

    # Trade 2 sizes off the GROWN equity ($10,400, not the original
    # $10,000): $10,400 * 2% = $208 / $5 = 41 units, one more than a
    # fixed-account_size sizer would have given.
    assert t2.outcome == "loss"
    assert t2.contracts == 41
    assert t2.pnl_dollars == pytest.approx(-205.0)


def test_simulate_trades_compounding_fraction_zero_matches_fixed_sizing():
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    df = pd.DataFrame(
        {
            "date": dates,
            "high": [100, 108, 111, 113],
            "low": [95, 97, 99, 104],
            "close": [100, 103, 110, 109],
        }
    )
    sig1 = _signal(entry_ts=dates[0], entry_price=100, stop_price=95, target_price=110)
    sig2 = _signal(entry_ts=dates[2], entry_price=110, stop_price=105, target_price=120)

    trades = simulate_trades_compounding(
        df, [sig1, sig2], multiplier=1, starting_capital=10_000, risk_pct_per_trade=0.02,
        compounding_fraction=0.0,
    )
    # With compounding_fraction=0.0, every trade sizes off starting_capital
    # only -- both trades should use the same 40-unit size (10,000*2%/$5),
    # unlike full compounding (fraction=1.0) where trade 2 got 41 units.
    assert [t.contracts for t in trades] == [40, 40]


def test_simulate_trades_compounding_skips_when_equity_cannot_size_even_one_unit():
    dates = pd.date_range("2026-01-01", periods=3, freq="D")
    df = pd.DataFrame(
        {"date": dates, "high": [100, 106, 106], "low": [89, 99, 99], "close": [100, 105, 105]}
    )
    # risk_points=10, multiplier=1000 -> $10,000 risk for 1 unit, but the
    # budget is only $100 (1% of $10k starting capital) -> skipped, same
    # convention as sized_and_costed_pnl / simulate_trades.
    signal = _signal(entry_ts=dates[0], stop_price=90, target_price=105)

    skip_log = []
    trades = simulate_trades_compounding(
        df, [signal], multiplier=1000, starting_capital=10_000, risk_pct_per_trade=0.01, skip_log=skip_log,
    )
    assert trades == []
    assert len(skip_log) == 1
    assert skip_log[0]["reason"] == "undersized"
