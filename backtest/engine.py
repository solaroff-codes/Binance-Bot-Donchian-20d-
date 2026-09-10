"""
Trade simulation: walks each strategy.signals.Signal forward through
subsequent bars to determine whether its stop or target was hit first,
producing a Trade record. compute_metrics() then aggregates a list of
Trades into the usual backtest summary stats.

Fill assumptions (documented, not hidden — a first-pass backtest, not a
realistic fill/slippage model):
  - Entry fills at signal.entry_price, on signal.entry_ts's bar. Since
    entry_price is the confirming swing's own high/low, this is the best
    possible price that bar could have offered.
  - From the next bar onward, if a single bar's range touches both the
    stop and the target, the stop is assumed hit first (conservative
    tie-break — avoids overstating results on wide bars).
  - A signal with neither stop nor target hit by the end of the available
    data is recorded as 'open' and excluded from win/loss-based metrics.
  - enforce_single_position=True (the default): signals are processed in
    entry_ts order, and any signal that would open while a prior simulated
    trade is still open (its entry falls before that trade's exit, or that
    trade never closed) is skipped rather than simulated as a second,
    independent trade. Without this, two signals a day apart in the same
    direction get counted as two independent bets, when a real account
    would already be in the first position — this only became visible
    once a strategy (the trendline cascade) started producing signals
    frequently enough to actually overlap; the sparser confluence
    strategy's signal counts were too low to have exposed it. Pass
    enforce_single_position=False to restore the old "every signal is an
    independent trade" behavior.

'sharpe_r' is a trade-level Sharpe-like ratio (mean/std of R-multiples,
scaled by sqrt(n)) — it is NOT an annualized, time-series Sharpe ratio.
Labeled explicitly so it isn't mistaken for one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np
import pandas as pd


class SignalLike(Protocol):
    """Structural type for anything simulate_trades can consume — both
    strategy.signals.Signal and strategy.trendline_signals.TrendlineSignal
    satisfy this without either module depending on the other."""

    entry_ts: object
    direction: Literal["long", "short"]
    entry_price: float
    stop_price: float
    target_price: float


@dataclass(frozen=True)
class Trade:
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp | None
    direction: Literal["long", "short"]
    entry_price: float
    stop_price: float
    target_price: float
    exit_price: float | None
    outcome: Literal["win", "loss", "open"]
    risk_points: float
    pnl_points: float | None
    r_multiple: float | None
    pnl_dollars: float | None


def simulate_trades(
    df: pd.DataFrame,
    signals: list[SignalLike],
    multiplier: float = 1.0,
    high_col: str = "high",
    low_col: str = "low",
    timestamp_col: str = "date",
    enforce_single_position: bool = True,
) -> list[Trade]:
    out = df.reset_index(drop=True)
    timestamps = out[timestamp_col]

    ordered_signals = sorted(signals, key=lambda s: s.entry_ts) if enforce_single_position else signals

    trades: list[Trade] = []
    blocked_until_date = None  # plain date; None = not currently blocked
    position_open_indefinitely = False

    for signal in ordered_signals:
        if enforce_single_position:
            if position_open_indefinitely:
                continue
            if blocked_until_date is not None:
                if pd.Timestamp(signal.entry_ts).date() < blocked_until_date:
                    continue

        risk_points = abs(signal.entry_price - signal.stop_price)
        future = out[timestamps > signal.entry_ts]

        exit_ts = None
        exit_price = None
        outcome: Literal["win", "loss", "open"] = "open"

        for _, bar in future.iterrows():
            hit_stop = (
                bar[low_col] <= signal.stop_price
                if signal.direction == "long"
                else bar[high_col] >= signal.stop_price
            )
            hit_target = (
                bar[high_col] >= signal.target_price
                if signal.direction == "long"
                else bar[low_col] <= signal.target_price
            )
            if hit_stop:
                exit_ts, exit_price, outcome = bar[timestamp_col], signal.stop_price, "loss"
                break
            if hit_target:
                exit_ts, exit_price, outcome = bar[timestamp_col], signal.target_price, "win"
                break

        if outcome == "open":
            pnl_points = None
            r_multiple = None
            pnl_dollars = None
        else:
            signed = 1 if signal.direction == "long" else -1
            pnl_points = signed * (exit_price - signal.entry_price)
            r_multiple = pnl_points / risk_points if risk_points else None
            pnl_dollars = pnl_points * multiplier

        trades.append(
            Trade(
                entry_ts=signal.entry_ts,
                exit_ts=exit_ts,
                direction=signal.direction,
                entry_price=signal.entry_price,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                exit_price=exit_price,
                outcome=outcome,
                risk_points=risk_points,
                pnl_points=pnl_points,
                r_multiple=r_multiple,
                pnl_dollars=pnl_dollars,
            )
        )

        if enforce_single_position:
            if outcome == "open":
                position_open_indefinitely = True
            else:
                blocked_until_date = pd.Timestamp(exit_ts).date()

    return trades


def compute_metrics(trades: list[Trade]) -> dict:
    closed = [t for t in trades if t.outcome != "open"]
    wins = [t for t in closed if t.outcome == "win"]
    losses = [t for t in closed if t.outcome == "loss"]

    metrics: dict = {
        "num_signals": len(trades),
        "num_closed": len(closed),
        "num_open": len(trades) - len(closed),
        "num_wins": len(wins),
        "num_losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else None,
    }

    gross_win_dollars = sum(t.pnl_dollars for t in wins)
    gross_loss_dollars = sum(t.pnl_dollars for t in losses)  # <= 0
    metrics["total_pnl_dollars"] = gross_win_dollars + gross_loss_dollars if closed else None
    metrics["profit_factor"] = (
        gross_win_dollars / abs(gross_loss_dollars) if gross_loss_dollars != 0 else None
    )

    r_multiples = [t.r_multiple for t in closed if t.r_multiple is not None]
    metrics["avg_r_multiple"] = float(np.mean(r_multiples)) if r_multiples else None
    if len(r_multiples) >= 2 and np.std(r_multiples) > 0:
        metrics["sharpe_r"] = float(
            np.mean(r_multiples) / np.std(r_multiples) * np.sqrt(len(r_multiples))
        )
    else:
        metrics["sharpe_r"] = None

    # Max drawdown on the cumulative dollar P&L curve, closed trades in exit
    # order. Prepend a 0 starting-equity baseline so a losing first trade
    # registers as a real drawdown from that baseline, not just from itself.
    closed_by_exit = sorted(closed, key=lambda t: t.exit_ts)
    if closed_by_exit:
        cum = np.concatenate([[0.0], np.cumsum([t.pnl_dollars for t in closed_by_exit])])
        running_peak = np.maximum.accumulate(cum)
        drawdowns = running_peak - cum
        metrics["max_drawdown_dollars"] = float(drawdowns.max())
    else:
        metrics["max_drawdown_dollars"] = None

    return metrics
