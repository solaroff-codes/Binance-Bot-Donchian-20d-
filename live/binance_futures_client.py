"""
Thin wrapper around python-binance's futures_* methods -- the
authenticated order-placement layer live_trader.py uses. Kept separate
from data/binance_connector.py deliberately: that module is public,
unauthenticated market-data only (plain urllib, no signing needed, used
for signal generation everywhere in this project); this module places
real orders and needs API credentials plus HMAC request signing, which
python-binance already implements correctly -- reused rather than
hand-rolled, since request signing is exactly the wrong place to
introduce a bug in trading code.

Reads credentials from the environment (BINANCE_TESTNET_API_KEY /
BINANCE_TESTNET_API_SECRET) rather than accepting them as constructor
arguments a caller might log or persist by accident. Never pass real
key/secret values as literals anywhere in this codebase.

testnet=True (the only mode built so far -- see live/README.md) points
at Binance's sandboxed Futures Testnet
(https://testnet.binancefuture.com/fapi) rather than the live exchange.
Checked directly rather than assumed: python-binance 1.0.37's own
testnet=True constructor flag does NOT redirect FUTURES_URL (it leaves
it at the live https://fapi.binance.com/fapi) -- FUTURES_URL is
overridden explicitly here instead of trusting that flag to cover
futures endpoints.
"""

from __future__ import annotations

import math
import os

from binance.client import Client

FUTURES_TESTNET_URL = "https://testnet.binancefuture.com/fapi"
MARGIN_TYPE_UNCHANGED_ERROR_CODE = "-4046"  # "No need to change margin type" -- already isolated, not a real error


def round_step(value: float, step: float) -> float:
    """Round down to the nearest multiple of step (Binance rejects order
    quantities/prices that aren't an exact multiple of the symbol's
    LOT_SIZE/PRICE_FILTER step -- rounding DOWN on quantity never asks
    for more than intended; rounding stop/target prices uses the same
    helper for consistency, at the cost of a sub-tick difference from
    the theoretical level, negligible in practice)."""
    if step <= 0:
        return value
    return math.floor(value / step) * step


class BinanceFuturesClient:
    def __init__(self, testnet: bool = True):
        api_key = os.environ.get("BINANCE_TESTNET_API_KEY")
        api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError(
                "BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET are not set in "
                "the environment. See live/README.md for where these come from (a "
                "local .env for testing, GitHub Actions repository secrets for the "
                "scheduled run) -- never hardcode these or pass them as literals."
            )
        # ping=False: python-binance's Client pings the LIVE mainnet spot
        # API (api.binance.com) during __init__ by default, before there's
        # any chance to redirect FUTURES_URL below -- this project never
        # calls that spot endpoint at all (only futures_* methods, against
        # whichever FUTURES_URL is set), so the startup ping is both
        # unnecessary and, in practice, the first thing to fail if the
        # network path to Binance's live API is restricted (e.g. some
        # cloud-hosted IP ranges, GitHub Actions runners included --
        # confirmed directly, not theoretical: this raised
        # BinanceAPIException "Service unavailable from a restricted
        # location" on the first real run, from the ping, not from any
        # futures call).
        self.client = Client(api_key, api_secret, testnet=testnet, ping=False)
        if testnet:
            self.client.FUTURES_URL = FUTURES_TESTNET_URL
        self.testnet = testnet
        self._symbol_filters_cache: dict[str, dict] = {}

    def get_symbol_filters(self, symbol: str) -> dict:
        """{'step_size': ..., 'tick_size': ...} for the symbol, fetched
        once from futures_exchange_info() and cached for the life of this
        client -- needed to round order quantity/price to what Binance
        will actually accept."""
        if symbol in self._symbol_filters_cache:
            return self._symbol_filters_cache[symbol]
        info = self.client.futures_exchange_info()
        symbol_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
        filters = {f["filterType"]: f for f in symbol_info["filters"]}
        result = {
            "step_size": float(filters["LOT_SIZE"]["stepSize"]),
            "tick_size": float(filters["PRICE_FILTER"]["tickSize"]),
        }
        self._symbol_filters_cache[symbol] = result
        return result

    def get_position(self, symbol: str) -> dict | None:
        """Current open position for symbol (One-way mode assumed -- a
        single net position per symbol, not per-side hedge mode), or
        None if flat. Returns the raw Binance position dict
        (positionAmt, entryPrice, ...) -- positionAmt is signed
        (+ long, - short)."""
        positions = self.client.futures_position_information(symbol=symbol)
        for p in positions:
            if float(p["positionAmt"]) != 0:
                return p
        return None

    def get_open_orders(self, symbol: str) -> list[dict]:
        return self.client.futures_get_open_orders(symbol=symbol)

    def get_order(self, symbol: str, order_id: int) -> dict:
        return self.client.futures_get_order(symbol=symbol, orderId=order_id)

    def ensure_leverage_and_isolated_margin(self, symbol: str, leverage: int) -> None:
        self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        try:
            self.client.futures_change_margin_type(symbol=symbol, marginType="ISOLATED")
        except Exception as exc:
            if MARGIN_TYPE_UNCHANGED_ERROR_CODE not in str(exc):
                raise

    def place_market_entry(self, symbol: str, direction: str, quantity: float) -> dict:
        side = "BUY" if direction == "long" else "SELL"
        return self.client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=quantity)

    def place_stop_loss(self, symbol: str, direction: str, quantity: float, stop_price: float) -> dict:
        side = "SELL" if direction == "long" else "BUY"  # exit side is opposite the position's own direction
        return self.client.futures_create_order(
            symbol=symbol, side=side, type="STOP_MARKET",
            stopPrice=stop_price, quantity=quantity, reduceOnly=True,
        )

    def place_take_profit(self, symbol: str, direction: str, quantity: float, target_price: float) -> dict:
        side = "SELL" if direction == "long" else "BUY"
        return self.client.futures_create_order(
            symbol=symbol, side=side, type="TAKE_PROFIT_MARKET",
            stopPrice=target_price, quantity=quantity, reduceOnly=True,
        )

    def cancel_order(self, symbol: str, order_id: int) -> None:
        self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
