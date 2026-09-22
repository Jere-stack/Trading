"""N12: the institutional accumulation footprint.

THE MECHANISM, which is textbook rather than speculative
--------------------------------------------------------
An institution that wants a position large relative to a stock's daily volume
cannot take it in one trade. Market impact forces the order to be split across
sessions, and execution algorithms do this explicitly: a participation ("POV")
algorithm targets a fixed share of each day's volume, typically 5-20%, until
the parent order is filled. Kyle (1985) derives the same behaviour from theory
-- an informed trader spreads trading over time precisely so the order does not
reveal itself.

The consequence is a testable prediction about the SHAPE of daily volume:

  * A news pop is ONE enormous session, then volume falls back to baseline
    within a few days. Attention is spent.
  * A genuine accumulation is volume elevated 20-50% above baseline for
    ten-to-twenty CONSECUTIVE sessions, with no single dramatic day, because
    the algorithm is deliberately avoiding one.

WHY THE STANDARD MEASURES CANNOT SEE THIS
-----------------------------------------
Both patterns produce the same elevated *average* volume over a month. Turnover,
the volume ratio, and Amihud illiquidity all average over their window, and
averaging is exactly the operation that destroys the distinction. The published
volume-return work compounds this: Gervais, Kaniel and Mingelgrin's high-volume
return premium is built on the LEVEL over a formation period, and Lee and
Swaminathan's turnover-conditioned momentum likewise. The one-day spike that
this module deliberately discards is a large part of what drives their result.

The claim being made here is narrow and should be read as such: not that nobody
has considered order splitting -- it is a textbook mechanism -- but that a
cross-sectional equity signal built on the PERSISTENCE of directional volume
elevation, computable from daily OHLCV alone, is not a member of the standard
factor zoo, and its closest published relatives use the quantity it filters out.

THE CONSTRUCTION
----------------
For each stock and each date:

  baseline   = rolling median dollar volume over `baseline_window` sessions,
               LAGGED by `baseline_lag` so the period being measured cannot
               contaminate the baseline it is measured against
  elevated   = dollar volume > k x baseline
  clv        = 2 x (close - low) / (high - low) - 1, in [-1, +1]: where the
               session settled within its own range, so +1 is a close on the
               high and -1 a close on the low
  footprint  = sum of clv over ELEVATED sessions in the trailing `window`,
               divided by `window`

That last line is the whole idea, and it is algebraically the product of two
things:

    footprint = (elevated sessions / window) x (mean clv on elevated sessions)

A one-day 8x spike closing on its high scores 1/21 x 1.0 = 0.05. Fifteen
sessions at 1.3x baseline closing two-thirds up their range score
15/21 x 0.33 = 0.24 -- five times larger, from a pattern with a far smaller
peak. The measure is built to rank the second above the first.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "FootprintParams",
    "accumulation_footprint",
    "close_location_value",
    "shuffle_clv",
    "shuffle_volume",
]


@dataclass(frozen=True, slots=True)
class FootprintParams:
    """Every free parameter in one object, so the grid test cannot miss one.

    N11 died because an estimation window was fixed at 60 days and never
    varied: the out-of-sample split validated the lag, which had been chosen
    in-sample, and could not validate the window, which was identical in both
    halves. Both halves faithfully replicated the same artefact. Collecting the
    parameters here is a structural defence against repeating that -- the grid
    test iterates this object, so a parameter that exists is a parameter that
    gets varied.
    """

    k: float = 1.25
    """Volume multiple over baseline that counts as 'elevated'."""

    window: int = 21
    """Trailing sessions over which the footprint is accumulated."""

    baseline_window: int = 120
    """Sessions used for the median-volume baseline."""

    baseline_lag: int = 21
    """Sessions the baseline is shifted back, so it predates the window."""

    def label(self) -> str:
        return f"k{self.k}/w{self.window}/b{self.baseline_window}/l{self.baseline_lag}"


def close_location_value(
    high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame
) -> pd.DataFrame:
    """Where each session settled within its own range, in [-1, +1].

    +1 is a close on the high, -1 a close on the low, 0 the midpoint. A session
    with no range (high == low) carries no information about the balance of
    buying and selling, so it scores 0 rather than being dropped -- dropping it
    would silently shorten the window for illiquid names only.
    """
    span = high.to_numpy(dtype=np.float32) - low.to_numpy(dtype=np.float32)
    c = close.to_numpy(dtype=np.float32)
    lo = low.to_numpy(dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        clv = np.where(span > 0, 2.0 * (c - lo) / span - 1.0, 0.0)
    clv = np.where(np.isfinite(clv), clv, np.nan)
    return pd.DataFrame(clv, index=close.index, columns=close.columns)


def accumulation_footprint(
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    dollar_volume: pd.DataFrame,
    params: FootprintParams | None = None,
    *,
    clv_override: pd.DataFrame | None = None,
    volume_override: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Panel of footprint scores, same shape as `close`. Higher is more bullish.

    `clv_override` and `volume_override` exist for the placebos: they replace
    one ingredient with a shuffled copy while leaving the other and the whole
    surrounding computation identical, which is what makes the comparison a
    test of that ingredient rather than of the pipeline.
    """
    params = params or FootprintParams()
    volume = volume_override if volume_override is not None else dollar_volume

    baseline = (
        volume.rolling(params.baseline_window, min_periods=params.baseline_window // 2)
        .median()
        .shift(params.baseline_lag)
    )
    elevated = volume > (params.k * baseline)

    clv = clv_override if clv_override is not None else close_location_value(high, low, close)

    # CLV on elevated sessions, zero elsewhere. Summing this over the window and
    # dividing by the window length is identically
    #     (elevated count / window) x (mean CLV on elevated sessions),
    # so persistence and direction multiply rather than being averaged apart.
    signed = clv.where(elevated, 0.0)
    return signed.rolling(params.window, min_periods=params.window // 2).sum() / params.window


def shuffle_volume(dollar_volume: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Permute each stock's volume history in time, preserving its distribution.

    THE central placebo. It leaves every stock's volume distribution -- and so
    roughly its count of elevated sessions -- untouched, and destroys only the
    CLUSTERING of those sessions. If the shuffled panel predicts as well as the
    real one, then persistence carries nothing and the signal is a level
    measure wearing a costume.
    """
    rng = np.random.default_rng(seed)
    values = dollar_volume.to_numpy(dtype=np.float32).copy()
    for col in range(values.shape[1]):
        column = values[:, col]
        ok = np.flatnonzero(np.isfinite(column))
        if ok.size > 1:
            column[ok] = column[rng.permutation(ok)]
    return pd.DataFrame(values, index=dollar_volume.index, columns=dollar_volume.columns)


def shuffle_clv(clv: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Permute each stock's CLV history, preserving the volume pattern.

    The second placebo, isolating the other ingredient. Real volume clustering
    is kept; only the direction attached to each session is scrambled. If this
    still predicts, the signal is really a volume-persistence measure and the
    close-location part is decoration.
    """
    rng = np.random.default_rng(seed)
    values = clv.to_numpy(dtype=np.float32).copy()
    for col in range(values.shape[1]):
        column = values[:, col]
        ok = np.flatnonzero(np.isfinite(column))
        if ok.size > 1:
            column[ok] = column[rng.permutation(ok)]
    return pd.DataFrame(values, index=clv.index, columns=clv.columns)
