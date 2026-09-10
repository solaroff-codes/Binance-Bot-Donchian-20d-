"""
Continuous futures series: stitches multiple historical contract months
(plus the current front month) together into one long, back-adjusted price
series, so backtests aren't limited to a single contract's own trading
life (~1-1.5 years for these instruments — see data/ibkr_connector.py and
scripts/fetch_all_instruments.py's docstring).

Roll rule: a fixed number of trading days before each contract's own
expiry — NOT the volume-crossover rule data/contracts.py uses for live
front-month resolution. True historical volume-crossover would mean
fetching both the outgoing and incoming contract's volume around every
past roll and comparing them day-by-day, for every roll, across years of
history — a lot more IBKR requests for a personal research project.
Fixed-days-before-expiry is a standard, well-understood simplification
used in practice. Documented here so it's a visible, deliberate choice,
not a hidden one.

Adjustment method: additive back-adjustment (the "Panama canal" method).
At each roll boundary, the price offset between the outgoing and incoming
contract on that date is computed and added to every earlier bar, so the
series has no artificial price jump at rolls. This preserves point
differences — what indicators.fibonacci and the stop/target math in
strategy/signals.py actually use — at the cost of the earliest segments'
absolute price levels no longer being the literal historical price.
Standard and expected for a back-adjusted continuous contract; NOT
suitable for anything that needs literal historical prices (e.g. "what was
gold actually worth on this date").

Real IBKR contract history depth varies a lot per contract (empirically:
anywhere from ~160 to ~500 daily bars for GC's expired contracts, not
simply proportional to contract age) — this module fetches whatever each
contract offers and uses it as-is rather than assuming a fixed depth.
"""

from __future__ import annotations

import datetime
import time
from dataclasses import dataclass

import pandas as pd
from ib_async import IB, Contract, Future

from data.contracts import load_instruments, resolve_front_month
from data.ibkr_connector import IBKRConnector


def _as_date(value) -> datetime.date:
    """Normalize a date/Timestamp/date-string (tz-aware or not) to a plain date."""
    return pd.Timestamp(value).date()


def list_expired_contracts(ib: IB, symbol: str) -> list[Contract]:
    """Every already-expired contract IBKR still has details for, oldest first."""
    instruments = load_instruments()
    if symbol not in instruments:
        raise KeyError(f"Unknown instrument '{symbol}' — add it to config/instruments.yaml")
    spec = instruments[symbol]

    stub = Future(
        symbol=symbol, exchange=spec["exchange"], currency=spec["currency"], includeExpired=True
    )
    details = ib.reqContractDetails(stub)
    if not details:
        raise RuntimeError(f"IBKR returned no contract details for {symbol} (includeExpired=True)")

    today = datetime.date.today().strftime("%Y%m%d")
    expired = [d.contract for d in details if d.contract.lastTradeDateOrContractMonth < today]
    return sorted(expired, key=lambda c: c.lastTradeDateOrContractMonth)


def fetch_contract_series(
    connector: IBKRConnector,
    contract: Contract,
    bar_size: str,
    duration: str = "2 Y",
) -> pd.DataFrame:
    """
    Fetch historical bars for a specific contract (expired or current),
    anchored at its own last trade date so an expired contract returns its
    actual trading history instead of an empty "now" window.
    """
    end_dt = f"{contract.lastTradeDateOrContractMonth} 23:59:59"
    return connector.fetch_historical_bars(
        contract, duration=duration, bar_size=bar_size, end_datetime=end_dt
    )


@dataclass(frozen=True)
class ContractSegment:
    contract: Contract
    df: pd.DataFrame  # that contract's own full fetched history


def build_continuous_series(
    segments: list[ContractSegment],
    roll_days_before_expiry: int = 5,
    price_cols: tuple[str, ...] = ("open", "high", "low", "close"),
    timestamp_col: str = "date",
) -> pd.DataFrame:
    """
    Stitch ContractSegments (any order — sorted here by expiry) into one
    continuous, back-adjusted series.

    Each contract but the newest contributes bars up to
    `roll_days_before_expiry` trading days before its own expiry; after
    that the next contract's series takes over. The newest contract's data
    is used unadjusted; every older segment is shifted by the cumulative
    offset needed to remove the price gap at each roll it precedes.
    """
    if not segments:
        return pd.DataFrame()

    segments = sorted(segments, key=lambda s: s.contract.lastTradeDateOrContractMonth)

    roll_dates: list[datetime.date | None] = []
    for seg in segments[:-1]:
        dates = seg.df[timestamp_col].map(_as_date)
        expiry = _as_date(seg.contract.lastTradeDateOrContractMonth)
        before_or_on_expiry = seg.df[dates <= expiry]
        if before_or_on_expiry.empty:
            roll_dates.append(None)
        elif len(before_or_on_expiry) <= roll_days_before_expiry:
            roll_dates.append(_as_date(before_or_on_expiry[timestamp_col].iloc[0]))
        else:
            roll_dates.append(
                _as_date(before_or_on_expiry[timestamp_col].iloc[-1 - roll_days_before_expiry])
            )

    # Walk backward from the newest (unadjusted) contract, accumulating
    # the additive offset needed at each roll.
    adjustments = [0.0] * len(segments)
    cumulative = 0.0
    for i in range(len(segments) - 2, -1, -1):
        roll_date = roll_dates[i]
        if roll_date is None:
            adjustments[i] = cumulative
            continue
        older_price = _price_at_or_before(segments[i].df, roll_date, timestamp_col)
        newer_price = _price_at_or_before(segments[i + 1].df, roll_date, timestamp_col)
        if older_price is None or newer_price is None:
            print(
                f"  warning: no overlapping price for {segments[i].contract.localSymbol} -> "
                f"{segments[i + 1].contract.localSymbol} roll near {roll_date}; "
                "joining unadjusted at this roll"
            )
        else:
            cumulative += newer_price - older_price
        adjustments[i] = cumulative

    pieces = []
    for i, seg in enumerate(segments):
        df = seg.df.copy()
        dates = df[timestamp_col].map(_as_date)

        if i < len(segments) - 1 and roll_dates[i] is not None:
            df = df[dates <= roll_dates[i]]
            dates = dates[dates <= roll_dates[i]]
        if i > 0 and roll_dates[i - 1] is not None:
            keep = dates > roll_dates[i - 1]
            df = df[keep]

        if adjustments[i] != 0.0:
            df = df.copy()
            for col in price_cols:
                if col in df.columns:
                    df[col] = df[col] + adjustments[i]

        if not df.empty:
            pieces.append(df)

    if not pieces:
        return pd.DataFrame()

    continuous = pd.concat(pieces, ignore_index=True)
    continuous = continuous.sort_values(timestamp_col).drop_duplicates(
        subset=[timestamp_col], keep="last"
    )
    return continuous.reset_index(drop=True)


def _price_at_or_before(
    df: pd.DataFrame, date: datetime.date, timestamp_col: str, price_col: str = "close"
) -> float | None:
    dates = df[timestamp_col].map(_as_date)
    candidates = df[dates <= date]
    if candidates.empty:
        return None
    return candidates.iloc[-1][price_col]


def build_and_cache_continuous(
    connector: IBKRConnector,
    symbol: str,
    bar_size: str,
    duration: str = "2 Y",
    roll_days_before_expiry: int = 5,
    request_delay_seconds: float = 0.5,
) -> pd.DataFrame:
    """
    Fetch every expired contract plus the current front month for `symbol`,
    stitch them into a continuous back-adjusted series, cache it to
    data/cache/{symbol}/{timeframe}/continuous.parquet, and return it.

    request_delay_seconds throttles between per-contract IBKR requests —
    this can be a dozen-plus sequential historical-data calls for one
    symbol/timeframe, and IBKR paces historical data requests.
    """
    from data import cache as cache_module  # local import: avoids a cache<->continuous cycle

    expired = list_expired_contracts(connector.ib, symbol)
    current = resolve_front_month(connector.ib, symbol)
    all_contracts = expired + [current]

    segments = []
    for contract in all_contracts:
        try:
            df = fetch_contract_series(connector, contract, bar_size, duration)
        except Exception as exc:
            print(f"  {contract.localSymbol}: fetch failed ({exc}), skipping")
            time.sleep(request_delay_seconds)
            continue
        if df is None or df.empty:
            print(f"  {contract.localSymbol}: no data returned, skipping")
        else:
            print(f"  {contract.localSymbol}: {len(df)} bars")
            segments.append(ContractSegment(contract=contract, df=df))
        time.sleep(request_delay_seconds)

    continuous = build_continuous_series(segments, roll_days_before_expiry=roll_days_before_expiry)

    timeframe_slug = bar_size.replace(" ", "_")
    path = cache_module.CACHE_ROOT / symbol / timeframe_slug / "continuous.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    continuous.to_parquet(path, index=False)
    return continuous
