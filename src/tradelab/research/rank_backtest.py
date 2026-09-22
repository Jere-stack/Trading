"""Long-only ranked-portfolio simulator with daily equity and honest costs.

Earlier cross-sectional work in this project used month-end equity points and
charged cost on the share of NAMES replaced. This simulator is stricter in the
three ways that matter when the result will be compared against simply holding
an index:

  * DAILY equity. Positions are bought at a rebalance close and drift with
    prices until the next one, exactly as a held book would. Volatility and
    drawdown are then measured on the same daily clock as the benchmark, not
    on smoothed month-end points.
  * COST ON TRADED VALUE. One-way turnover is half the sum of absolute weight
    changes between the drifted book and the new target -- which includes
    trimming winners back to equal weight, not only swapping names. The round-
    trip cost is charged on that one-way figure (selling V and buying V is one
    round trip on V).
  * DELISTED NAMES ARE LIQUIDATED, NOT DROPPED. A name that stops trading is
    carried at its last price to the next rebalance and sold there. Dropping it
    would delete exactly the failures survivorship bias is about.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

__all__ = ["RankedResult", "perf_stats", "run_ranked"]


@dataclass
class RankedResult:
    equity: pd.Series
    """Daily net asset value, starting at 1.0 on the first rebalance close."""
    holdings: dict[pd.Timestamp, list[str]] = field(default_factory=dict)
    turnover: list[float] = field(default_factory=list)
    """One-way turnover at each rebalance, as a fraction of the book."""
    cost_paid: float = 0.0
    """Sum of cost fractions charged."""

    @property
    def mean_turnover(self) -> float:
        return float(np.mean(self.turnover)) if self.turnover else 0.0


def _select(scores: pd.Series, held: list[str], n_hold: int, hold_band: int) -> list[str]:
    ranked = list(scores.dropna().sort_values(ascending=False).index)
    if hold_band <= 0:
        return ranked[:n_hold]
    keep_zone = set(ranked[: n_hold + hold_band])
    chosen = [s for s in held if s in keep_zone]
    for s in ranked:
        if len(chosen) >= n_hold:
            break
        if s not in chosen:
            chosen.append(s)
    return chosen


def run_ranked(
    scores: pd.DataFrame,
    closes: pd.DataFrame,
    *,
    n_hold: int,
    hold_band: int = 0,
    cost_bps: float = 33.1,
    end: pd.Timestamp | None = None,
) -> RankedResult:
    """Hold the top `n_hold` by score at each rebalance date, equal weight.

    `scores` is indexed by rebalance dates (which must be trading sessions in
    `closes`); NaN means ineligible on that date. Higher scores are better.
    """
    dates = [d for d in scores.index if d in closes.index]
    if len(dates) < 2:
        raise ValueError("need at least two rebalance dates present in closes")
    final = closes.index[-1] if end is None else pd.Timestamp(end)

    segments: list[pd.Series] = []
    nav = 1.0
    held: list[str] = []
    drifted = pd.Series(dtype=float)
    result = RankedResult(equity=pd.Series(dtype=float))

    for i, date in enumerate(dates):
        stop = dates[i + 1] if i + 1 < len(dates) else final
        target_names = _select(scores.loc[date], held, n_hold, hold_band)
        target_names = [s for s in target_names if pd.notna(closes.at[date, s])]
        if not target_names:
            continue
        target = pd.Series(1.0 / len(target_names), index=target_names)

        union = target.index.union(drifted.index)
        one_way = 0.5 * float(
            (target.reindex(union, fill_value=0.0) - drifted.reindex(union, fill_value=0.0)).abs().sum()
        )
        cost = one_way * cost_bps / 10_000.0
        nav *= 1.0 - cost
        result.turnover.append(one_way)
        result.cost_paid += cost
        result.holdings[date] = target_names

        window = closes.loc[date:stop, target_names].ffill()
        rel = window / window.iloc[0]
        values = rel.mul(target, axis=1).sum(axis=1)
        seg = nav * values
        segments.append(seg if not segments else seg.iloc[1:])
        nav = float(seg.iloc[-1])

        last = rel.iloc[-1] * target
        drifted = last / last.sum()
        held = target_names

    result.equity = pd.concat(segments)
    result.equity = result.equity[~result.equity.index.duplicated(keep="last")]
    return result


def perf_stats(equity: pd.Series) -> dict[str, float]:
    """CAGR, volatility, Sharpe (risk-free = 0), max drawdown, from DAILY values.

    Risk-free is set to zero for strategy and benchmark alike: the comparison
    this project cares about is relative, and a common rf moves both equally.
    """
    values = equity.dropna().to_numpy(dtype=float)
    if len(values) < 30:
        return {"cagr": np.nan, "vol": np.nan, "sharpe": np.nan, "maxdd": np.nan}
    days = (equity.dropna().index[-1] - equity.dropna().index[0]).days
    years = days / 365.25
    rets = np.diff(values) / values[:-1]
    vol = float(rets.std(ddof=1) * np.sqrt(252))
    cagr = float((values[-1] / values[0]) ** (1 / years) - 1)
    mean_ann = float(rets.mean() * 252)
    peak = np.maximum.accumulate(values)
    return {
        "cagr": cagr,
        "vol": vol,
        "sharpe": mean_ann / vol if vol > 0 else np.nan,
        "maxdd": float((values / peak - 1).min()),
    }
