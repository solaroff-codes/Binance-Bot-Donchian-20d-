"""
Trailing-stop trade simulation for the trendline cascade strategy: instead
of a fixed stop and target, the stop trails the CURRENT walk-forward
trendline as price advances (only ever tightens, never loosens), and there
is no fixed target — the trade runs until the trailing stop is hit or the
data ends. The classic trend-following exit rule ("let winners run"), as
an alternative to strategy/trendline_signals.py's fixed R-multiple target.

Requires the same CascadeState used to generate the signals (specifically
its walk-forward support_lines / resistance_lines) so the trailing stop at
bar i only ever uses a trendline that could actually have been known at
bar i — same point-in-time-correctness requirement as signal generation
itself (see indicators.trendlines.walk_forward_trendlines's docstring).

Reuses backtest.engine.Trade/CostModel/PositionSizer/compute_metrics so
results are directly comparable to the fixed-target version — same
schema, same cost/sizing model, same metrics function.
"""

from __future__ import annotations

import pandas as pd

from backtest.engine import CostModel, PositionSizer, Trade, sized_and_costed_pnl
from strategy.trendline_config import TrendlineStrategyConfig
from strategy.trendline_signals import CascadeState, TrendlineSignal


def simulate_trailing_trades(
    state: CascadeState,
    signals: list[TrendlineSignal],
    config: TrendlineStrategyConfig,
    multiplier: float = 1.0,
    enforce_single_position: bool = True,
    cost_model: CostModel | None = None,
    position_sizer: PositionSizer | None = None,
    skip_log: list | None = None,
) -> list[Trade]:
    trigger = state.trigger
    timestamp_col = state.timestamp_col
    ts_to_idx = {ts: i for i, ts in enumerate(trigger[timestamp_col])}

    ordered_signals = sorted(signals, key=lambda s: s.entry_ts) if enforce_single_position else list(signals)

    trades: list[Trade] = []
    blocked_until_date = None
    position_open_indefinitely = False

    for signal in ordered_signals:
        if enforce_single_position:
            if position_open_indefinitely:
                continue
            if blocked_until_date is not None:
                if pd.Timestamp(signal.entry_ts).date() < blocked_until_date:
                    continue

        entry_idx = ts_to_idx.get(signal.entry_ts)
        if entry_idx is None:
            continue  # signal's entry_ts isn't a bar in this state's trigger df

        lines = state.support_lines if signal.direction == "long" else state.resistance_lines
        risk_points = abs(signal.entry_price - signal.stop_price)
        running_stop = signal.stop_price

        exit_idx = None
        exit_price = None
        outcome = "open"

        for i in range(entry_idx + 1, len(trigger)):
            line = lines[i]
            if line is not None:
                line_price = line.price_at(i)
                buffer = line_price * config.stop_buffer_pct
                candidate = line_price - buffer if signal.direction == "long" else line_price + buffer
                running_stop = (
                    max(running_stop, candidate) if signal.direction == "long" else min(running_stop, candidate)
                )

            bar = trigger.iloc[i]
            hit = (
                bar[state.low_col] <= running_stop
                if signal.direction == "long"
                else bar[state.high_col] >= running_stop
            )
            if hit:
                exit_idx = i
                exit_price = running_stop
                went_favorable = (
                    exit_price > signal.entry_price if signal.direction == "long" else exit_price < signal.entry_price
                )
                outcome = "win" if went_favorable else "loss"
                break

        exit_ts = trigger[timestamp_col].iloc[exit_idx] if exit_idx is not None else None

        if outcome == "open":
            pnl_points = None
            r_multiple = None
            pnl_dollars = None
            contracts = 1
        else:
            signed = 1 if signal.direction == "long" else -1
            pnl_points = signed * (exit_price - signal.entry_price)
            r_multiple = pnl_points / risk_points if risk_points else None

            sized = sized_and_costed_pnl(
                signal.direction, signal.entry_price, exit_price, risk_points,
                multiplier, cost_model, position_sizer,
            )
            if sized is None:
                if skip_log is not None:
                    skip_log.append({
                        "entry_ts": signal.entry_ts, "reason": "undersized",
                        "risk_points": risk_points,
                        "risk_dollars_at_1_contract": risk_points * multiplier,
                    })
                continue
            pnl_dollars, contracts = sized

        trades.append(
            Trade(
                entry_ts=signal.entry_ts,
                exit_ts=exit_ts,
                direction=signal.direction,
                entry_price=signal.entry_price,
                stop_price=running_stop,  # final trailing level, not the original fixed stop
                target_price=signal.target_price,  # carried over for reference; unused for exit here
                exit_price=exit_price,
                outcome=outcome,
                risk_points=risk_points,
                pnl_points=pnl_points,
                r_multiple=r_multiple,
                pnl_dollars=pnl_dollars,
                contracts=contracts,
            )
        )

        if enforce_single_position:
            if outcome == "open":
                position_open_indefinitely = True
            elif exit_ts is not None:
                blocked_until_date = pd.Timestamp(exit_ts).date()

    return trades
