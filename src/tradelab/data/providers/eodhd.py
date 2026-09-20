"""EODHD (End of Day Historical Data) provider.

**The reason to pay for this is delisted tickers.** IBKR cannot serve contracts
it can no longer resolve, so an IBKR-built universe silently excludes every
company that went bankrupt, was acquired at a discount, or was delisted --
typically worth 1-4% per year of spurious return, which is comparable to or
larger than any edge in `docs/06-strategy-hypotheses.md`. EODHD's
`exchange-symbol-list` endpoint with `delisted=1` is what fixes that.

It also covers Nasdaq Helsinki, which matters for a EUR-denominated account
where trading in the base currency avoids FX cost entirely.

**Adjustment: the choice is not cosmetic.** The API returns both `close` and
`adjusted_close`, and they answer different questions:

* `close` is what actually traded. Correct for per-share commission, tick-size
  and minimum-price filters -- the cost model needs real price levels.
* `adjusted_close` is back-adjusted for splits and dividends. Correct for
  returns and therefore for signals: an unadjusted 4:1 split reads as a -75%
  one-day return, which any reversal strategy will enthusiastically buy.

This provider defaults to adjusted, because a wrong return corrupts the signal
itself, while a wrong commission is a bounded error. It carries
`adjustment_factor` on every bar so the size of that bounded error is
measurable rather than assumed -- see `adjustment_distortion`.

**API budget.** Paid plans allow 100,000 calls per day at 1 call per symbol per
EOD request, so a 500-symbol universe with full history costs 500 calls. The
practical consequence is that **one month of subscription is enough to download
everything you need**: buy a month, pull the history, store it locally, cancel.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import pandas as pd

from tradelab.data.providers.base import BarProvider, ProviderError
from tradelab.data.schema import Adjustment, normalise_bars

BASE_URL = "https://eodhd.com/api"

# EODHD exchange codes relevant to this mandate. The full list comes from
# /exchanges-list, but these are the ones a EUR-based stocks-only account
# would realistically trade.
EXCHANGE_CODES = {
    "US": "US (NYSE, NASDAQ, AMEX consolidated)",
    "HE": "Nasdaq Helsinki",
    "ST": "Nasdaq Stockholm",
    "CO": "Nasdaq Copenhagen",
    "OL": "Oslo Bors",
    "XETRA": "Deutsche Boerse XETRA",
    "PA": "Euronext Paris",
    "AS": "Euronext Amsterdam",
    "BR": "Euronext Brussels",
    "LSE": "London Stock Exchange",
    "MC": "Bolsa de Madrid",
    "MI": "Borsa Italiana",
    "VI": "Wiener Boerse",
    "IR": "Euronext Dublin",
    "LS": "Euronext Lisbon",
}


class RateLimiter:
    """Shared token gate so concurrent workers stay under the published limit.

    EODHD allows 1,000 requests/minute. Eight workers each pacing themselves
    independently would issue eight times that, so the gate is shared: workers
    queue for a slot, then make their request outside the lock. Concurrency
    therefore hides latency without raising the request rate.
    """

    def __init__(self, per_minute: int) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self.min_interval = 60.0 / per_minute
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.min_interval
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)


class EodhdProvider(BarProvider):
    """Daily bars, symbol lists and delisted tickers from EODHD."""

    name = "eodhd"

    def __init__(
        self,
        api_token: str | None = None,
        *,
        adjustment: Adjustment = Adjustment.SPLIT_AND_DIVIDEND,
        exchange: str = "US",
        timeout: int = 60,
        pacing_seconds: float = 0.1,
        max_retries: int = 3,
        max_workers: int = 1,
        requests_per_minute: int = 900,
    ) -> None:
        token = api_token or os.environ.get("EODHD_API_TOKEN")
        if not token:
            raise ProviderError(
                "no EODHD API token. Pass api_token=... or set EODHD_API_TOKEN in the "
                "environment. Never commit the token to the repository -- it is a "
                "credential, and this repository is on GitHub."
            )
        self.api_token = token
        self.adjustment = adjustment
        self.exchange = exchange.upper()
        self.timeout = timeout
        # 1,000 requests/minute is the published ceiling; 0.1s between calls
        # stays comfortably under it without making a 500-symbol pull slow.
        self.pacing_seconds = pacing_seconds
        self.max_retries = max_retries
        self.max_workers = max(1, max_workers)
        # 900/min against a published ceiling of 1,000 leaves headroom for the
        # symbol-list calls and for clock skew between client and server.
        self.rate_limiter = RateLimiter(requests_per_minute)
        self.includes_delisted = False  # set True by fetch() when delisted symbols are included
        self.dropped_bars: dict[str, dict[str, int]] = {}
        """Per-ticker record of bars removed at ingestion. See `drop_report`."""
        self.failures: dict[str, str] = {}
        """Symbols that could not be fetched, and why."""
        self.max_failure_rate = 0.0
        """Tolerated share of failed symbols. 0.0 means any failure aborts;
        large universes should raise it deliberately and report the result."""
        self._failure_lock = threading.Lock()

    # ------------------------------------------------------------------ http

    def _get(self, path: str, **params: Any) -> Any:
        params.setdefault("fmt", "json")
        params["api_token"] = self.api_token
        url = f"{BASE_URL}/{path}?{urllib.parse.urlencode(params)}"
        self.rate_limiter.acquire()

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as response:
                    payload = response.read().decode("utf-8")
                return json.loads(payload)
            except urllib.error.HTTPError as exc:
                # 401/403 are credential or entitlement problems. Retrying
                # cannot fix them and only burns API calls.
                if exc.code in (401, 403):
                    raise ProviderError(
                        f"EODHD returned {exc.code} for {path}. Either the token is "
                        "wrong, or your plan does not include this endpoint "
                        "(exchange-symbol-list and delisted tickers need a paid plan).",
                        retryable=False,
                    ) from exc
                if exc.code == 429:
                    raise ProviderError(
                        "EODHD rate limit hit (429). Paid plans allow 100,000 calls/day "
                        "at 1,000/minute; increase pacing_seconds.",
                        retryable=True,
                    ) from exc
                last_error = exc
            except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
                last_error = exc
            if attempt < self.max_retries - 1:
                time.sleep(2**attempt)

        raise ProviderError(
            f"EODHD request for {path} failed after {self.max_retries} attempts: {last_error}",
            retryable=True,
        )

    # --------------------------------------------------------------- symbols

    def exchanges(self) -> list[dict]:
        """All exchanges EODHD covers. Requires a paid plan."""
        return self._get("exchanges-list/")

    def symbols(self, exchange: str | None = None, *, delisted: bool = False) -> pd.DataFrame:
        """Active or delisted tickers on an exchange.

        `delisted=True` is the whole reason for this subscription. A universe
        built only from active tickers is survivorship-biased, and no amount of
        careful backtesting afterwards repairs that.
        """
        exchange = (exchange or self.exchange).upper()
        params: dict[str, Any] = {}
        if delisted:
            params["delisted"] = 1
        rows = self._get(f"exchange-symbol-list/{exchange}", **params)
        if not rows:
            raise ProviderError(
                f"EODHD returned no {'delisted' if delisted else 'active'} symbols "
                f"for exchange {exchange}"
            )
        frame = pd.DataFrame(rows)
        frame["delisted"] = delisted
        frame["exchange"] = exchange
        return frame

    def survivorship_free_universe(
        self, exchange: str | None = None, *, max_symbols: int | None = None
    ) -> pd.DataFrame:
        """Active AND delisted tickers, which is what a real backtest needs.

        Returning both together, with a `delisted` flag, makes the composition
        explicit: a universe where nothing is flagged delisted is the signature
        the quality auditor treats as CRITICAL.
        """
        exchange = (exchange or self.exchange).upper()
        active = self.symbols(exchange, delisted=False)
        try:
            dead = self.symbols(exchange, delisted=True)
        except ProviderError as exc:
            raise ProviderError(
                f"could not retrieve delisted symbols for {exchange}: {exc}. "
                "Proceeding with active tickers only would produce a "
                "survivorship-biased universe, which is the specific problem this "
                "provider exists to solve.",
                retryable=exc.retryable,
            ) from exc

        combined = pd.concat([active, dead], ignore_index=True)
        combined = combined.drop_duplicates(subset=["Code"], keep="first")
        if max_symbols is not None:
            combined = combined.head(max_symbols)
        return combined

    # ------------------------------------------------------------------ bars

    def fetch(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        bar_size: str = "1 day",
    ) -> pd.DataFrame:
        """Fetch daily bars. Symbols may be bare ("AAPL") or suffixed ("AAPL.US")."""
        if bar_size.lower().strip() not in ("1 day", "1d", "d"):
            raise ProviderError(
                f"{self.name} supports daily bars only, got {bar_size!r}. The cost "
                "structure of a EUR 10k account rules out intraday holding periods "
                "anyway -- see docs/01-broker-selection.md."
            )
        if start >= end:
            raise ProviderError(f"start {start} is not before end {end}")

        frames: list[pd.DataFrame] = []
        for frame in self.iter_fetch(symbols, start, end):
            frames.append(frame)

        if not frames:
            raise ProviderError(
                "no symbols returned data. Failures:\n  "
                + "\n  ".join(f"{k}: {v}" for k, v in list(self.failures.items())[:10])
            )
        self._check_failure_rate(len(symbols))
        return normalise_bars(pd.concat(frames, ignore_index=True), tz="UTC")

    def iter_fetch(self, symbols: list[str], start: datetime, end: datetime):
        """Yield per-symbol frames as they arrive, so callers can stream to disk.

        A 22,000-symbol universe does not fit comfortably in memory and takes
        long enough that losing it to a crash matters. Yielding lets the caller
        persist each symbol as it lands.

        Failures are collected in `self.failures` rather than raised per symbol,
        because one dead ticker among thousands should not abort the run --
        but the aggregate rate is checked against `max_failure_rate`, since a
        high failure rate silently reintroduces the survivorship bias this
        provider exists to remove.
        """
        self.failures.clear()
        if self.max_workers == 1:
            for symbol in symbols:
                frame = self._fetch_guarded(symbol, start, end)
                if frame is not None:
                    yield frame
            return

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self._fetch_guarded, symbol, start, end): symbol for symbol in symbols
            }
            for future in as_completed(futures):
                frame = future.result()
                if frame is not None:
                    yield frame

    # ------------------------------------------------------------- dividends

    def dividends(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Dividend history for one symbol, with declaration dates.

        `declarationDate` is the field that makes this endpoint worth using.
        The ex-date is when the price adjusts; the **declaration date** is when
        the market learned. An event study anchored on the ex-date of a cut
        measures a drift that began days earlier, and reports the part that had
        already happened as though it were tradable.

        Where the declaration date is missing -- it is, for a minority of older
        records -- the row is kept with `declaration_date` null rather than
        silently back-filled from the ex-date. A caller studying announcements
        must drop those rows; one studying the payment stream need not, and the
        two should not be forced into the same compromise here.
        """
        ticker = symbol if "." in symbol else f"{symbol}.{self.exchange}"
        rows = self._get(
            f"div/{ticker}",
            **{"from": start.strftime("%Y-%m-%d"), "to": end.strftime("%Y-%m-%d")},
        )
        if not rows:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "ex_date",
                    "declaration_date",
                    "record_date",
                    "payment_date",
                    "period",
                    "value",
                    "unadjusted_value",
                    "currency",
                ]
            )

        frame = pd.DataFrame(rows)
        out = pd.DataFrame(
            {
                "symbol": symbol,
                "ex_date": pd.to_datetime(frame.get("date"), errors="coerce", utc=True),
                "declaration_date": pd.to_datetime(
                    frame.get("declarationDate"), errors="coerce", utc=True
                ),
                "record_date": pd.to_datetime(frame.get("recordDate"), errors="coerce", utc=True),
                "payment_date": pd.to_datetime(frame.get("paymentDate"), errors="coerce", utc=True),
                "period": frame.get("period", pd.Series(dtype="object")).astype("string"),
                # `value` is adjusted for later splits; `unadjustedValue` is what
                # was actually declared. A cut is a change in the per-share rate,
                # so the comparison must be made on a consistently adjusted
                # series -- a 2:1 split halves the unadjusted dividend and is not
                # a cut. `value` is therefore the one to compare.
                "value": pd.to_numeric(frame.get("value"), errors="coerce"),
                "unadjusted_value": pd.to_numeric(frame.get("unadjustedValue"), errors="coerce"),
                "currency": frame.get("currency", pd.Series(dtype="object")).astype("string"),
            }
        )
        out = out.dropna(subset=["ex_date", "value"])
        return out.sort_values("ex_date").reset_index(drop=True)

    def iter_dividends(self, symbols: list[str], start: datetime, end: datetime):
        """Yield per-symbol dividend frames as they arrive.

        Same contract as `iter_fetch`: failures are collected rather than
        raised, because one dead ticker among thousands should not abort a
        17,000-symbol pull, and the aggregate rate is what matters.
        """
        self.failures.clear()
        if self.max_workers == 1:
            for symbol in symbols:
                frame = self._dividends_guarded(symbol, start, end)
                if frame is not None:
                    yield frame
            return

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {
                pool.submit(self._dividends_guarded, symbol, start, end): symbol
                for symbol in symbols
            }
            for future in as_completed(futures):
                frame = future.result()
                if frame is not None:
                    yield frame

    def _dividends_guarded(self, symbol: str, start: datetime, end: datetime):
        try:
            return self.dividends(symbol, start, end)
        except ProviderError as exc:
            with self._failure_lock:
                self.failures[symbol] = str(exc)
            return None
        except Exception as exc:
            with self._failure_lock:
                self.failures[symbol] = f"{type(exc).__name__}: {exc}"
            return None

    def _fetch_guarded(self, symbol: str, start: datetime, end: datetime):
        try:
            return self._fetch_one(symbol, start, end)
        except ProviderError as exc:
            with self._failure_lock:
                self.failures[symbol] = str(exc)
            return None
        except Exception as exc:
            with self._failure_lock:
                self.failures[symbol] = f"{type(exc).__name__}: {exc}"
            return None

    def _check_failure_rate(self, requested: int) -> None:
        if not self.failures or requested == 0:
            return
        rate = len(self.failures) / requested
        if rate > self.max_failure_rate:
            sample = "\n  ".join(f"{k}: {v}" for k, v in list(self.failures.items())[:8])
            raise ProviderError(
                f"{len(self.failures)} of {requested} symbols failed ({rate:.1%}), above "
                f"the {self.max_failure_rate:.0%} tolerance. Refusing to return this "
                "universe: silently dropping names reintroduces survivorship bias at "
                f"the fetch step.\n  {sample}"
            )

    def _fetch_one(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
        ticker = symbol if "." in symbol else f"{symbol}.{self.exchange}"
        rows = self._get(
            f"eod/{ticker}",
            **{
                "from": start.strftime("%Y-%m-%d"),
                "to": end.strftime("%Y-%m-%d"),
                "period": "d",
            },
        )
        if not rows:
            raise ProviderError(f"no bars returned for {ticker}")

        frame = pd.DataFrame(rows)
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = required - set(frame.columns)
        if missing:
            raise ProviderError(f"{ticker}: response missing columns {sorted(missing)}")

        frame = self._drop_non_trading_days(frame, ticker)
        if frame.empty:
            raise ProviderError(f"{ticker}: every bar was a non-trading day")

        frame["symbol"] = symbol.split(".")[0].upper()

        if "adjusted_close" in frame.columns:
            # The adjustment factor scales the whole bar consistently. Applying
            # it to close alone would break the OHLC relationships and produce
            # bars where the close sits outside the high-low range.
            factor = frame["adjusted_close"] / frame["close"].replace(0, pd.NA)
            frame["adjustment_factor"] = factor.astype("float64")
            frame["unadjusted_close"] = frame["close"].astype("float64")
            if self.adjustment is Adjustment.SPLIT_AND_DIVIDEND:
                for column in ("open", "high", "low", "close"):
                    frame[column] = frame[column] * factor
        else:
            frame["adjustment_factor"] = 1.0
            frame["unadjusted_close"] = frame["close"].astype("float64")

        return frame[
            [
                "date",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "adjustment_factor",
                "unadjusted_close",
            ]
        ]

    def _drop_non_trading_days(self, frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
        """Remove bars where the instrument did not trade.

        Thinly traded Nordic names have sessions with no trades at all. EODHD
        represents these by carrying the previous close into high/low/close
        while reporting `open` as 0, because there was no opening trade.
        Measured on Helsinki: Afarak (AFE1V) had 38 such bars out of 2,050
        (1.9%), and **every one also had zero volume** -- they are genuinely
        no-trade days, not corrupt prices.

        Dropping them is not silent repair, for three reasons:

        1. A session with no trades contains no tradeable information. No fill
           could have occurred, and the simulator already refuses to fill on
           zero volume.
        2. Keeping `open = 0` would corrupt anything computed from the open --
           a gap measure, an opening-range rule, or a market-on-open fill.
        3. The removal is **counted and reported**, and the resulting calendar
           gaps are picked up by the quality auditor. A name with many
           no-trade days is illiquid, and surfacing that is the point.

        A zero price accompanied by non-zero volume is a different animal --
        genuinely corrupt -- and is dropped too, but counted separately so it
        can be distinguished in the report.
        """
        price_columns = ["open", "high", "low", "close"]
        for column in [*price_columns, "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        bad_price = frame[price_columns].le(0).any(axis=1) | frame[price_columns].isna().any(axis=1)
        if not bad_price.any():
            return frame

        no_volume = frame["volume"].fillna(0) <= 0
        non_trading = int((bad_price & no_volume).sum())
        corrupt = int((bad_price & ~no_volume).sum())

        self.dropped_bars[ticker] = {
            "non_trading_days": non_trading,
            "corrupt_prices": corrupt,
            "total_bars": len(frame),
        }
        return frame.loc[~bad_price].reset_index(drop=True)

    def drop_report(self) -> pd.DataFrame:
        """What was removed at ingestion, per ticker. Empty when nothing was."""
        if not self.dropped_bars:
            return pd.DataFrame(
                columns=["non_trading_days", "corrupt_prices", "total_bars", "dropped_pct"]
            )
        report = pd.DataFrame(self.dropped_bars).T
        report["dropped_pct"] = (
            (report["non_trading_days"] + report["corrupt_prices"]) / report["total_bars"] * 100
        )
        return report.sort_values("dropped_pct", ascending=False)


def adjustment_distortion(frame: pd.DataFrame) -> pd.DataFrame:
    """Measure how far adjusted prices sit from what actually traded.

    Back-adjusted prices understate historical price levels, so a per-share
    commission model computes too many shares for a given notional and overstates
    cost -- or understates it, if adjustment runs the other way. The error is
    bounded but not always small, and this quantifies it per symbol instead of
    leaving it as an unexamined assumption.

    A `max_distortion` near 1.0 means adjusted and traded prices barely differ
    and the commission model is unaffected. A value of 0.25 means the oldest
    adjusted prices are a quarter of what actually traded, and per-share cost
    estimates over that period should not be trusted.
    """
    if "adjustment_factor" not in frame.columns:
        raise ValueError(
            "frame has no `adjustment_factor` column; fetch it with EodhdProvider "
            "so the distortion can be measured rather than assumed"
        )
    grouped = frame.groupby("symbol", observed=True)["adjustment_factor"]
    out = pd.DataFrame(
        {
            "min_factor": grouped.min(),
            "max_factor": grouped.max(),
            "last_factor": grouped.last(),
        }
    )
    out["max_distortion"] = out["min_factor"]
    out["commission_error_pct"] = (1.0 / out["min_factor"] - 1.0) * 100.0
    return out.sort_values("max_distortion")
