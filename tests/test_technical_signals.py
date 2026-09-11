import pandas as pd
import pytest

from indicators.technical import atr
from strategy.technical_signals import (
    generate_donchian_breakout_signals,
    generate_ema_cross_signals,
    generate_rsi_reversion_signals,
    generate_trend_pullback_signals,
)


def _rise_then_fall_df(n_each: int = 20) -> pd.DataFrame:
    # A short flat settling period first: EMA's ewm seeding makes fast and
    # slow EMA start exactly equal at bar 0, which would otherwise look
    # like an immediate "crossover" right as the series begins — before
    # ATR (or anything else) has warmed up. Flat bars let fast/slow settle
    # into a stable fast<slow relationship first, so the real crossover
    # this test wants to observe happens well after every indicator's
    # warmup period, not accidentally coincide with it.
    settle = [100] * 10
    closes = settle + [100 + i for i in range(n_each)] + [100 + n_each - 1 - i for i in range(n_each)]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"date": dates, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes}
    )


def test_ema_cross_signals_obey_atr_stop_target_formula_both_directions():
    df = _rise_then_fall_df()
    signals = generate_ema_cross_signals(df, fast=3, slow=8, atr_window=5, stop_atr_mult=2.0, target_atr_mult=3.0)

    assert len(signals) > 0
    assert any(s.direction == "long" for s in signals)
    assert any(s.direction == "short" for s in signals)

    atr_series = atr(df, window=5)
    ts_to_idx = {t: i for i, t in enumerate(df["date"])}
    for s in signals:
        a = atr_series.iloc[ts_to_idx[s.entry_ts]]
        if s.direction == "long":
            assert s.stop_price == pytest.approx(s.entry_price - 2.0 * a)
            assert s.target_price == pytest.approx(s.entry_price + 3.0 * a)
        else:
            assert s.stop_price == pytest.approx(s.entry_price + 2.0 * a)
            assert s.target_price == pytest.approx(s.entry_price - 3.0 * a)


def test_rsi_reversion_fires_on_oversold_bounce():
    # Decline pushes RSI into oversold territory, then a sharp recovery
    # bar should trip the bounce condition.
    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    closes = [100 - i for i in range(15)] + [86, 90, 95, 99, 103]
    df = pd.DataFrame(
        {"date": dates, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes}
    )

    signals = generate_rsi_reversion_signals(df, rsi_window=5, oversold=30, overbought=70, atr_window=5)
    assert any(s.direction == "long" for s in signals)
    for s in signals:
        assert s.direction == "long"  # no overbought condition ever reached in this series
        assert s.stop_price < s.entry_price < s.target_price


def test_donchian_breakout_fires_on_channel_break_not_before():
    dates = pd.date_range("2026-01-01", periods=25, freq="D")
    flat = [100] * 20
    breakout = [105, 110, 115]  # clears the 20-bar high of 100
    closes = flat + breakout + [112, 108]
    df = pd.DataFrame(
        {"date": dates, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes}
    )

    signals = generate_donchian_breakout_signals(df, window=20, atr_window=5)
    assert len(signals) >= 1
    assert signals[0].direction == "long"
    assert signals[0].entry_ts == dates[20]  # the first bar (105) that clears the 20-bar high


def test_trend_pullback_fires_long_when_trend_and_rsi_dip_both_align():
    # Sustained rise (price stays above its own lagging trend EMA
    # throughout) with a brief dip-and-recover superimposed — the RSI
    # pullback-in-an-uptrend pattern this strategy is built to catch.
    dates = pd.date_range("2026-01-01", periods=40, freq="D")
    rising = [100 + i * 10 for i in range(20)]  # steep, long rise -> trend EMA lags well behind
    dip = [rising[-1] - 20, rising[-1] - 45]  # sharp enough to push RSI(4) below 40...
    recover = [rising[-1] - 20, rising[-1] + 5, rising[-1] + 25]  # ...without price dropping below the (lagging) trend EMA
    closes = rising + dip + recover + [c + 5 for c in recover] + [c + 10 for c in recover[:2]]
    df = pd.DataFrame(
        {"date": dates[: len(closes)], "high": [c + 1 for c in closes], "low": [c - 1 for c in closes],
         "close": closes}
    )
    signals = generate_trend_pullback_signals(df, trend_ema=10, rsi_window=4, atr_window=5)
    assert any(s.direction == "long" for s in signals)
    for s in signals:
        if s.direction == "long":
            assert s.stop_price < s.entry_price < s.target_price
        else:
            assert s.target_price < s.entry_price < s.stop_price
