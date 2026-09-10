"""
Wyckoff-style trading range detection: identifies periods where price is
consolidating (a contracted, sideways range) rather than trending, and
flags whether each bar is inside an established range or breaking out of
one.

Scope is deliberately limited to range detection + breakout flagging for
this pass — full Wyckoff phase labeling (PS, SC, AR, ST, Spring, SOS, LPS...)
and spring/upthrust detection are a more subjective/discretionary layer,
left for later.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TradingRange:
    start_ts: pd.Timestamp
    end_ts: pd.Timestamp
    support: float
    resistance: float

    @property
    def width_pct(self) -> float:
        mid = (self.support + self.resistance) / 2
        return (self.resistance - self.support) / mid


def detect_trading_ranges(
    df: pd.DataFrame,
    window: int = 20,
    max_range_pct: float = 0.05,
    min_bars: int = 10,
    high_col: str = "high",
    low_col: str = "low",
    timestamp_col: str = "date",
) -> list[TradingRange]:
    """
    Identify trading ranges: runs of at least `min_bars` consecutive bars
    where the rolling window's high-low spread stays within `max_range_pct`
    of its midpoint — i.e. volatility has contracted into a sideways range
    rather than a trend.

    window: bars used to measure the rolling high/low spread.
    max_range_pct: max (high-low)/midpoint over the window to count as "contracted".
    min_bars: minimum consecutive contracted bars before a run counts as a range.
    """
    out = df.reset_index(drop=True)
    rolling_high = out[high_col].rolling(window).max()
    rolling_low = out[low_col].rolling(window).min()
    rolling_mid = (rolling_high + rolling_low) / 2
    rolling_pct = (rolling_high - rolling_low) / rolling_mid
    is_contracted = (rolling_pct <= max_range_pct).fillna(False)

    ranges: list[TradingRange] = []
    run_start = None
    for i, contracted in enumerate(is_contracted):
        if contracted and run_start is None:
            run_start = i
        elif not contracted and run_start is not None:
            _add_range_if_long_enough(
                ranges, out, run_start, i - 1, min_bars, high_col, low_col, timestamp_col
            )
            run_start = None
    if run_start is not None:
        _add_range_if_long_enough(
            ranges, out, run_start, len(out) - 1, min_bars, high_col, low_col, timestamp_col
        )

    return ranges


def _add_range_if_long_enough(
    ranges: list[TradingRange],
    out: pd.DataFrame,
    start_i: int,
    end_i: int,
    min_bars: int,
    high_col: str,
    low_col: str,
    timestamp_col: str,
) -> None:
    if end_i - start_i + 1 < min_bars:
        return
    span = out.iloc[start_i : end_i + 1]
    ranges.append(
        TradingRange(
            start_ts=span[timestamp_col].iloc[0],
            end_ts=span[timestamp_col].iloc[-1],
            support=span[low_col].min(),
            resistance=span[high_col].max(),
        )
    )


def label_bar_state(
    df: pd.DataFrame,
    ranges: list[TradingRange],
    close_col: str = "close",
    timestamp_col: str = "date",
) -> pd.Series:
    """
    Per-bar state relative to the most recently *completed* trading range as
    of that bar: 'inside_range', 'breakout_up', 'breakdown', or None (no
    completed range yet to compare against).

    A range's state persists forward until a newer range completes — it
    does not automatically "expire" after some number of bars. That's a
    reasonable first pass; revisit if stale ranges start producing
    misleading breakout flags long after the range itself is no longer
    relevant.
    """
    out = df.reset_index(drop=True)
    sorted_ranges = sorted(ranges, key=lambda r: r.end_ts)
    states: list[str | None] = [None] * len(out)

    range_idx = 0
    for i in range(len(out)):
        ts = out[timestamp_col].iloc[i]

        if not sorted_ranges:
            continue

        while range_idx + 1 < len(sorted_ranges) and sorted_ranges[range_idx + 1].end_ts <= ts:
            range_idx += 1

        active = sorted_ranges[range_idx]
        if active.end_ts > ts:
            continue

        close = out[close_col].iloc[i]
        if close > active.resistance:
            states[i] = "breakout_up"
        elif close < active.support:
            states[i] = "breakdown"
        else:
            states[i] = "inside_range"

    return pd.Series(states, index=df.index, name="wyckoff_state")
