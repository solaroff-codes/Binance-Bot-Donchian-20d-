"""
ATR-based trailing-stop trade simulation ("chandelier exit"): instead of
a fixed target, the stop trails behind the best price reached since entry
by a fixed multiple of the CURRENT bar's ATR, only ever tightening in the
favorable direction, never loosening. The trade runs until the trailing
stop is hit or the data ends — the classic trend-following "let winners
run" exit, as an alternative to a fixed R-multiple target.

Unlike backtest/trailing.py (which trails against a walk-forward
trendline, and is specific to the trendline cascade strategy's
CascadeState), this only needs an ATR series and works with any
SignalLike — it's the generic version, usable for Donchian breakout or
any other strategy that defines an entry/stop/target off ATR.

trail_atr_mult defaults to matching whatever multiple the signal's own
initial stop already used (e.g. Donchian breakout's default
stop_atr_mult=2.0) rather than introducing a new, separately-chosen
number — the trailing stop tightens the same distance behind price that
the strategy already uses to define its initial risk.

Reuses backtest.engine.Trade/CostModel/PositionSizer/sized_and_costed_pnl
so results are directly comparable to the fixed-target version — same
schema, same cost/sizing model.
"""

from __future__ import annotations

import pandas as pd

from backtest.engine import CostModel, PositionSizer, SignalLike, Trade, sized_and_costed_pnl


def simulate_atr_trailing_trades(
    df: pd.DataFrame,
    signals: list[SignalLike],
    atr_series: pd.Series,
    trail_atr_mult: float,
    multiplier: float = 1.0,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
    timestamp_col: str = "date",
    enforce_single_position: bool = True,
    cost_model: CostModel | None = None,
    position_sizer: PositionSizer | None = None,
    skip_log: list | None = None,
) -> list[Trade]:
    out = df.reset_index(drop=True)
    timestamps = out[timestamp_col]
    ts_to_idx = {ts: i for i, ts in enumerate(timestamps)}

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
            continue

        risk_points = abs(signal.entry_price - signal.stop_price)
        running_stop = signal.stop_price
        best_price = signal.entry_price  # highest close since entry (long) / lowest (short)

        exit_idx = None
        exit_price = None
        outcome = "open"

        for i in range(entry_idx + 1, len(out)):
            bar = out.iloc[i]
            a = atr_series.iloc[i]

            if pd.notna(a):
                if signal.direction == "long":
                    best_price = max(best_price, bar[close_col])
                    candidate = best_price - trail_atr_mult * a
                    running_stop = max(running_stop, candidate)
                else:
                    best_price = min(best_price, bar[close_col])
                    candidate = best_price + trail_atr_mult * a
                    running_stop = min(running_stop, candidate)

            hit = bar[low_col] <= running_stop if signal.direction == "long" else bar[high_col] >= running_stop
            if hit:
                exit_idx = i
                exit_price = running_stop
                went_favorable = (
                    exit_price > signal.entry_price if signal.direction == "long" else exit_price < signal.entry_price
                )
                outcome = "win" if went_favorable else "loss"
                break

        exit_ts = timestamps.iloc[exit_idx] if exit_idx is not None else None

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
