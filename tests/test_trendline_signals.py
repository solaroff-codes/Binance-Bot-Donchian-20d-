import numpy as np
import pandas as pd
import pytest

from strategy.trendline_config import TrendlineStrategyConfig
from strategy.trendline_signals import _bias_lookup, generate_trendline_signals


def test_bias_lookup_uses_most_recent_bar_at_or_before_target():
    dates = np.array(
        [pd.Timestamp(d).date() for d in ["2026-01-01", "2026-01-05", "2026-01-10"]]
    )
    biases = ["neutral", "bullish", "bearish"]

    assert _bias_lookup(dates, biases, pd.Timestamp("2025-12-31").date()) == "neutral"
    assert _bias_lookup(dates, biases, pd.Timestamp("2026-01-01").date()) == "neutral"
    assert _bias_lookup(dates, biases, pd.Timestamp("2026-01-07").date()) == "bullish"
    assert _bias_lookup(dates, biases, pd.Timestamp("2026-01-15").date()) == "bearish"


def _three_pivot_uptrend(dates) -> pd.DataFrame:
    # Swing lows at idx4 (100), idx14 (106), idx24 (118) — same shape used
    # to hand-verify indicators/trendlines.py's steepest-line selection.
    values = (
        [150, 150]
        + [140, 130, 100, 130, 140]
        + [150] * 5
        + [140, 130, 106, 130, 140]
        + [150] * 5
        + [140, 130, 118, 130, 140]
        + [150, 150]
    )
    return pd.DataFrame(
        {"date": dates, "open": values, "high": values, "low": values, "close": values}
    )


def test_generate_trendline_signals_produces_only_long_bounces_matching_bullish_bias():
    bias_dates = pd.date_range("2026-01-01", periods=29, freq="D")
    bias_df = _three_pivot_uptrend(bias_dates)

    # Trigger timeframe: 2 clean pivots, then a controlled drift down toward
    # the resulting line producing one touch-and-confirm bounce.
    trigger_values = (
        [150, 150] + [140, 130, 100, 130, 140] + [150] * 5 + [140, 130, 106, 130, 140] + [150] * 5
        + [140, 130, 118]
    )
    close = list(trigger_values) + [125, 128, 124, 123.5, 127, 129, 131]
    low = list(trigger_values) + [124, 127, 123, 122.6, 126, 130, 130.8]
    high = list(trigger_values) + [126, 129, 125, 124, 128, 129.5, 131.5]
    trigger_dates = pd.date_range("2026-01-20", periods=len(close), freq="h")
    trigger_df = pd.DataFrame(
        {"date": trigger_dates, "open": close, "high": high, "low": low, "close": close}
    )

    config = TrendlineStrategyConfig(
        symbol="TEST", bias_timeframes=("1 day",), trigger_timeframe="1 hour"
    )
    signals = generate_trendline_signals({"1 day": bias_df}, trigger_df, config)

    assert len(signals) > 0
    for s in signals:
        # Bias only turns bullish once daily price clears back above its
        # support line (idx15, 2026-01-16) — no signal should predate that.
        assert s.entry_ts.date() >= pd.Timestamp("2026-01-16").date()
        assert s.direction == "long"
        assert s.event_type == "bounce"
        assert s.stop_price < s.entry_price < s.target_price
        # target_r_multiple=2.0 by default: reward must be exactly 2x risk.
        risk = s.entry_price - s.stop_price
        reward = s.target_price - s.entry_price
        assert reward == pytest.approx(2.0 * risk)
        assert all(b == "bullish" for _, b in s.bias_by_timeframe)


def test_generate_trendline_signals_empty_when_bias_conflicts():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    flat = [100] * 10
    bullish_df = pd.DataFrame(
        {"date": dates, "open": flat, "high": flat, "low": flat, "close": flat}
    )
    trigger_df = bullish_df.copy()

    config = TrendlineStrategyConfig(
        symbol="TEST", bias_timeframes=("1 day",), trigger_timeframe="1 day"
    )
    # Flat data never forms a swing point, so bias is neutral everywhere —
    # no opinion means no trade, regardless of what the trigger does.
    signals = generate_trendline_signals({"1 day": bullish_df}, trigger_df, config)
    assert signals == []
