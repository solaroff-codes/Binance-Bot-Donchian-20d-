from datetime import timedelta

import pandas as pd

from paper.paper_trader import paper_trades_since


def _breakout_df() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=25, freq="D")
    flat = [100] * 20
    # A single breakout bar, then an immediate pullback that stays below
    # the now-higher rolling 20-bar high -- deliberately just one
    # qualifying signal, not a cascade of re-breakouts, so this test's
    # trade count is unambiguous.
    closes = flat + [105, 104, 103, 102, 101]
    return pd.DataFrame(
        {"date": dates, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes}
    )


def test_paper_trades_since_excludes_signals_before_start_date():
    df = _breakout_df()
    # The breakout signal fires on the 21st bar (index 20). A start date
    # on or before that day includes it; the day after excludes it -- the
    # paper account should only ever see signals from its own start
    # forward, never "inherit" a trade from before paper trading began.
    entry_date = df["date"].iloc[20].date()

    included = paper_trades_since(df, paper_start_date=entry_date)
    assert len(included) == 1
    assert included[0].direction == "long"

    excluded = paper_trades_since(df, paper_start_date=entry_date + timedelta(days=1))
    assert len(excluded) == 0


def test_paper_trades_since_returns_empty_list_with_no_qualifying_signals():
    df = _breakout_df()
    far_future = df["date"].iloc[-1].date() + timedelta(days=365)
    assert paper_trades_since(df, paper_start_date=far_future) == []
