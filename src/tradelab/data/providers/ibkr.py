"""Historical bars from Interactive Brokers.

**This is the primary data source for this project**, and the reason is
alignment rather than convenience: the contract that returns a bar is the same
contract object that will carry an order. Any external vendor introduces a
symbol-mapping problem -- vendor "NOKIA" versus IBKR's Helsinki listing versus
the New York ADR -- and a mapping error means researching one instrument and
trading another, which looks correct at every step.

It also avoids paying a second vendor for data the account already entitles you
to, and it is the only source whose adjustment policy you can state precisely
rather than infer.

Three IBKR-specific constraints are handled here, because each produces a
confusing failure otherwise:

* **Pacing.** IBKR throttles historical requests (roughly 60 per 10 minutes,
  with tighter limits on identical requests). Exceeding it gets the connection
  dropped rather than an error, so requests are spaced deliberately.
* **Duration limits.** A single request cannot span an unlimited period; long
  histories must be chunked and stitched.
* **`whatToShow` semantics.** `TRADES` gives actual traded prices (with
  volume); `MIDPOINT` gives the quote mid and reports no volume. Research on
  MIDPOINT bars silently has no volume, which disables the capacity check.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd

from tradelab.core.enums import Venue
from tradelab.core.types import Instrument
from tradelab.data.providers.base import BarProvider, ProviderError
from tradelab.data.schema import Adjustment, normalise_bars

_BAR_SIZE_MAP = {
    "1 day": "1 day",
    "1d": "1 day",
    "1 hour": "1 hour",
    "1h": "1 hour",
    "30 mins": "30 mins",
    "15 mins": "15 mins",
    "5 mins": "5 mins",
    "1 min": "1 min",
}

# Conservative per-request spans. IBKR's published maxima are larger, but
# requesting near the limit is where pacing violations begin.
_MAX_DURATION_DAYS = {
    "1 day": 365 * 5,
    "1 hour": 30,
    "30 mins": 14,
    "15 mins": 10,
    "5 mins": 5,
    "1 min": 2,
}


class IbkrBarProvider(BarProvider):
    """Historical bars over an existing `ib_async` connection.

    Takes a live `IB` instance rather than creating one, so that data fetching
    and order execution share a single connection. IBKR limits concurrent
    client connections, and a second one competing for the same client id is a
    common and opaque failure.
    """

    name = "ibkr"
    adjustment = Adjustment.SPLIT_AND_DIVIDEND
    includes_delisted = False
    """IBKR serves data for contracts it can still resolve. Delisted names are
    generally NOT retrievable, so an IBKR-built universe is survivorship-biased.
    This is the provider's main weakness and the quality auditor will flag it."""

    def __init__(
        self,
        ib,
        *,
        use_rth: bool = True,
        what_to_show: str = "TRADES",
        pacing_seconds: float = 11.0,
        currency: str = "USD",
        venue: Venue = Venue.SMART,
    ) -> None:
        self.ib = ib
        self.use_rth = use_rth
        self.what_to_show = what_to_show
        self.pacing_seconds = pacing_seconds
        self.currency = currency
        self.venue = venue
        if what_to_show == "MIDPOINT":
            self.adjustment = Adjustment.UNKNOWN

    def fetch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        bar_size: str = "1 day",
    ) -> pd.DataFrame:
        size = _BAR_SIZE_MAP.get(bar_size.lower().strip())
        if size is None:
            raise ProviderError(
                f"unsupported bar size {bar_size!r}; supported: {sorted(set(_BAR_SIZE_MAP))}"
            )
        if start >= end:
            raise ProviderError(f"start {start} is not before end {end}")

        frames: list[pd.DataFrame] = []
        failures: list[str] = []
        for symbol in symbols:
            try:
                frames.append(self._fetch_one(symbol, start, end, size))
            except ProviderError as exc:
                failures.append(f"{symbol}: {exc}")

        if not frames:
            raise ProviderError("no symbols returned data. Failures:\n  " + "\n  ".join(failures))
        if failures:
            # Surface partial failure loudly. A universe silently missing names
            # is a survivorship problem introduced by the fetch itself.
            raise ProviderError(
                f"{len(failures)} of {len(symbols)} symbols failed; refusing to return "
                "a partial universe, because silently dropping names introduces "
                "survivorship bias at the fetch step:\n  " + "\n  ".join(failures)
            )
        return normalise_bars(pd.concat(frames, ignore_index=True))

    def _fetch_one(self, symbol: str, start: datetime, end: datetime, size: str) -> pd.DataFrame:
        contract = self._qualify(symbol)
        max_days = _MAX_DURATION_DAYS.get(size, 365)
        chunks: list[pd.DataFrame] = []
        cursor = end

        while cursor > start:
            span_days = min(max_days, max(1, (cursor - start).days))
            duration = f"{span_days} D"
            try:
                bars = self.ib.reqHistoricalData(
                    contract,
                    endDateTime=cursor,
                    durationStr=duration,
                    barSizeSetting=size,
                    whatToShow=self.what_to_show,
                    useRTH=self.use_rth,
                    formatDate=2,  # UTC epoch, avoiding local-timezone ambiguity
                )
            except Exception as exc:
                raise ProviderError(
                    f"reqHistoricalData failed for {symbol}: {exc}", retryable=True
                ) from exc

            if not bars:
                break
            chunk = pd.DataFrame(
                [
                    {
                        "timestamp": b.date,
                        "symbol": symbol.upper(),
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": max(b.volume, 0),
                    }
                    for b in bars
                ]
            )
            chunks.append(chunk)
            earliest = pd.to_datetime(chunk["timestamp"]).min()
            next_cursor = earliest.to_pydatetime() - timedelta(seconds=1)
            if next_cursor >= cursor:
                break  # no progress; stop rather than loop forever
            cursor = next_cursor
            time.sleep(self.pacing_seconds)

        if not chunks:
            raise ProviderError(f"IBKR returned no bars for {symbol}")

        frame = pd.concat(chunks, ignore_index=True)
        frame = frame.drop_duplicates(subset=["symbol", "timestamp"])
        return frame

    def _qualify(self, symbol: str):
        """Resolve a symbol, refusing ambiguity for the same reason as orders."""
        try:
            import ib_async
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ProviderError(
                "ib_async is required for IBKR data. Install with: uv pip install 'tradelab[ibkr]'",
                retryable=False,
            ) from exc

        exchange = "SMART" if self.venue is Venue.SMART else self.venue.value
        contract = ib_async.Stock(symbol, exchange, self.currency.upper())
        matches = self.ib.qualifyContracts(contract)
        if not matches:
            raise ProviderError(f"IBKR could not resolve {symbol} on {exchange}")
        if len(matches) > 1:
            detail = ", ".join(
                f"{m.symbol}@{m.primaryExchange or m.exchange}/{m.currency}" for m in matches[:5]
            )
            raise ProviderError(
                f"{symbol} is ambiguous ({detail}). Researching one listing and "
                "trading another looks correct at every step, so this is refused "
                "rather than guessed."
            )
        return matches[0]

    def instrument(self, symbol: str) -> Instrument:
        return Instrument(symbol=symbol.upper(), venue=self.venue, currency=self.currency.upper())
