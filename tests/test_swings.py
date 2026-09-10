import pandas as pd

from indicators.swings import SwingPoint, alternate_swings, detect_swing_points, get_swing_points


def _bars(values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=len(values), freq="D"),
            "high": [v + 1 for v in values],
            "low": [v - 1 for v in values],
            "close": values,
        }
    )


def test_detects_single_swing_high():
    df = _bars([1, 2, 3, 10, 3, 2, 1])
    flagged = detect_swing_points(df, n=2)
    assert flagged.loc[3, "swing_high"]
    assert flagged["swing_high"].sum() == 1


def test_detects_single_swing_low():
    df = _bars([10, 9, 8, 1, 8, 9, 10])
    flagged = detect_swing_points(df, n=2)
    assert flagged.loc[3, "swing_low"]
    assert flagged["swing_low"].sum() == 1


def test_edge_bars_never_flagged():
    df = _bars([5, 4, 3, 2, 1])
    flagged = detect_swing_points(df, n=2)
    assert not flagged["swing_high"].any()
    assert not flagged["swing_low"].any()


def test_get_swing_points_orders_by_time():
    df = _bars([1, 2, 3, 10, 3, 2, 1, 8, 1, 2, 3])
    points = get_swing_points(df, n=2)
    assert [p.timestamp for p in points] == sorted(p.timestamp for p in points)
    assert all(isinstance(p, SwingPoint) for p in points)


def test_alternate_swings_collapses_same_kind_runs():
    points = [
        SwingPoint(pd.Timestamp("2026-01-01"), 10, "high"),
        SwingPoint(pd.Timestamp("2026-01-02"), 12, "high"),  # more extreme, replaces prior
        SwingPoint(pd.Timestamp("2026-01-03"), 5, "low"),
        SwingPoint(pd.Timestamp("2026-01-04"), 3, "low"),  # more extreme, replaces prior
        SwingPoint(pd.Timestamp("2026-01-05"), 20, "high"),
    ]
    result = alternate_swings(points)
    assert [p.price for p in result] == [12, 3, 20]
    assert [p.kind for p in result] == ["high", "low", "high"]


def test_alternate_swings_empty_input():
    assert alternate_swings([]) == []
