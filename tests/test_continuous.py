from types import SimpleNamespace

import pandas as pd

from data.continuous import ContractSegment, build_continuous_series


def _contract(local_symbol: str, expiry: str):
    # A lightweight stand-in for ib_async's Contract — build_continuous_series
    # only reads .localSymbol and .lastTradeDateOrContractMonth.
    return SimpleNamespace(localSymbol=local_symbol, lastTradeDateOrContractMonth=expiry)


def test_build_continuous_series_removes_roll_gap():
    # Contract A: Jan 1-31, close = 100 + i (i=0..30). Expires Feb 1.
    a_dates = pd.date_range("2026-01-01", periods=31, freq="D")
    a_close = [100 + i for i in range(31)]
    df_a = pd.DataFrame(
        {"date": a_dates, "open": a_close, "high": a_close, "low": a_close, "close": a_close}
    )
    seg_a = ContractSegment(contract=_contract("AF6", "20260201"), df=df_a)

    # Contract B: Jan 20 - Feb 28, close = 200 + i (i=0 at Jan 20) — a
    # different absolute price level (simulating contango), overlapping A.
    b_dates = pd.date_range("2026-01-20", periods=40, freq="D")
    b_close = [200 + i for i in range(40)]
    df_b = pd.DataFrame(
        {"date": b_dates, "open": b_close, "high": b_close, "low": b_close, "close": b_close}
    )
    seg_b = ContractSegment(contract=_contract("AG6", "20260301"), df=df_b)

    continuous = build_continuous_series([seg_a, seg_b], roll_days_before_expiry=5)

    # roll_days_before_expiry=5 on contract A (31 bars, all <= its own
    # 20260201 expiry): roll date = 6th-from-last bar = 2026-01-26.
    roll_date = pd.Timestamp("2026-01-26").date()

    dates_in_result = set(pd.to_datetime(continuous["date"]).dt.date)
    assert roll_date in dates_in_result
    # No duplicate dates across the stitched segments.
    assert len(continuous) == len(dates_in_result)

    # A's contribution to the roll date (raw close 125) must be shifted to
    # match B's raw close on that same date (206) — that's the whole point
    # of back-adjustment: no price jump at the seam.
    row_at_roll = continuous[pd.to_datetime(continuous["date"]).dt.date == roll_date].iloc[0]
    assert row_at_roll["close"] == 206  # 100+25 (raw A) + 81 (adjustment) == 206 (raw B)

    # The bar right after the roll comes from B, unadjusted.
    day_after = continuous[
        pd.to_datetime(continuous["date"]).dt.date == pd.Timestamp("2026-01-27").date()
    ].iloc[0]
    assert day_after["close"] == 207  # raw B close, no shift

    # The earliest bar (from A) should carry the same +81 adjustment.
    first_row = continuous.iloc[0]
    assert pd.to_datetime(first_row["date"]).date() == pd.Timestamp("2026-01-01").date()
    assert first_row["close"] == 181  # raw 100 + 81

    # The newest contract's own far-future bars are untouched.
    last_row = continuous.iloc[-1]
    assert pd.to_datetime(last_row["date"]).date() == pd.Timestamp("2026-02-28").date()
    assert last_row["close"] == 239  # raw B: 200 + 39, unadjusted


def test_build_continuous_series_single_segment_is_unadjusted():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    close = [50 + i for i in range(10)]
    df = pd.DataFrame(
        {"date": dates, "open": close, "high": close, "low": close, "close": close}
    )
    seg = ContractSegment(contract=_contract("AF6", "20260201"), df=df)

    continuous = build_continuous_series([seg])

    assert len(continuous) == 10
    assert continuous["close"].tolist() == close


def test_build_continuous_series_empty_input():
    assert build_continuous_series([]).empty
