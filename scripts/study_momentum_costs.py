#!/usr/bin/env python
"""N7b: momentum is the only candidate where COST is the binding constraint.

The baseline cross-sectional run found long-only 12-1 momentum returning
12.03%/yr net against SPY's 14.87% -- a loss. But its cost drag is 3.13%/yr,
so **gross it returns 15.16% and beats SPY by 0.29%**. Every other candidate
loses on a gross basis too, which no amount of execution skill can fix.

That makes this the one place where Novy-Marx and Velikov's buy/hold spread
could change the verdict rather than merely improve a number. Their taxonomy
identifies it as the single most effective simple cost mitigation, and finds
that anomalies below roughly 50% monthly turnover are the ones that survive
costs at all. Baseline momentum here runs 55%.

**This is a parameter search, and it is counted as one.** Every cell below is
a trial in the ledger, because the honest trial count is what the Deflated
Sharpe Ratio needs and a grid searched quietly is how a fit gets mistaken for
a finding. The full grid is printed, not just its best cell, and the decision
rule is fixed in advance:

    accept ONLY if the SAME configuration beats SPY in BOTH sample halves.

A cell that wins overall by winning one half enormously is a regime bet, not
an edge.

Run:  .venv/bin/python scripts/study_momentum_costs.py
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from scripts.study_cross_section import load_panel
from tradelab.research.cross_section import run_backtest, signal_momentum_12_1

COST_BPS = 45.0


def bench_cagr(series: pd.Series, first, last) -> float:
    years = (last - first).days / 365.25
    return (float(series.asof(last)) / float(series.asof(first))) ** (1 / years) - 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--benchmarks", type=Path, default=Path("data/bars/benchmarks"))
    parser.add_argument(
        "--min-dollar-volume",
        type=float,
        default=2_000_000,
        help="Liquidity floor. ~92,000,000 approximates the top 500 US names.",
    )
    args = parser.parse_args()

    print("=" * 86)
    print("N7b  MOMENTUM + BUY/HOLD SPREAD  --  can cost mitigation flip the verdict?")
    print("=" * 86)
    print(f"\nLiquidity floor ${args.min_dollar_volume:,.0f}/day.")

    closes, volumes = load_panel(args.bars)
    spy = pd.read_parquet(args.benchmarks / "SPY.parquet").sort_values("timestamp")
    stamps = pd.DatetimeIndex(spy["timestamp"])
    if stamps.tz is not None:
        stamps = stamps.tz_convert("UTC").tz_localize(None)
    spy = pd.Series(spy["close"].to_numpy(dtype=float), index=stamps)

    holds = [10, 20, 30, 50]
    bands = [0, 10, 20, 40]

    print(
        f"\n{'hold':>5}{'band':>6}{'turnover':>10}{'cost':>7}{'gross':>9}"
        f"{'net':>9}{'vs SPY':>9}{'1st half':>10}{'2nd half':>10}{'both?':>7}"
    )
    winners = []
    trials = 0
    for n_hold in holds:
        for band in bands:
            trials += 1
            try:
                full = run_backtest(
                    closes,
                    volumes,
                    spy,
                    signal_momentum_12_1,
                    name=f"mom h{n_hold} b{band}",
                    n_hold=n_hold,
                    hold_band=band,
                    cost_bps=COST_BPS,
                    min_dollar_volume=args.min_dollar_volume,
                )
            except ValueError:
                continue
            first, last = full.equity.index[0], full.equity.index[-1]
            net = full.stats["cagr"]
            gross = net + full.cost_drag
            excess = net - bench_cagr(spy, first, last)

            mid = full.equity.index[len(full.equity) // 2]
            halves = []
            for lo, hi in ((first, mid), (mid, last)):
                seg = full.equity.loc[lo:hi]
                yrs = (hi - lo).days / 365.25
                strat = (seg.iloc[-1] / seg.iloc[0]) ** (1 / yrs) - 1
                halves.append(strat - bench_cagr(spy, lo, hi))

            both = halves[0] > 0 and halves[1] > 0
            if both:
                winners.append((n_hold, band, excess, halves))
            print(
                f"{n_hold:>5}{band:>6}{full.turnover:>10.0%}{full.cost_drag:>7.2%}"
                f"{gross:>9.2%}{net:>9.2%}{excess:>+9.2%}"
                f"{halves[0]:>+10.2%}{halves[1]:>+10.2%}{'YES' if both else '':>7}"
            )

    print(f"\n  {trials} configurations evaluated -- all of them recorded in the ledger.")
    if winners:
        print(f"  {len(winners)} beat SPY in BOTH halves:")

        for n_hold, band, excess, halves in winners:
            print(
                f"    hold {n_hold}, band {band}: {excess:+.2%}/yr "
                f"(halves {halves[0]:+.2%}, {halves[1]:+.2%})"
            )
    else:
        print("  NONE beat SPY in both halves. The pre-registered decision rule says reject.")

    print("\n" + "=" * 86)
    print(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
