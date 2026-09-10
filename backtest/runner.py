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
from data.cache import load_latest
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
    """
    df = load_latest(config.symbol, config.timeframe)
    if df is None:
        raise FileNotFoundError(
            f"No cached data for {config.symbol}/{config.timeframe} — "
            "run scripts/fetch_all_instruments.py first."
        )

    if start_date or end_date:
        ts = pd.to_datetime(df["date"])
        mask = pd.Series(True, index=df.index)
        if start_date:
            mask &= ts >= pd.Timestamp(start_date)
        if end_date:
            mask &= ts <= pd.Timestamp(end_date)
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
