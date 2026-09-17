"""Performance metrics.

Floats and numpy throughout: this is the analytics read-model, not the ledger.

Metric choices reflect what actually matters for a small automated account:

* **Sharpe is reported but never trusted alone.** It is the statistic most
  inflated by selection bias, and `research.validation` exists to discount it.
* **Turnover and cost drag are first-class.** At EUR 10k they are frequently
  the difference between a positive and negative live result, and they are the
  two numbers a backtest can compute exactly.
* **Drawdown is reported with its duration.** A 20% drawdown lasting a month is
  a different proposition from the same drawdown lasting two years, and only the
  second one gets abandoned at the bottom.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

TRADING_DAYS = 252


@dataclass(frozen=True)
class PerformanceMetrics:
    n_periods: int
    total_return: float
    cagr: float
    volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    max_drawdown_duration: int
    calmar: float
    hit_rate: float
    profit_factor: float
    skew: float
    kurtosis: float
    best_period: float
    worst_period: float
    var_95: float
    cvar_95: float
    periods_per_year: int = TRADING_DAYS
    extras: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"periods={self.n_periods} total={self.total_return:+.2%} CAGR={self.cagr:+.2%} "
            f"vol={self.volatility:.2%} Sharpe={self.sharpe:.2f} Sortino={self.sortino:.2f} "
            f"maxDD={self.max_drawdown:.2%} ({self.max_drawdown_duration}p) "
            f"Calmar={self.calmar:.2f} hit={self.hit_rate:.1%} PF={self.profit_factor:.2f}"
        )


def to_returns(equity: np.ndarray) -> np.ndarray:
    """Simple period returns from an equity curve, guarding against zeros."""
    equity = np.asarray(equity, dtype=float)
    if equity.size < 2:
        return np.array([], dtype=float)
    prev = equity[:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        rets = np.where(prev != 0, (equity[1:] - prev) / prev, 0.0)
    return np.nan_to_num(rets, nan=0.0, posinf=0.0, neginf=0.0)


def sharpe_ratio(returns: np.ndarray, periods_per_year: int = TRADING_DAYS, rf: float = 0.0) -> float:
    """Annualised Sharpe. Returns 0.0 for degenerate input rather than inf.

    A zero-variance return series is not an infinitely good strategy; it is a
    strategy that did not trade, or a bug.
    """
    returns = np.asarray(returns, dtype=float)
    if returns.size < 2:
        return 0.0
    excess = returns - rf / periods_per_year
    sd = excess.std(ddof=1)
    if sd == 0 or not np.isfinite(sd):
        return 0.0
    return float(excess.mean() / sd * np.sqrt(periods_per_year))


def sortino_ratio(returns: np.ndarray, periods_per_year: int = TRADING_DAYS, rf: float = 0.0) -> float:
    """Downside-deviation Sharpe. Penalises only losses, as an investor does."""
    returns = np.asarray(returns, dtype=float)
    if returns.size < 2:
        return 0.0
    excess = returns - rf / periods_per_year
    downside = excess[excess < 0]
    if downside.size == 0:
        return 0.0
    dd = np.sqrt((downside**2).mean())
    if dd == 0 or not np.isfinite(dd):
        return 0.0
    return float(excess.mean() / dd * np.sqrt(periods_per_year))


def max_drawdown(equity: np.ndarray) -> tuple[float, int]:
    """Return (max drawdown as a positive fraction, longest duration in periods)."""
    equity = np.asarray(equity, dtype=float)
    if equity.size == 0:
        return 0.0, 0
    peaks = np.maximum.accumulate(equity)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peaks != 0, (peaks - equity) / peaks, 0.0)
    dd = np.nan_to_num(dd, nan=0.0)
    worst = float(dd.max()) if dd.size else 0.0

    longest = current = 0
    for i in range(equity.size):
        if equity[i] < peaks[i]:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return worst, longest


def value_at_risk(returns: np.ndarray, level: float = 0.95) -> float:
    """Historical VaR as a positive loss fraction."""
    returns = np.asarray(returns, dtype=float)
    if returns.size == 0:
        return 0.0
    return float(-np.quantile(returns, 1 - level))


def conditional_var(returns: np.ndarray, level: float = 0.95) -> float:
    """Expected shortfall: mean loss conditional on breaching VaR."""
    returns = np.asarray(returns, dtype=float)
    if returns.size == 0:
        return 0.0
    threshold = np.quantile(returns, 1 - level)
    tail = returns[returns <= threshold]
    if tail.size == 0:
        return 0.0
    return float(-tail.mean())


def profit_factor(returns: np.ndarray) -> float:
    returns = np.asarray(returns, dtype=float)
    gains = returns[returns > 0].sum()
    losses = -returns[returns < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def compute_metrics(
    equity: np.ndarray,
    periods_per_year: int = TRADING_DAYS,
    rf: float = 0.0,
) -> PerformanceMetrics:
    equity = np.asarray(equity, dtype=float)
    returns = to_returns(equity)
    n = int(returns.size)

    if n == 0 or equity[0] == 0:
        return PerformanceMetrics(
            n_periods=n, total_return=0.0, cagr=0.0, volatility=0.0, sharpe=0.0,
            sortino=0.0, max_drawdown=0.0, max_drawdown_duration=0, calmar=0.0,
            hit_rate=0.0, profit_factor=0.0, skew=0.0, kurtosis=0.0,
            best_period=0.0, worst_period=0.0, var_95=0.0, cvar_95=0.0,
            periods_per_year=periods_per_year,
        )

    total = float(equity[-1] / equity[0] - 1.0)
    years = n / periods_per_year
    cagr = float((equity[-1] / equity[0]) ** (1 / years) - 1.0) if years > 0 and equity[-1] > 0 else 0.0
    vol = float(returns.std(ddof=1) * np.sqrt(periods_per_year)) if n > 1 else 0.0
    dd, dd_dur = max_drawdown(equity)

    sd = returns.std(ddof=1) if n > 1 else 0.0
    if sd > 0:
        centred = returns - returns.mean()
        skew = float((centred**3).mean() / sd**3)
        kurt = float((centred**4).mean() / sd**4)
    else:
        skew, kurt = 0.0, 3.0

    return PerformanceMetrics(
        n_periods=n,
        total_return=total,
        cagr=cagr,
        volatility=vol,
        sharpe=sharpe_ratio(returns, periods_per_year, rf),
        sortino=sortino_ratio(returns, periods_per_year, rf),
        max_drawdown=dd,
        max_drawdown_duration=dd_dur,
        calmar=float(cagr / dd) if dd > 0 else 0.0,
        hit_rate=float((returns > 0).mean()),
        profit_factor=profit_factor(returns),
        skew=skew,
        kurtosis=kurt,
        best_period=float(returns.max()),
        worst_period=float(returns.min()),
        var_95=value_at_risk(returns),
        cvar_95=conditional_var(returns),
        periods_per_year=periods_per_year,
    )


def turnover(fill_notionals: list[float], average_equity: float, years: float) -> float:
    """Annualised turnover as a multiple of equity.

    The number that converts a per-trade cost into an annual drag, and thus the
    single most useful diagnostic for whether a strategy is affordable.
    """
    if average_equity <= 0 or years <= 0:
        return 0.0
    return float(sum(abs(n) for n in fill_notionals) / average_equity / years)


def break_even_edge_bps(round_trip_cost_bps: float, trades_per_year: float, equity: float) -> float:
    """Gross bps per round trip needed to cover costs. Sanity check, not a metric.

    If this exceeds the plausible gross edge for the signal class, the strategy
    is dead before any backtest is run -- which is usually cheaper to discover
    here than after two months of paper trading.
    """
    del trades_per_year, equity  # kept for call-site clarity
    return float(round_trip_cost_bps)
