import numpy as np
import pandas as pd
import pytest

from indicators.technical import atr, bollinger_bands, donchian_channel, ema, macd, rsi, sma, supertrend


def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5])
    result = sma(s, window=3)
    assert result.iloc[:2].isna().all()
    assert result.iloc[2:].tolist() == [2, 3, 4]


def test_ema_matches_recursive_formula():
    # span=3 -> alpha=2/(3+1)=0.5. ema[0]=10, ema[1]=0.5*20+0.5*10=15, ema[2]=0.5*30+0.5*15=22.5
    s = pd.Series([10.0, 20.0, 30.0])
    result = ema(s, window=3)
    assert result.tolist() == pytest.approx([10.0, 15.0, 22.5])


def test_rsi_is_100_for_pure_gains_and_0_for_pure_losses():
    rising = pd.Series(range(1, 11), dtype=float)
    assert rsi(rising, window=3).iloc[-1] == pytest.approx(100.0)

    falling = pd.Series(range(10, 0, -1), dtype=float)
    assert rsi(falling, window=3).iloc[-1] == pytest.approx(0.0)


def test_macd_histogram_is_macd_minus_signal():
    s = pd.Series(np.sin(np.linspace(0, 10, 60)) * 10 + 100)
    macd_line, signal_line, histogram = macd(s, fast=5, slow=10, signal=3)
    pd.testing.assert_series_equal(histogram, macd_line - signal_line, check_names=False)


def test_bollinger_bands_symmetric_around_middle():
    s = pd.Series([100, 102, 98, 101, 99, 103, 97, 100, 105, 95], dtype=float)
    upper, middle, lower = bollinger_bands(s, window=5, num_std=2.0)
    valid = middle.notna()
    upper_gap = (upper - middle)[valid].to_numpy()
    lower_gap = (middle - lower)[valid].to_numpy()
    assert upper_gap == pytest.approx(lower_gap)


def test_atr_with_window_1_equals_true_range():
    df = pd.DataFrame(
        {
            "high": [105, 110],
            "low": [95, 100],
            "close": [100, 105],
        }
    )
    result = atr(df, window=1)
    # bar1: TR = max(high-low=10, |high-prev_close|=|110-100|=10, |low-prev_close|=|100-100|=0) = 10
    assert result.iloc[1] == pytest.approx(10.0)


def test_donchian_channel_excludes_current_bar():
    df = pd.DataFrame({"high": [10, 12, 11, 15, 13], "low": [8, 9, 7, 10, 11]})
    upper, lower = donchian_channel(df, window=3)
    # upper at idx4 = max(high[1:4]) = max(12,11,15) = 15 (idx4's own high of 13 excluded)
    assert upper.iloc[4] == 15
    assert lower.iloc[4] == min(9, 7, 10)


def test_supertrend_direction_flips_on_sharp_reversal():
    # Steady rise for 20 bars, then a sharp multi-bar drop well below any
    # recent support — direction should read +1 during the rise and
    # flip to -1 after the drop.
    rising = list(range(100, 120))
    dropping = [119, 110, 95, 80, 70, 65]
    closes = rising + dropping
    df = pd.DataFrame(
        {
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
        }
    )
    line, direction = supertrend(df, atr_window=5, multiplier=2.0)
    assert direction.iloc[19] == 1  # still in the steady rise
    assert direction.iloc[-1] == -1  # after the sharp drop
    assert set(direction.unique()) <= {1, -1}
