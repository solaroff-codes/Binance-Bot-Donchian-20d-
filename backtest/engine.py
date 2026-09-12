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

cost_model / position_sizer (both optional, default None = old behavior
unchanged, fully backward compatible):
  - CostModel adds slippage (a fixed number of ticks, applied unfavorably
    on both entry and exit — e.g. a long pays slip on the buy and gives up
    slip on the sell) and a flat round-turn commission per contract. Real
    numbers, not modeled before this — every profit-factor figure reported
    earlier in this project's history had zero transaction costs in it.
  - PositionSizer replaces the implicit "1 contract per trade" assumption
    with dollar-risk sizing: contracts = floor(account_size *
    risk_pct_per_trade / (risk_points * multiplier)). A signal that can't
    even size 1 contract within the risk budget is skipped entirely (not
    floored up to 1, which would silently blow through the risk budget) —
    pass a list to skip_log to see what got skipped and why. This can
    matter a lot for wide-stop instruments on a small account: a single CL
    contract's stop-loss risk is easily $1,000+, which alone can exceed a
    1-2% budget on a $10k account.

'sharpe_r' is a trade-level Sharpe-like ratio (mean/std of R-multiples,
scaled by sqrt(n)) — it is NOT an annualized, time-series Sharpe ratio.
Labeled explicitly so it isn't mistaken for one.
"""

from __future__ import annotations

import math
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
class CostModel:
    """Per-fill transaction costs. slippage_ticks is applied once on entry
    and once on exit (both unfavorably); tick_size must be given if
    slippage_ticks > 0. commission_per_contract is a flat round-turn
    dollar amount per contract (covers both the entry and exit fill)."""

    slippage_ticks: float = 0.0
    tick_size: float | None = None
    commission_per_contract: float = 0.0
    # Percentage-of-notional commission (e.g. Binance's ~0.1% spot taker
    # fee), applied to both entry and exit notional separately — a
    # different fee structure than futures' flat commission_per_contract.
    # Both fields coexist and add; a given cost model would normally set
    # only one of them non-zero.
    commission_pct: float = 0.0


@dataclass(frozen=True)
class PositionSizer:
    """Dollar-risk position sizing: risk_pct_per_trade of account_size
    determines how many contracts a trade gets, given its own stop
    distance. e.g. account_size=10_000, risk_pct_per_trade=0.02 -> each
    trade risks up to $200."""

    account_size: float
    risk_pct_per_trade: float


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
    contracts: int = 1


def sized_and_costed_pnl(
    direction: Literal["long", "short"],
    entry_price: float,
    exit_price: float,
    risk_points: float,
    multiplier: float,
    cost_model: CostModel | None,
    position_sizer: PositionSizer | None,
) -> tuple[float, int] | None:
    """Returns (pnl_dollars, contracts), or None if position_sizer would
    size this trade at 0 contracts (skip it)."""
    if position_sizer is not None:
        if risk_points <= 0:
            return None
        risk_dollars_target = position_sizer.account_size * position_sizer.risk_pct_per_trade
        contracts = math.floor(risk_dollars_target / (risk_points * multiplier))
        if contracts < 1:
            return None
    else:
        contracts = 1

    fill_entry = entry_price
    fill_exit = exit_price
    if cost_model is not None and cost_model.slippage_ticks and cost_model.tick_size:
        slip = cost_model.slippage_ticks * cost_model.tick_size
        if direction == "long":
            fill_entry += slip  # pay more to buy
            fill_exit -= slip  # receive less to sell
        else:
            fill_entry -= slip  # receive less selling short
            fill_exit += slip  # pay more to buy back

    signed = 1 if direction == "long" else -1
    pnl_points = signed * (fill_exit - fill_entry)
    pnl_dollars = pnl_points * multiplier * contracts
    if cost_model is not None:
        pnl_dollars -= cost_model.commission_per_contract * contracts
        if cost_model.commission_pct:
            entry_notional = abs(fill_entry) * multiplier * contracts
            exit_notional = abs(fill_exit) * multiplier * contracts
            pnl_dollars -= cost_model.commission_pct * (entry_notional + exit_notional)

    return pnl_dollars, contracts


def simulate_trades(
    df: pd.DataFrame,
    signals: list[SignalLike],
    multiplier: float = 1.0,
    high_col: str = "high",
    low_col: str = "low",
    timestamp_col: str = "date",
    enforce_single_position: bool = True,
    cost_model: CostModel | None = None,
    position_sizer: PositionSizer | None = None,
    skip_log: list | None = None,
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
                stop_price=signal.stop_price,
                target_price=signal.target_price,
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
            else:
                blocked_until_date = pd.Timestamp(exit_ts).date()

    return trades


def simulate_trades_compounding(
    df: pd.DataFrame,
    signals: list[SignalLike],
    multiplier: float = 1.0,
    high_col: str = "high",
    low_col: str = "low",
    timestamp_col: str = "date",
    starting_capital: float = 10_000.0,
    risk_pct_per_trade: float = 0.01,
    cost_model: CostModel | None = None,
    skip_log: list | None = None,
    compounding_fraction: float = 1.0,
) -> list[Trade]:
    """
    Same stop/target walk-forward as simulate_trades(), but position size
    is recomputed from CURRENT equity (starting_capital plus every
    realized P&L so far) before each trade, instead of a fixed
    account_size throughout — what a real account actually does: a bigger
    balance after a winning streak sizes the next trade bigger, a smaller
    one after a loss sizes smaller. simulate_trades()'s PositionSizer is
    deliberately non-compounding (fixed account_size) because that's the
    right choice for validating an edge — comparing runs at a constant
    baseline isolates the strategy's own performance from the sizing
    mechanism. This is for the different question of "what would this
    account's balance actually have done," where compounding is the
    realistic behavior.

    compounding_fraction (default 1.0 = full compounding) scales how much
    of the realized P&L feeds back into the sizing base: sizing equity =
    starting_capital + compounding_fraction * cumulative_pnl_so_far. 0.0
    reduces to fixed sizing off starting_capital (matching simulate_trades
    with a constant PositionSizer); a fraction like 0.5 is the standard
    "fractional Kelly"-style compromise — captures part of the growth
    benefit of reinvesting winners without fully compounding losing
    streaks too, which is what makes full (1.0) compounding's drawdown
    grow faster than proportionally as risk_pct_per_trade increases.

    Always single-position (no enforce_single_position=False option) —
    "what would this account's balance have been" only makes sense for a
    strictly sequential, one-position-at-a-time account; parallel
    overlapping positions would need to split a single balance between
    them in some order-dependent way that has no one right answer.

    A trade that can't size even the smallest unit within the risk budget
    is skipped (same convention as sized_and_costed_pnl) — this can
    happen here even where it wouldn't with a fixed account_size, if a
    losing streak has shrunk equity enough.
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
            contracts = 1
        else:
            signed = 1 if signal.direction == "long" else -1
            pnl_points = signed * (exit_price - signal.entry_price)
            r_multiple = pnl_points / risk_points if risk_points else None

            sizing_equity = starting_capital + compounding_fraction * cumulative_pnl
            sizer = PositionSizer(account_size=sizing_equity, risk_pct_per_trade=risk_pct_per_trade)
            sized = sized_and_costed_pnl(
                signal.direction, signal.entry_price, exit_price, risk_points,
                multiplier, cost_model, sizer,
            )
            if sized is None:
                if skip_log is not None:
                    skip_log.append({
                        "entry_ts": signal.entry_ts, "reason": "undersized",
                        "risk_points": risk_points,
                        "risk_dollars_at_1_contract": risk_points * multiplier,
                        "equity_at_time": sizing_equity,
                    })
                continue
            pnl_dollars, contracts = sized
            cumulative_pnl += pnl_dollars

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
                contracts=contracts,
            )
        )

        if outcome == "open":
            position_open_indefinitely = True
        else:
            blocked_until_date = pd.Timestamp(exit_ts).date()

    return trades


def simulate_trades_with_contributions(
    df: pd.DataFrame,
    signals: list[SignalLike],
    multiplier: float = 1.0,
    high_col: str = "high",
    low_col: str = "low",
    timestamp_col: str = "date",
    starting_capital: float = 10_000.0,
    risk_pct_per_trade: float = 0.01,
    cost_model: CostModel | None = None,
    skip_log: list | None = None,
    compounding_fraction: float = 1.0,
    contributions: list[tuple] | None = None,
) -> list[Trade]:
    """
    Same as simulate_trades_compounding(), plus scheduled cash deposits:
    contributions is a list of (date, amount) pairs (any date-like object
    comparable via pd.Timestamp) -- e.g. the 1st of every month for 6
    years. Each trade sizes off starting_capital, plus every contribution
    whose date is on or before that trade's entry, plus
    compounding_fraction * realized P&L so far. Contributed cash is never
    itself "at risk" beyond what the risk_pct_per_trade formula already
    implies -- it just grows the base the next trade's position size is
    computed from, exactly like a real account depositing more cash would.

    Reports contributed-to-date via skip_log entries the same way
    simulate_trades_compounding does for undersized trades; the caller is
    expected to separately track total contributions over the period
    (e.g. len(contributions) * amount, or sum(a for _, a in contributions))
    for building an equity curve that distinguishes deposits from
    trading profit -- this function only affects position sizing, it
    doesn't return a running balance series itself.
    """
    out = df.reset_index(drop=True)
    timestamps = out[timestamp_col]
    ordered_signals = sorted(signals, key=lambda s: s.entry_ts)
    ordered_contributions = sorted(contributions or [], key=lambda c: pd.Timestamp(c[0]))

    trades: list[Trade] = []
    cumulative_pnl = 0.0
    blocked_until_date = None
    position_open_indefinitely = False

    def _contributed_by(ts) -> float:
        cutoff = pd.Timestamp(ts)
        return sum(amount for date, amount in ordered_contributions if pd.Timestamp(date) <= cutoff)

    for signal in ordered_signals:
        if position_open_indefinitely:
            continue
        if blocked_until_date is not None and pd.Timestamp(signal.entry_ts).date() < blocked_until_date:
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
            contracts = 1
        else:
            signed = 1 if signal.direction == "long" else -1
            pnl_points = signed * (exit_price - signal.entry_price)
            r_multiple = pnl_points / risk_points if risk_points else None

            sizing_equity = starting_capital + _contributed_by(signal.entry_ts) + compounding_fraction * cumulative_pnl
            sizer = PositionSizer(account_size=sizing_equity, risk_pct_per_trade=risk_pct_per_trade)
            sized = sized_and_costed_pnl(
                signal.direction, signal.entry_price, exit_price, risk_points,
                multiplier, cost_model, sizer,
            )
            if sized is None:
                if skip_log is not None:
                    skip_log.append({
                        "entry_ts": signal.entry_ts, "reason": "undersized",
                        "risk_points": risk_points,
                        "risk_dollars_at_1_contract": risk_points * multiplier,
                        "equity_at_time": sizing_equity,
                    })
                continue
            pnl_dollars, contracts = sized
            cumulative_pnl += pnl_dollars

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
                contracts=contracts,
            )
        )

        if outcome == "open":
            position_open_indefinitely = True
        else:
            blocked_until_date = pd.Timestamp(exit_ts).date()

    return trades


def simulate_portfolio_with_rebalancing(
    assets: dict[str, tuple[pd.DataFrame, float, list[SignalLike]]],
    starting_capital_per_asset: float,
    risk_pct_per_trade: float,
    rebalance_dates: list,
    high_col: str = "high",
    low_col: str = "low",
    timestamp_col: str = "date",
    cost_model: CostModel | None = None,
) -> dict[str, list[Trade]]:
    """
    Multiple sleeves (assets = {symbol: (df, multiplier, signals)}), each
    compounding independently (always full compounding — this function
    exists specifically to test periodic rebalancing on top of the
    already-chosen full-compounding configuration, not to generalize
    every knob), EXCEPT that at each date in rebalance_dates, every
    sleeve's current equity is pooled and split back to equal shares —
    simulating selling down whichever sleeve grew fastest and topping up
    the laggards, the standard periodic-rebalancing portfolio technique.

    Unlike every other simulate_* function here, this one drives multiple
    symbols' signals against their own price data on ONE shared timeline
    (rebalancing only makes sense as a cross-sleeve event), so it can't
    reuse the single-asset walk-forward functions directly — the stop/
    target walk-forward logic is the same as simulate_trades_compounding's,
    just interleaved with rebalance events in chronological order.

    Returns {symbol: [Trade, ...]} exactly like the single-asset
    functions, so callers build equity curves the same way afterward.
    """
    prepped = {}
    for symbol, (df, multiplier, signals) in assets.items():
        out = df.reset_index(drop=True)
        prepped[symbol] = {
            "out": out, "timestamps": out[timestamp_col], "multiplier": multiplier,
            "signals": sorted(signals, key=lambda s: s.entry_ts),
        }

    equity = {symbol: starting_capital_per_asset for symbol in assets}
    blocked_until_date = {symbol: None for symbol in assets}
    position_open_indefinitely = {symbol: False for symbol in assets}
    trades_out: dict[str, list[Trade]] = {symbol: [] for symbol in assets}

    # One shared chronological timeline: every signal (tagged by symbol)
    # plus every rebalance date. Rebalances are ordered before same-day
    # signals (arbitrary but consistent tie-break) via the 0/1 sort key.
    events = [(pd.Timestamp(d), 0, None, None) for d in rebalance_dates]
    for symbol, p in prepped.items():
        events += [(pd.Timestamp(s.entry_ts), 1, symbol, s) for s in p["signals"]]
    events.sort(key=lambda e: (e[0], e[1]))

    for _, kind, symbol, signal in events:
        if kind == 0:
            total = sum(equity.values())
            share = total / len(equity)
            for s in equity:
                equity[s] = share
            continue

        if position_open_indefinitely[symbol]:
            continue
        if blocked_until_date[symbol] is not None and pd.Timestamp(signal.entry_ts).date() < blocked_until_date[symbol]:
            continue

        p = prepped[symbol]
        out, timestamps, multiplier = p["out"], p["timestamps"], p["multiplier"]
        risk_points = abs(signal.entry_price - signal.stop_price)
        future = out[timestamps > signal.entry_ts]

        exit_ts = None
        exit_price = None
        outcome: Literal["win", "loss", "open"] = "open"
        for _, bar in future.iterrows():
            hit_stop = (
                bar[low_col] <= signal.stop_price if signal.direction == "long" else bar[high_col] >= signal.stop_price
            )
            hit_target = (
                bar[high_col] >= signal.target_price if signal.direction == "long" else bar[low_col] <= signal.target_price
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
            contracts = 1
        else:
            signed = 1 if signal.direction == "long" else -1
            pnl_points = signed * (exit_price - signal.entry_price)
            r_multiple = pnl_points / risk_points if risk_points else None

            sizer = PositionSizer(account_size=equity[symbol], risk_pct_per_trade=risk_pct_per_trade)
            sized = sized_and_costed_pnl(
                signal.direction, signal.entry_price, exit_price, risk_points, multiplier, cost_model, sizer,
            )
            if sized is None:
                continue
            pnl_dollars, contracts = sized
            equity[symbol] += pnl_dollars

        trades_out[symbol].append(
            Trade(
                entry_ts=signal.entry_ts, exit_ts=exit_ts, direction=signal.direction,
                entry_price=signal.entry_price, stop_price=signal.stop_price, target_price=signal.target_price,
                exit_price=exit_price, outcome=outcome, risk_points=risk_points,
                pnl_points=pnl_points, r_multiple=r_multiple, pnl_dollars=pnl_dollars, contracts=contracts,
            )
        )

        if outcome == "open":
            position_open_indefinitely[symbol] = True
        else:
            blocked_until_date[symbol] = pd.Timestamp(exit_ts).date()

    return trades_out


def compute_drawdown_curve(trades: list[Trade]) -> dict:
    """
    The full drawdown profile from ONE continuous equity curve — trades
    sorted by exit_ts, cumulative dollar P&L from a $0 baseline, no
    chopping into calendar years or any other sub-period first.

    Why this exists alongside compute_metrics()'s own max_drawdown_dollars:
    that number is correct for whatever trade list it's given, but a
    calendar-year (or any other) breakdown that calls compute_metrics()
    separately on each year's trades resets the running-peak baseline to
    $0 at every boundary. A real account never does that — it carries
    equity across Dec 31 into Jan 1 — so a decline that straddles a
    boundary (e.g. starts in November, bottoms in February) gets recorded
    as two smaller, understated drawdowns instead of the one real one a
    continuously-held position would experience. This walks the ENTIRE
    trade history as a single curve and also reports *when* the worst
    drawdown happened and whether/when it recovered, not just how deep.

    Returns a dict with max_drawdown_dollars, peak_ts (when the
    pre-drawdown equity high was first reached), trough_ts (the lowest
    point), and recovered_ts (first later point equity exceeded peak_ts's
    value again, or None if it never did within this trade history) — all
    None if there are no closed trades.
    """
    closed = sorted([t for t in trades if t.outcome != "open"], key=lambda t: t.exit_ts)
    if not closed:
        return {"max_drawdown_dollars": None, "peak_ts": None, "trough_ts": None, "recovered_ts": None}

    cum = np.concatenate([[0.0], np.cumsum([t.pnl_dollars for t in closed])])
    running_peak = np.maximum.accumulate(cum)
    drawdowns = running_peak - cum
    trough_idx = int(np.argmax(drawdowns))
    max_dd = float(drawdowns[trough_idx])
    # argmax returns the FIRST index attaining the max — i.e. the earliest
    # point equity reached this drawdown's starting peak, which is the
    # right "when did the decline begin" answer.
    peak_idx = int(np.argmax(cum[: trough_idx + 1]))

    recovered_idx = None
    if trough_idx + 1 < len(cum):
        above_peak = np.flatnonzero(cum[trough_idx + 1 :] > cum[peak_idx])
        if above_peak.size:
            recovered_idx = trough_idx + 1 + int(above_peak[0])

    def _ts_at(idx: int):
        # idx 0 is the $0 baseline before any trade closed — label it with
        # the first trade's own entry_ts rather than None, so callers
        # always get a usable timestamp.
        return closed[0].entry_ts if idx == 0 else closed[idx - 1].exit_ts

    return {
        "max_drawdown_dollars": max_dd,
        "peak_ts": _ts_at(peak_idx),
        "trough_ts": _ts_at(trough_idx),
        "recovered_ts": _ts_at(recovered_idx) if recovered_idx is not None else None,
    }


def compute_metrics(trades: list[Trade], account_size: float | None = None) -> dict:
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

    if account_size is not None and metrics["max_drawdown_dollars"] is not None:
        metrics["max_drawdown_pct_of_account"] = metrics["max_drawdown_dollars"] / account_size
    else:
        metrics["max_drawdown_pct_of_account"] = None

    return metrics
