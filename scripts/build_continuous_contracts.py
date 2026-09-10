"""
Build back-adjusted continuous contract series (see data/continuous.py)
for one or more instruments/timeframes, caching each to
data/cache/{symbol}/{timeframe}/continuous.parquet.

This makes a lot of sequential IBKR historical-data requests (one per
expired contract still on record, plus the current front month — roughly
a dozen per symbol/timeframe for GC). Expect it to take a few minutes per
symbol/timeframe combination, not seconds.

Usage:
    python scripts/build_continuous_contracts.py                       # all instruments, daily + 1hour
    python scripts/build_continuous_contracts.py GC                    # one instrument, both timeframes
    python scripts/build_continuous_contracts.py GC --timeframe "1 day"  # one instrument, one timeframe
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.continuous import build_and_cache_continuous
from data.contracts import load_instruments
from data.ibkr_connector import IBKRConnector

TIMEFRAMES = ["1 day", "1 hour"]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--timeframe")]
    timeframe_arg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--timeframe=")), None)

    symbols = args if args else list(load_instruments().keys())
    timeframes = [timeframe_arg] if timeframe_arg else TIMEFRAMES

    # 4001 = IB Gateway, live/regular account, Read-Only API enabled.
    connector = IBKRConnector(port=4001)
    try:
        connector.connect()
    except Exception as exc:
        print(f"Could not connect to IB Gateway on {connector.host}:{connector.port} ({exc})")
        sys.exit(1)

    try:
        for symbol in symbols:
            for timeframe in timeframes:
                print(f"\n=== {symbol} {timeframe} ===")
                try:
                    df = build_and_cache_continuous(connector, symbol, timeframe)
                    if df.empty:
                        print("  no data assembled")
                    else:
                        print(
                            f"  continuous series: {len(df)} bars, "
                            f"range {df['date'].min()} -> {df['date'].max()}"
                        )
                except Exception as exc:
                    print(f"  FAILED: {exc}")
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
