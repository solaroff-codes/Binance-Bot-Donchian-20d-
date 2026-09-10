"""
Fibonacci retracement and extension levels for a two-point price swing
(start_price -> end_price). Works for both up-swings (end > start) and
down-swings (end < start) — the direction of the move is captured in
(end_price - start_price), so the same formulas produce sensible levels
either way.
"""

from __future__ import annotations

RETRACEMENT_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
EXTENSION_RATIOS = (1.272, 1.618, 2.0)


def retracement_levels(start_price: float, end_price: float) -> dict[float, float]:
    """
    Retracement levels between start_price and end_price, keyed by the
    fraction retraced back from end_price toward start_price
    (0.0 -> end_price itself, 1.0 -> fully back to start_price).
    """
    diff = end_price - start_price
    return {ratio: end_price - diff * ratio for ratio in RETRACEMENT_RATIOS}


def extension_levels(start_price: float, end_price: float) -> dict[float, float]:
    """
    Extension levels projected beyond start_price in the direction of the
    start_price -> end_price move (e.g. Elliott Wave 3/5 price targets
    measured off wave 1 / wave A).
    """
    diff = end_price - start_price
    return {ratio: start_price + diff * ratio for ratio in EXTENSION_RATIOS}


def fib_levels(start_price: float, end_price: float) -> dict[float, float]:
    """Retracement and extension levels combined into one ratio -> price dict."""
    levels = retracement_levels(start_price, end_price)
    levels.update(extension_levels(start_price, end_price))
    return levels
