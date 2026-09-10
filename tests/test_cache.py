import pandas as pd

from data.cache import drop_untraded_bars


def test_drop_untraded_bars_removes_zero_volume_rows():
    df = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=4, freq="D"),
            "close": [100, 101, 102, 103],
            "volume": [0, 5, 0, 10],
        }
    )
    result = drop_untraded_bars(df)
    assert result["volume"].tolist() == [5, 10]
    assert result["close"].tolist() == [101, 103]


def test_drop_untraded_bars_keeps_all_when_no_zero_volume():
    df = pd.DataFrame({"close": [1, 2, 3], "volume": [1, 2, 3]})
    result = drop_untraded_bars(df)
    assert len(result) == 3


def test_drop_untraded_bars_resets_index():
    df = pd.DataFrame({"close": [1, 2, 3], "volume": [0, 5, 0]})
    result = drop_untraded_bars(df)
    assert result.index.tolist() == [0]
