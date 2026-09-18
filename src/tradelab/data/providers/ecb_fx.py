"""ECB reference exchange rates.

The European Central Bank publishes daily reference rates as a free, stable,
keyless CSV covering 1999 to present. For a EUR-based account this is the
authoritative source, and it removes a real dependency risk: FX rates are
needed to compute equity at all, so a portfolio that cannot price its USD
holdings cannot evaluate a single risk limit.

Two caveats that matter for backtesting, both stated plainly because neither is
visible in the data:

1. **These are reference rates fixed once daily around 16:00 CET**, not tradable
   rates and not the rate at which IBKR would convert. For marking a portfolio
   they are appropriate. For modelling the cost of a conversion, use
   `tradelab.costs.fx` -- the reference rate contains no spread.

2. **The published rate is same-day.** Using the day-t rate to mark a position
   at day-t close is correct; using it to price a conversion you decided on at
   day-t open is lookahead.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import pandas as pd

from tradelab.data.providers.base import FxProvider, ProviderError

ECB_HISTORY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"


class EcbFxProvider(FxProvider):
    """Daily ECB reference rates, quoted per 1 EUR."""

    name = "ecb"

    def __init__(self, url: str = ECB_HISTORY_URL, timeout: int = 60) -> None:
        self.url = url
        self.timeout = timeout
        self._frame: pd.DataFrame | None = None

    def load(self, *, force: bool = False) -> pd.DataFrame:
        """Download and parse the full history. Cached in memory."""
        if self._frame is not None and not force:
            return self._frame

        try:
            import urllib.request

            with urllib.request.urlopen(self.url, timeout=self.timeout) as response:
                payload = response.read()
        except Exception as exc:
            raise ProviderError(
                f"could not download ECB reference rates from {self.url}: {exc}",
                retryable=True,
            ) from exc

        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                name = archive.namelist()[0]
                with archive.open(name) as handle:
                    frame = pd.read_csv(handle)
        except Exception as exc:
            raise ProviderError(f"could not parse the ECB archive: {exc}") from exc

        frame.columns = [str(c).strip().upper() for c in frame.columns]
        if "DATE" not in frame.columns:
            raise ProviderError(f"unexpected ECB layout; columns: {list(frame.columns)[:8]}")

        frame["DATE"] = pd.to_datetime(frame["DATE"], errors="coerce")
        frame = frame.dropna(subset=["DATE"]).set_index("DATE").sort_index()
        # Empty trailing columns and 'N/A' placeholders are both present in the
        # published file; coerce rather than trusting the dtypes.
        frame = frame.apply(pd.to_numeric, errors="coerce")
        frame = frame.dropna(axis=1, how="all")
        self._frame = frame
        return frame

    def rates(self, base: str, quote: str, start: datetime, end: datetime) -> pd.Series:
        """Units of `quote` per one unit of `base`.

        The file is published as "X per 1 EUR", so any non-EUR base is derived
        by dividing two columns -- which is exact, not an approximation, since
        both are quoted against the same numeraire on the same date.
        """
        frame = self.load()
        base, quote = base.upper(), quote.upper()

        def column(code: str) -> pd.Series:
            if code == "EUR":
                return pd.Series(1.0, index=frame.index)
            if code not in frame.columns:
                raise ProviderError(
                    f"ECB does not publish {code}. Available: "
                    f"{', '.join(sorted(frame.columns)[:12])}..."
                )
            return frame[code]

        series = (column(quote) / column(base)).dropna()
        window = series[(series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end))]
        if window.empty:
            raise ProviderError(
                f"no ECB rates for {base}/{quote} in [{start:%Y-%m-%d} .. {end:%Y-%m-%d}]"
            )
        return window.rename(f"{base}{quote}")

    def rate_on(self, base: str, quote: str, when: datetime) -> float:
        """Rate on `when`, or the most recent prior publication.

        Falls back to the previous business day because the ECB does not publish
        on TARGET holidays. Looking *forward* would be lookahead, so the search
        only ever goes backwards.
        """
        frame = self.load()
        series = self.rates(base, quote, frame.index.min().to_pydatetime(), when)
        if series.empty:
            raise ProviderError(f"no {base}/{quote} rate on or before {when:%Y-%m-%d}")
        return float(series.iloc[-1])
