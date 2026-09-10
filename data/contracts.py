"""
Instrument definitions and futures front-month resolution.

Contracts are defined in config/instruments.yaml (symbol -> exchange/currency/
multiplier). resolve_front_month() implements the volume-crossover rule: of
the two nearest, non-expired contract months, whichever has the higher recent
traded volume is treated as the front month. This is the standard definition
of "front month" (liquidity has already shifted there) as opposed to a fixed
days-before-expiry cutoff.

Contracts returned here carry lastTradeDateOrContractMonth, which the cache
layer uses to key parquet files per contract-month. That means a later
"continuous contract" stitching step (needed for backtesting across rolls)
can be built on top of these per-contract files without changing this module.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from ib_async import IB, Contract, Future

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "instruments.yaml"


def load_instruments() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def get_contract_candidates(ib: IB, symbol: str) -> list[Contract]:
    """Return qualified, non-expired contracts for `symbol`, nearest expiry first."""
    instruments = load_instruments()
    if symbol not in instruments:
        raise KeyError(f"Unknown instrument '{symbol}' — add it to config/instruments.yaml")
    spec = instruments[symbol]

    stub = Future(symbol=symbol, exchange=spec["exchange"], currency=spec["currency"])
    details = ib.reqContractDetails(stub)
    if not details:
        raise RuntimeError(
            f"IBKR returned no contract details for {symbol} on {spec['exchange']}. "
            "Check the symbol/exchange in config/instruments.yaml and that TWS/Gateway "
            "has market data permissions for this product."
        )

    contracts = sorted(
        (d.contract for d in details),
        key=lambda c: c.lastTradeDateOrContractMonth,
    )
    return contracts


def resolve_front_month(ib: IB, symbol: str, volume_lookback_days: int = 5) -> Contract:
    """
    Pick the front-month contract for `symbol` using a volume-crossover rule:
    compare recent daily volume between the two nearest expiries and return
    whichever is more actively traded.
    """
    candidates = get_contract_candidates(ib, symbol)
    nearest_two = candidates[:2]

    if len(nearest_two) == 1:
        return nearest_two[0]

    volumes = {}
    for contract in nearest_two:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=f"{volume_lookback_days} D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        volumes[contract.conId] = sum(bar.volume for bar in bars) if bars else 0

    return max(nearest_two, key=lambda c: volumes.get(c.conId, 0))
