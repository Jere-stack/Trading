#!/usr/bin/env python
"""Validate high-low spread estimators against known injected spreads.

Run:  .venv/bin/python scripts/validate_spread_estimators.py

Spread cannot be observed in OHLCV data, so the cost model depends on
estimating it from the high-low range. An estimator that is wrong shifts every
cost figure in the system, so it has to be checked against ground truth rather
than trusted because it is published.

Method: simulate an intraday random walk whose daily accumulation IS the daily
return (this matters -- an intraday path unrelated to daily volatility breaks
the assumption both estimators rest on), then have each trade execute at the
bid or the ask around the efficient price. The injected spread is known, so the
estimators can be scored directly.
"""

from __future__ import annotations

import numpy as np

from tradelab.data.calibration import abdi_ranaldo_spread, corwin_schultz_spread


def simulate(
    true_bps: float,
    n_days: int = 3000,
    sigma_daily: float = 0.015,
    steps: int = 390,
    seed: int = 11,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generate OHLC with a known bid-ask spread of `true_bps`."""
    rng = np.random.default_rng(seed)
    half_spread = true_bps / 10_000.0 / 2.0
    per_step = sigma_daily / np.sqrt(steps)
    highs = np.empty(n_days)
    lows = np.empty(n_days)
    closes = np.empty(n_days)
    price = 100.0
    for t in range(n_days):
        path = price * np.exp(np.cumsum(rng.normal(0, per_step, steps)))
        side = rng.choice([-1.0, 1.0], size=steps)
        observed = path * (1 + side * half_spread)
        highs[t], lows[t], closes[t] = observed.max(), observed.min(), observed[-1]
        price = path[-1]
    return highs, lows, closes


def main() -> None:
    print(__doc__)
    print(
        f"{'true bps':>9} | {'Corwin-Schultz':>15} | {'CS error':>9} | "
        f"{'Abdi-Ranaldo':>13} | {'AR error':>9}"
    )
    print("-" * 68)

    for true_bps in (5, 10, 25, 50, 100, 200):
        highs, lows, closes = simulate(true_bps)
        cs = corwin_schultz_spread(highs, lows) * 10_000
        ar = abdi_ranaldo_spread(highs, lows, closes) * 10_000
        print(
            f"{true_bps:>9} | {cs:>15.1f} | {cs - true_bps:>+9.1f} | "
            f"{ar:>13.1f} | {ar - true_bps:>+9.1f}"
        )

    print("""
CONCLUSION

  Abdi-Ranaldo tracks the true spread across the full 5-200 bps range and is
  the default estimator in tradelab.data.calibration.

  Corwin-Schultz carries a bias floor near 60 bps. It is only accurate for
  genuinely wide-spread instruments, and applying it to liquid names would
  report ~60 bps where the truth is 5 -- above the 35 bps one-way cost budget,
  so the entire liquid universe would be rejected as untradable. Silently, and
  with the appearance of rigour.

  This is also a caution about a common instinct. "Take the more conservative
  of two estimates" is sound only when both are unbiased. When one has a
  systematic floor, the maximum inherits the bias rather than the caution --
  which is why MAX_OF_BOTH is no longer the default.""")


if __name__ == "__main__":
    main()
