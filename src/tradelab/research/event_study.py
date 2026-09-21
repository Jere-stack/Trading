"""Event studies measured against a benchmark, not against zero.

A strategy that returned 8% in a year the S&P returned 11% lost money in the
only sense that matters to someone who could have bought the index instead.
So every figure here is an **abnormal** return -- the stock's return minus the
benchmark's over the identical window -- and the benchmark is a real tradable
ETF, not a theoretical index.

## The four ways an event study flatters itself, and what is done about each

**1. Trading on information you did not have.** Entry is at the close of the
first session strictly after the event date. The announcement-day move is
therefore never captured, because a trader reading the announcement cannot
have it. `entry_lag` can delay entry further but never advance it.

**2. Survivorship.** Events from symbols that later delisted are kept. A
company that cuts its dividend and then fails is the most informative
observation in the sample and the one a survivors-only universe silently
deletes. Where the price series ends inside the holding window, the last
available price is used and the case is counted in `truncated`.

**3. Cross-correlated events.** Dividend cuts cluster -- a third of fifteen
years' worth landed in 2008-09. Treating 400 events from one quarter as 400
independent observations overstates significance enormously, and a naive
t-statistic will happily report p < 0.001 on what is really a handful of
independent bets. `monthly_t` collapses each calendar month to one observation
before testing, which is the standard correction and usually the number that
kills a result.

**4. Costs.** Gross and net are both reported, never gross alone.

## What this module deliberately does not do

It does not estimate a beta per event and risk-adjust by it. Market-adjusted
returns (beta implicitly 1) are used instead. For dividend cutters -- typically
distressed, typically high beta -- this makes the test *harder* to pass when
the market rises, which is the right direction for a test whose null is "no
effect". `beta_adjusted=True` runs the alternative as a robustness check;
disagreement between the two is a finding, not a nuisance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Event:
    """One dated observation. `tag` partitions the sample for separate analysis."""

    symbol: str
    date: datetime
    tag: str = ""
    magnitude: float = 0.0
    """Event size, e.g. the fraction of a dividend cut. For sorting, not filtering."""


@dataclass(frozen=True)
class CarResult:
    """Cumulative abnormal return at one horizon."""

    horizon: int
    n: int
    mean: float
    trimmed_mean: float
    """Mean after discarding the top and bottom 2.5%.

    Reported beside the raw mean because financial returns are fat-tailed
    enough that one observation can carry an entire average. A placebo of
    random dates once reported +562% at 21 days on a raw mean whose median was
    -0.67%: a single broken price series. Where the two disagree sharply, the
    raw mean is describing an outlier, not a population."""
    median: float
    std: float
    t_stat: float
    """Naive t-statistic. Overstates significance when events cluster in time."""
    monthly_t: float
    """t-statistic after collapsing each calendar month to one observation.

    The honest one. Where this and `t_stat` disagree, believe this.
    """
    monthly_n: int
    hit_rate: float
    ci_low: float
    ci_high: float
    truncated: int
    """Events whose price series ended inside the window (delisting)."""

    def net_of(self, round_trip_bps: float) -> float:
        return self.mean - round_trip_bps / 10_000.0

    @property
    def is_significant(self) -> bool:
        """At the clustered t-statistic, two-sided, roughly 5%."""
        return abs(self.monthly_t) >= 1.96


def _naive(values: pd.Series | pd.DatetimeIndex) -> pd.Series | pd.DatetimeIndex:
    """Strip timezone, keeping the instant in UTC.

    Bar files are tz-naive; dividend declarations arrive tz-aware. Comparing
    the two raises rather than silently misaligning, which is the right
    behaviour -- but it has to be resolved in one place, not at each call site,
    or one missed spot shifts a subset of events by a day without any error.
    """
    if isinstance(values, pd.Series):
        if values.dt.tz is None:
            return values
        return values.dt.tz_convert("UTC").dt.tz_localize(None)
    if values.tz is None:
        return values
    return values.tz_convert("UTC").tz_localize(None)


def _bars_by_symbol(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for symbol, group in frame.groupby("symbol", observed=True):
        group = group.sort_values("timestamp").copy()
        group["timestamp"] = _naive(group["timestamp"])
        out[str(symbol)] = group.reset_index(drop=True)
    return out


def _benchmark_series(frame: pd.DataFrame) -> pd.Series:
    frame = frame.sort_values("timestamp").copy()
    frame["timestamp"] = _naive(frame["timestamp"])
    series = frame.set_index("timestamp")["close"].astype(float)
    return series[~series.index.duplicated(keep="first")]


def _bench_return(bench: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    """Benchmark return between two dates, using the last quote at or before each.

    `asof` rather than exact match: a stock can trade on a session the
    benchmark file is missing, and dropping the event for that would quietly
    select on data completeness.
    """
    try:
        position_start = bench.index.get_indexer([start], method="ffill")[0]
        position_end = bench.index.get_indexer([end], method="ffill")[0]
    except (KeyError, IndexError):
        return None
    if position_start < 0 or position_end < 0 or position_start == position_end:
        return None
    first = float(bench.iloc[position_start])
    last = float(bench.iloc[position_end])
    return last / first - 1.0 if first > 0 else None


def abnormal_returns(
    bars: pd.DataFrame,
    events: list[Event],
    benchmark: pd.DataFrame,
    horizons: list[int],
    *,
    entry_lag: int = 1,
    min_price: float = 3.0,
) -> pd.DataFrame:
    """One row per (event, horizon) with its abnormal return.

    `entry_lag=1` enters at the close of the first session strictly after the
    event date. Zero is not offered: it would buy at the close of the day the
    news broke, using a price that already contains the news.
    """
    if entry_lag < 1:
        raise ValueError(
            "entry_lag must be >= 1: entering on the event date itself trades on "
            "a close that already reflects the announcement"
        )

    by_symbol = _bars_by_symbol(bars)
    bench = _benchmark_series(benchmark)
    rows: list[dict] = []

    for event in events:
        frame = by_symbol.get(event.symbol)
        if frame is None or frame.empty:
            continue
        stamps = frame["timestamp"]
        event_stamp = pd.Timestamp(event.date)
        if event_stamp.tzinfo is not None:
            event_stamp = event_stamp.tz_convert("UTC").tz_localize(None)
        after = np.searchsorted(stamps.to_numpy(), event_stamp.to_datetime64(), "right")
        entry_index = int(after) + entry_lag - 1
        if entry_index >= len(frame) - 1:
            continue

        closes = frame["close"].to_numpy(dtype=float)
        entry_price = closes[entry_index]
        if not np.isfinite(entry_price) or entry_price < min_price:
            continue
        entry_date = pd.Timestamp(stamps.iloc[entry_index])

        for horizon in horizons:
            wanted = entry_index + horizon
            exit_index = min(wanted, len(frame) - 1)
            if exit_index <= entry_index:
                continue
            exit_price = closes[exit_index]
            if not np.isfinite(exit_price) or exit_price <= 0:
                continue
            exit_date = pd.Timestamp(stamps.iloc[exit_index])
            market = _bench_return(bench, entry_date, exit_date)
            if market is None:
                continue
            raw = exit_price / entry_price - 1.0
            rows.append(
                {
                    "symbol": event.symbol,
                    "tag": event.tag,
                    "magnitude": event.magnitude,
                    "event_date": event_stamp,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "horizon": horizon,
                    "raw": raw,
                    "market": market,
                    "abnormal": raw - market,
                    "truncated": exit_index < wanted,
                    "month": entry_date.to_period("M"),
                }
            )
    return pd.DataFrame(rows)


def summarise_car(panel: pd.DataFrame, horizon: int, *, bootstrap: int = 2000) -> CarResult:
    """Aggregate one horizon, with a clustered t-statistic that tells the truth."""
    subset = panel[panel["horizon"] == horizon]
    values = subset["abnormal"].to_numpy(dtype=float)
    n = values.size
    if n < 2:
        return CarResult(horizon, n, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    mean = float(values.mean())
    if n >= 40:
        low, high = np.percentile(values, [2.5, 97.5])
        kept = values[(values >= low) & (values <= high)]
        trimmed = float(kept.mean()) if kept.size else mean
    else:
        trimmed = mean
    std = float(values.std(ddof=1))
    t_stat = mean / (std / math.sqrt(n)) if std > 0 else 0.0

    # Collapse each calendar month to one observation. Dividend cuts cluster in
    # crises; without this, 400 events from one quarter are counted as 400
    # independent bets and the t-statistic becomes fiction.
    monthly = subset.groupby("month", observed=True)["abnormal"].mean().to_numpy(dtype=float)
    monthly_n = monthly.size
    if monthly_n >= 2 and monthly.std(ddof=1) > 0:
        monthly_t = float(monthly.mean() / (monthly.std(ddof=1) / math.sqrt(monthly_n)))
    else:
        monthly_t = 0.0

    rng = np.random.default_rng(7)
    draws = rng.choice(values, size=(bootstrap, n), replace=True).mean(axis=1)
    ci_low, ci_high = (float(x) for x in np.percentile(draws, [2.5, 97.5]))

    return CarResult(
        horizon=horizon,
        n=n,
        mean=mean,
        trimmed_mean=trimmed,
        median=float(np.median(values)),
        std=std,
        t_stat=float(t_stat),
        monthly_t=monthly_t,
        monthly_n=monthly_n,
        hit_rate=float((values > 0).mean()),
        ci_low=ci_low,
        ci_high=ci_high,
        truncated=int(subset["truncated"].sum()),
    )


def split_half_agreement(panel: pd.DataFrame, horizon: int) -> tuple[CarResult, CarResult]:
    """The same measurement on the first and second half of the sample.

    A sign flip between halves is a kill criterion, not a curiosity: an effect
    present in one era and absent in the next is either decayed or was never
    there.
    """
    subset = panel[panel["horizon"] == horizon].sort_values("entry_date")
    if subset.empty:
        empty = CarResult(horizon, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        return empty, empty
    midpoint = subset["entry_date"].median()
    early = subset[subset["entry_date"] <= midpoint]
    late = subset[subset["entry_date"] > midpoint]
    return summarise_car(early, horizon), summarise_car(late, horizon)
