"""
Standard technical indicators: moving averages, RSI, MACD, Bollinger
Bands, ATR, Donchian channels, and Supertrend.

Unlike indicators/trendlines.py's walk-forward trendline fitting (an
O(bars * pivots^2) refit process that turned out to be computationally
infeasible on crypto's high-pivot-density hourly data — see
config/trendline_strategies/BTC.yaml), every function here is a single
vectorized pandas pass over the whole series (Supertrend is the one
exception: its bands are path-dependent bar to bar, so it's a single O(n)
Python loop, not O(n^2) — still fast on tens of thousands of bars).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI (Wilder smoothing = ewm with alpha=1/window)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def bollinger_bands(
    series: pd.Series, window: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, middle, lower)."""
    middle = sma(series, window)
    std = series.rolling(window).std()
    return middle + num_std * std, middle, middle - num_std * std


def atr(
    df: pd.DataFrame,
    window: int = 14,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
) -> pd.Series:
    """Wilder's Average True Range."""
    high, low, close = df[high_col], df[low_col], df[close_col]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def donchian_channel(
    df: pd.DataFrame, window: int = 20, high_col: str = "high", low_col: str = "low"
) -> tuple[pd.Series, pd.Series]:
    """
    Returns (upper, lower): the highest high / lowest low over the PRIOR
    `window` bars, excluding the current bar (shifted by 1) — so a
    breakout is "this bar closed beyond where the channel already was,"
    not "beyond a channel that includes this bar's own extreme," which
    would make every bar trivially touch its own edge.
    """
    upper = df[high_col].rolling(window).max().shift(1)
    lower = df[low_col].rolling(window).min().shift(1)
    return upper, lower


def supertrend(
    df: pd.DataFrame,
    atr_window: int = 10,
    multiplier: float = 3.0,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
) -> tuple[pd.Series, pd.Series]:
    """
    Returns (line, direction): direction is +1 while the line sits below
    price acting as support (uptrend), -1 while it sits above price
    acting as resistance (downtrend). Standard basic-bands + trend-flip
    construction — not any particular platform's exact proprietary
    variant, but the commonly-published algorithm.
    """
    hl2 = (df[high_col] + df[low_col]) / 2
    atr_series = atr(df, window=atr_window, high_col=high_col, low_col=low_col, close_col=close_col)
    basic_upper = (hl2 + multiplier * atr_series).to_numpy()
    basic_lower = (hl2 - multiplier * atr_series).to_numpy()
    close = df[close_col].to_numpy()
    n = len(df)

    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)
    line = np.full(n, np.nan)

    for i in range(n):
        if i == 0 or np.isnan(basic_upper[i - 1]) or np.isnan(final_upper[i - 1]):
            final_upper[i] = basic_upper[i]
            final_lower[i] = basic_lower[i]
            direction[i] = 1
            line[i] = final_lower[i]
            continue

        final_upper[i] = (
            min(basic_upper[i], final_upper[i - 1]) if close[i - 1] <= final_upper[i - 1] else basic_upper[i]
        )
        final_lower[i] = (
            max(basic_lower[i], final_lower[i - 1]) if close[i - 1] >= final_lower[i - 1] else basic_lower[i]
        )

        if close[i] > final_upper[i - 1]:
            direction[i] = 1
        elif close[i] < final_lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

        line[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return pd.Series(line, index=df.index), pd.Series(direction, index=df.index)
