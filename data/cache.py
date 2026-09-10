"""
Local parquet cache for historical bars, so we don't re-hit the IBKR API on
every run.

Layout: data/cache/{symbol}/{timeframe}/{contract_month}.parquet
e.g.    data/cache/GC/1_day/20261226.parquet

Keying by contract_month (not just symbol+timeframe) means each futures
expiry gets its own file — this is what makes continuous-contract stitching
possible later without re-fetching or overwriting anything.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from data.ibkr_connector import IBKRConnector

CACHE_ROOT = Path(__file__).resolve().parent / "cache"


def _cache_path(symbol: str, timeframe: str, contract_month: str) -> Path:
    timeframe_slug = timeframe.replace(" ", "_")
    return CACHE_ROOT / symbol / timeframe_slug / f"{contract_month}.parquet"


def load(symbol: str, timeframe: str, contract_month: str) -> pd.DataFrame | None:
    path = _cache_path(symbol, timeframe, contract_month)
    if path.exists():
        return pd.read_parquet(path)
    return None


def load_latest(symbol: str, timeframe: str) -> pd.DataFrame | None:
    """
    Load the most recently-active cached contract-month for symbol/timeframe
    (contract expiries only move forward as rolls happen, so the lexically
    greatest YYYYMMDD filename is always the latest roll). Useful when the
    caller just wants "the current front-month data" without separately
    tracking which specific contract that currently is.

    Glob is restricted to digit-prefixed filenames (contract-month files
    are always "YYYYMMDD.parquet") so it does NOT pick up
    "continuous.parquet" sitting in the same directory — "c" sorts after
    any digit, so an unrestricted glob would silently return the
    continuous series here instead of the latest single contract.
    """
    timeframe_slug = timeframe.replace(" ", "_")
    dir_path = CACHE_ROOT / symbol / timeframe_slug
    if not dir_path.exists():
        return None
    files = sorted(dir_path.glob("[0-9]*.parquet"))
    if not files:
        return None
    return pd.read_parquet(files[-1])


def load_continuous(symbol: str, timeframe: str) -> pd.DataFrame | None:
    """Load the cached back-adjusted continuous series, if built — see data/continuous.py."""
    timeframe_slug = timeframe.replace(" ", "_")
    path = CACHE_ROOT / symbol / timeframe_slug / "continuous.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return None


def drop_untraded_bars(df: pd.DataFrame, volume_col: str = "volume") -> pd.DataFrame:
    """
    Drop bars with zero volume. These are IBKR placeholder/reference bars
    for periods before a contract was actively traded — flat
    open == high == low == close, no real price discovery — not genuine
    market activity. Seen in practice on a single fresh front-month
    contract's own "2 Y" fetch: up to ~45% of its earliest bars can be
    these placeholders, which would otherwise look like a trivial
    zero-volatility "trading range" to indicators.wyckoff.

    Raw cache loaders (load/load_latest/load_continuous) deliberately do
    NOT apply this — they return exactly what was cached, unmodified.
    Callers that need real trading data for signal generation or
    backtesting should call this explicitly.
    """
    return df[df[volume_col] != 0].reset_index(drop=True)


def save(symbol: str, timeframe: str, contract_month: str, df: pd.DataFrame) -> Path:
    path = _cache_path(symbol, timeframe, contract_month)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def fetch_or_load(
    connector: IBKRConnector,
    symbol: str,
    contract,
    timeframe: str,
    duration: str,
    force_refresh: bool = False,
    **fetch_kwargs,
) -> tuple[pd.DataFrame, Path, bool]:
    """
    Return (df, cache_path, was_cached). Loads from the local parquet cache
    if present (and force_refresh is False); otherwise pulls fresh bars from
    IBKR via `connector` and writes them to cache before returning.
    """
    contract_month = contract.lastTradeDateOrContractMonth

    if not force_refresh:
        cached = load(symbol, timeframe, contract_month)
        if cached is not None:
            return cached, _cache_path(symbol, timeframe, contract_month), True

    df = connector.fetch_historical_bars(
        contract, duration=duration, bar_size=timeframe, **fetch_kwargs
    )
    path = save(symbol, timeframe, contract_month, df)
    return df, path, False
