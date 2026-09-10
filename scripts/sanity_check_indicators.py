"""
Quick sanity check: run the swing/fibonacci/wyckoff indicators against real
cached GC daily data (not synthetic test fixtures) and print what they find.
Not a formal test — just a manual eyeball check that the modules behave
sensibly on real market data.

Run from the project root with the venv active:
    python scripts/sanity_check_indicators.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from indicators.fibonacci import fib_levels
from indicators.swings import alternate_swings, get_swing_points
from indicators.wyckoff import detect_trading_ranges, label_bar_state

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "GC" / "1_day"


def _latest_cache_file() -> Path:
    files = sorted(CACHE_DIR.glob("*.parquet"))
    if not files:
        print(f"No cached data in {CACHE_DIR} — run scripts/fetch_all_instruments.py first.")
        sys.exit(1)
    return files[-1]  # filenames are contract expiry (YYYYMMDD) — latest-named is most recent roll


def main() -> None:
    cache_file = _latest_cache_file()
    df = pd.read_parquet(cache_file)
    print(f"Loaded {len(df)} daily GC bars from {cache_file.name}")

    raw_points = get_swing_points(df, n=2)
    points = alternate_swings(raw_points)
    print(f"\nSwing points: {len(raw_points)} raw fractals -> {len(points)} alternating")
    for p in points[-6:]:
        print(f"  {p.timestamp}  {p.kind:>4}  {p.price:.1f}")

    if len(points) >= 2:
        a, b = points[-2], points[-1]
        levels = fib_levels(a.price, b.price)
        print(f"\nFib levels for last swing ({a.kind} {a.price:.1f} @ {a.timestamp} -> {b.kind} {b.price:.1f} @ {b.timestamp}):")
        for ratio, price in sorted(levels.items()):
            print(f"  {ratio:>6.3f}  {price:.1f}")

    ranges = detect_trading_ranges(df, window=10, max_range_pct=0.04, min_bars=8)
    print(f"\nTrading ranges detected: {len(ranges)}")
    for r in ranges:
        print(
            f"  {r.start_ts} -> {r.end_ts}  "
            f"support={r.support:.1f}  resistance={r.resistance:.1f}  width={r.width_pct:.1%}"
        )

    states = label_bar_state(df, ranges)
    print("\nLast 5 bars' state relative to most recent completed range:")
    print(pd.DataFrame({"date": df["date"], "close": df["close"], "state": states}).tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
