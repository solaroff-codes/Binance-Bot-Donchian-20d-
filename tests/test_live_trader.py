import pytest

from live.live_trader import compute_order_quantity, determine_daily_action


def test_compute_order_quantity_sizes_by_risk_and_rounds_to_step():
    # $1,000 equity, 3% risk = $30 budget; $5 risk-per-unit -> 6 units
    # raw, rounded down to the 0.5 step size -> 6.0 (already a multiple).
    qty = compute_order_quantity(equity=1_000.0, risk_pct=0.03, entry_price=100, stop_price=95, step_size=0.5)
    assert qty == pytest.approx(6.0)


def test_compute_order_quantity_rounds_down_to_step_size():
    # $1,000 * 3% = $30 / $5 risk = 6.0 raw units, but step_size=1.0 with
    # a slightly different risk distance forcing a non-exact result.
    qty = compute_order_quantity(equity=1_000.0, risk_pct=0.03, entry_price=100, stop_price=93, step_size=1.0)
    # 30 / 7 = 4.2857... -> floor to nearest 1.0 -> 4.0
    assert qty == pytest.approx(4.0)


def test_compute_order_quantity_zero_risk_distance_returns_zero():
    assert compute_order_quantity(equity=1_000.0, risk_pct=0.03, entry_price=100, stop_price=100, step_size=0.5) == 0.0


def test_determine_daily_action_hold_when_exchange_shows_position():
    action = determine_daily_action(position={"positionAmt": "1.0"}, latest_signal=None, symbol_state={"open_trade": {"x": 1}})
    assert action == "HOLD"


def test_determine_daily_action_reconcile_closed_when_local_open_but_exchange_flat():
    action = determine_daily_action(position=None, latest_signal=None, symbol_state={"open_trade": {"x": 1}})
    assert action == "RECONCILE_CLOSED"


def test_determine_daily_action_open_new_when_flat_with_fresh_signal():
    action = determine_daily_action(position=None, latest_signal=object(), symbol_state={"open_trade": None})
    assert action == "OPEN_NEW"


def test_determine_daily_action_stay_flat_when_nothing_going_on():
    action = determine_daily_action(position=None, latest_signal=None, symbol_state={"open_trade": None})
    assert action == "STAY_FLAT"
