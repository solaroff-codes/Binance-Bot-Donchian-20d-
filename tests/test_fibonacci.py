from indicators.fibonacci import extension_levels, fib_levels, retracement_levels


def test_retracement_levels_up_move():
    levels = retracement_levels(100, 200)
    assert levels[0.0] == 200
    assert levels[1.0] == 100
    assert levels[0.5] == 150
    assert round(levels[0.618], 1) == 138.2


def test_retracement_levels_down_move():
    levels = retracement_levels(200, 100)
    assert levels[0.0] == 100
    assert levels[1.0] == 200
    assert levels[0.5] == 150


def test_extension_levels_project_beyond_end_price():
    levels = extension_levels(100, 200)
    assert levels[1.272] == 100 + 100 * 1.272
    assert round(levels[1.618], 1) == 261.8
    assert levels[2.0] == 300


def test_fib_levels_combines_retracements_and_extensions():
    levels = fib_levels(100, 200)
    assert 0.618 in levels
    assert 1.618 in levels
    assert len(levels) == 10
