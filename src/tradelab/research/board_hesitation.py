"""Board Hesitation Breadth: how many boards are late against their own rhythm.

Dividend declaration dates are almost the only corporate calendar event that is
**pure board discretion**. Earnings dates are constrained by filing deadlines;
nothing forces a board to declare on a particular day. Boards nonetheless
settle into rigid private rhythms -- the same meeting week each quarter, for
years. A board that misses its own rhythm was waiting on information before
committing cash, and dividend policy is famously sticky, so the delay is costly
to fake.

The **firm-level** link is published: Economics Letters (2016) finds that the
longer the interval between dividend announcements, the higher the probability
of a cut, on US firms 1971-2014. That part is not claimed here.

What appears unpublished is the **aggregation**. Market-breadth measures are
advance/decline -- what investors did with public information. Aggregate
dividend measures are dividend-price ratios -- valuation. Neither counts how
many boards are currently deviating from their own established cadence, which
is an aggregation of *private* information expressed through an action rather
than a statement.

## Two transformations that make aggregation possible

**Self-referential cadence.** Each firm is measured against its own median
interval, not a cross-sectional average, so a monthly REIT and a quarterly
industrial become directly comparable. Without this the measure is dominated by
payment frequency rather than by hesitation.

**One-sided lateness.** Only overdue counts. Boards do not rush good news --
increases are announced on schedule -- so earliness carries no information and
averaging it in would cancel the signal.

## The trap this code is built around

A firm that stops paying forever is *permanently* late. Counted naively, every
omission would inflate the index for the rest of the sample, and the measure
would drift upward as the universe aged -- a signal built entirely out of dead
companies. `exit_multiple` retires a firm from the eligible set once it is
grossly overdue: past that point it is not hesitating, it has stopped.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LATE_THRESHOLD = 1.15
"""Overdue ratio at which a board counts as hesitating. 15% past its own
median interval -- outside ordinary meeting-date jitter, well inside the range
where a firm has clearly stopped paying."""

EXIT_MULTIPLE = 3.0
"""Overdue ratio at which a firm leaves the eligible set entirely.

Without this, an omission makes a firm late forever and the index becomes a
count of dead companies."""

MIN_HISTORY = 8
"""Declarations required before a firm's cadence is considered established."""


@dataclass(frozen=True)
class BreadthSeries:
    """Daily breadth, plus the denominators needed to audit it."""

    raw: pd.Series
    """Fraction of eligible firms currently late."""
    eligible: pd.Series
    """Firms with an established cadence and not yet retired. Watch this: if it
    collapses, the index is measuring a shrinking club rather than sentiment."""
    late: pd.Series

    def seasonally_adjusted(self, years: int = 3) -> pd.Series:
        """Raw breadth minus its own same-ISO-week average over prior years.

        Boards cluster meetings around holidays and quarter ends. Without this
        the index measures the calendar, and the calendar is not information.
        Only prior years are used, so the adjustment is point-in-time.
        """
        frame = pd.DataFrame({"raw": self.raw})
        frame["week"] = frame.index.isocalendar().week.to_numpy()
        frame["year"] = frame.index.year
        out = pd.Series(np.nan, index=frame.index, dtype=float)
        for _week, group in frame.groupby("week"):
            ordered = group.sort_index()
            for stamp, row in ordered.iterrows():
                prior = ordered[
                    (ordered["year"] < row["year"]) & (ordered["year"] >= row["year"] - years)
                ]
                if prior["year"].nunique() >= years:
                    out.loc[stamp] = row["raw"] - float(prior["raw"].mean())
        return out

    def percentile(self, window_years: int = 5) -> pd.Series:
        """Seasonally adjusted breadth as a percentile of its own trailing window.

        Expressed as a percentile rather than a z-score because the level drifts
        with universe composition and a percentile is invariant to that.
        """
        adjusted = self.seasonally_adjusted().dropna()
        window = int(252 * window_years)
        return adjusted.rolling(window, min_periods=window // 2).apply(
            lambda values: float((values[:-1] < values[-1]).mean()), raw=True
        )


def _late_intervals(
    declarations: np.ndarray,
    *,
    late_threshold: float,
    exit_multiple: float,
    min_history: int,
) -> list[tuple[np.datetime64, np.datetime64, np.datetime64, np.datetime64]]:
    """(eligible_from, eligible_to, late_from, late_to) for one firm.

    Everything is derived from declarations strictly *before* the interval, so
    nothing here can see its own future.
    """
    out = []
    if declarations.size <= min_history:
        return out
    days = declarations.astype("datetime64[D]").astype(np.int64)
    for i in range(min_history, declarations.size):
        history = np.diff(days[i - min_history : i])
        cadence = float(np.median(history))
        if cadence <= 0:
            continue
        start = days[i - 1]
        nxt = days[i]
        late_from = start + late_threshold * cadence
        retire_at = start + exit_multiple * cadence
        eligible_to = min(nxt, retire_at)
        if late_from >= eligible_to:
            late_from = eligible_to  # never late before it is retired
        out.append(
            (
                np.datetime64(int(start), "D"),
                np.datetime64(int(eligible_to), "D"),
                np.datetime64(int(late_from), "D"),
                np.datetime64(int(eligible_to), "D"),
            )
        )
    # The open interval after the final declaration: eligible until retirement.
    last = days[-1]
    history = np.diff(days[-min_history - 1 :])
    cadence = float(np.median(history)) if history.size else 0.0
    if cadence > 0:
        out.append(
            (
                np.datetime64(int(last), "D"),
                np.datetime64(int(last + exit_multiple * cadence), "D"),
                np.datetime64(int(last + late_threshold * cadence), "D"),
                np.datetime64(int(last + exit_multiple * cadence), "D"),
            )
        )
    return out


def board_hesitation_breadth(
    dividends: pd.DataFrame,
    *,
    late_threshold: float = LATE_THRESHOLD,
    exit_multiple: float = EXIT_MULTIPLE,
    min_history: int = MIN_HISTORY,
    shuffle_seed: int | None = None,
) -> BreadthSeries:
    """Daily breadth of board hesitation across the dividend-paying universe.

    `shuffle_seed` is the placebo: declaration dates are permuted *within* each
    firm, preserving every firm's number of declarations and the overall date
    distribution while destroying the ordering that carries the information. A
    signal that survives its own placebo is measuring the calendar, not boards.
    """
    frame = dividends.dropna(subset=["declaration_date"]).copy()
    frame["declaration_date"] = pd.to_datetime(frame["declaration_date"], utc=True).dt.tz_localize(
        None
    )
    rng = np.random.default_rng(shuffle_seed) if shuffle_seed is not None else None

    starts, ends, late_starts, late_ends = [], [], [], []
    for _, group in frame.groupby("symbol", observed=True):
        dates = np.sort(group["declaration_date"].unique())
        if shuffle_seed is not None and dates.size > 2:
            span_lo, span_hi = dates[0], dates[-1]
            width = (span_hi - span_lo).astype("timedelta64[D]").astype(int)
            if width > dates.size:
                offsets = rng.choice(width, size=dates.size, replace=False)
                dates = np.sort(span_lo + offsets.astype("timedelta64[D]"))
        for s, e, ls, le in _late_intervals(
            dates,
            late_threshold=late_threshold,
            exit_multiple=exit_multiple,
            min_history=min_history,
        ):
            starts.append(s)
            ends.append(e)
            late_starts.append(ls)
            late_ends.append(le)

    if not starts:
        empty = pd.Series(dtype=float)
        return BreadthSeries(empty, empty, empty)

    # Truncate at the last real declaration. Firms are projected forward to
    # their retirement date, so beyond the data every remaining firm is
    # trivially "late" and breadth runs to 100% -- an artefact of the
    # projection, not a reading.
    observed_end = np.datetime64(pd.Timestamp(frame["declaration_date"].max()).to_datetime64(), "D")
    calendar = pd.date_range(min(starts), min(max(ends), observed_end), freq="D")
    eligible = np.zeros(len(calendar), dtype=np.int32)
    late = np.zeros(len(calendar), dtype=np.int32)
    base = calendar[0].to_datetime64().astype("datetime64[D]").astype(np.int64)

    def accumulate(counter: np.ndarray, lows: list, highs: list) -> None:
        lo = np.array(lows, dtype="datetime64[D]").astype(np.int64) - base
        hi = np.array(highs, dtype="datetime64[D]").astype(np.int64) - base
        lo = np.clip(lo, 0, len(counter))
        hi = np.clip(hi, 0, len(counter))
        # Difference array: O(events) rather than O(events x days).
        np.add.at(counter, lo[lo < len(counter)], 1)
        np.add.at(counter, hi[hi < len(counter)], -1)

    accumulate(eligible, starts, ends)
    accumulate(late, late_starts, late_ends)
    eligible = np.cumsum(eligible)
    late = np.cumsum(late)

    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.where(eligible > 0, late / eligible, np.nan)
    return BreadthSeries(
        raw=pd.Series(raw, index=calendar, name="bhb"),
        eligible=pd.Series(eligible, index=calendar, name="eligible"),
        late=pd.Series(late, index=calendar, name="late"),
    )


def lead_lag_profile(signal: pd.Series, prices: pd.Series, *, months: int = 12) -> pd.DataFrame:
    """Correlation of the signal with market returns at leads and lags.

    **The test that decides whether this idea is alive.** If the strongest
    relationship sits at a non-positive lag, the index is coincident or lagging
    -- it reacts to the market rather than anticipating it -- and no threshold,
    weighting or parameter can repair that. Positive `lag_months` means the
    return comes AFTER the signal.
    """
    monthly_price = prices.resample("ME").last()
    monthly_signal = signal.resample("ME").last()
    joined = pd.DataFrame({"signal": monthly_signal, "price": monthly_price}).dropna()
    returns = joined["price"].pct_change()

    rows = []
    for lag in range(-months, months + 1):
        shifted = returns.shift(-lag)
        pair = pd.DataFrame({"s": joined["signal"], "r": shifted}).dropna()
        if len(pair) < 24:
            continue
        rows.append(
            {
                "lag_months": lag,
                "n": len(pair),
                "correlation": float(pair["s"].corr(pair["r"])),
            }
        )
    return pd.DataFrame(rows)
