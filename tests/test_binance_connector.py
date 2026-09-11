from data.binance_connector import INTERVAL_MAP, SYMBOL_MAP, load_crypto_instruments


def test_load_crypto_instruments_has_expected_fields():
    instruments = load_crypto_instruments()
    assert set(instruments.keys()) == set(SYMBOL_MAP.keys())
    for symbol, spec in instruments.items():
        assert spec["multiplier"] == 1
        assert spec["tick_size"] > 0
        assert 0 < spec["micro_multiplier"] < 1  # fractional lot size, not a whole-contract count
        assert spec["commission_pct"] > 0


def test_symbol_and_interval_maps_are_non_empty_and_distinct():
    assert len(SYMBOL_MAP) == 5
    assert len(set(SYMBOL_MAP.values())) == 5  # no two short names map to the same Binance pair
    assert len(INTERVAL_MAP) == 5
    assert len(set(INTERVAL_MAP.values())) == 5
