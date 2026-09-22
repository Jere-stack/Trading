"""Information coefficient analysis: the cheapest honest test of a signal.

A backtest answers "would this configuration have made money", which bundles
the signal with portfolio construction, weighting, rebalance timing and costs.
When the answer is no, the bundle tells you nothing about which part failed;
when it is yes, the bundle is where overfitting hides.

The information coefficient asks the prior question -- does the signal rank
next period's returns at all -- and it answers using every name on every date
rather than the handful a portfolio would have held. It is both far cheaper
and far harder to fool, which is why it runs before any backtest here.

Rank correlation, not Pearson, throughout: a raw return distribution has tails
that let two or three names decide a cross-sectional correlation, and a signal
that merely identifies one huge winner a year is not a signal that can be
traded.

THE STANDARD ERROR IS THE PART PEOPLE GET WRONG. A monthly IC series is
autocorrelated -- the same slow-moving names sit at the top of the ranking for
months -- so the usual t = mean/(sd/sqrt(n)) overstates significance by
whatever the persistence is. Every t-statistic here is reported on the
EFFECTIVE sample size, n_eff = n(1-r)/(1+r). That correction is what killed
N11's `listing_rate`, where 177 monthly observations carried 2.4 independent
ones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

__all__ = [
    "ICSummary",
    "forward_returns",
    "information_coefficient",
    "neutralise",
    "summarise_ic",
]


@dataclass(frozen=True)
class ICSummary:
    """What an IC series is worth, with the standard error done honestly."""

    n: int
    """Periods observed."""
    n_eff: float
    """Independent periods after the autocorrelation correction."""
    mean: float
    std: float
    autocorr: float
    t_stat: float
    """Computed on n_eff, not n."""
    hit_rate: float
    """Fraction of periods with IC > 0. A real signal is right more often than
    it is spectacularly right once."""
    ir: float
    """Mean / std -- the information ratio of the IC series itself."""

    def verdict(self, threshold: float = 2.0) -> str:
        return "PASS" if abs(self.t_stat) >= threshold else "FAIL"

    def line(self) -> str:
        return (
            f"IC {self.mean:+.4f}  sd {self.std:.4f}  IR {self.ir:+.3f}  "
            f"hit {self.hit_rate:.0%}  n {self.n}  n_eff {self.n_eff:.1f}  "
            f"t {self.t_stat:+.2f}  {self.verdict()}"
        )


def forward_returns(closes: pd.DataFrame, dates: list[pd.Timestamp]) -> pd.DataFrame:
    """Return from each date in `dates` to the next one, per symbol.

    A name that stops trading inside the window is carried at its last observed
    price rather than dropped: dropping it deletes exactly the failures that
    survivorship bias is about, and a signal that looks good only because its
    losers vanish is the oldest mistake in the field.
    """
    rows = {}
    for i, date in enumerate(dates[:-1]):
        nxt = dates[i + 1]
        entry = closes.loc[date]
        window = closes.loc[date:nxt]
        exit_price = window.ffill().iloc[-1]
        rows[date] = (exit_price / entry - 1.0).replace([np.inf, -np.inf], np.nan)
    return pd.DataFrame(rows).T


def information_coefficient(
    signal: pd.DataFrame,
    forward: pd.DataFrame,
    *,
    min_names: int = 20,
) -> pd.Series:
    """Per-date Spearman rank correlation between signal and forward return.

    `min_names` guards against a cross-section too small to carry a rank
    correlation; a 5-name IC is noise with a decimal point.
    """
    common_dates = signal.index.intersection(forward.index)
    common_cols = signal.columns.intersection(forward.columns)
    out = {}
    for date in common_dates:
        s = signal.loc[date, common_cols]
        f = forward.loc[date, common_cols]
        ok = s.notna() & f.notna()
        if int(ok.sum()) < min_names:
            continue
        sv = s[ok].to_numpy(dtype=float)
        fv = f[ok].to_numpy(dtype=float)
        # A constant signal has no ranking to correlate; scipy returns NaN and
        # warns. Skipping is right: it is an absent observation, not a zero one.
        if np.all(sv == sv[0]) or np.all(fv == fv[0]):
            continue
        rho, _ = stats.spearmanr(sv, fv)
        if np.isfinite(rho):
            out[date] = float(rho)
    return pd.Series(out, name="ic").sort_index()


def summarise_ic(ic: pd.Series) -> ICSummary:
    """Mean IC with a t-statistic computed on the EFFECTIVE sample size."""
    values = ic.dropna().to_numpy(dtype=float)
    n = len(values)
    if n < 3:
        return ICSummary(n, float(n), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    mean = float(values.mean())
    std = float(values.std(ddof=1))
    lag1 = float(np.corrcoef(values[:-1], values[1:])[0, 1]) if n > 3 else 0.0
    if not np.isfinite(lag1):
        lag1 = 0.0
    # Clamp below 1: a series with r -> 1 carries no independent observations
    # and the formula diverges rather than reporting that honestly.
    r = min(max(lag1, -0.99), 0.99)
    n_eff = n * (1 - r) / (1 + r)
    n_eff = max(n_eff, 1.0)

    t_stat = mean / (std / math.sqrt(n_eff)) if std > 0 else 0.0
    return ICSummary(
        n=n,
        n_eff=float(n_eff),
        mean=mean,
        std=std,
        autocorr=lag1,
        t_stat=float(t_stat),
        hit_rate=float((values > 0).mean()),
        ir=float(mean / std) if std > 0 else 0.0,
    )


def neutralise(signal: pd.DataFrame, control: pd.DataFrame) -> pd.DataFrame:
    """Signal with the cross-sectional effect of `control` regressed out, per date.

    Both are converted to cross-sectional ranks first, so the residual answers
    "does this signal rank names differently from the control" rather than
    being dominated by either variable's distribution. This is how a team
    establishes that a candidate is not a restatement of a factor it already
    has -- and momentum is a factor this project has already rejected, so a
    signal that turns out to BE momentum inherits that rejection.
    """
    out = {}
    for date in signal.index.intersection(control.index):
        s = signal.loc[date]
        c = control.loc[date]
        ok = s.notna() & c.notna()
        if int(ok.sum()) < 10:
            continue
        sr = s[ok].rank(pct=True).to_numpy(dtype=float)
        cr = c[ok].rank(pct=True).to_numpy(dtype=float)
        cr_centred = cr - cr.mean()
        denom = float((cr_centred**2).sum())
        beta = float((cr_centred * (sr - sr.mean())).sum() / denom) if denom > 0 else 0.0
        residual = sr - sr.mean() - beta * cr_centred
        out[date] = pd.Series(residual, index=s[ok].index)
    return pd.DataFrame(out).T.reindex(columns=signal.columns)
