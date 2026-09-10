"""
Multi-timeframe trendline cascade: higher timeframes (monthly/weekly/daily/
4h by default) set directional bias, the trigger timeframe (1h by default)
fires an entry when its own trendline produces a bounce or break event that
agrees with that bias.

Bias per timeframe, at any bar: 'bullish' if a valid support trendline
exists there and price is above it; 'bearish' if a valid resistance line
exists and price is below it; 'neutral' otherwise (no valid line, or both/
neither condition holds — e.g. price sitting between an intact support and
an intact resistance is genuinely undecided, not a bug). Overall cascade
bias requires every non-neutral bias timeframe to agree — one bullish and
one bearish among the bias timeframes means "no trade," not a coin flip.

Point-in-time correctness matters a lot here: a trigger-timeframe bar must
only ever be compared against bias computed from *lower or equal* real
time on each higher timeframe, and only against a trigger-timeframe
trendline that could actually have been drawn before that bar happened.
Both bias and trigger trendlines come from indicators.trendlines.
walk_forward_trendlines(), which is built specifically to guarantee that
(see its docstring) — this module must not call
find_steepest_unbroken_trendline() directly on a full dataframe, as that
freely uses future pivots.

This module only produces TrendlineSignal objects. TrendlineSignal
deliberately shares field names (entry_ts, direction, entry_price,
stop_price, target_price) with strategy.signals.Signal so it's a drop-in
input to backtest.engine.simulate_trades() without any changes there.

Split into a two-phase pipeline (CascadeState + generate_trendline_signals)
because the expensive part — walk_forward_trendlines on the trigger
timeframe and every bias timeframe — depends only on the input data,
fractal_n, max_pivots, bias_timeframes, and trigger_timeframe. It does NOT
depend on touch_tolerance_pct, break_buffer_pct, target_r_multiple,
stop_buffer_pct, allow_bounce, or allow_break. A parameter sweep over
those cheap knobs would otherwise redo the expensive walk-forward fit from
scratch for every single combination — compute_cascade_state() does it
once, generate_trendline_signals() (or the lower-level
signals_from_state()) is cheap to call repeatedly after that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from indicators.trendlines import TrendLine, walk_forward_trendlines
from strategy.trendline_config import TrendlineStrategyConfig

Bias = Literal["bullish", "bearish", "neutral"]


@dataclass(frozen=True)
class TrendlineSignal:
    entry_ts: object
    direction: Literal["long", "short"]
    entry_price: float
    stop_price: float
    target_price: float
    event_type: Literal["bounce", "break"]
    trendline_start_ts: object
    trendline_slope: float
    bias_by_timeframe: tuple[tuple[str, str], ...]  # ((timeframe, bias), ...) for transparency


def _bias_series_for_timeframe(
    df: pd.DataFrame,
    config: TrendlineStrategyConfig,
    close_col: str = "close",
) -> list[Bias]:
    support_lines = walk_forward_trendlines(
        df, n=config.fractal_n, kind="support", max_pivots=config.max_pivots
    )
    resistance_lines = walk_forward_trendlines(
        df, n=config.fractal_n, kind="resistance", max_pivots=config.max_pivots
    )
    closes = df[close_col].to_numpy()

    biases: list[Bias] = []
    for idx in range(len(df)):
        support = support_lines[idx]
        resistance = resistance_lines[idx]
        bullish = support is not None and closes[idx] > support.price_at(idx)
        bearish = resistance is not None and closes[idx] < resistance.price_at(idx)
        if bullish and not bearish:
            biases.append("bullish")
        elif bearish and not bullish:
            biases.append("bearish")
        else:
            biases.append("neutral")
    return biases


def _bias_lookup(dates_sorted: np.ndarray, biases: list[Bias], target_date) -> Bias:
    pos = int(np.searchsorted(dates_sorted, target_date, side="right")) - 1
    return biases[pos] if pos >= 0 else "neutral"


def _check_bar_event(
    prior_bar: pd.Series,
    prior_line_price: float,
    current_bar: pd.Series,
    current_line_price: float,
    kind: Literal["support", "resistance"],
    touch_tolerance_pct: float,
    break_buffer_pct: float,
    high_col: str,
    low_col: str,
    close_col: str,
) -> tuple[Literal["bounce", "break"], Literal["long", "short"]] | tuple[None, None]:
    if kind == "support":
        if current_bar[close_col] < current_line_price * (1 - break_buffer_pct):
            return "break", "short"
        touched_prior = prior_bar[low_col] <= prior_line_price * (1 + touch_tolerance_pct)
        if touched_prior and current_bar[close_col] > current_line_price:
            return "bounce", "long"
    else:
        if current_bar[close_col] > current_line_price * (1 + break_buffer_pct):
            return "break", "long"
        touched_prior = prior_bar[high_col] >= prior_line_price * (1 - touch_tolerance_pct)
        if touched_prior and current_bar[close_col] < current_line_price:
            return "bounce", "short"
    return None, None


@dataclass(frozen=True)
class CascadeState:
    """The expensive, parameter-independent half of the pipeline — see
    module docstring. Reuse across a sweep over the cheap knobs instead of
    recomputing this for every combination."""

    trigger: pd.DataFrame
    support_lines: list[TrendLine | None]
    resistance_lines: list[TrendLine | None]
    bias_series_by_tf: dict[str, list[Bias]]
    bias_dates_by_tf: dict[str, np.ndarray]
    high_col: str
    low_col: str
    close_col: str
    timestamp_col: str


def compute_cascade_state(
    bias_dfs: dict[str, pd.DataFrame],
    trigger_df: pd.DataFrame,
    config: TrendlineStrategyConfig,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
    timestamp_col: str = "date",
) -> CascadeState:
    """
    bias_dfs: {timeframe: df} for every timeframe in config.bias_timeframes.
    trigger_df: config.trigger_timeframe's own OHLCV data.
    """
    bias_series_by_tf: dict[str, list[Bias]] = {}
    bias_dates_by_tf: dict[str, np.ndarray] = {}
    for tf in config.bias_timeframes:
        bdf = bias_dfs[tf]
        bias_series_by_tf[tf] = _bias_series_for_timeframe(bdf, config, close_col)
        bias_dates_by_tf[tf] = bdf[timestamp_col].map(lambda v: pd.Timestamp(v).date()).to_numpy()

    trigger = trigger_df.reset_index(drop=True)
    support_lines = walk_forward_trendlines(
        trigger, n=config.fractal_n, kind="support", max_pivots=config.max_pivots,
        high_col=high_col, low_col=low_col, close_col=close_col, timestamp_col=timestamp_col,
    )
    resistance_lines = walk_forward_trendlines(
        trigger, n=config.fractal_n, kind="resistance", max_pivots=config.max_pivots,
        high_col=high_col, low_col=low_col, close_col=close_col, timestamp_col=timestamp_col,
    )

    return CascadeState(
        trigger=trigger,
        support_lines=support_lines,
        resistance_lines=resistance_lines,
        bias_series_by_tf=bias_series_by_tf,
        bias_dates_by_tf=bias_dates_by_tf,
        high_col=high_col,
        low_col=low_col,
        close_col=close_col,
        timestamp_col=timestamp_col,
    )


def signals_from_state(state: CascadeState, config: TrendlineStrategyConfig) -> list[TrendlineSignal]:
    """
    The cheap half: apply config's entry/exit knobs (touch_tolerance_pct,
    break_buffer_pct, target_r_multiple, stop_buffer_pct, allow_bounce,
    allow_break) against an already-computed CascadeState. config's
    bias_timeframes/trigger_timeframe/fractal_n/max_pivots are assumed to
    match whatever built `state` — this function does not re-check that.
    """
    trigger = state.trigger
    high_col, low_col, close_col, timestamp_col = (
        state.high_col, state.low_col, state.close_col, state.timestamp_col
    )
    signals: list[TrendlineSignal] = []

    for idx in range(1, len(trigger)):
        bar_date = pd.Timestamp(trigger[timestamp_col].iloc[idx]).date()

        biases: dict[str, Bias] = {
            tf: _bias_lookup(state.bias_dates_by_tf[tf], state.bias_series_by_tf[tf], bar_date)
            for tf in config.bias_timeframes
        }
        opinions = {b for b in biases.values() if b != "neutral"}
        if len(opinions) != 1:
            continue
        overall_bias = opinions.pop()

        line: TrendLine | None = (
            state.support_lines[idx - 1] if overall_bias == "bullish" else state.resistance_lines[idx - 1]
        )
        if line is None:
            continue

        kind: Literal["support", "resistance"] = "support" if overall_bias == "bullish" else "resistance"
        prior_bar = trigger.iloc[idx - 1]
        current_bar = trigger.iloc[idx]
        event_type, direction = _check_bar_event(
            prior_bar,
            line.price_at(idx - 1),
            current_bar,
            line.price_at(idx),
            kind,
            config.touch_tolerance_pct,
            config.break_buffer_pct,
            high_col,
            low_col,
            close_col,
        )
        if event_type is None:
            continue
        expected_direction = "long" if overall_bias == "bullish" else "short"
        if direction != expected_direction:
            continue
        if (event_type == "bounce" and not config.allow_bounce) or (
            event_type == "break" and not config.allow_break
        ):
            continue

        entry_price = float(current_bar[close_col])
        line_price = line.price_at(idx)
        stop_buffer = line_price * config.stop_buffer_pct
        stop_price = line_price - stop_buffer if direction == "long" else line_price + stop_buffer
        risk = abs(entry_price - stop_price)
        target_price = (
            entry_price + config.target_r_multiple * risk
            if direction == "long"
            else entry_price - config.target_r_multiple * risk
        )

        signals.append(
            TrendlineSignal(
                entry_ts=current_bar[timestamp_col],
                direction=direction,
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                event_type=event_type,
                trendline_start_ts=line.start_ts,
                trendline_slope=line.slope,
                bias_by_timeframe=tuple(biases.items()),
            )
        )

    return signals


def generate_trendline_signals(
    bias_dfs: dict[str, pd.DataFrame],
    trigger_df: pd.DataFrame,
    config: TrendlineStrategyConfig,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
    timestamp_col: str = "date",
) -> list[TrendlineSignal]:
    """Convenience wrapper: compute_cascade_state() + signals_from_state()
    in one call. Prefer calling them separately when sweeping the cheap
    knobs (touch_tolerance_pct, break_buffer_pct, target_r_multiple, ...)
    across many configs that all share the same data/fractal_n/max_pivots/
    bias_timeframes/trigger_timeframe — see module docstring."""
    state = compute_cascade_state(bias_dfs, trigger_df, config, high_col, low_col, close_col, timestamp_col)
    return signals_from_state(state, config)
