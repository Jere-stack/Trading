#!/usr/bin/env python
"""Does N7 momentum become tradable at EUR 25,000? The decisive statistics.

Round 3 rejected N7 and recorded a specific prediction about this question:

    "The edge survives to 150bps -- so cost is NOT what kills it. That matters:
     this rejection is about the signal's reliability, not account size, and a
     larger account would not rescue it."

scripts/study_capital_scaling.py confirms the cost half: a round trip falls
from 40.2 bps at EUR 10k to 33.1 bps at EUR 25k, and momentum's headline
excess over SPY rises from +2.43% to +2.81%. That is the number a backtest
would advertise.

This script prints the four numbers that decide whether it is tradable, all
measured at the EUR 25k cost level:

  1. Realised Sharpe against the deflation bar at the project's honest trial
     count, read from the ledger rather than typed in.
  2. Volatility and maximum drawdown against SPY.
  3. Year-by-year excess -- fourteen observations, not two.
  4. The parameter surface: does the edge exist across the grid, or at one
     cell? This is the test that killed N11.

Run:  .venv/bin/python -m scripts.verify_momentum_at_25k
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.study_cross_section import load_panel
from tradelab.research.cross_section import run_backtest, signal_momentum_12_1
from tradelab.research.ledger import ResearchLedger
from tradelab.research.validation import expected_max_sharpe

COST_BPS_25K = 33.1
ANNUAL_FIXED_EUR = 870.0
CAPITAL_EUR = 25_000.0


def annual_excess(equity: pd.Series, spy: pd.Series) -> pd.Series:
    """Calendar-year return of the strategy minus the index, in points."""
    strat = equity.resample("YE").last().pct_change().dropna()
    bench = spy.reindex(equity.index).resample("YE").last().pct_change().dropna()
    joined = pd.concat([strat, bench], axis=1, keys=["s", "b"]).dropna()
    return (joined["s"] - joined["b"]).rename("excess")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--benchmarks", type=Path, default=Path("data/bars/benchmarks"))
    args = parser.parse_args()

    print("=" * 82)
    print(f"N7 MOMENTUM AT EUR 25,000  --  cost {COST_BPS_25K:.1f} bps round trip")
    print("=" * 82)

    closes, volumes = load_panel(args.bars)
    frame = pd.read_parquet(args.benchmarks / "SPY.parquet").sort_values("timestamp")
    stamps = pd.DatetimeIndex(frame["timestamp"])
    if stamps.tz is not None:
        stamps = stamps.tz_convert("UTC").tz_localize(None)
    spy = pd.Series(frame["close"].to_numpy(dtype=float), index=stamps)

    base = run_backtest(
        closes, volumes, spy, signal_momentum_12_1,
        name="N7", n_hold=20, hold_band=10, cost_bps=COST_BPS_25K,
    )
    s, b = base.stats, base.benchmark_stats

    # ------------------------------------------------- 1. deflation bar
    print("\n" + "-" * 82)
    print("1. REALISED SHARPE vs THE DEFLATION BAR")
    print("-" * 82)
    ledger = ResearchLedger(Path("research/ledger.jsonl"))
    n_trials = ledger.trial_count()
    monthly = base.equity.pct_change().dropna().to_numpy(dtype=float)
    # Sharpe dispersion across the configurations actually tried is unknown, so
    # use the standard conservative proxy: the variance of a single worthless
    # strategy's Sharpe estimate at this sample length.
    var_proxy = 1.0 / len(monthly)
    bar_monthly = expected_max_sharpe(n_trials, var_proxy)
    bar_annual = bar_monthly * np.sqrt(12)
    print(f"  configurations evaluated (from ledger)   {n_trials:>10,}")
    print(f"  monthly observations                     {len(monthly):>10,}")
    print(f"  realised annualised Sharpe               {s['sharpe']:>10.2f}")
    print(f"  bar: expected max Sharpe if worthless    {bar_annual:>10.2f}")
    verdict = "CLEARS" if s["sharpe"] > bar_annual else "BELOW THE BAR"
    print(f"  verdict                                  {verdict:>10}")

    # ------------------------------------------------- 2. risk
    print("\n" + "-" * 82)
    print("2. RISK TAKEN FOR THAT RETURN")
    print("-" * 82)
    print(f"  {'':<14}{'CAGR':>9}{'vol':>9}{'Sharpe':>9}{'maxDD':>9}")
    print(f"  {'momentum':<14}{s['cagr']:>9.2%}{s['vol']:>9.1%}{s['sharpe']:>9.2f}{s['maxdd']:>9.1%}")
    print(f"  {'SPY':<14}{b['cagr']:>9.2%}{b['vol']:>9.1%}{b['sharpe']:>9.2f}{b['maxdd']:>9.1%}")
    print(f"\n  excess CAGR before fixed costs           {s['cagr'] - b['cagr']:>+10.2%}")
    print(f"  fixed research stack on EUR 25k          {-ANNUAL_FIXED_EUR / CAPITAL_EUR:>+10.2%}")
    print(f"  excess AFTER the cost of running it      "
          f"{s['cagr'] - b['cagr'] - ANNUAL_FIXED_EUR / CAPITAL_EUR:>+10.2%}")

    # ------------------------------------------------- 3. year by year
    print("\n" + "-" * 82)
    print("3. YEAR BY YEAR -- fourteen observations, not two")
    print("-" * 82)
    excess = annual_excess(base.equity, spy)
    for year, value in excess.items():
        bar = "+" if value > 0 else "-"
        print(f"  {year.year:>6}{value:>+10.2%}   {bar * min(int(abs(value) * 100), 60)}")
    positive = int((excess > 0).sum())
    print(f"\n  positive years {positive}/{len(excess)}   "
          f"median {excess.median():+.2%}   worst {excess.min():+.2%}")
    on_capital = excess.min() * CAPITAL_EUR
    print(f"  worst year on EUR 25,000: {on_capital:,.0f} EUR relative to just holding SPY")

    # ------------------------------------------------- 4. parameter surface
    print("\n" + "-" * 82)
    print("4. THE PARAMETER SURFACE -- one cell, or a region?")
    print("-" * 82)
    holds = (10, 15, 20, 30, 40)
    bands = (0, 5, 10, 20, 40)
    print(f"  {'hold':>6}" + "".join(f"{f'band {x}':>11}" for x in bands))
    wins = total = 0
    for hold in holds:
        cells = []
        for band in bands:
            try:
                r = run_backtest(
                    closes, volumes, spy, signal_momentum_12_1,
                    name="g", n_hold=hold, hold_band=band, cost_bps=COST_BPS_25K,
                )
                cells.append(r.excess_cagr)
                total += 1
                wins += r.excess_cagr > 0
            except Exception:
                cells.append(float("nan"))
        print(f"  {hold:>6}" + "".join(f"{v:>+11.2%}" for v in cells))
    print(f"\n  cells beating SPY: {wins}/{total} ({wins / max(total, 1):.0%})")

    print("\n" + "=" * 82)
    print("Read 1 and 3 together before reading 2.")
    print("=" * 82)


if __name__ == "__main__":
    main()
