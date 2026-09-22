"""Aggregate market-state signals from daily OHLCV, and whether they lead.

Built method-first after N10, where a signal was constructed, validated, tuned
and only then tested for whether it led the market -- and it followed by six
months. One correlation chart on day one would have refuted the premise. So
nothing here is a strategy; these are candidate *state variables*, and the only
question asked of them is whether they arrive before the market moves or after.

Each signal is a single daily number computed across the whole universe. All
are point-in-time by construction: they use the current cross-section and
trailing windows, never a forward value.

The list mixes published controls -- 52-week-low breadth, average pairwise
correlation, aggregate Amihud illiquidity -- with less-explored constructions.
The controls are what make the scan interpretable: if a known signal leads and
a novel one does not, that is information about where information lives, not a
failed search.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _safe(frame: pd.DataFrame) -> np.ndarray:
    return frame.to_numpy(dtype=np.float32)


def clv_breadth(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame) -> pd.Series:
    """Mean close-location value: where in the day's range stocks settled.

    Advance/decline breadth uses the SIGN of the daily change. This uses the
    position of the close within the range, which distinguishes a market that
    closed up on its highs from one that closed up having given back most of
    the day -- the same sign, a different balance of control at the close.
    """
    h, low_, c = _safe(high), _safe(low), _safe(close)
    span = h - low_
    with np.errstate(divide="ignore", invalid="ignore"):
        clv = np.where(span > 0, (c - low_) / span, 0.5)
    clv = np.where(np.isfinite(clv), clv, np.nan)
    return pd.Series(np.nanmean(clv, axis=1), index=close.index, name="clv_breadth")


def return_dispersion(close: pd.DataFrame) -> pd.Series:
    """Cross-sectional standard deviation of daily returns."""
    returns = close.pct_change()
    return pd.Series(np.nanstd(_safe(returns), axis=1), index=close.index, name="return_dispersion")


def return_skew(close: pd.DataFrame) -> pd.Series:
    """Cross-sectional skew of daily returns.

    Negative skew across the cross-section means the average stock is fine and
    a tail is being hit -- damage concentrated rather than broad, which is a
    different market state from a uniform decline.
    """
    returns = _safe(close.pct_change())
    mean = np.nanmean(returns, axis=1, keepdims=True)
    deviation = returns - mean
    sd = np.nanstd(returns, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        third = np.nanmean(deviation**3, axis=1)
        skew = np.where(sd > 0, third / sd**3, np.nan)
    return pd.Series(skew, index=close.index, name="return_skew")


def range_expansion(
    high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, window: int = 60
) -> pd.Series:
    """Mean daily range as a multiple of its own trailing median."""
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = (_safe(high) - _safe(low)) / _safe(close)
    daily = pd.Series(np.nanmean(rel, axis=1), index=close.index)
    return (daily / daily.rolling(window).median()).rename("range_expansion")


def gap_breadth(open_: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame) -> pd.Series:
    """Fraction of stocks opening outside the previous day's range."""
    o = _safe(open_)
    prior_high = _safe(high.shift(1))
    prior_low = _safe(low.shift(1))
    gapped = (o > prior_high) | (o < prior_low)
    valid = np.isfinite(o) & np.isfinite(prior_high) & np.isfinite(prior_low)
    with np.errstate(divide="ignore", invalid="ignore"):
        fraction = np.where(
            valid.sum(axis=1) > 0, (gapped & valid).sum(axis=1) / valid.sum(axis=1), np.nan
        )
    return pd.Series(fraction, index=open_.index, name="gap_breadth")


def volume_concentration(dollar_volume: pd.DataFrame) -> pd.Series:
    """Herfindahl index of dollar volume across the universe.

    Standard breadth measures ask how many stocks *moved*. This asks how few
    stocks were *traded* -- whether the market's attention and liquidity are
    narrowing into a handful of names while the index still looks healthy.
    """
    values = _safe(dollar_volume)
    total = np.nansum(values, axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(total > 0, values / total, 0.0)
    return pd.Series(
        np.nansum(share**2, axis=1), index=dollar_volume.index, name="volume_concentration"
    )


def new_low_breadth(close: pd.DataFrame, window: int = 252) -> pd.Series:
    """Fraction within 2% of a trailing 52-week low. PUBLISHED CONTROL."""
    rolling_low = close.rolling(window, min_periods=window // 2).min()
    near = _safe(close) <= _safe(rolling_low) * 1.02
    valid = np.isfinite(_safe(close)) & np.isfinite(_safe(rolling_low))
    with np.errstate(divide="ignore", invalid="ignore"):
        fraction = np.where(
            valid.sum(axis=1) > 0, (near & valid).sum(axis=1) / valid.sum(axis=1), np.nan
        )
    return pd.Series(fraction, index=close.index, name="new_low_breadth")


def listing_churn(close: pd.DataFrame, window: int = 90) -> tuple[pd.Series, pd.Series]:
    """Trailing counts of universe entries and exits.

    A stock's first bar is its arrival, its last its departure. Listing booms
    cluster at market tops and delisting waves at bottoms -- but whether either
    arrives *before* the move is exactly what has not been established.
    """
    priced = close.notna()
    first = priced.idxmax()
    reversed_index = priced.iloc[::-1]
    last = reversed_index.idxmax()
    entries = pd.Series(0, index=close.index, dtype=float)
    exits = pd.Series(0, index=close.index, dtype=float)
    for stamp in first.dropna():
        entries.loc[stamp] += 1
    final = close.index[-1]
    for stamp in last.dropna():
        if stamp < final:
            exits.loc[stamp] += 1
    return (
        entries.rolling(window).sum().rename("listing_rate"),
        exits.rolling(window).sum().rename("delisting_rate"),
    )


def reversal_breadth(open_: pd.DataFrame, close: pd.DataFrame) -> pd.Series:
    """Fraction where the intraday move opposes the overnight move.

    Overnight returns are dominated by news and by institutional order flow at
    the open; intraday by what happens next. A market where most stocks spend
    the day undoing the gap is one where the overnight move is not being
    believed.
    """
    overnight = _safe(open_) - _safe(close.shift(1))
    intraday = _safe(close) - _safe(open_)
    opposed = (overnight * intraday) < 0
    valid = np.isfinite(overnight) & np.isfinite(intraday) & (overnight != 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        fraction = np.where(
            valid.sum(axis=1) > 0, (opposed & valid).sum(axis=1) / valid.sum(axis=1), np.nan
        )
    return pd.Series(fraction, index=close.index, name="reversal_breadth")


def average_correlation(close: pd.DataFrame, window: int = 60) -> pd.Series:
    """Index volatility over mean single-stock volatility. PUBLISHED CONTROL.

    A ratio near one means everything is moving together. Computed this way
    rather than as a pairwise average because an N x N correlation matrix over
    5,000 names is not worth the compute for a scan.
    """
    returns = close.pct_change()
    equal_weighted = returns.mean(axis=1)
    index_vol = equal_weighted.rolling(window).std()
    single_vol = pd.Series(
        np.nanmean(_safe(returns.rolling(window).std()), axis=1), index=close.index
    )
    return (index_vol / single_vol).rename("avg_correlation")


def illiquidity(close: pd.DataFrame, dollar_volume: pd.DataFrame) -> pd.Series:
    """Mean |return| per dollar traded -- aggregate Amihud. PUBLISHED CONTROL."""
    returns = np.abs(_safe(close.pct_change()))
    volume = _safe(dollar_volume)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(volume > 0, returns / volume, np.nan)
    return pd.Series(np.nanmean(ratio, axis=1) * 1e9, index=close.index, name="illiquidity")
