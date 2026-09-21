#!/usr/bin/env python
"""Attack the two momentum configurations that survived the both-halves rule.

Two of sixteen cells beat SPY in both sample halves: hold 30 / band 40 at
+4.95%/yr and hold 50 / band 10 at +4.56%/yr. That is the first result in this
project to clear a pre-registered decision rule, and it is also exactly the
moment such a project goes wrong -- **2 of 16 is close to what chance
produces**, and both first halves are thin enough (+1.50%, +1.09%) that moving
the split date by a few months could flip them.

So this script tries to kill them, in the four ways the protocol demands:

1. **Year by year.** A halves test has two observations. Fourteen years of
   excess returns cannot hide a result that lives in 2020 and 2023. Anything
   whose edge comes from two years is a regime bet.
2. **Every split point, not the convenient one.** The median date was an
   arbitrary choice. If the edge only survives that particular cut, it is an
   artefact of where the cut fell.
3. **Cost sensitivity.** 45 bps was an assumption. At what cost does the edge
   die, and is that number comfortably above what IBKR actually charges?
4. **Neighbour stability.** A cell surrounded by failures is a spike, not a
   plateau. A real parameter has neighbours that also work.

Run:  .venv/bin/python -m scripts.stress_momentum
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from scripts.study_cross_section import load_panel
from tradelab.research.cross_section import run_backtest, signal_momentum_12_1

SURVIVORS = [(30, 40), (50, 10)]


def bench_cagr(series: pd.Series, first, last) -> float:
    years = (last - first).days / 365.25
    if years <= 0:
        return 0.0
    return (float(series.asof(last)) / float(series.asof(first))) ** (1 / years) - 1


def annual_excess(equity: pd.Series, spy: pd.Series) -> pd.Series:
    """Calendar-year excess return over SPY."""
    rows = {}
    for year, group in equity.groupby(equity.index.year):
        if len(group) < 6:
            continue
        lo, hi = group.index[0], group.index[-1]
        strat = float(group.iloc[-1] / group.iloc[0]) - 1.0
        index = float(spy.asof(hi) / spy.asof(lo)) - 1.0
        rows[year] = strat - index
    return pd.Series(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--benchmarks", type=Path, default=Path("data/bars/benchmarks"))
    parser.add_argument(
        "--min-dollar-volume",
        type=float,
        default=2_000_000,
        help="Must match the floor the surviving cells were found at, or this "
        "stress-tests a different strategy than the one that passed.",
    )
    args = parser.parse_args()

    print("=" * 84)
    print("STRESS TEST  --  trying to kill the two surviving momentum configurations")
    print("=" * 84)
    print(
        f"\nLiquidity floor ${args.min_dollar_volume:,.0f}/day -- MUST match the grid "
        "that produced the survivors."
    )

    closes, volumes = load_panel(args.bars)
    spy = pd.read_parquet(args.benchmarks / "SPY.parquet").sort_values("timestamp")
    stamps = pd.DatetimeIndex(spy["timestamp"])
    if stamps.tz is not None:
        stamps = stamps.tz_convert("UTC").tz_localize(None)
    spy = pd.Series(spy["close"].to_numpy(dtype=float), index=stamps)

    results = {}
    for hold, band in SURVIVORS:
        results[(hold, band)] = run_backtest(
            closes,
            volumes,
            spy,
            signal_momentum_12_1,
            name=f"h{hold}b{band}",
            n_hold=hold,
            hold_band=band,
            min_dollar_volume=args.min_dollar_volume,
            cost_bps=45.0,
        )

    # ------------------------------------------------------------ 1. year by year
    print("\n1. YEAR BY YEAR  -- excess return over SPY, per calendar year")
    print("   A halves test has two observations. This has fourteen.\n")
    table = pd.DataFrame(
        {f"h{h}b{b}": annual_excess(r.equity, spy) for (h, b), r in results.items()}
    )
    print(f"   {'year':>6}" + "".join(f"{c:>12}" for c in table.columns))
    for year, row in table.iterrows():
        marks = "".join(f"{v:>+12.2%}" for v in row)
        print(f"   {year:>6}{marks}")
    print(f"   {'':>6}" + "".join("-" * 12 for _ in table.columns))
    for label, fn in (
        ("positive", lambda s: f"{(s > 0).sum()}/{len(s)}"),
        ("median", lambda s: f"{s.median():+.2%}"),
        ("worst", lambda s: f"{s.min():+.2%}"),
    ):
        print(f"   {label:>6}" + "".join(f"{fn(table[c]):>12}" for c in table.columns))

    # ------------------------------------------------- 2. every split point
    print("\n2. EVERY SPLIT POINT  -- not just the median date")
    print("   Fraction of split dates where BOTH halves beat SPY.\n")
    print(f"   {'config':<10}{'splits tested':>15}{'both halves win':>18}{'share':>9}")
    for (hold, band), result in results.items():
        equity = result.equity
        wins = tested = 0
        for i in range(24, len(equity) - 24):
            mid = equity.index[i]
            ok = True
            for lo, hi in ((equity.index[0], mid), (mid, equity.index[-1])):
                seg = equity.loc[lo:hi]
                yrs = (hi - lo).days / 365.25
                if yrs <= 0:
                    ok = False
                    break
                strat = (seg.iloc[-1] / seg.iloc[0]) ** (1 / yrs) - 1
                if strat - bench_cagr(spy, lo, hi) <= 0:
                    ok = False
                    break
            tested += 1
            wins += ok
        print(f"   h{hold}b{band:<7}{tested:>15}{wins:>18}{wins / max(tested, 1):>9.0%}")

    # ------------------------------------------------------ 3. cost sensitivity
    print("\n3. COST SENSITIVITY  -- at what cost does the edge die?")
    print("   IBKR tiered on a EUR 10k account is ~25-45 bps round trip.\n")
    print(
        f"   {'config':<10}"
        + "".join(f"{c:>10}" for c in ("0bps", "45bps", "75bps", "100bps", "150bps"))
    )
    for hold, band in SURVIVORS:
        cells = []
        for cost in (0.0, 45.0, 75.0, 100.0, 150.0):
            r = run_backtest(
                closes,
                volumes,
                spy,
                signal_momentum_12_1,
                name="c",
                n_hold=hold,
                hold_band=band,
                min_dollar_volume=args.min_dollar_volume,
                cost_bps=cost,
            )
            first, last = r.equity.index[0], r.equity.index[-1]
            cells.append(r.stats["cagr"] - bench_cagr(spy, first, last))
        print(f"   h{hold}b{band:<7}" + "".join(f"{v:>+10.2%}" for v in cells))

    # ------------------------------------------------- 4. liquidity sensitivity
    print("\n4. LIQUIDITY FLOOR SENSITIVITY  -- is it specific to 'top 500'?")
    print(
        f"   {'config':<10}"
        + "".join(f"{c:>12}" for c in ("$20M", "$50M", "$92M", "$150M", "$250M"))
    )
    for hold, band in SURVIVORS:
        cells = []
        for floor in (20e6, 50e6, 92e6, 150e6, 250e6):
            try:
                r = run_backtest(
                    closes,
                    volumes,
                    spy,
                    signal_momentum_12_1,
                    name="l",
                    n_hold=hold,
                    hold_band=band,
                    min_dollar_volume=floor,
                    cost_bps=45.0,
                )
                first, last = r.equity.index[0], r.equity.index[-1]
                cells.append(r.stats["cagr"] - bench_cagr(spy, first, last))
            except ValueError:
                cells.append(float("nan"))
        print(f"   h{hold}b{band:<7}" + "".join(f"{v:>+12.2%}" for v in cells))

    print("\n5. RISK  -- what you carry to earn it")
    print(f"   {'config':<10}{'CAGR':>9}{'vol':>8}{'maxDD':>9}{'turnover':>10}")
    for (hold, band), r in results.items():
        s = r.stats
        print(
            f"   h{hold}b{band:<7}{s['cagr']:>9.2%}{s['vol']:>8.1%}{s['maxdd']:>9.1%}"
            f"{r.turnover:>10.0%}"
        )
    b = next(iter(results.values())).benchmark_stats
    print(f"   {'SPY':<10}{b['cagr']:>9.2%}{b['vol']:>8.1%}{b['maxdd']:>9.1%}{0:>10.0%}")

    print("\n" + "=" * 84)
    print(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
