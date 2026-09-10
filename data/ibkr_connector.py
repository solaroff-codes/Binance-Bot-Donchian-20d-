"""
Thin wrapper around ib_async's IB client for connecting to TWS / IB Gateway
and pulling historical OHLCV bars.

Default port (7497) is TWS's paper-trading socket port. IB Gateway's paper
port is 4002 instead — pass port=4002 if you're running Gateway rather than
TWS. Live-account ports are 7496 (TWS) / 4001 (Gateway); this project should
only ever point at paper ports during development.
"""

from __future__ import annotations

import pandas as pd
from ib_async import IB, Contract, util


class IBKRConnector:
    """Manages a single IBKR API connection and fetches historical bars."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()

    def connect(self) -> IB:
        self.ib.connect(self.host, self.port, clientId=self.client_id)
        return self.ib

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def __enter__(self) -> "IBKRConnector":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    def fetch_historical_bars(
        self,
        contract: Contract,
        duration: str = "1 Y",
        bar_size: str = "1 day",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
        end_datetime: str = "",
    ) -> pd.DataFrame:
        """
        Pull historical OHLCV bars for a qualified contract.

        duration: IBKR duration string, e.g. "1 Y", "30 D", "6 M".
        bar_size: IBKR bar size string, e.g. "1 day", "1 hour", "5 mins".
        end_datetime: "" means "now" (most recent available bar).
        """
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime=end_datetime,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
        )
        return util.df(bars)
