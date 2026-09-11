"""
Eight popular technical strategies, each producing TechnicalSignal objects
(entry_ts, direction, entry_price, stop_price, target_price) that plug
directly into backtest.engine.simulate_trades via the SignalLike protocol
— no new simulation code needed, same engine as every other strategy in
this project.

All eight use ATR-based risk management (stop = entry -/+ N*ATR, target =
entry +/- M*ATR) rather than a fixed-percent or instrument-specific stop:
this is the standard, volatility-adjusted way to size a stop in technical
trading (Wilder's own reason for inventing ATR), and it means the same
strategy code applies sensibly across BTC/ETH/SOL's very different price
scales and volatility regimes without per-instrument tuning. Default
multiples (stop ~1.5-2x ATR, target ~2.5-3x ATR, i.e. roughly 1.5:1-1.7:1
reward:risk) reflect standard risk-management practice — cut losses
relatively tight, let winners run somewhat further — not a fitted or
optimized choice.

RSI uses a 10-period window by default rather than the traditional 14:
crypto's higher volatility means the traditional TA-textbook RSI(14) is
often too slow, and shorter windows (9-11) are commonly used for crypto
specifically (see backtest/README.md's strategy-showdown section).

Entries fill at the confirming bar's own close, consistent with the fill
assumption already documented in backtest/engine.py's module docstring —
not repeated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from indicators.technical import atr, bollinger_bands, donchian_channel, ema, macd, rsi, supertrend


@dataclass(frozen=True)
class TechnicalSignal:
    entry_ts: object
    direction: Literal["long", "short"]
    entry_price: float
    stop_price: float
    target_price: float
    strategy: str


def _stop_target(entry_price: float, atr_value: float, direction: str, stop_mult: float, target_mult: float):
    if direction == "long":
        return entry_price - stop_mult * atr_value, entry_price + target_mult * atr_value
    return entry_price + stop_mult * atr_value, entry_price - target_mult * atr_value


def generate_ema_cross_signals(
    df: pd.DataFrame, fast: int = 20, slow: int = 50, atr_window: int = 14,
    stop_atr_mult: float = 2.0, target_atr_mult: float = 3.0,
    close_col: str = "close", timestamp_col: str = "date",
) -> list[TechnicalSignal]:
    """Trend-following: fast EMA crosses slow EMA."""
    fast_ema = ema(df[close_col], fast)
    slow_ema = ema(df[close_col], slow)
    atr_series = atr(df, window=atr_window)

    signals = []
    for i in range(1, len(df)):
        if pd.isna(fast_ema.iloc[i - 1]) or pd.isna(slow_ema.iloc[i - 1]) or pd.isna(atr_series.iloc[i]):
            continue
        bull_cross = fast_ema.iloc[i - 1] <= slow_ema.iloc[i - 1] and fast_ema.iloc[i] > slow_ema.iloc[i]
        bear_cross = fast_ema.iloc[i - 1] >= slow_ema.iloc[i - 1] and fast_ema.iloc[i] < slow_ema.iloc[i]
        if not (bull_cross or bear_cross):
            continue
        direction = "long" if bull_cross else "short"
        entry = float(df[close_col].iloc[i])
        stop, target = _stop_target(entry, float(atr_series.iloc[i]), direction, stop_atr_mult, target_atr_mult)
        signals.append(TechnicalSignal(df[timestamp_col].iloc[i], direction, entry, stop, target, "ema_cross"))
    return signals


def generate_rsi_reversion_signals(
    df: pd.DataFrame, rsi_window: int = 10, oversold: float = 30, overbought: float = 70,
    atr_window: int = 14, stop_atr_mult: float = 1.5, target_atr_mult: float = 2.5,
    close_col: str = "close", timestamp_col: str = "date",
) -> list[TechnicalSignal]:
    """Mean reversion: RSI dips below oversold (or above overbought) then recrosses back."""
    rsi_series = rsi(df[close_col], rsi_window)
    atr_series = atr(df, window=atr_window)

    signals = []
    for i in range(1, len(df)):
        prev, cur = rsi_series.iloc[i - 1], rsi_series.iloc[i]
        if pd.isna(prev) or pd.isna(cur) or pd.isna(atr_series.iloc[i]):
            continue
        bounce = prev <= oversold and cur > oversold
        reversal = prev >= overbought and cur < overbought
        if not (bounce or reversal):
            continue
        direction = "long" if bounce else "short"
        entry = float(df[close_col].iloc[i])
        stop, target = _stop_target(entry, float(atr_series.iloc[i]), direction, stop_atr_mult, target_atr_mult)
        signals.append(TechnicalSignal(df[timestamp_col].iloc[i], direction, entry, stop, target, "rsi_reversion"))
    return signals


def generate_macd_cross_signals(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, atr_window: int = 14,
    stop_atr_mult: float = 2.0, target_atr_mult: float = 3.0,
    close_col: str = "close", timestamp_col: str = "date",
) -> list[TechnicalSignal]:
    """Momentum: MACD line crosses its signal line."""
    macd_line, signal_line, _ = macd(df[close_col], fast, slow, signal)
    atr_series = atr(df, window=atr_window)

    signals = []
    for i in range(1, len(df)):
        if pd.isna(macd_line.iloc[i - 1]) or pd.isna(signal_line.iloc[i - 1]) or pd.isna(atr_series.iloc[i]):
            continue
        bull_cross = macd_line.iloc[i - 1] <= signal_line.iloc[i - 1] and macd_line.iloc[i] > signal_line.iloc[i]
        bear_cross = macd_line.iloc[i - 1] >= signal_line.iloc[i - 1] and macd_line.iloc[i] < signal_line.iloc[i]
        if not (bull_cross or bear_cross):
            continue
        direction = "long" if bull_cross else "short"
        entry = float(df[close_col].iloc[i])
        stop, target = _stop_target(entry, float(atr_series.iloc[i]), direction, stop_atr_mult, target_atr_mult)
        signals.append(TechnicalSignal(df[timestamp_col].iloc[i], direction, entry, stop, target, "macd_cross"))
    return signals


def generate_bollinger_reversion_signals(
    df: pd.DataFrame, window: int = 20, num_std: float = 2.0, atr_window: int = 14,
    stop_atr_mult: float = 1.5, target_atr_mult: float = 2.5,
    close_col: str = "close", timestamp_col: str = "date",
) -> list[TechnicalSignal]:
    """Mean reversion: close re-enters the bands after closing outside them."""
    upper, _, lower = bollinger_bands(df[close_col], window, num_std)
    atr_series = atr(df, window=atr_window)
    close = df[close_col]

    signals = []
    for i in range(1, len(df)):
        if pd.isna(lower.iloc[i - 1]) or pd.isna(upper.iloc[i - 1]) or pd.isna(atr_series.iloc[i]):
            continue
        bounce = close.iloc[i - 1] < lower.iloc[i - 1] and close.iloc[i] >= lower.iloc[i]
        reversal = close.iloc[i - 1] > upper.iloc[i - 1] and close.iloc[i] <= upper.iloc[i]
        if not (bounce or reversal):
            continue
        direction = "long" if bounce else "short"
        entry = float(close.iloc[i])
        stop, target = _stop_target(entry, float(atr_series.iloc[i]), direction, stop_atr_mult, target_atr_mult)
        signals.append(TechnicalSignal(df[timestamp_col].iloc[i], direction, entry, stop, target, "bollinger_reversion"))
    return signals


def generate_donchian_breakout_signals(
    df: pd.DataFrame, window: int = 20, atr_window: int = 14,
    stop_atr_mult: float = 2.0, target_atr_mult: float = 3.0,
    high_col: str = "high", low_col: str = "low", close_col: str = "close", timestamp_col: str = "date",
) -> list[TechnicalSignal]:
    """Trend-following breakout (Turtle-style): close breaks the N-bar channel."""
    upper, lower = donchian_channel(df, window, high_col, low_col)
    atr_series = atr(df, window=atr_window)
    close = df[close_col]

    signals = []
    for i in range(len(df)):
        if pd.isna(upper.iloc[i]) or pd.isna(lower.iloc[i]) or pd.isna(atr_series.iloc[i]):
            continue
        breakout_up = close.iloc[i] > upper.iloc[i]
        breakout_down = close.iloc[i] < lower.iloc[i]
        if not (breakout_up or breakout_down):
            continue
        direction = "long" if breakout_up else "short"
        entry = float(close.iloc[i])
        stop, target = _stop_target(entry, float(atr_series.iloc[i]), direction, stop_atr_mult, target_atr_mult)
        signals.append(TechnicalSignal(df[timestamp_col].iloc[i], direction, entry, stop, target, "donchian_breakout"))
    return signals


def generate_supertrend_signals(
    df: pd.DataFrame, atr_window: int = 10, multiplier: float = 3.0,
    target_atr_mult: float = 3.0,
    close_col: str = "close", timestamp_col: str = "date",
) -> list[TechnicalSignal]:
    """Trend-following: Supertrend direction flips. Stop placed at the
    Supertrend line itself at entry (its standard, natural stop level),
    target still a fixed ATR multiple for apples-to-apples comparison
    with the other seven strategies here."""
    line, direction = supertrend(df, atr_window=atr_window, multiplier=multiplier)
    atr_series = atr(df, window=atr_window)
    close = df[close_col]

    signals = []
    for i in range(1, len(df)):
        if pd.isna(line.iloc[i]) or pd.isna(atr_series.iloc[i]) or direction.iloc[i] == direction.iloc[i - 1]:
            continue
        entry_direction = "long" if direction.iloc[i] == 1 else "short"
        entry = float(close.iloc[i])
        stop = float(line.iloc[i])
        target = (
            entry + target_atr_mult * float(atr_series.iloc[i])
            if entry_direction == "long"
            else entry - target_atr_mult * float(atr_series.iloc[i])
        )
        signals.append(TechnicalSignal(df[timestamp_col].iloc[i], entry_direction, entry, stop, target, "supertrend"))
    return signals


def generate_trend_pullback_signals(
    df: pd.DataFrame, trend_ema: int = 200, rsi_window: int = 10,
    pullback_low: float = 40, pullback_high: float = 60, atr_window: int = 14,
    stop_atr_mult: float = 1.5, target_atr_mult: float = 3.0,
    close_col: str = "close", timestamp_col: str = "date",
) -> list[TechnicalSignal]:
    """
    Confluence: 'buy the dip in an uptrend' — a very common retail rule.
    Only take long entries while price is above the long EMA (established
    uptrend), triggered by RSI dipping into the pullback zone and
    recovering; mirror for shorts below the long EMA.
    """
    trend = ema(df[close_col], trend_ema)
    rsi_series = rsi(df[close_col], rsi_window)
    atr_series = atr(df, window=atr_window)
    close = df[close_col]

    signals = []
    for i in range(1, len(df)):
        if pd.isna(trend.iloc[i]) or pd.isna(rsi_series.iloc[i - 1]) or pd.isna(atr_series.iloc[i]):
            continue
        uptrend = close.iloc[i] > trend.iloc[i]
        downtrend = close.iloc[i] < trend.iloc[i]
        bounce = rsi_series.iloc[i - 1] <= pullback_low and rsi_series.iloc[i] > pullback_low
        reversal = rsi_series.iloc[i - 1] >= pullback_high and rsi_series.iloc[i] < pullback_high

        direction = None
        if uptrend and bounce:
            direction = "long"
        elif downtrend and reversal:
            direction = "short"
        if direction is None:
            continue

        entry = float(close.iloc[i])
        stop, target = _stop_target(entry, float(atr_series.iloc[i]), direction, stop_atr_mult, target_atr_mult)
        signals.append(TechnicalSignal(df[timestamp_col].iloc[i], direction, entry, stop, target, "trend_pullback"))
    return signals


def generate_macd_rsi_confluence_signals(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, rsi_window: int = 10,
    rsi_long_range: tuple[float, float] = (40, 65), rsi_short_range: tuple[float, float] = (35, 60),
    atr_window: int = 14, stop_atr_mult: float = 2.0, target_atr_mult: float = 3.0,
    close_col: str = "close", timestamp_col: str = "date",
) -> list[TechnicalSignal]:
    """
    Confluence: MACD crossover filtered by RSI not already at an extreme —
    avoids buying a bullish MACD cross that's already overbought, and vice
    versa. A commonly recommended way to reduce MACD's false-signal rate.
    """
    macd_line, signal_line, _ = macd(df[close_col], fast, slow, signal)
    rsi_series = rsi(df[close_col], rsi_window)
    atr_series = atr(df, window=atr_window)
    close = df[close_col]

    signals = []
    for i in range(1, len(df)):
        if (
            pd.isna(macd_line.iloc[i - 1]) or pd.isna(signal_line.iloc[i - 1])
            or pd.isna(rsi_series.iloc[i]) or pd.isna(atr_series.iloc[i])
        ):
            continue
        bull_cross = macd_line.iloc[i - 1] <= signal_line.iloc[i - 1] and macd_line.iloc[i] > signal_line.iloc[i]
        bear_cross = macd_line.iloc[i - 1] >= signal_line.iloc[i - 1] and macd_line.iloc[i] < signal_line.iloc[i]

        direction = None
        if bull_cross and rsi_long_range[0] <= rsi_series.iloc[i] <= rsi_long_range[1]:
            direction = "long"
        elif bear_cross and rsi_short_range[0] <= rsi_series.iloc[i] <= rsi_short_range[1]:
            direction = "short"
        if direction is None:
            continue

        entry = float(close.iloc[i])
        stop, target = _stop_target(entry, float(atr_series.iloc[i]), direction, stop_atr_mult, target_atr_mult)
        signals.append(TechnicalSignal(df[timestamp_col].iloc[i], direction, entry, stop, target, "macd_rsi_confluence"))
    return signals


STRATEGY_REGISTRY = {
    "ema_cross": generate_ema_cross_signals,
    "rsi_reversion": generate_rsi_reversion_signals,
    "macd_cross": generate_macd_cross_signals,
    "bollinger_reversion": generate_bollinger_reversion_signals,
    "donchian_breakout": generate_donchian_breakout_signals,
    "supertrend": generate_supertrend_signals,
    "trend_pullback": generate_trend_pullback_signals,
    "macd_rsi_confluence": generate_macd_rsi_confluence_signals,
}
