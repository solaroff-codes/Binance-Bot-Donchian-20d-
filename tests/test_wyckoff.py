import pandas as pd

from indicators.wyckoff import detect_trading_ranges, label_bar_state


def _synthetic_range_then_breakout() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=20, freq="D")
    # First 10 bars: tight consolidation around 100 (support 99, resistance 101).
    # Next 10 bars: a clean breakout and trend higher.
    highs = [101] * 10 + [105, 108, 111, 114, 117, 120, 123, 126, 129, 132]
    lows = [99] * 10 + [102, 104, 107, 110, 113, 116, 119, 122, 125, 128]
    closes = [100] * 10 + [104, 107, 110, 113, 116, 119, 122, 125, 128, 131]
    return pd.DataFrame({"date": dates, "high": highs, "low": lows, "close": closes})


def test_detects_contracted_range():
    df = _synthetic_range_then_breakout()
    ranges = detect_trading_ranges(df, window=5, max_range_pct=0.05, min_bars=5)

    assert len(ranges) == 1
    r = ranges[0]
    assert r.support == 99
    assert r.resistance == 101


def test_no_range_detected_when_never_contracted():
    df = _synthetic_range_then_breakout()
    # Impossibly tight threshold — nothing should qualify.
    ranges = detect_trading_ranges(df, window=5, max_range_pct=0.001, min_bars=5)
    assert ranges == []


def test_label_bar_state_flags_breakout_and_inside():
    df = _synthetic_range_then_breakout()
    ranges = detect_trading_ranges(df, window=5, max_range_pct=0.05, min_bars=5)
    states = label_bar_state(df, ranges)

    # Before the range has enough bars to be detected, state is unknown.
    # (pandas coerces None to NaN in this object-dtype Series, so use isna.)
    assert pd.isna(states.iloc[0])
    # Once the range is established, a bar closing inside it is flagged as such.
    assert states.iloc[9] == "inside_range"
    # The clean breakout at the end should be flagged.
    assert states.iloc[-1] == "breakout_up"
