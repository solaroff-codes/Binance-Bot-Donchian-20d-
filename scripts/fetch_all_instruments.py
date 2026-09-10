"""
Pull and cache historical bars for every instrument in config/instruments.yaml
(GC, CL, ES, NQ): resolves each front-month contract, fetches monthly,
weekly, daily, 4-hour, and 1-hour bars, and writes them to the parquet cache.

Unlike scripts/test_connector.py (a single-symbol connectivity smoke test),
this is the general-purpose "populate the cache" utility — run it whenever
you want the local cache refreshed/extended across all configured
instruments. One symbol failing (e.g. missing market data permissions) does
not stop the others.

Pulls "2 Y" duration for both daily and 1-hour bars. In practice this
doesn't mean 2 years of real data — a single futures contract only has as
much history as it's actually been listed for (IBKR silently caps the
returned range to whatever exists rather than erroring), which for the
current front-month contracts works out to roughly 1.5 years of daily bars
and roughly 1 year of 1-hour bars. Getting more than that requires
stitching together multiple historical contract months into a continuous
series, which is intentionally not built yet (see data/cache.py's
contract-month keying, which was designed to support adding that later).

Run from the project root with the venv active:
    python scripts/fetch_all_instruments.py            # use cache if present
    python scripts/fetch_all_instruments.py --force     # re-pull from IBKR even if cached
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import cache
from data.contracts import load_instruments, resolve_front_month
from data.ibkr_connector import IBKRConnector

# (timeframe, duration) pairs pulled for each instrument. Duration is a
# request ceiling, not a guarantee — see module docstring. Added for the
# trendline cascade strategy (monthly -> weekly -> daily -> 4h -> 1h):
# 1 month/1 week/4 hours are all valid IBKR bar sizes, verified directly
# against a live connection before adding them here.
TIMEFRAMES = [
    ("1 month", "20 Y"),
    ("1 week", "15 Y"),
    ("1 day", "2 Y"),
    ("4 hours", "2 Y"),
    ("1 hour", "2 Y"),
]


def fetch_symbol(connector: IBKRConnector, symbol: str, force_refresh: bool) -> None:
    contract = resolve_front_month(connector.ib, symbol)
    print(
        f"\n=== {symbol}: front-month {contract.localSymbol} "
        f"(expiry {contract.lastTradeDateOrContractMonth}) ==="
    )

    for timeframe, duration in TIMEFRAMES:
        df, path, from_cache = cache.fetch_or_load(
            connector, symbol, contract, timeframe, duration, force_refresh=force_refresh
        )
        source = "local cache" if from_cache else "IBKR (freshly pulled, now cached)"
        date_range = f"{df['date'].min()} -> {df['date'].max()}" if len(df) else "empty"
        print(f"  [{timeframe}] {len(df)} bars, source: {source}, range {date_range} -> {path}")


def main() -> None:
    force_refresh = "--force" in sys.argv
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
                fetch_symbol(connector, symbol, force_refresh)
            except Exception as exc:
                print(f"\n=== {symbol}: FAILED — {exc} ===")
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
