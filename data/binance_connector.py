"""
Historical OHLCV data from Binance's public REST API (no authentication
required — this is public market data, not account access). Used for
crypto (BTC/ETH/SOL), which is out of scope for the IBKR connector
(data/ibkr_connector.py) — crypto is spot trading here, not a futures
contract with an expiry, so there's no "front month" or rollover concept
at all; see data/contracts.py for that machinery, which does not apply.

fetch_klines() pages through Binance's 1000-candle-per-request limit to
assemble a full history in one call. Output columns match the same
schema the rest of this project already uses for IBKR data (date, open,
high, low, close, volume) so every existing indicator/strategy/backtest
function works unchanged — 'date' is UTC-aware for intraday bar sizes
(4 hours, 1 hour) and a plain date for daily/weekly/monthly, mirroring
the same daily-vs-intraday distinction IBKR data already has (see
indicators/README.md's data-shape note).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd
import yaml

BASE_URL = "https://api.binance.com/api/v3/klines"
CRYPTO_INSTRUMENTS_PATH = Path(__file__).resolve().parent.parent / "config" / "crypto_instruments.yaml"

# This project's timeframe strings -> Binance's interval strings.
INTERVAL_MAP = {
    "1 month": "1M",
    "1 week": "1w",
    "1 day": "1d",
    "4 hours": "4h",
    "1 hour": "1h",
}

# This project's short symbol -> Binance's USDT trading pair. BNB and XRP
# added first, then LTC/ADA/DOGE/LINK/TRX -- all chosen the same way,
# as additional, long-history (listed on Binance since well before 2020)
# large-cap coins to test the already-validated fixed Donchian rule
# against, decided for history length before looking at any result -- not
# picked after the fact for looking good.
SYMBOL_MAP = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT",
    "LTC": "LTCUSDT",
    "ADA": "ADAUSDT",
    "DOGE": "DOGEUSDT",
    "LINK": "LINKUSDT",
    "TRX": "TRXUSDT",
}

_DAILY_OR_LARGER = {"1 month", "1 week", "1 day"}


def _request_klines(binance_symbol: str, interval: str, start_ms: int, limit: int = 1000) -> list:
    params = {
        "symbol": binance_symbol,
        "interval": interval,
        "startTime": start_ms,
        "limit": limit,
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)


def fetch_full_history(
    symbol: str,
    timeframe: str,
    start_date: str,
    request_delay_seconds: float = 0.3,
) -> pd.DataFrame:
    """
    Page through Binance's public klines endpoint from start_date to now,
    for `symbol` (this project's short name, e.g. "BTC") and `timeframe`
    (this project's timeframe string, e.g. "1 hour"). Returns a DataFrame
    with columns: date, open, high, low, close, volume.
    """
    if symbol not in SYMBOL_MAP:
        raise KeyError(f"Unknown crypto symbol '{symbol}' — add it to data/binance_connector.SYMBOL_MAP")
    if timeframe not in INTERVAL_MAP:
        raise KeyError(f"Unsupported timeframe '{timeframe}' — add it to data/binance_connector.INTERVAL_MAP")

    binance_symbol = SYMBOL_MAP[symbol]
    interval = INTERVAL_MAP[timeframe]

    start_ms = int(pd.Timestamp(start_date, tz="UTC").timestamp() * 1000)
    now_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)

    rows: list[list] = []
    cursor = start_ms
    while cursor < now_ms:
        batch = _request_klines(binance_symbol, interval, cursor)
        if not batch:
            break
        rows.extend(batch)
        last_open_ms = batch[-1][0]
        next_cursor = last_open_ms + 1
        if next_cursor <= cursor:
            break  # safety: guarantee forward progress
        cursor = next_cursor
        if len(batch) < 1000:
            break  # fewer than a full page = caught up to the present
        time.sleep(request_delay_seconds)

    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(
        rows,
        columns=[
            "open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "num_trades", "taker_buy_base", "taker_buy_quote", "ignore",
        ],
    )
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    timestamps = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    if timeframe in _DAILY_OR_LARGER:
        df["date"] = timestamps.dt.date
    else:
        df["date"] = timestamps

    df = df[["date", "open", "high", "low", "close", "volume"]]
    df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def load_crypto_instruments() -> dict:
    """Load config/crypto_instruments.yaml — multiplier/tick_size/
    micro_multiplier/commission_pct per symbol, the crypto analog of
    data.contracts.load_instruments() for IBKR futures."""
    with open(CRYPTO_INSTRUMENTS_PATH, "r") as f:
        return yaml.safe_load(f)
