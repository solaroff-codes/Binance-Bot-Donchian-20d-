"""
Build back-adjusted continuous contract series (see data/continuous.py)
for one or more instruments/timeframes, caching each to
data/cache/{symbol}/{timeframe}/continuous.parquet.

This makes a lot of sequential IBKR historical-data requests (one per
expired contract still on record, plus the current front month — roughly
a dozen per symbol/timeframe for GC). Expect it to take a few minutes per
symbol/timeframe combination, not seconds.

Usage:
    python scripts/build_continuous_contracts.py                         # all instruments, all timeframes
    python scripts/build_continuous_contracts.py GC                      # one instrument, all timeframes
    python scripts/build_continuous_contracts.py GC --timeframe="1 day"  # one instrument, one timeframe

Note on monthly/weekly depth: continuous stitching is still bounded by how
many expired contracts IBKR has on file (~1.5-3 years depending on
instrument — see data/continuous.py), regardless of bar size. A "monthly"
trendline built from this data reflects that same ~1.5-3 year window, not
decades of macro history — it's a longer-term swing trendline, not a true
multi-decade one.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.continuous import build_and_cache_continuous
from data.contracts import load_instruments
from data.ibkr_connector import IBKRConnector

# timeframe -> duration requested per historical contract (a ceiling, not
# a guarantee — see data/continuous.py's docstring).
TIMEFRAME_DURATIONS = {
    "1 month": "20 Y",
    "1 week": "15 Y",
    "1 day": "2 Y",
    "4 hours": "2 Y",
    "1 hour": "2 Y",
}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--timeframe")]
    timeframe_arg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--timeframe=")), None)

    symbols = args if args else list(load_instruments().keys())
    timeframes = [timeframe_arg] if timeframe_arg else list(TIMEFRAME_DURATIONS.keys())

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
                duration = TIMEFRAME_DURATIONS.get(timeframe, "2 Y")
                print(f"\n=== {symbol} {timeframe} ===")
                try:
                    df = build_and_cache_continuous(connector, symbol, timeframe, duration=duration)
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
