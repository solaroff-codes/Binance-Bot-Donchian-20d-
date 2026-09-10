from backtest.sweep import generate_grid
from strategy.config import StrategyConfig


def _base_config() -> StrategyConfig:
    return StrategyConfig(symbol="GC", timeframe="1 day")


def test_generate_grid_produces_cartesian_product():
    configs = generate_grid(_base_config(), range_max_pct=[0.03, 0.05], range_min_bars=[5, 10])
    assert len(configs) == 4
    pairs = {(c.range_max_pct, c.range_min_bars) for c in configs}
    assert pairs == {(0.03, 5), (0.03, 10), (0.05, 5), (0.05, 10)}


def test_generate_grid_preserves_other_base_fields():
    configs = generate_grid(_base_config(), range_max_pct=[0.03, 0.05])
    for c in configs:
        assert c.symbol == "GC"
        assert c.timeframe == "1 day"
        assert c.fractal_n == 2  # untouched default


def test_generate_grid_single_param_single_value():
    configs = generate_grid(_base_config(), range_max_pct=[0.05])
    assert len(configs) == 1
    assert configs[0].range_max_pct == 0.05


def test_generate_grid_empty_param_grids_returns_base_only():
    configs = generate_grid(_base_config())
    assert len(configs) == 1
    assert configs[0] == _base_config()
