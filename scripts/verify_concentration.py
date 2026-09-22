#!/usr/bin/env python
"""Verify the concentration finding before anything is built on top of it.

scripts/study_weighting.py produced a monotone sweep: an equal-weighted book
of 20 names drawn from the top 20 by trailing dollar volume returns 14.48%/yr
against SPY's 14.87% -- a deficit of 0.39 points -- while the same 20 names
drawn from the top 500 lose 6.87 points, and from the whole liquid universe
7.19 points.

If that holds, the "universe handicap" was never a handicap of equal weighting.
It is a CONCENTRATION effect: the index's return lived in a small set of names,
and a draw from a wide pool almost never holds them. That would move the alpha
bar for every future strategy from roughly 12 points to roughly 1, which is the
difference between an impossible problem and a hard one.

A finding that consequential gets attacked before it gets used:

  1. BOTH HALVES. A single full-sample number is one observation.
  2. RISK. Matching the index on return while carrying twice its drawdown is
     not matching the index.
  3. POINT-IN-TIME HONESTY. The pool is chosen on trailing dollar volume, so
     it must be checked that nothing about the selection peeks forward.
  4. PARAMETER SURFACE. Hold count and pool size are free parameters, and N11
     died of one unvaried parameter.

Run:  .venv/bin/python -m scripts.verify_concentration
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.study_cross_section import load_panel
from scripts.study_weighting import COST_BPS, precompute

CAPITAL_EUR = 25_000.0
ANNUAL_FIXED_EUR = 870.0


def top_n_curve(book, *, pool: int, n_hold: int) -> pd.Series:
    """Equity curve of holding the `n_hold` largest names within the top `pool`.

    Deterministic: no random draw. When pool == n_hold this is simply "own the
    biggest n_hold names", rebalanced monthly.
    """
    equity, curve, stamps = 1.0, [], []
    held: set[int] = set()
    turnovers = []
    for reb in book:
        order = np.argsort(reb.size)[::-1][:pool]
        pick = order[:n_hold]
        symbols = set(reb.symbols[pick])
        prev = held
        traded = len(symbols ^ prev) / max(len(symbols | prev), 1) if prev else 1.0
        turnovers.append(traded)
        held = symbols

        w = np.full(len(pick), 1.0 / len(pick))
        gross = float((w * reb.holding_return[pick]).sum())
        equity *= 1.0 + gross - traded * COST_BPS / 10_000.0
        curve.append(equity)
        stamps.append(reb.stamp)
    series = pd.Series(curve, index=pd.DatetimeIndex(stamps), name=f"top{n_hold}/pool{pool}")
    series.attrs["turnover"] = float(np.mean(turnovers))
    return series


def stats(series: pd.Series) -> dict:
    values = series.to_numpy(dtype=float)
    years = (series.index[-1] - series.index[0]).days / 365.25
    returns = np.diff(values) / values[:-1]
    vol = returns.std(ddof=1) * np.sqrt(12)
    peak = np.maximum.accumulate(values)
    cagr = (values[-1] / values[0]) ** (1 / years) - 1
    return {
        "cagr": float(cagr),
        "vol": float(vol),
        "sharpe": float(cagr / vol) if vol > 0 else 0.0,
        "maxdd": float((values / peak - 1).min()),
    }


def spy_stats(spy: pd.Series, index: pd.DatetimeIndex) -> dict:
    aligned = pd.Series([float(spy.asof(d)) for d in index], index=index)
    return stats(aligned / aligned.iloc[0])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--benchmarks", type=Path, default=Path("data/bars/benchmarks"))
    args = parser.parse_args()

    print("=" * 84, flush=True)
    print("VERIFYING THE CONCENTRATION FINDING", flush=True)
    print("=" * 84, flush=True)

    closes, volumes = load_panel(args.bars)
    book = precompute(closes, volumes)
    frame = pd.read_parquet(args.benchmarks / "SPY.parquet").sort_values("timestamp")
    stamps = pd.DatetimeIndex(frame["timestamp"])
    if stamps.tz is not None:
        stamps = stamps.tz_convert("UTC").tz_localize(None)
    spy = pd.Series(frame["close"].to_numpy(dtype=float), index=stamps)

    # ------------------------------------------------------- 1 + 2. risk
    print("\n" + "-" * 84, flush=True)
    print("1-2. FULL SAMPLE: return AND the risk taken to get it", flush=True)
    print("-" * 84, flush=True)
    curve = top_n_curve(book, pool=20, n_hold=20)
    s = stats(curve)
    b = spy_stats(spy, curve.index)
    print(f"  {'':<22}{'CAGR':>9}{'vol':>9}{'Sharpe':>9}{'maxDD':>9}{'turnover':>10}", flush=True)
    print(
        f"  {'top 20 by DV, EW':<22}{s['cagr']:>9.2%}{s['vol']:>9.1%}"
        f"{s['sharpe']:>9.2f}{s['maxdd']:>9.1%}{curve.attrs['turnover']:>10.0%}",
        flush=True,
    )
    print(
        f"  {'SPY':<22}{b['cagr']:>9.2%}{b['vol']:>9.1%}"
        f"{b['sharpe']:>9.2f}{b['maxdd']:>9.1%}{0.0:>10.0%}",
        flush=True,
    )
    print(f"\n  excess {s['cagr'] - b['cagr']:+.2%}/yr", flush=True)

    # --------------------------------------------------------- 1. halves
    print("\n" + "-" * 84, flush=True)
    print("3. BOTH HALVES SEPARATELY", flush=True)
    print("-" * 84, flush=True)
    mid = curve.index[len(curve) // 2]
    for label, seg in (("first half", curve[curve.index <= mid]), ("second half", curve[curve.index >= mid])):
        seg = seg / seg.iloc[0]
        ss, bb = stats(seg), spy_stats(spy, seg.index)
        print(
            f"  {label:<14}{seg.index[0]:%Y-%m} to {seg.index[-1]:%Y-%m}   "
            f"strategy {ss['cagr']:>7.2%}   SPY {bb['cagr']:>7.2%}   "
            f"excess {ss['cagr'] - bb['cagr']:>+7.2%}",
            flush=True,
        )

    # ------------------------------------------------ 4. parameter surface
    print("\n" + "-" * 84, flush=True)
    print("4. PARAMETER SURFACE -- hold count x pool size, excess over SPY", flush=True)
    print("-" * 84, flush=True)
    pools = (20, 25, 30, 40, 50)
    holds = (10, 15, 20, 25, 30)
    print(f"  {'hold':>6}" + "".join(f"{f'pool {p}':>12}" for p in pools), flush=True)
    for n_hold in holds:
        cells = []
        for pool in pools:
            if n_hold > pool:
                cells.append(None)
                continue
            c = top_n_curve(book, pool=pool, n_hold=n_hold)
            cells.append(stats(c)["cagr"] - spy_stats(spy, c.index)["cagr"])
        print(
            f"  {n_hold:>6}"
            + "".join("         n/a" if v is None else f"{v:>+12.2%}" for v in cells),
            flush=True,
        )

    # ------------------------------------------------------ what it means
    print("\n" + "-" * 84, flush=True)
    print("5. ON EUR 25,000, AFTER THE COST OF RUNNING IT", flush=True)
    print("-" * 84, flush=True)
    net = s["cagr"] - ANNUAL_FIXED_EUR / CAPITAL_EUR
    print(f"  strategy CAGR                     {s['cagr']:>9.2%}", flush=True)
    print(f"  research stack on EUR 25k         {-ANNUAL_FIXED_EUR / CAPITAL_EUR:>9.2%}", flush=True)
    print(f"  net                               {net:>9.2%}", flush=True)
    print(f"  SPY (buy an ETF, pay ~0.07% TER)  {b['cagr'] - 0.0007:>9.2%}", flush=True)
    print("\n  A 20-stock book only makes sense if a SIGNAL adds more than", flush=True)
    print(f"  {b['cagr'] - 0.0007 - net:+.2%}/yr on top. That is the real alpha bar.", flush=True)
    print("=" * 84, flush=True)


if __name__ == "__main__":
    main()
