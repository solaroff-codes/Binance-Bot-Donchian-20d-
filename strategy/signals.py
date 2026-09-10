"""
Combines indicators/wyckoff.py, indicators/swings.py, and
indicators/fibonacci.py into a single confluence entry signal:

  1. A Wyckoff trading range forms, then price breaks out of it
     (indicators.wyckoff.detect_trading_ranges / label_bar_state).
  2. Price makes an initial impulsive move away from the broken range
     boundary until the next opposite-direction swing point forms
     (indicators.swings) — treated as the first impulse leg
     (Elliott wave 1 / wave A).
  3. Price retraces into a configured Fibonacci zone off that leg
     (indicators.fibonacci) without the range boundary being breached
     again during the pullback (a re-entry there means the breakout
     failed, not that a pullback is in progress).
  4. A new swing point in the breakout direction confirms the pullback
     held — that swing's bar is the entry trigger.

Stop-loss sits just beyond the confirming swing point; the target is a
Fibonacci extension of the initial impulse leg. This module only produces
signals (entries/stops/targets) — running them through history to produce
P&L and trade-level metrics is backtest/'s job, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from indicators.fibonacci import retracement_levels
from indicators.swings import SwingPoint, alternate_swings, get_swing_points
from indicators.wyckoff import TradingRange, detect_trading_ranges, label_bar_state
from strategy.config import StrategyConfig


@dataclass(frozen=True)
class Signal:
    entry_ts: pd.Timestamp
    direction: Literal["long", "short"]
    entry_price: float
    stop_price: float
    target_price: float
    range_start_ts: pd.Timestamp
    range_end_ts: pd.Timestamp
    impulse_start_price: float
    impulse_extreme_price: float
    retracement_ratio: float


def generate_signals(
    df: pd.DataFrame,
    config: StrategyConfig,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
    timestamp_col: str = "date",
) -> list[Signal]:
    out = df.reset_index(drop=True)

    ranges = detect_trading_ranges(
        out,
        window=config.range_window,
        max_range_pct=config.range_max_pct,
        min_bars=config.range_min_bars,
        high_col=high_col,
        low_col=low_col,
        timestamp_col=timestamp_col,
    )
    if not ranges:
        return []

    states = label_bar_state(out, ranges, close_col=close_col, timestamp_col=timestamp_col)

    raw_swings = get_swing_points(
        out, n=config.fractal_n, high_col=high_col, low_col=low_col, timestamp_col=timestamp_col
    )
    swings = alternate_swings(raw_swings)

    signals: list[Signal] = []
    for trading_range in ranges:
        signal = _signal_for_range(
            out, trading_range, states, swings, config, high_col, low_col, timestamp_col
        )
        if signal is not None:
            signals.append(signal)

    return signals


def _signal_for_range(
    df: pd.DataFrame,
    trading_range: TradingRange,
    states: pd.Series,
    swings: list[SwingPoint],
    config: StrategyConfig,
    high_col: str,
    low_col: str,
    timestamp_col: str,
) -> Signal | None:
    timestamps = df[timestamp_col]

    breakout_mask = (timestamps > trading_range.end_ts) & states.isin(["breakout_up", "breakdown"])
    breakout_positions = np.flatnonzero(breakout_mask.to_numpy())
    if len(breakout_positions) == 0:
        return None
    breakout_idx = breakout_positions[0]

    direction: Literal["long", "short"] = (
        "long" if states.iloc[breakout_idx] == "breakout_up" else "short"
    )
    breakout_ts = timestamps.iloc[breakout_idx]
    impulse_start_price = trading_range.resistance if direction == "long" else trading_range.support

    # First opposite-direction swing after the breakout marks the end of the impulse leg.
    opposite_kind = "high" if direction == "long" else "low"
    impulse_extreme = next(
        (p for p in swings if p.timestamp >= breakout_ts and p.kind == opposite_kind), None
    )
    if impulse_extreme is None:
        return None

    # retracement_zone bounds can be any ratio, not just the standard set
    # indicators.fibonacci.retracement_levels() returns — compute the zone
    # edges directly with the same formula rather than a dict lookup.
    diff = impulse_extreme.price - impulse_start_price
    zone_lo_ratio, zone_hi_ratio = config.retracement_zone
    zone_prices = sorted(
        [
            impulse_extreme.price - diff * zone_lo_ratio,
            impulse_extreme.price - diff * zone_hi_ratio,
        ]
    )

    # Confirming swing: same kind as the breakout direction, after the
    # impulse extreme, with a price that landed inside the retracement zone.
    confirm_kind = "low" if direction == "long" else "high"
    confirm_swing = next(
        (
            p
            for p in swings
            if p.timestamp > impulse_extreme.timestamp
            and p.kind == confirm_kind
            and zone_prices[0] <= p.price <= zone_prices[1]
        ),
        None,
    )
    if confirm_swing is None:
        return None

    # Invalidate if price re-entered the original range during the pullback
    # — that means the breakout failed, not that a valid retracement is in progress.
    between_mask = (timestamps > impulse_extreme.timestamp) & (timestamps <= confirm_swing.timestamp)
    if direction == "long":
        if (df.loc[between_mask, low_col] < trading_range.resistance).any():
            return None
    else:
        if (df.loc[between_mask, high_col] > trading_range.support).any():
            return None

    stop_buffer = confirm_swing.price * config.stop_buffer_pct
    stop_price = (
        confirm_swing.price - stop_buffer if direction == "long" else confirm_swing.price + stop_buffer
    )

    # target_extension_ratio can be any ratio too — compute directly rather
    # than looking it up in extension_levels()'s fixed standard-ratio dict.
    target_price = impulse_start_price + diff * config.target_extension_ratio

    # For reporting only: which standard Fibonacci ratio the confirming
    # swing landed nearest to.
    retracements = retracement_levels(impulse_start_price, impulse_extreme.price)
    retracement_ratio_hit = min(
        retracements, key=lambda ratio: abs(retracements[ratio] - confirm_swing.price)
    )

    return Signal(
        entry_ts=confirm_swing.timestamp,
        direction=direction,
        entry_price=confirm_swing.price,
        stop_price=stop_price,
        target_price=target_price,
        range_start_ts=trading_range.start_ts,
        range_end_ts=trading_range.end_ts,
        impulse_start_price=impulse_start_price,
        impulse_extreme_price=impulse_extreme.price,
        retracement_ratio=retracement_ratio_hit,
    )
