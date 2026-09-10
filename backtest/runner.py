"""
Orchestrates a full backtest run: load cached data for a StrategyConfig's
instrument/timeframe, optionally restrict to a date range, generate
signals, simulate trades, and compute metrics.

run_sweep() does this for multiple configs in one call and writes a single
comparable summary (CSV + JSON) alongside a per-config trade log CSV —
this is the "sweep multiple instruments and parameter sets in one run"
entry point.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from backtest.engine import Trade, compute_metrics, simulate_trades
from data.cache import drop_untraded_bars, load_continuous, load_latest
from data.contracts import load_instruments
from strategy.config import StrategyConfig
from strategy.signals import generate_signals

OUTPUT_DIR = Path(__file__).resolve().parent / "output"


def run_backtest(
    config: StrategyConfig,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[list[Trade], dict]:
    """
    Load cached data for config.symbol/config.timeframe, generate signals,
    simulate trades, and return (trades, metrics). Raises FileNotFoundError
    if no cached data exists for that instrument/timeframe.

    config.use_continuous selects the back-adjusted continuous series
    (data/continuous.py) instead of the latest single contract-month —
    far more history, at the cost of early segments' prices being
    adjusted rather than literal.
    """
    df = load_continuous(config.symbol, config.timeframe) if config.use_continuous else None
    if df is None and config.use_continuous:
        raise FileNotFoundError(
            f"No cached continuous series for {config.symbol}/{config.timeframe} — "
            "run scripts/build_continuous_contracts.py first."
        )
    if df is None:
        df = load_latest(config.symbol, config.timeframe)
    if df is None:
        raise FileNotFoundError(
            f"No cached data for {config.symbol}/{config.timeframe} — "
            "run scripts/fetch_all_instruments.py first."
        )

    # Zero-volume bars are IBKR placeholder data from before a contract was
    # actively traded (flat OHLC, no real price discovery) — see
    # data.cache.drop_untraded_bars. Left in raw cache, filtered here since
    # they'd otherwise masquerade as trivial "trading ranges."
    df = drop_untraded_bars(df)

    if start_date or end_date:
        # Compare by date only (not full Timestamp) so this works whether
        # the 'date' column holds tz-naive daily dates or tz-aware hourly
        # timestamps — a bare pd.Timestamp(start_date) is tz-naive and
        # would fail comparing directly against tz-aware hourly data.
        bar_dates = df["date"].map(lambda v: pd.Timestamp(v).date())
        mask = pd.Series(True, index=df.index)
        if start_date:
            mask &= bar_dates >= pd.Timestamp(start_date).date()
        if end_date:
            mask &= bar_dates <= pd.Timestamp(end_date).date()
        df = df[mask].reset_index(drop=True)

    signals = generate_signals(df, config)

    multiplier = float(load_instruments()[config.symbol]["multiplier"])
    trades = simulate_trades(df, signals, multiplier=multiplier)
    metrics = compute_metrics(trades)
    return trades, metrics


def run_sweep(
    configs: list[StrategyConfig],
    start_date: str | None = None,
    end_date: str | None = None,
    output_dir: Path = OUTPUT_DIR,
) -> pd.DataFrame:
    """
    Run every config, write each one's trade log to
    {output_dir}/{config}_trades.csv, and write a single summary
    (one row per config, comparable across instruments/params) to
    {output_dir}/summary.csv and summary.json. Returns the summary DataFrame.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = []

    for config in configs:
        config_name = f"{config.symbol}_{config.timeframe.replace(' ', '_')}"
        try:
            trades, metrics = run_backtest(config, start_date, end_date)
        except FileNotFoundError as exc:
            summary_rows.append({"config": config_name, "error": str(exc)})
            continue

        trade_log_path = output_dir / f"{config_name}_trades.csv"
        pd.DataFrame([asdict(t) for t in trades]).to_csv(trade_log_path, index=False)

        summary_rows.append(
            {"config": config_name, "trade_log": str(trade_log_path), "error": None, **metrics}
        )

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(output_dir / "summary.csv", index=False)
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary_rows, f, indent=2, default=str)

    return summary
