"""
A fundamentally different, much more dangerous position-sizing scheme
than anything else in this project: instead of sizing a position from
risk (risk_pct_per_trade of equity, given the stop distance -- what
every other simulator here does), this sizes a position from NOTIONAL
exposure directly: position_notional = current_equity * leverage, using
the entire current equity as margin. This is what "trading with Nx
leverage" means in the common, aggressive retail sense -- not the
capital-efficiency sense tested in backtest/README.md's leveraged-futures
section (where position size stayed risk-based and leverage only
affected how much collateral needed to be posted).

The consequence: the dollar loss if the strategy's own 2x-ATR stop is
hit is no longer capped at a fixed, small percent of equity -- it's
equity * leverage * (stop_distance / entry_price), which can easily
exceed the entire posted margin. When it does, the exchange's
liquidation (same formula as backtest/README.md's leverage section: the
tighter of the strategy's own stop or the leverage-driven liquidation
price binds) closes the position first, realizing a loss of very close
to 100% of that sleeve's entire equity at the time -- a genuine,
realistic account-blowup event, not a backtest abstraction.

Reuses the same walk-forward stop/target logic as backtest/engine.py's
simulate_trades()/simulate_trades_compounding() (same fill assumptions,
same tie-break rules) -- only the position-sizing formula differs, which
is exactly the point: isolating what changes when sizing is leverage-
driven instead of risk-driven, with everything else held constant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from backtest.engine import CostModel, SignalLike, Trade


@dataclass(frozen=True)
class LiquidationEvent:
    entry_ts: object
    symbol: str
    equity_before: float
    equity_after: float
    loss_pct_of_sleeve: float


def simulate_leveraged_notional_trades(
    df: pd.DataFrame,
    signals: list[SignalLike],
    multiplier: float,
    leverage: float,
    starting_capital: float,
    high_col: str = "high",
    low_col: str = "low",
    timestamp_col: str = "date",
    cost_model: CostModel | None = None,
    maintenance_margin_rate: float = 0.005,
    compounding_fraction: float = 1.0,
    liquidation_log: list[LiquidationEvent] | None = None,
) -> list[Trade]:
    """
    Full equity is used as margin for every trade (the aggressive,
    common-retail-mistake case -- no reserve buffer at all): position
    notional = sizing_equity * leverage, position size in coin units =
    notional / (entry_price * multiplier). The strategy's own stop/target
    are unchanged; the effective exit is whichever of (the strategy's own
    stop, the leverage-driven liquidation price) is closer to entry.

    liquidation_log, if given, gets one LiquidationEvent appended per
    trade where liquidation (not the strategy's own stop or target) was
    the actual exit reason -- the metric that matters for understanding
    risk of ruin, not just average performance.
    """
    out = df.reset_index(drop=True)
    timestamps = out[timestamp_col]
    ordered_signals = sorted(signals, key=lambda s: s.entry_ts)

    trades: list[Trade] = []
    cumulative_pnl = 0.0
    blocked_until_date = None
    position_open_indefinitely = False

    for signal in ordered_signals:
        if position_open_indefinitely:
            continue
        if blocked_until_date is not None and pd.Timestamp(signal.entry_ts).date() < blocked_until_date:
            continue

        sizing_equity = starting_capital + compounding_fraction * cumulative_pnl
        if sizing_equity <= 0:
            continue  # sleeve is ruined -- nothing left to trade with

        notional = sizing_equity * leverage
        contracts = math.floor(notional / (signal.entry_price * multiplier))
        if contracts < 1:
            continue

        if signal.direction == "long":
            liq_price = signal.entry_price * (1 - 1 / leverage + maintenance_margin_rate)
            effective_stop = max(signal.stop_price, liq_price)
        else:
            liq_price = signal.entry_price * (1 + 1 / leverage - maintenance_margin_rate)
            effective_stop = min(signal.stop_price, liq_price)
        was_liquidated = effective_stop != signal.stop_price

        risk_points = abs(signal.entry_price - effective_stop)
        future = out[timestamps > signal.entry_ts]

        exit_ts = None
        exit_price = None
        outcome: Literal["win", "loss", "open"] = "open"
        for _, bar in future.iterrows():
            hit_stop = (
                bar[low_col] <= effective_stop if signal.direction == "long" else bar[high_col] >= effective_stop
            )
            hit_target = (
                bar[high_col] >= signal.target_price if signal.direction == "long" else bar[low_col] <= signal.target_price
            )
            if hit_stop:
                exit_ts, exit_price, outcome = bar[timestamp_col], effective_stop, "loss"
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
            fill_entry = signal.entry_price
            fill_exit = exit_price
            if cost_model is not None and cost_model.slippage_ticks and cost_model.tick_size:
                slip = cost_model.slippage_ticks * cost_model.tick_size
                if signal.direction == "long":
                    fill_entry += slip
                    fill_exit -= slip
                else:
                    fill_entry -= slip
                    fill_exit += slip

            pnl_points_raw = signed * (fill_exit - fill_entry)
            pnl_dollars = pnl_points_raw * multiplier * contracts
            if cost_model is not None:
                pnl_dollars -= cost_model.commission_per_contract * contracts
                if cost_model.commission_pct:
                    entry_notional = abs(fill_entry) * multiplier * contracts
                    exit_notional = abs(fill_exit) * multiplier * contracts
                    pnl_dollars -= cost_model.commission_pct * (entry_notional + exit_notional)

            # A leveraged account can't realize a loss bigger than the
            # margin actually posted for the trade -- clamp at -sizing_equity
            # (a total wipeout of that sleeve), the real floor.
            pnl_dollars = max(pnl_dollars, -sizing_equity)

            pnl_points = signed * (exit_price - signal.entry_price)
            r_multiple = pnl_points / risk_points if risk_points else None

            equity_before = sizing_equity
            cumulative_pnl += pnl_dollars
            equity_after = starting_capital + compounding_fraction * cumulative_pnl

            if was_liquidated and outcome == "loss" and liquidation_log is not None:
                liquidation_log.append(
                    LiquidationEvent(
                        entry_ts=signal.entry_ts, symbol="", equity_before=equity_before,
                        equity_after=max(equity_after, 0.0),
                        loss_pct_of_sleeve=(-pnl_dollars / equity_before) if equity_before else 1.0,
                    )
                )

        trades.append(
            Trade(
                entry_ts=signal.entry_ts, exit_ts=exit_ts, direction=signal.direction,
                entry_price=signal.entry_price, stop_price=effective_stop, target_price=signal.target_price,
                exit_price=exit_price, outcome=outcome, risk_points=risk_points,
                pnl_points=pnl_points, r_multiple=r_multiple, pnl_dollars=pnl_dollars, contracts=contracts,
            )
        )

        if outcome == "open":
            position_open_indefinitely = True
        else:
            blocked_until_date = pd.Timestamp(exit_ts).date()

    return trades
