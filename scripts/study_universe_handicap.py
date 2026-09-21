#!/usr/bin/env python
"""Is the problem the SIGNAL, or the UNIVERSE it draws from?

Every cross-sectional test so far picked 20 names, equal-weighted, from ~3,000
liquid US stocks -- and lost to SPY. But SPY is 500 large caps, cap-weighted,
and 2012-2026 was the most extreme mega-cap era on record. A strategy can beat
its own opportunity set handsomely and still trail a cap-weighted mega-cap
index, and the two diagnoses have completely different fixes:

    signal is weak        -> the effect is not there; stop
    universe is the drag  -> the effect is real; change what it draws from

This measures the baseline directly. If an equal-weighted basket of random
liquid stocks returns far less than SPY, then every long-only result so far
has been carrying a handicap that has nothing to do with the signal, and the
right response is to run the same signals inside a large-cap universe.

Run:  .venv/bin/python -m scripts.study_universe_handicap
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.study_cross_section import load_panel
from tradelab.research.cross_section import (
    month_end_dates,
    run_backtest,
    signal_low_idiosyncratic_vol,
    signal_momentum_12_1,
)

COST_BPS = 45.0


def signal_random(seed: int):
    """A random score. The honest baseline for 'pick 20 and equal-weight'."""
    rng = np.random.default_rng(seed)

    def fn(window: pd.DataFrame) -> pd.Series:
        return pd.Series(rng.random(window.shape[1]), index=window.columns)

    return fn


def signal_largest(dollar_volume: pd.DataFrame):
    """Pick the largest names by dollar volume -- a crude size proxy.

    Dollar volume is not market cap, but it is the size signal available from
    price data alone, and it is what decides tradability anyway.
    """

    def fn(window: pd.DataFrame) -> pd.Series:
        recent = dollar_volume.loc[window.index[-60] : window.index[-1], window.columns]
        return recent.median(skipna=True)

    return fn


def bench_cagr(series: pd.Series, first, last) -> float:
    years = (last - first).days / 365.25
    return (float(series.asof(last)) / float(series.asof(first))) ** (1 / years) - 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--benchmarks", type=Path, default=Path("data/bars/benchmarks"))
    args = parser.parse_args()

    print("=" * 84)
    print("UNIVERSE HANDICAP  --  is the signal weak, or is the opportunity set?")
    print("=" * 84)

    closes, volumes = load_panel(args.bars)
    spy = pd.read_parquet(args.benchmarks / "SPY.parquet").sort_values("timestamp")
    stamps = pd.DatetimeIndex(spy["timestamp"])
    if stamps.tz is not None:
        stamps = stamps.tz_convert("UTC").tz_localize(None)
    spy = pd.Series(spy["close"].to_numpy(dtype=float), index=stamps)

    # ------------------------------------------------- the equal-weight baseline
    print("\nBASELINE: pick 20 at RANDOM from the liquid universe, equal-weight, monthly.")
    print("This is what any 20-name equal-weighted strategy must beat before it has")
    print("said anything about its signal.\n")
    print(f"  {'draw':>6}{'CAGR':>9}{'vol':>8}{'maxDD':>9}{'vs SPY':>9}")
    randoms = []
    for seed in range(5):
        result = run_backtest(
            closes,
            volumes,
            spy,
            signal_random(seed),
            name=f"random {seed}",
            n_hold=20,
            cost_bps=COST_BPS,
        )
        s = result.stats
        first, last = result.equity.index[0], result.equity.index[-1]
        excess = s["cagr"] - bench_cagr(spy, first, last)
        randoms.append(s["cagr"])
        print(f"  {seed:>6}{s['cagr']:>9.2%}{s['vol']:>8.1%}{s['maxdd']:>9.1%}{excess:>+9.2%}")
    baseline = float(np.mean(randoms))
    spy_cagr = bench_cagr(spy, first, last)
    print(f"\n  mean random-20 CAGR {baseline:>7.2%}")
    print(f"  SPY over same dates {spy_cagr:>7.2%}")
    print(f"  UNIVERSE HANDICAP   {baseline - spy_cagr:>+7.2%}/yr")

    # --------------------------------------------- signals against that baseline
    print("\nSIGNALS RE-SCORED AGAINST THEIR OWN OPPORTUNITY SET")
    print(f"  {'strategy':<28}{'CAGR':>9}{'vs SPY':>9}{'vs random-20':>14}")
    for label, fn in (
        ("momentum 12-1", signal_momentum_12_1),
        ("low idiosyncratic vol", signal_low_idiosyncratic_vol),
        ("largest by dollar volume", signal_largest(volumes)),
    ):
        result = run_backtest(closes, volumes, spy, fn, name=label, n_hold=20, cost_bps=COST_BPS)
        s = result.stats
        print(
            f"  {label:<28}{s['cagr']:>9.2%}{s['cagr'] - spy_cagr:>+9.2%}"
            f"{s['cagr'] - baseline:>+14.2%}"
        )

    # ------------------------------------------------- size-restricted universe
    print("\nSAME SIGNALS, LARGE-CAP UNIVERSE ONLY")
    print("Restricting to the top 500 names by trailing dollar volume removes the")
    print("size handicap and asks the real question: can a rule beat cap-weighting")
    print("among the names SPY itself holds?\n")

    # A universe cap is applied by raising the liquidity floor to whatever the
    # 500th-largest name traded, recomputed at every rebalance inside the
    # backtest via min_dollar_volume. Approximate it with a high fixed floor.
    dates = closes.index
    rebs = [d for d in month_end_dates(dates) if d >= dates[252]]
    floors = []
    for date in rebs[::12]:
        pos = dates.get_loc(date)
        med = volumes.iloc[max(0, pos - 60) : pos + 1].median(skipna=True).dropna()
        if len(med) >= 500:
            floors.append(float(med.nlargest(500).iloc[-1]))
    large_floor = float(np.median(floors)) if floors else 5e7
    print(f"  top-500 dollar-volume floor (median across sample): ${large_floor:,.0f}/day")

    print(
        f"\n  {'strategy':<28}{'CAGR':>9}{'vol':>8}{'maxDD':>9}{'vs SPY':>9}"
        f"{'1st half':>10}{'2nd half':>10}"
    )
    for label, fn in (
        ("random 20 (baseline)", signal_random(0)),
        ("momentum 12-1", signal_momentum_12_1),
        ("low idiosyncratic vol", signal_low_idiosyncratic_vol),
    ):
        result = run_backtest(
            closes,
            volumes,
            spy,
            fn,
            name=label,
            n_hold=20,
            min_dollar_volume=large_floor,
            cost_bps=COST_BPS,
        )
        s = result.stats
        first, last = result.equity.index[0], result.equity.index[-1]
        mid = result.equity.index[len(result.equity) // 2]
        halves = []
        for lo, hi in ((first, mid), (mid, last)):
            seg = result.equity.loc[lo:hi]
            yrs = (hi - lo).days / 365.25
            halves.append((seg.iloc[-1] / seg.iloc[0]) ** (1 / yrs) - 1 - bench_cagr(spy, lo, hi))
        print(
            f"  {label:<28}{s['cagr']:>9.2%}{s['vol']:>8.1%}{s['maxdd']:>9.1%}"
            f"{s['cagr'] - bench_cagr(spy, first, last):>+9.2%}"
            f"{halves[0]:>+10.2%}{halves[1]:>+10.2%}"
        )

    print("\n" + "=" * 84)
    print(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
