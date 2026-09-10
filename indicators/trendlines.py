"""
Trendline fitting and bounce/break event detection, built on top of the
swing high/low fractals in indicators/swings.py.

A trendline connects two swing points of the same kind: a "support" line
runs through swing lows (the line an uptrend respects from below); a
"resistance" line runs through swing highs (the line a downtrend respects
from above). find_steepest_unbroken_trendline() implements the "steepest
unbroken line" heuristic: among every pair of same-kind swing points, it
keeps only lines that no bar has *closed* through since the line's first
anchor point (closing-basis, not wick-basis — an intrabar wick piercing a
trendline is normal noise, not a break), and returns the steepest of those
— the one hugging price most tightly, which is what a trader means by
"the" current trendline rather than an arbitrarily old one.

Search is restricted to the most recent `max_pivots` swing points (default
20): trendlines drawn from a pivot years in the past aren't something a
trader would actually be watching "now," and it keeps the O(P^2) pair
search fast.

detect_trendline_events() then walks forward bar by bar looking for two
event types against a fitted line:
  - "bounce": price reaches the line, then the next bar closes back away
    from it in the trend direction (one-bar reversal confirmation).
  - "break": a bar closes through the line by more than break_buffer_pct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from indicators.swings import detect_swing_points


@dataclass(frozen=True)
class TrendLine:
    kind: Literal["support", "resistance"]
    start_idx: int
    end_idx: int
    start_price: float
    end_price: float
    start_ts: object
    end_ts: object
    slope: float  # price change per bar

    def price_at(self, idx: int) -> float:
        return self.start_price + self.slope * (idx - self.start_idx)


def find_steepest_unbroken_trendline(
    df: pd.DataFrame,
    n: int = 2,
    kind: Literal["support", "resistance"] = "support",
    max_pivots: int = 20,
    as_of_idx: int | None = None,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
    timestamp_col: str = "date",
) -> TrendLine | None:
    """
    Fit the steepest currently-unbroken trendline of `kind` using swing
    points up to and including `as_of_idx` (defaults to the last bar in
    df). Returns None if fewer than 2 qualifying pivots exist or no
    candidate pair survives the unbroken-on-a-closing-basis check.
    """
    flagged = detect_swing_points(df, n=n, high_col=high_col, low_col=low_col)
    if as_of_idx is None:
        as_of_idx = len(flagged) - 1

    swing_col = "swing_low" if kind == "support" else "swing_high"
    price_col = low_col if kind == "support" else high_col

    pivot_indices = flagged.index[flagged[swing_col] & (flagged.index <= as_of_idx)].tolist()
    pivot_indices = pivot_indices[-max_pivots:]
    if len(pivot_indices) < 2:
        return None

    closes = flagged[close_col]
    best: tuple[float, int, int, float, float] | None = None

    for i, start_idx in enumerate(pivot_indices):
        start_price = flagged.loc[start_idx, price_col]
        for end_idx in pivot_indices[i + 1 :]:
            end_price = flagged.loc[end_idx, price_col]
            slope = (end_price - start_price) / (end_idx - start_idx)

            check_idx = closes.loc[start_idx:as_of_idx].index
            projected = start_price + slope * (check_idx.to_numpy() - start_idx)
            check_closes = closes.loc[start_idx:as_of_idx].to_numpy()

            violated = (
                (check_closes < projected).any()
                if kind == "support"
                else (check_closes > projected).any()
            )
            if violated:
                continue

            is_better = best is None or (
                slope > best[0] if kind == "support" else slope < best[0]
            )
            if is_better:
                best = (slope, start_idx, end_idx, start_price, end_price)

    if best is None:
        return None

    slope, start_idx, end_idx, start_price, end_price = best
    return TrendLine(
        kind=kind,
        start_idx=start_idx,
        end_idx=end_idx,
        start_price=start_price,
        end_price=end_price,
        start_ts=flagged.loc[start_idx, timestamp_col],
        end_ts=flagged.loc[end_idx, timestamp_col],
        slope=slope,
    )


@dataclass(frozen=True)
class TrendlineEvent:
    idx: int
    timestamp: object
    event_type: Literal["bounce", "break"]
    direction: Literal["long", "short"]
    price: float


def detect_trendline_events(
    df: pd.DataFrame,
    trendline: TrendLine,
    touch_tolerance_pct: float = 0.002,
    break_buffer_pct: float = 0.002,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
    timestamp_col: str = "date",
) -> list[TrendlineEvent]:
    """
    Scan bars after trendline.end_idx for bounce/break events against the
    line's projected price at each bar.

    Support line: a "touch" is a bar whose low comes within
    touch_tolerance_pct of (or below) the line; that bar is a candidate
    bounce if the *next* bar closes back above the line — direction 'long'.
    A "break" is a bar whose close falls below the line by more than
    break_buffer_pct — direction 'short'.
    Resistance line: mirror image (touch from below -> bounce short;
    close-through-above -> break long).
    """
    events: list[TrendlineEvent] = []
    n = len(df)

    for idx in range(trendline.end_idx + 1, n):
        line_price = trendline.price_at(idx)
        bar = df.iloc[idx]

        if trendline.kind == "support":
            touched = bar[low_col] <= line_price * (1 + touch_tolerance_pct)
            broke = bar[close_col] < line_price * (1 - break_buffer_pct)
            if broke:
                events.append(
                    TrendlineEvent(idx, bar[timestamp_col], "break", "short", bar[close_col])
                )
            elif touched and idx + 1 < n:
                next_bar = df.iloc[idx + 1]
                next_line_price = trendline.price_at(idx + 1)
                if next_bar[close_col] > next_line_price:
                    events.append(
                        TrendlineEvent(
                            idx + 1, next_bar[timestamp_col], "bounce", "long", next_bar[close_col]
                        )
                    )
        else:  # resistance
            touched = bar[high_col] >= line_price * (1 - touch_tolerance_pct)
            broke = bar[close_col] > line_price * (1 + break_buffer_pct)
            if broke:
                events.append(
                    TrendlineEvent(idx, bar[timestamp_col], "break", "long", bar[close_col])
                )
            elif touched and idx + 1 < n:
                next_bar = df.iloc[idx + 1]
                next_line_price = trendline.price_at(idx + 1)
                if next_bar[close_col] < next_line_price:
                    events.append(
                        TrendlineEvent(
                            idx + 1, next_bar[timestamp_col], "bounce", "short", next_bar[close_col]
                        )
                    )

    return events
