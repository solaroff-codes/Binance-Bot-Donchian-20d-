import pandas as pd
import pytest

from backtest.trailing import simulate_trailing_trades
from indicators.trendlines import TrendLine
from strategy.trendline_config import TrendlineStrategyConfig
from strategy.trendline_signals import CascadeState, TrendlineSignal


def _make_state(trigger: pd.DataFrame, line: TrendLine, from_idx: int) -> CascadeState:
    # Same line object "known" from from_idx onward — enough to exercise
    # the trailing-stop ratchet without needing a full walk-forward fit.
    support_lines = [None] * from_idx + [line] * (len(trigger) - from_idx)
    return CascadeState(
        trigger=trigger,
        support_lines=support_lines,
        resistance_lines=[None] * len(trigger),
        bias_series_by_tf={},
        bias_dates_by_tf={},
        high_col="high",
        low_col="low",
        close_col="close",
        timestamp_col="date",
    )


def test_trailing_stop_ratchets_up_and_exits_when_hit():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    # line.price_at(i) = 90 + i (start_idx=0, start_price=90, slope=1.0)
    line = TrendLine(
        kind="support", start_idx=0, end_idx=0, start_price=90, end_price=90,
        start_ts=dates[0], end_ts=dates[0], slope=1.0,
    )
    # lows chosen so the trailing stop (line_price - 1% buffer) is never
    # touched until idx9, where the ratcheted stop (98.01) catches a dip to 97.
    lows = [0, 0, 0, 96, 96, 96, 96, 97, 98, 97]
    highs = [x + 5 for x in lows]
    closes = [x + 2 for x in lows]
    df = pd.DataFrame({"date": dates, "high": highs, "low": lows, "close": closes})

    state = _make_state(df, line, from_idx=3)
    config = TrendlineStrategyConfig(symbol="TEST", stop_buffer_pct=0.01)
    signal = TrendlineSignal(
        entry_ts=dates[2], direction="long", entry_price=96, stop_price=95,
        target_price=999, event_type="bounce", trendline_start_ts=dates[0],
        trendline_slope=0.0, bias_by_timeframe=(),
    )

    trades = simulate_trailing_trades(state, [signal], config, multiplier=1.0)

    assert len(trades) == 1
    t = trades[0]
    assert t.exit_ts == dates[9]
    assert t.stop_price == pytest.approx(98.01)  # final ratcheted stop level
    assert t.outcome == "win"  # exit (98.01) ended up above entry (96)
    assert t.pnl_points == pytest.approx(98.01 - 96)


def test_trailing_stop_never_loosens():
    # Even if the trendline itself dips (a weaker/lower candidate later),
    # the stop must not retreat below a level it already ratcheted to.
    dates = pd.date_range("2026-01-01", periods=6, freq="D")
    rising_then_falling = [
        TrendLine(kind="support", start_idx=0, end_idx=0, start_price=90, end_price=90,
                  start_ts=dates[0], end_ts=dates[0], slope=2.0),  # steep: price_at(i)=90+2i
    ]
    # Swap to a much shallower (lower) line partway through — its price_at
    # values fall below what the steep line already implied.
    shallow_line = TrendLine(
        kind="support", start_idx=0, end_idx=0, start_price=90, end_price=90,
        start_ts=dates[0], end_ts=dates[0], slope=0.1,
    )
    support_lines = [None, None, rising_then_falling[0], rising_then_falling[0], shallow_line, shallow_line]

    df = pd.DataFrame({
        "date": dates,
        "high": [100, 100, 100, 100, 100, 100],
        "low": [90, 90, 97, 97, 93, 93],
        "close": [95, 95, 97, 97, 97, 97],
    })
    state = CascadeState(
        trigger=df, support_lines=support_lines, resistance_lines=[None] * 6,
        bias_series_by_tf={}, bias_dates_by_tf={},
        high_col="high", low_col="low", close_col="close", timestamp_col="date",
    )
    config = TrendlineStrategyConfig(symbol="TEST", stop_buffer_pct=0.0)
    signal = TrendlineSignal(
        entry_ts=dates[1], direction="long", entry_price=95, stop_price=90,
        target_price=999, event_type="bounce", trendline_start_ts=dates[0],
        trendline_slope=0.0, bias_by_timeframe=(),
    )

    trades = simulate_trailing_trades(state, [signal], config, multiplier=1.0)
    # idx2: steep line price_at(2)=94 -> stop ratchets to 94 (low=97, no hit).
    # idx3: steep line price_at(3)=96 -> stop ratchets to 96 (low=97, no hit).
    # idx4: shallow line price_at(4)=90.4 -> lower than 96, must be ignored;
    #       stop stays 96, and low=93 hits it (proving it did NOT loosen —
    #       had it reset to 90.4, low=93 would not have triggered an exit).
    assert len(trades) == 1
    assert trades[0].exit_ts == dates[4]
    assert trades[0].stop_price == 96
