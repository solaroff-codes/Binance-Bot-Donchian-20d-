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
    # 4001 = IB Gateway, live/regular account, Read-Only API enabled.
    # This connector only calls reqHistoricalData/reqContractDetails (no
    # order placement), and Read-Only API rejects order calls at the IBKR
    # level regardless, so this is safe to point at the live account.
    connector = IBKRConnector(port=4001)

    try:
        connector.connect()
    except Exception as exc:
        print(f"Could not connect to IB Gateway on {connector.host}:{connector.port} ({exc})")
        print()
        print("Checklist:")
        print("  1. IB Gateway is running and logged into your live/regular account")
        print("  2. Configure > Settings > API > Settings:")
        print("       - 'Enable ActiveX and Socket Clients' is checked")
        print("       - 'Read-Only API' is checked")
        print("       - Socket port is 4001")
        print("       - 127.0.0.1 is in 'Trusted IPs' (or accept the connection prompt in Gateway)")
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
