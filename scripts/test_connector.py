"""
Manual smoke test for the IBKR connector: connects to TWS/IB Gateway,
resolves the GC (gold) front-month contract, pulls daily and 1-hour bars,
caches them to parquet, and prints a summary.

Run from the project root with the venv active:
    python scripts/test_connector.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import cache
from data.contracts import resolve_front_month
from data.ibkr_connector import IBKRConnector

SYMBOL = "GC"

# (timeframe, duration) pairs to pull for the smoke test.
TIMEFRAMES = [
    ("1 day", "1 Y"),
    ("1 hour", "30 D"),
]


def main() -> None:
    # 7497 = TWS paper port. Use 4002 if you're running IB Gateway instead.
    connector = IBKRConnector(port=7497)

    try:
        connector.connect()
    except Exception as exc:
        print(f"Could not connect to TWS/IB Gateway on {connector.host}:{connector.port} ({exc})")
        print()
        print("Checklist:")
        print("  1. TWS or IB Gateway is running and logged into the PAPER account")
        print("  2. File > Global Configuration > API > Settings:")
        print("       - 'Enable ActiveX and Socket Clients' is checked")
        print("       - Socket port matches (7497 for TWS paper, 4002 for IB Gateway paper)")
        print("       - 127.0.0.1 is in 'Trusted IPs' (or disable the read-only prompt)")
        sys.exit(1)

    try:
        contract = resolve_front_month(connector.ib, SYMBOL)
        print(
            f"Front-month contract resolved: {contract.localSymbol} "
            f"(expiry {contract.lastTradeDateOrContractMonth})"
        )

        for timeframe, duration in TIMEFRAMES:
            df, path, from_cache = cache.fetch_or_load(
                connector, SYMBOL, contract, timeframe, duration
            )
            source = "local cache" if from_cache else "IBKR (freshly pulled, now cached)"
            print(f"\n[{SYMBOL} {timeframe}] {len(df)} bars, source: {source}")
            print(f"  cache file: {path}")
            print(df.head(3).to_string(index=False))
            print("  ...")
            print(df.tail(3).to_string(index=False))
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
