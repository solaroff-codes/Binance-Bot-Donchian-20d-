"""
Swing high/low detection using the classic N-bar fractal method: a bar is a
swing high if its high is strictly greater than the high of the N bars
before and after it (a swing low is the mirror condition on lows). This is
the foundational input Elliott Wave counting is built on top of.

Fractals are found independently on highs and lows, so consecutive
swing-high fractals can occur without an intervening swing low (and vice
versa) — e.g. a strong uptrend can print several local-high fractals in a
row as it grinds higher. alternate_swings() collapses raw fractals down to
a strictly alternating high/low sequence (keeping the most extreme point of
each run), which is what Elliott Wave counting actually needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


@dataclass(frozen=True)
class SwingPoint:
    timestamp: pd.Timestamp
    price: float
    kind: Literal["high", "low"]


def detect_swing_points(
    df: pd.DataFrame,
    n: int = 2,
    high_col: str = "high",
    low_col: str = "low",
) -> pd.DataFrame:
    """
    Return a copy of df (index reset) with two added boolean columns,
    'swing_high' and 'swing_low', flagging N-bar fractals. The first/last n
    bars are never flagged — there aren't enough bars on one side to compare.
    """
    if n < 1:
        raise ValueError("n must be >= 1")

    out = df.reset_index(drop=True).copy()
    highs = out[high_col].to_numpy()
    lows = out[low_col].to_numpy()
    count = len(out)

    swing_high = [False] * count
    swing_low = [False] * count

    for i in range(n, count - n):
        left_high, right_high = highs[i - n : i], highs[i + 1 : i + n + 1]
        if highs[i] > left_high.max() and highs[i] > right_high.max():
            swing_high[i] = True

        left_low, right_low = lows[i - n : i], lows[i + 1 : i + n + 1]
        if lows[i] < left_low.min() and lows[i] < right_low.min():
            swing_low[i] = True

    out["swing_high"] = swing_high
    out["swing_low"] = swing_low
    return out


def get_swing_points(
    df: pd.DataFrame,
    n: int = 2,
    high_col: str = "high",
    low_col: str = "low",
    timestamp_col: str = "date",
) -> list[SwingPoint]:
    """Run detect_swing_points and return the flagged points as a flat, time-ordered list."""
    flagged = detect_swing_points(df, n=n, high_col=high_col, low_col=low_col)

    points: list[SwingPoint] = []
    for _, row in flagged.iterrows():
        if row["swing_high"]:
            points.append(SwingPoint(row[timestamp_col], row[high_col], "high"))
        if row["swing_low"]:
            points.append(SwingPoint(row[timestamp_col], row[low_col], "low"))

    points.sort(key=lambda p: p.timestamp)
    return points


def alternate_swings(points: list[SwingPoint]) -> list[SwingPoint]:
    """
    Collapse a list of swing points (which may contain consecutive
    same-kind runs) into a strictly alternating high/low sequence, keeping
    the most extreme point of each run: the highest 'high', the lowest 'low'.
    """
    if not points:
        return []

    alternating: list[SwingPoint] = [points[0]]
    for point in points[1:]:
        last = alternating[-1]
        if point.kind == last.kind:
            is_more_extreme = (
                point.price > last.price if point.kind == "high" else point.price < last.price
            )
            if is_more_extreme:
                alternating[-1] = point
        else:
            alternating.append(point)

    return alternating
