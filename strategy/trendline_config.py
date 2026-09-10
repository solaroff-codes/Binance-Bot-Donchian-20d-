"""
Configuration for the multi-timeframe trendline cascade strategy: higher
timeframes set directional bias, the lowest (trigger) timeframe fires
entries off its own bounce/break events, filtered to agree with that bias.

Configs live as YAML under config/trendline_strategies/{name}.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "trendline_strategies"


@dataclass(frozen=True)
class TrendlineStrategyConfig:
    symbol: str

    # Cascade: higher timeframes set bias (bullish/bearish/neutral per
    # timeframe, must agree — see strategy/trendline_signals.py), the
    # trigger timeframe fires the actual entry.
    bias_timeframes: tuple[str, ...] = ("1 month", "1 week", "1 day", "4 hours")
    trigger_timeframe: str = "1 hour"
    use_continuous: bool = False

    # indicators.trendlines
    fractal_n: int = 2
    max_pivots: int = 20
    touch_tolerance_pct: float = 0.002
    break_buffer_pct: float = 0.002

    # trade management
    stop_buffer_pct: float = 0.005
    target_r_multiple: float = 2.0

    # which trigger-timeframe event types to trade
    allow_bounce: bool = True
    allow_break: bool = True


def load_config(name: str) -> TrendlineStrategyConfig:
    """Load a TrendlineStrategyConfig from config/trendline_strategies/{name}.yaml."""
    path = CONFIG_DIR / f"{name}.yaml"
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    raw["bias_timeframes"] = tuple(raw["bias_timeframes"])
    return TrendlineStrategyConfig(**raw)
