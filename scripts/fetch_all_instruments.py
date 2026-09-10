"""
Pull and cache historical bars for every instrument in config/instruments.yaml
(GC, CL, ES, NQ): resolves each front-month contract, fetches daily and
1-hour bars, and writes them to the parquet cache.

Unlike scripts/test_connector.py (a single-symbol connectivity smoke test),
this is the general-purpose "populate the cache" utility — run it whenever
you want the local cache refreshed/extended across all configured
instruments. One symbol failing (e.g. missing market data permissions) does
not stop the others.

Run from the project root with the venv active:
    python scripts/fetch_all_instruments.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import cache
from data.contracts import load_instruments, resolve_front_month
from data.ibkr_connector import IBKRConnector

# (timeframe, duration) pairs pulled for each instrument.
TIMEFRAMES = [
    ("1 day", "1 Y"),
    ("1 hour", "30 D"),
]


def fetch_symbol(connector: IBKRConnector, symbol: str) -> None:
    contract = resolve_front_month(connector.ib, symbol)
    print(
        f"\n=== {symbol}: front-month {contract.localSymbol} "
        f"(expiry {contract.lastTradeDateOrContractMonth}) ==="
    )

    for timeframe, duration in TIMEFRAMES:
        df, path, from_cache = cache.fetch_or_load(
            connector, symbol, contract, timeframe, duration
        )
        source = "local cache" if from_cache else "IBKR (freshly pulled, now cached)"
        print(f"  [{timeframe}] {len(df)} bars, source: {source} -> {path}")


def main() -> None:
    symbols = list(load_instruments().keys())

    # 4001 = IB Gateway, live/regular account, Read-Only API enabled.
    connector = IBKRConnector(port=4001)

    try:
        connector.connect()
    except Exception as exc:
        print(f"Could not connect to IB Gateway on {connector.host}:{connector.port} ({exc})")
        sys.exit(1)

    try:
        for symbol in symbols:
            try:
                fetch_symbol(connector, symbol)
            except Exception as exc:
                print(f"\n=== {symbol}: FAILED — {exc} ===")
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
