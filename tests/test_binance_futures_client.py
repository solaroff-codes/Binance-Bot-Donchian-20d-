import pytest

from live.binance_futures_client import round_step


def test_round_step_rounds_down_to_nearest_multiple():
    assert round_step(0.12345, 0.001) == pytest.approx(0.123)
    assert round_step(1.999, 0.5) == pytest.approx(1.5)


def test_round_step_exact_multiple_unchanged():
    assert round_step(2.0, 0.5) == pytest.approx(2.0)


def test_round_step_zero_step_returns_value_unchanged():
    assert round_step(3.14159, 0.0) == pytest.approx(3.14159)
