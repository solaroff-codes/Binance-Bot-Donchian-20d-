"""
Parameter-grid sweep utility: given a base StrategyConfig and a dict of
{field_name: [values...]}, generates the Cartesian product of
StrategyConfig variants (via dataclasses.replace) and runs each through
run_backtest, returning one comparable summary row per combination.

This complements strategy.config's one-config-per-instrument YAML files by
making it easy to search across parameter values for a single instrument
without hand-writing a YAML file per combination.
"""

from __future__ import annotations

import itertools

import pandas as pd
from dataclasses import replace

from backtest.runner import run_backtest
from strategy.config import StrategyConfig


def generate_grid(base_config: StrategyConfig, **param_grids: list) -> list[StrategyConfig]:
    """
    Cartesian product of param_grids over base_config's fields, e.g.
    generate_grid(base, range_max_pct=[0.03, 0.05], range_min_bars=[5, 10])
    returns 4 StrategyConfig variants.
    """
    names = list(param_grids.keys())
    value_lists = [param_grids[name] for name in names]
    return [
        replace(base_config, **dict(zip(names, combo))) for combo in itertools.product(*value_lists)
    ]


def run_param_sweep(
    base_config: StrategyConfig,
    start_date: str | None = None,
    end_date: str | None = None,
    **param_grids: list,
) -> pd.DataFrame:
    """
    Sweep base_config across param_grids, run each variant through
    run_backtest, and return one row per combination with the swept
    parameter values plus its metrics. No trade-log CSVs are written here —
    this is for scanning many combinations quickly, not archiving every run
    (use backtest.runner.run_sweep for that once a specific config is chosen).
    """
    rows = []
    for config in generate_grid(base_config, **param_grids):
        try:
            _, metrics = run_backtest(config, start_date, end_date)
        except FileNotFoundError as exc:
            rows.append({"symbol": config.symbol, "error": str(exc)})
            continue

        row = {"symbol": config.symbol}
        for name in param_grids:
            row[name] = getattr(config, name)
        row.update(metrics)
        rows.append(row)

    return pd.DataFrame(rows)
