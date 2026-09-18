"""Derive cost-model parameters from bar data.

The system's cost model has three per-instrument inputs -- average daily
volume, daily volatility, and quoted spread. Until they are measured, every
cost figure is a default, and a default spread is wrong by two orders of
magnitude across a mixed universe.

ADV and volatility are directly measurable from bars. **Spread is not**, and that is
the hard part: OHLCV data contains no quotes. Two published estimators recover
it from the high-low range, both exploiting the same insight -- the daily high
is more likely to be a trade at the ask and the low a trade at the bid, so the
high-low range contains the spread plus volatility, and the two can be
separated by how they scale over one day versus two.

* **Abdi-Ranaldo (2017)** -- uses the gap between the close and the mid of the
  high-low range. **This is the default, and the choice is load-bearing.**
* **Corwin-Schultz (2012)** -- the better-known estimator. Uses the ratio of
  single-day to two-day high-low ranges.

**Measured accuracy against known spreads.** Both estimators were run against
simulated OHLC where the true spread was injected (see
`scripts/validate_spread_estimators.py`, and `tests/test_calibration.py` pins
the result):

    true bps |  Corwin-Schultz |  Abdi-Ranaldo
    ---------+-----------------+--------------
           5 |            60.9 |           7.8
          10 |            62.5 |          14.3
          25 |            71.8 |          28.9
          50 |            87.2 |          47.6
         100 |           121.0 |          97.9
         200 |           201.9 |         201.3

Abdi-Ranaldo tracks the truth across the whole range. **Corwin-Schultz carries
a bias floor near 60 bps** and is only usable for genuinely wide-spread names.
This matters enormously here: a 60 bps floor applied to liquid stocks exceeds
the 35 bps one-way cost budget outright, so a Corwin-Schultz calibration would
reject the entire liquid universe as untradable -- silently, and with the
appearance of rigour.

The lesson generalises. "Take the more conservative of two estimates" is only
sound when both are unbiased; when one has a systematic floor, the maximum
inherits the bias rather than the caution. `MAX_OF_BOTH` is retained for
wide-spread instruments but is no longer the default.

**A second bias, in the opposite direction, bounds where this works.**
Abdi-Ranaldo's accuracy depends on there being enough intraday trades for the
high-low mid to approximate the efficient price. Measured against the same
simulation at differing trade counts:

    true bps | ~200 trades/day | ~390 trades/day
    ---------+-----------------+----------------
          10 |             0.0 |            15.4
          25 |             9.8 |            28.6
          50 |            44.2 |            52.9
         100 |            97.0 |           102.5

For thinly-traded names the estimator **understates** the spread, and at a
genuinely small spread it can collapse to zero. That is the dangerous
direction: it makes an illiquid instrument look cheap to trade precisely where
the real cost is worst and where the estimate is least reliable.

The practical rule: **trust a calibrated spread only for actively traded
names, and never let a low estimated spread alone qualify a thin instrument.**
`screen_universe` therefore rejects on ADV participation independently of the
spread estimate, so a thin name cannot pass on an optimistic spread alone.

Both remain estimates with real error. **A measured quote is always
preferable**, and when the IBKR connection is available the spread should be
sampled from live quotes instead (`sample_spread_from_quotes`). These exist so
that historical research is not blocked on a quote database nobody at retail
scale has.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

import numpy as np
import pandas as pd

from tradelab.core.enums import Venue
from tradelab.core.types import Instrument

_SQRT2 = math.sqrt(2.0)
_K = 3.0 - 2.0 * _SQRT2


class SpreadEstimator(StrEnum):
    ABDI_RANALDO = "ABDI_RANALDO"
    """The default. Accurate across 5-200 bps in validation."""

    CORWIN_SCHULTZ = "CORWIN_SCHULTZ"
    """Carries a ~60 bps bias floor. Use only for known wide-spread instruments."""

    MAX_OF_BOTH = "MAX_OF_BOTH"
    """Larger of the two. NOT a safe default: it inherits the Corwin-Schultz
    bias floor and would reject the entire liquid universe as untradable."""


@dataclass(frozen=True)
class InstrumentStats:
    """Measured parameters for one instrument."""

    symbol: str
    n_bars: int
    last_price: float
    median_price: float
    adv_shares: float
    adv_currency: float
    sigma_daily: float
    spread_bps_cs: float
    spread_bps_ar: float
    spread_bps: float

    def round_trip_cost_bps(self, notional: float, commission_bps: float) -> float:
        """Modelled round-trip cost at `notional`, excluding market impact."""
        del notional
        return 2.0 * (self.spread_bps / 2.0 + commission_bps)

    def summary(self) -> str:
        return (
            f"{self.symbol:<8} px={self.last_price:>9.2f} "
            f"ADV={self.adv_currency / 1e6:>8.1f}M "
            f"sigma={self.sigma_daily:>6.2%} "
            f"spread={self.spread_bps:>7.1f}bps (CS {self.spread_bps_cs:.1f} / "
            f"AR {self.spread_bps_ar:.1f})"
        )


def corwin_schultz_spread(high: np.ndarray, low: np.ndarray, clamp_negative: bool = True) -> float:
    """Corwin-Schultz (2012) high-low spread estimator, as a fraction of price.

    The published two-day formulation. Negative daily estimates are set to zero
    before averaging, per the authors' recommendation -- a negative spread is
    not meaningful, and keeping them biases the mean downward.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    if high.size < 2:
        return float("nan")

    valid = (high > 0) & (low > 0) & (high >= low)
    if valid.sum() < 2:
        return float("nan")

    hi, lo = high[valid], low[valid]
    log_hl = np.log(hi / lo)
    beta = log_hl[:-1] ** 2 + log_hl[1:] ** 2

    two_day_high = np.maximum(hi[:-1], hi[1:])
    two_day_low = np.minimum(lo[:-1], lo[1:])
    gamma = np.log(two_day_high / two_day_low) ** 2

    with np.errstate(invalid="ignore", divide="ignore"):
        alpha = (np.sqrt(2.0 * beta) - np.sqrt(beta)) / _K - np.sqrt(gamma / _K)
        spread = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))

    spread = spread[np.isfinite(spread)]
    if spread.size == 0:
        return float("nan")
    if clamp_negative:
        spread = np.maximum(spread, 0.0)
    return float(np.mean(spread))


def abdi_ranaldo_spread(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> float:
    """Abdi-Ranaldo (2017) close-high-low spread estimator, as a fraction of price.

    S^2 = 4 * E[(c_t - eta_t) * (c_t - eta_{t+1})] where eta is the log mid of
    the high-low range. More robust than Corwin-Schultz for illiquid names.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    if high.size < 2:
        return float("nan")

    valid = (high > 0) & (low > 0) & (close > 0) & (high >= low)
    if valid.sum() < 2:
        return float("nan")

    log_high = np.log(high[valid])
    log_low = np.log(low[valid])
    log_close = np.log(close[valid])
    eta = (log_high + log_low) / 2.0
    products = 4.0 * (log_close[:-1] - eta[:-1]) * (log_close[:-1] - eta[1:])
    products = products[np.isfinite(products)]
    if products.size == 0:
        return float("nan")
    mean = float(np.mean(products))
    return math.sqrt(mean) if mean > 0 else 0.0


def calibrate(
    frame: pd.DataFrame,
    *,
    lookback: int | None = 252,
    estimator: SpreadEstimator = SpreadEstimator.ABDI_RANALDO,
    min_bars: int = 60,
) -> dict[str, InstrumentStats]:
    """Measure cost parameters for every symbol in a canonical bar frame.

    `lookback` limits the window to the most recent N bars. Recent data is the
    right basis for a *forward-looking* cost estimate: spreads have narrowed
    substantially over the last two decades, so a 10-year average overstates
    today's cost and would wrongly reject viable instruments.

    Defaults to Abdi-Ranaldo. See the module docstring for why Corwin-Schultz
    is not the default despite being better known.
    """
    stats: dict[str, InstrumentStats] = {}

    for symbol, group in frame.groupby("symbol", observed=True):
        group = group.sort_values("timestamp")
        if lookback is not None:
            group = group.tail(lookback)
        if len(group) < min_bars:
            continue

        high = group["high"].to_numpy(dtype=float)
        low = group["low"].to_numpy(dtype=float)
        close = group["close"].to_numpy(dtype=float)
        volume = group["volume"].to_numpy(dtype=float)

        returns = np.diff(close) / close[:-1]
        returns = returns[np.isfinite(returns)]
        sigma = float(np.std(returns, ddof=1)) if returns.size > 1 else float("nan")

        cs = corwin_schultz_spread(high, low) * 10_000.0
        ar = abdi_ranaldo_spread(high, low, close) * 10_000.0
        chosen = _choose_spread(cs, ar, estimator)

        # Median, not mean: a single volume spike on an index rebalance or an
        # earnings day would otherwise overstate routine tradable liquidity,
        # and the capacity check exists to bound the *typical* exit.
        adv_shares = float(np.median(volume))
        median_price = float(np.median(close))

        stats[str(symbol)] = InstrumentStats(
            symbol=str(symbol),
            n_bars=len(group),
            last_price=float(close[-1]),
            median_price=median_price,
            adv_shares=adv_shares,
            adv_currency=adv_shares * median_price,
            sigma_daily=sigma,
            spread_bps_cs=cs,
            spread_bps_ar=ar,
            spread_bps=chosen,
        )
    return stats


def _choose_spread(cs: float, ar: float, estimator: SpreadEstimator) -> float:
    if estimator is SpreadEstimator.CORWIN_SCHULTZ:
        return cs
    if estimator is SpreadEstimator.ABDI_RANALDO:
        return ar
    candidates = [v for v in (cs, ar) if np.isfinite(v)]
    return max(candidates) if candidates else float("nan")


def build_instruments(
    stats: dict[str, InstrumentStats],
    *,
    venue: Venue = Venue.SMART,
    currency: str = "USD",
    tick_size: Decimal = Decimal("0.01"),
) -> dict[str, Instrument]:
    """Turn measured statistics into calibrated `Instrument` objects.

    The result is what makes `require_calibrated_spread=True` usable: every
    instrument carries a measured spread rather than a global guess, so the
    cost model can refuse to price anything it has not measured.
    """
    instruments: dict[str, Instrument] = {}
    for symbol, s in stats.items():
        if not np.isfinite(s.spread_bps) or not np.isfinite(s.sigma_daily):
            continue
        instruments[symbol] = Instrument(
            symbol=symbol,
            venue=venue,
            currency=currency.upper(),
            tick_size=tick_size,
            adv=Decimal(str(round(s.adv_shares, 2))),
            sigma_daily=Decimal(str(round(s.sigma_daily, 6))),
            spread_bps=Decimal(str(round(s.spread_bps, 2))),
        )
    return instruments


@dataclass(frozen=True)
class UniverseScreen:
    """Which instruments are economically tradable at a given position size."""

    tradable: list[InstrumentStats]
    rejected: list[tuple[InstrumentStats, str]]
    position_notional: float
    max_cost_bps: float

    def summary(self) -> str:
        total = len(self.tradable) + len(self.rejected)
        lines = [
            f"{len(self.tradable)} of {total} instruments tradable at "
            f"{self.position_notional:,.0f} notional under a {self.max_cost_bps:.0f} bps "
            "one-way cost budget"
        ]
        reasons: dict[str, int] = {}
        for _, reason in self.rejected:
            key = reason.split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        lines.extend(
            f"  rejected -- {reason}: {count}"
            for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1])
        )
        return "\n".join(lines)


def screen_universe(
    stats: dict[str, InstrumentStats],
    *,
    position_notional: float = 1000.0,
    commission_bps: float = 12.5,
    max_cost_bps: float = 35.0,
    min_price: float = 3.0,
    max_participation: float = 0.01,
    spread_multiplier: float = 1.25,
) -> UniverseScreen:
    """Screen a universe on the cost model, before any strategy is written.

    This is the data-driven counterpart to `CostEfficiencyCheck`: it answers
    "which instruments can this account afford to trade at all?" and typically
    removes a large fraction of any broad universe. Running it first avoids
    researching signals on names that were never tradable.
    """
    tradable: list[InstrumentStats] = []
    rejected: list[tuple[InstrumentStats, str]] = []

    for s in sorted(stats.values(), key=lambda x: -x.adv_currency):
        if not np.isfinite(s.spread_bps):
            rejected.append((s, "uncalibrated: spread could not be estimated"))
            continue
        if s.last_price < min_price:
            rejected.append((s, f"price: {s.last_price:.2f} below {min_price:.2f}"))
            continue

        shares = position_notional / s.last_price
        participation = shares / s.adv_shares if s.adv_shares > 0 else float("inf")
        if participation > max_participation:
            rejected.append(
                (s, f"capacity: {participation:.2%} of ADV exceeds {max_participation:.2%}")
            )
            continue

        one_way = (s.spread_bps / 2.0) * spread_multiplier + commission_bps
        if one_way > max_cost_bps:
            rejected.append((s, f"cost: {one_way:.0f} bps one-way exceeds {max_cost_bps:.0f} bps"))
            continue
        tradable.append(s)

    return UniverseScreen(
        tradable=tradable,
        rejected=rejected,
        position_notional=position_notional,
        max_cost_bps=max_cost_bps,
    )


def sample_spread_from_quotes(quotes) -> dict[str, float]:
    """Measure spread directly from observed quotes, in bps of mid.

    Strictly preferable to any high-low estimator, and the right thing to use
    once an IBKR connection is available: sample top-of-book during regular
    trading hours over a few sessions and feed the result into
    `Instrument.spread_bps`.

    Uses the **median**, not the mean. Quoted spreads widen dramatically at the
    open, the close and around news, and those episodes are a small fraction of
    the session; a mean would be dominated by them and would overstate the cost
    of trading at a normal moment.
    """
    from collections import defaultdict

    samples: dict[str, list[float]] = defaultdict(list)
    for quote in quotes:
        if quote.is_crossed:
            continue
        bps = float(quote.spread_bps)
        if bps > 0:
            samples[quote.instrument.symbol].append(bps)
    return {symbol: float(np.median(values)) for symbol, values in samples.items() if values}
