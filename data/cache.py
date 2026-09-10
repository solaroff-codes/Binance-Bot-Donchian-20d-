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
