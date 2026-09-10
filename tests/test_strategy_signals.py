import pandas as pd
import pytest

from strategy.config import StrategyConfig
from strategy.signals import generate_signals


def _confluence_setup_df() -> pd.DataFrame:
    """
    Synthetic bars covering, in order:
      - a flat consolidation (range: support 99, resistance 101)
      - a breakout and impulsive rally to a swing high at 132
      - a pullback into the 0.5-0.786 retracement zone, bottoming at a
        swing low of 110, which is the confirmed long entry
      - two more bars confirming the swing-low fractal
    Built so exactly one long signal should be produced.
    """
    dates = pd.date_range("2026-01-01", periods=25, freq="D")

    # idx 0-9: flat range
    close = [100] * 10
    high = [101] * 10
    low = [99] * 10

    # idx 10-17: impulsive rally to swing high at idx17 (high=132)
    close += [104, 108, 112, 116, 120, 124, 127, 130]
    high += [106, 110, 114, 118, 122, 126, 129, 132]
    low += [102, 106, 110, 114, 118, 122, 125, 128]

    # idx 18-19: two bars lower, confirming the idx17 swing-high fractal
    close += [127, 124]
    high += [129, 126]
    low += [125, 122]

    # idx 20-22: pullback down to swing low at idx22 (low=110)
    close += [120, 116, 112]
    high += [122, 118, 114]
    low += [118, 114, 110]

    # idx 23-24: two bars higher, confirming the idx22 swing-low fractal
    close += [116, 120]
    high += [118, 122]
    low += [114, 118]

    return pd.DataFrame({"date": dates, "high": high, "low": low, "close": close})


def _config(**overrides) -> StrategyConfig:
    defaults = dict(
        symbol="GC",
        timeframe="1 day",
        fractal_n=2,
        range_window=5,
        range_max_pct=0.05,
        range_min_bars=5,
        retracement_zone=(0.5, 0.786),
        target_extension_ratio=1.618,
        stop_buffer_pct=0.005,
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


def test_generates_one_long_signal_on_confluence_setup():
    df = _confluence_setup_df()
    signals = generate_signals(df, _config())

    assert len(signals) == 1
    signal = signals[0]

    assert signal.direction == "long"
    assert signal.entry_ts == df["date"].iloc[22]
    assert signal.entry_price == 110
    assert signal.stop_price == pytest.approx(110 - 110 * 0.005)
    assert signal.target_price == pytest.approx(101 + (132 - 101) * 1.618)
    assert signal.range_start_ts == df["date"].iloc[4]
    assert signal.range_end_ts == df["date"].iloc[9]
    assert signal.impulse_start_price == 101
    assert signal.impulse_extreme_price == 132
    assert signal.retracement_ratio == 0.786


def test_no_signal_when_retracement_zone_never_hit():
    df = _confluence_setup_df()
    # Require a much deeper retracement than this setup ever reaches.
    config = _config(retracement_zone=(0.85, 0.95))
    assert generate_signals(df, config) == []


def test_no_signal_when_no_trading_range_forms():
    df = _confluence_setup_df()
    # Impossibly tight contraction threshold — no range is ever detected.
    config = _config(range_max_pct=0.0001)
    assert generate_signals(df, config) == []
