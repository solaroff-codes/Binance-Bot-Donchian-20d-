"""
Strategy configuration: instrument, timeframe, and the parameter set that
drives indicator computation and signal thresholds. Config-driven so the
signal-generation logic in signals.py runs unchanged across instruments,
timeframes, and parameter sets — only the config differs.

Configs live as YAML files under config/strategies/{name}.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "strategies"


@dataclass(frozen=True)
class StrategyConfig:
    symbol: str
    timeframe: str

    # data source: single (latest) contract-month vs the back-adjusted
    # continuous series built by data/continuous.py. Continuous gives far
    # more history but its early segments' absolute prices are adjusted,
    # not literal historical prices — see that module's docstring.
    use_continuous: bool = False

    # indicators.swings
    fractal_n: int = 2

    # indicators.wyckoff
    range_window: int = 20
    range_max_pct: float = 0.05
    range_min_bars: int = 10

    # confluence rules
    retracement_zone: tuple[float, float] = (0.5, 0.786)  # the "golden zone" for pullback entries
    target_extension_ratio: float = 1.618
    stop_buffer_pct: float = 0.005  # extra cushion beyond the invalidation point


def load_config(name: str) -> StrategyConfig:
    """Load a StrategyConfig from config/strategies/{name}.yaml."""
    path = CONFIG_DIR / f"{name}.yaml"
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    raw["retracement_zone"] = tuple(raw["retracement_zone"])
    return StrategyConfig(**raw)
