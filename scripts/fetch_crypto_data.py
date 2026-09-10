"""
Pull historical OHLCV data for BTC/ETH/SOL from Binance's public API
(data/binance_connector.py — no account/auth needed) and cache it via the
same continuous.parquet slot the futures continuous-contract pipeline uses
(data.cache.save_continuous) — crypto spot data has no rollover concept at
all, so it's naturally one continuous series, same as that slot already
represents for stitched futures.

Pulls 6 years of history by default (BTC/ETH have had this pair listed
since 2017; SOL since 2020-08, so its history is capped at whatever
actually exists rather than the full 6 years requested).

Run from the project root with the venv active:
    python scripts/fetch_crypto_data.py                # all 3 symbols, all 5 timeframes
    python scripts/fetch_crypto_data.py BTC ETH         # specific symbols
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from data.binance_connector import SYMBOL_MAP, fetch_full_history
from data.cache import save_continuous

TIMEFRAMES = ["1 month", "1 week", "1 day", "4 hours", "1 hour"]
YEARS_OF_HISTORY = 6


def main() -> None:
    symbols = sys.argv[1:] or list(SYMBOL_MAP.keys())
    start_date = (pd.Timestamp.now(tz="UTC") - pd.DateOffset(years=YEARS_OF_HISTORY)).strftime("%Y-%m-%d")

    for symbol in symbols:
        print(f"\n=== {symbol} ({SYMBOL_MAP[symbol]}) ===")
        for timeframe in TIMEFRAMES:
            try:
                df = fetch_full_history(symbol, timeframe, start_date=start_date)
            except Exception as exc:
                print(f"  [{timeframe}] FAILED: {exc}")
                continue
            if df.empty:
                print(f"  [{timeframe}] no data returned")
                continue
            path = save_continuous(symbol, timeframe, df)
            print(f"  [{timeframe}] {len(df)} bars, range {df['date'].min()} -> {df['date'].max()} -> {path}")


if __name__ == "__main__":
    main()
