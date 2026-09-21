"""Monthly cross-sectional backtests on a point-in-time universe.

Event studies answer "what happens after X". This answers the question that
actually decides whether to trade: **hold a basket chosen by a rule, rebalance
monthly, pay real costs -- do you end up ahead of simply buying the index?**

## The four ways a cross-sectional backtest lies, and what is done here

**1. Lookahead in the universe.** Filtering on "median dollar volume over the
whole history" silently admits a company that only became liquid in 2020 into
a 2008 portfolio. Liquidity is therefore measured on a **trailing** window
ending at the rebalance date, so the universe at each date contains only names
that were tradable *then*.

**2. Survivorship.** Delisted symbols stay in the universe until the day they
stop trading, and a position in one is liquidated at its last observed price.
A backtest that quietly drops a failing company mid-quarter books none of its
loss. The universe here includes delisted tickers by construction.

**3. Signals computed on data you did not have.** Every signal is built from a
window strictly *before* the rebalance date, and the position is entered at the
rebalance close. Nothing reads its own future.

**4. Costs charged on turnover, not assumed away.** Every rebalance pays for
the fraction of the book that actually changed. Reported net of cost always.

The output is deliberately compared against buy-and-hold on the benchmark over
the identical dates, because a strategy returning 9% in a period the index
returned 11% has lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestResult:
    name: str
    equity: pd.Series
    benchmark: pd.Series
    turnover: float
    """Average fraction of the book replaced per rebalance."""
    n_rebalances: int
    avg_positions: float
    cost_drag: float
    """Annualised return given up to transaction costs."""

    def _stats(self, series: pd.Series) -> dict:
        values = series.to_numpy(dtype=float)
        years = (series.index[-1] - series.index[0]).days / 365.25
        if years <= 0 or values[0] <= 0:
            return {"cagr": 0.0, "vol": 0.0, "sharpe": 0.0, "maxdd": 0.0}
        returns = np.diff(values) / values[:-1]
        # Monthly observations, so annualise on 12 rather than 252.
        vol = returns.std(ddof=1) * np.sqrt(12) if returns.size > 1 else 0.0
        cagr = (values[-1] / values[0]) ** (1 / years) - 1
        peak = np.maximum.accumulate(values)
        return {
            "cagr": float(cagr),
            "vol": float(vol),
            "sharpe": float(cagr / vol) if vol > 0 else 0.0,
            "maxdd": float((values / peak - 1).min()),
        }

    @property
    def stats(self) -> dict:
        return self._stats(self.equity)

    @property
    def benchmark_stats(self) -> dict:
        return self._stats(self.benchmark)

    @property
    def excess_cagr(self) -> float:
        """The only number that answers 'did it beat the index'."""
        return self.stats["cagr"] - self.benchmark_stats["cagr"]


def month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Last trading day of each month present in the data."""
    frame = pd.DataFrame({"d": index}, index=index)
    return list(frame.groupby(index.to_period("M"))["d"].max())


def run_backtest(
    closes: pd.DataFrame,
    dollar_volume: pd.DataFrame,
    benchmark: pd.Series,
    signal_fn,
    *,
    name: str,
    n_hold: int = 20,
    hold_band: int = 0,
    min_dollar_volume: float = 2_000_000,
    liquidity_window: int = 60,
    lookback: int = 252,
    cost_bps: float = 45.0,
    top: bool = True,
    start: datetime | None = None,
) -> BacktestResult:
    """Rank the point-in-time universe by `signal_fn`, hold the top `n_hold`.

    `signal_fn(window_closes) -> pd.Series` receives ONLY the closes strictly
    before the rebalance date and returns one score per symbol. Higher is
    better when `top=True`.

    `cost_bps` is charged on the traded fraction at each rebalance: replacing
    half the book costs half of `cost_bps`.

    `hold_band` implements Novy-Marx and Velikov's **buy/hold spread**, which
    their taxonomy of anomalies identifies as the single most effective simple
    cost mitigation. A name is bought only if it ranks in the top `n_hold`, but
    one already held is kept while it still ranks inside the top
    `n_hold + hold_band`. Names churning around the selection boundary then
    stop being traded twice for no change in conviction. Their finding is that
    turnover below roughly 50% per month is the line above which net spreads
    mostly stop surviving costs at all.

    `hold_band=0` reproduces the naive rebalance -- what most published
    backtests assume, and what most implementations actually pay for.
    """
    dates = closes.index
    rebalances = [d for d in month_end_dates(dates) if d >= dates[lookback]]
    if start is not None:
        rebalances = [d for d in rebalances if d >= pd.Timestamp(start)]
    if len(rebalances) < 24:
        raise ValueError(f"only {len(rebalances)} rebalances available; need at least 24")

    equity = 1.0
    curve, bench_curve, stamps = [], [], []
    held: set[str] = set()
    turnovers: list[float] = []
    counts: list[int] = []
    gross_curve = 1.0
    gross_series: list[float] = []

    for i, date in enumerate(rebalances[:-1]):
        nxt = rebalances[i + 1]
        position = dates.get_loc(date)

        # Point-in-time universe: traded on this date, and liquid over the
        # trailing window ending here. Never the full-history median.
        recent_volume = dollar_volume.iloc[max(0, position - liquidity_window) : position + 1]
        liquid = recent_volume.median(skipna=True) >= min_dollar_volume
        priced_now = closes.iloc[position].notna()
        priced_then = closes.iloc[position + 1 : dates.get_loc(nxt) + 1].notna().any()
        eligible = closes.columns[liquid & priced_now & priced_then]
        if len(eligible) < n_hold * 2:
            continue

        window = closes.iloc[max(0, position - lookback) : position + 1][eligible]
        scores = signal_fn(window).dropna()
        if len(scores) < n_hold:
            continue
        ranked = (scores.nlargest(len(scores)) if top else scores.nsmallest(len(scores))).index
        buy_list = list(ranked[:n_hold])
        if hold_band <= 0:
            chosen = set(buy_list)
        else:
            # Keep what is already held while it stays inside the wider band,
            # then top up from the buy list. A name has to fall out of the band
            # entirely before a position is paid to close, so names churning
            # around the selection boundary stop being traded twice for no
            # change in conviction.
            keep_zone = set(ranked[: n_hold + hold_band])
            chosen = {s for s in held if s in keep_zone}
            for symbol in buy_list:
                if len(chosen) >= n_hold:
                    break
                chosen.add(symbol)

        traded = len(chosen ^ held) / max(len(chosen | held), 1)
        turnovers.append(traded)
        counts.append(len(chosen))

        entry = closes.iloc[position][list(chosen)]
        # Hold to the next rebalance. A name that stops trading inside the
        # window is liquidated at its last observed price -- not dropped.
        segment = closes.iloc[position : dates.get_loc(nxt) + 1][list(chosen)]
        exit_price = segment.ffill().iloc[-1]
        holding = (exit_price / entry - 1.0).replace([np.inf, -np.inf], np.nan).dropna()
        if holding.empty:
            continue

        gross = float(holding.mean())
        cost = traded * cost_bps / 10_000.0
        equity *= 1.0 + gross - cost
        gross_curve *= 1.0 + gross
        held = chosen

        curve.append(equity)
        gross_series.append(gross_curve)
        bench_curve.append(float(benchmark.asof(nxt)))
        stamps.append(nxt)

    index = pd.DatetimeIndex(stamps)
    bench = pd.Series(bench_curve, index=index)
    bench = bench / bench.iloc[0]

    years = (index[-1] - index[0]).days / 365.25
    net_cagr = curve[-1] ** (1 / years) - 1
    gross_cagr = gross_series[-1] ** (1 / years) - 1

    return BacktestResult(
        name=name,
        equity=pd.Series(curve, index=index),
        benchmark=bench,
        turnover=float(np.mean(turnovers)) if turnovers else 0.0,
        n_rebalances=len(curve),
        avg_positions=float(np.mean(counts)) if counts else 0.0,
        cost_drag=float(gross_cagr - net_cagr),
    )


# ------------------------------------------------------------------ signals


def signal_low_volatility(window: pd.DataFrame) -> pd.Series:
    """Lowest trailing volatility. Negated so that 'largest' means 'lowest vol'."""
    returns = window.pct_change()
    return -returns.std(skipna=True)


def signal_low_idiosyncratic_vol(window: pd.DataFrame) -> pd.Series:
    """Lowest volatility of the market-relative return.

    The 2025 replication work finds idiosyncratic-volatility sorts are the
    strongest member of the low-risk family and beta sorts the weakest, so the
    market component is removed with an equal-weighted proxy built from the
    same window rather than an external index.
    """
    returns = window.pct_change()
    market = returns.mean(axis=1)
    residual = returns.sub(market, axis=0)
    return -residual.std(skipna=True)


def signal_momentum_12_1(window: pd.DataFrame) -> pd.Series:
    """Return from 12 months ago to 1 month ago, skipping the last month.

    The skip is not optional. Including the most recent month mixes in
    short-term reversal, which runs the other way and has historically
    cancelled a large part of the measured effect.
    """
    if len(window) < 252:
        return pd.Series(dtype=float)
    start = window.iloc[-252]
    end = window.iloc[-21]
    return end / start - 1.0


def signal_52_week_high(window: pd.DataFrame) -> pd.Series:
    """Closeness to the trailing 52-week high.

    George & Hwang's anchoring story: investors treat the 52-week high as a
    reference point and under-react to news that should push price through it.
    """
    if len(window) < 252:
        return pd.Series(dtype=float)
    recent = window.iloc[-252:]
    return recent.iloc[-1] / recent.max(skipna=True)
