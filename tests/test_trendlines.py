import pandas as pd

from indicators.trendlines import (
    TrendLine,
    detect_trendline_events,
    find_steepest_unbroken_trendline,
    walk_forward_trendlines,
)


def _flat_bars(values: list[float]) -> pd.DataFrame:
    # Flat OHLC bars (open=high=low=close) keep the swing-fractal and
    # trendline-violation math easy to hand-verify.
    dates = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return pd.DataFrame(
        {"date": dates, "open": values, "high": values, "low": values, "close": values}
    )


def test_picks_steepest_valid_line_and_rejects_one_a_pivot_pokes_through():
    # Three swing lows at idx4 (100), idx14 (106), idx24 (118).
    # Line 4->24 (slope 0.9) is invalidated because dip2 (106 @ idx14) sits
    # below it (the straight line there projects to 109). Line 4->14
    # (slope 0.6) and line 14->24 (slope 1.2) both stay valid throughout —
    # the steepest of those, 14->24, should be the one returned.
    values = (
        [150, 150]
        + [140, 130, 100, 130, 140]
        + [150] * 5
        + [140, 130, 106, 130, 140]
        + [150] * 5
        + [140, 130, 118, 130, 140]
        + [150, 150]
    )
    df = _flat_bars(values)

    line = find_steepest_unbroken_trendline(df, n=2, kind="support")

    assert line is not None
    assert (line.start_idx, line.end_idx) == (14, 24)
    assert (line.start_price, line.end_price) == (106, 118)
    assert line.slope == 1.2


def test_walk_forward_trendlines_is_point_in_time_correct():
    # Same three-pivot setup as the steepest-line test above.
    values = (
        [150, 150]
        + [140, 130, 100, 130, 140]
        + [150] * 5
        + [140, 130, 106, 130, 140]
        + [150] * 5
        + [140, 130, 118, 130, 140]
        + [150, 150]
    )
    df = _flat_bars(values)

    lines = walk_forward_trendlines(df, n=2, kind="support")
    assert len(lines) == len(df)

    # Before the 2nd pivot (idx14) confirms, only 1 pivot exists — no line yet.
    assert lines[13] is None

    # At idx14, the 2nd pivot just confirmed: only line 4->14 (slope 0.6)
    # is a candidate so far — it must be the one in use.
    assert (lines[14].start_idx, lines[14].end_idx) == (4, 14)
    assert lines[14].slope == 0.6

    # No new pivot and no violation between pivots 2 and 3 — same line
    # object's values should still be in effect mid-way through that gap.
    assert (lines[20].start_idx, lines[20].end_idx) == (4, 14)
    assert lines[20].slope == 0.6

    # At the final bar, this must match find_steepest_unbroken_trendline's
    # own default (as_of_idx = last bar) — the steepest valid line, 14->24.
    final = find_steepest_unbroken_trendline(df, n=2, kind="support")
    assert (lines[-1].start_idx, lines[-1].end_idx) == (final.start_idx, final.end_idx)
    assert lines[-1].slope == final.slope


def test_returns_none_with_fewer_than_two_pivots():
    values = [100, 99, 98, 97, 96, 97, 98, 99, 100]  # single dip, one swing low
    df = _flat_bars(values)
    assert find_steepest_unbroken_trendline(df, n=2, kind="support") is None


def test_detect_trendline_events_bounce_then_break():
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    high = [150] * 6 + [113, 116, 116, 201]
    low = [150] * 6 + [112.0, 115, 109, 199]
    close = [150] * 6 + [113, 116, 110, 200]
    df = pd.DataFrame({"date": dates, "high": high, "low": low, "close": close})

    line = TrendLine(
        kind="support",
        start_idx=0,
        end_idx=5,
        start_price=100,
        end_price=110,
        start_ts=dates[0],
        end_ts=dates[5],
        slope=2.0,  # price_at(idx) = 100 + 2*idx
    )

    events = detect_trendline_events(df, line)

    assert len(events) == 2
    bounce, break_ = events
    assert bounce.idx == 7 and bounce.event_type == "bounce" and bounce.direction == "long"
    assert break_.idx == 8 and break_.event_type == "break" and break_.direction == "short"


def test_detect_trendline_events_resistance_mirrors_support():
    dates = pd.date_range("2026-01-01", periods=9, freq="D")
    # Resistance line: price_at(idx) = 100 - 2*idx (descending), anchored 0-5.
    # price_at(6)=88, price_at(7)=86, price_at(8)=84
    high = [50] * 6 + [87.9, 85, 91]
    low = [50] * 6 + [86, 83, 89]
    close = [50] * 6 + [87, 84, 90]
    df = pd.DataFrame({"date": dates, "high": high, "low": low, "close": close})

    line = TrendLine(
        kind="resistance",
        start_idx=0,
        end_idx=5,
        start_price=100,
        end_price=90,
        start_ts=dates[0],
        end_ts=dates[5],
        slope=-2.0,
    )
    # price_at(6)=88, price_at(7)=86, price_at(8)=84

    events = detect_trendline_events(df, line)

    assert len(events) == 2
    bounce, break_ = events
    assert bounce.idx == 7 and bounce.event_type == "bounce" and bounce.direction == "short"
    assert break_.idx == 8 and break_.event_type == "break" and break_.direction == "long"
