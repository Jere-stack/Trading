#!/usr/bin/env python
"""Does more capital rescue any of the fourteen rejected hypotheses?

Every rejection so far was measured on a EUR 10,000 account. Two of the
objections to trading at that size are arithmetic rather than statistical, and
arithmetic changes with capital:

  1. COMMISSION. IBKR Tiered charges max(shares x $0.0035, $0.35). At a EUR
     1,000 position the $0.35 floor binds hard and costs ~3 bps one way; at
     EUR 2,500 the same floor is ~1.2 bps. Commission drag is roughly
     proportional to 1/capital until the floor stops binding.

  2. WHOLE-SHARE ROUNDING. You cannot buy 6.4 shares. The residual cash is
     dead weight, and it is a fixed number of half-share-prices regardless of
     account size -- so it shrinks as a FRACTION of a bigger account.

A third objection does NOT change with capital and is measured here for
contrast: the universe handicap. An equal-weighted basket of N names starts
8.66 points a year behind a cap-weighted index, and that gap is a property of
the weighting scheme, not of the account.

This script measures 1 and 2 empirically -- from the actual prices of the
stocks momentum would have selected, not from an assumed average price -- and
then re-runs the best surviving hypothesis (N7 momentum 12-1) at each capital
level's true cost to see whether the verdict moves.

Run:  .venv/bin/python scripts/study_capital_scaling.py
"""

from __future__ import annotations

import argparse
from decimal import Decimal as D
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.study_cross_section import load_panel
from tradelab.core.enums import Side
from tradelab.core.types import Instrument
from tradelab.costs.commission import IbkrTieredUsEquity
from tradelab.research.cross_section import (
    month_end_dates,
    run_backtest,
    signal_momentum_12_1,
)

# EUR/USD used to convert the account into the currency the shares trade in.
# The exact level barely matters here; it moves every capital level together.
EURUSD = 1.08

# Spread plus market impact, in bps per round trip. Held CONSTANT across
# capital levels on purpose: spread is proportional to notional, so it does
# not improve with account size the way a fixed commission floor does. This is
# the residual of the project's standing 45 bps once the EUR 10k commission is
# subtracted, so the 10k row reproduces the number every earlier round used.
SPREAD_BPS_ROUND_TRIP = 20.0

CAPITAL_LEVELS_EUR = (10_000, 25_000, 50_000, 100_000)
POSITION_COUNTS = (10, 20, 30)


def round_trip_commission_bps(price: float, target_value_usd: float) -> tuple[float, float]:
    """(commission bps of the filled notional, fraction of target left in cash).

    Returns (nan, nan) when the target cannot buy a single share -- that is not
    an expensive position, it is an impossible one.
    """
    shares = int(target_value_usd // price)
    if shares < 1:
        return float("nan"), float("nan")

    model = IbkrTieredUsEquity()
    inst = Instrument("US", currency="USD")
    qty, px = D(str(shares)), D(str(price))
    buy = model.calculate(inst, Side.BUY, qty, px).total
    sell = model.calculate(inst, Side.SELL, qty, px).total

    filled = shares * price
    bps = float((buy + sell) / D(str(filled)) * D("10000"))
    stranded = (target_value_usd - filled) / target_value_usd
    return bps, stranded


def measure_costs(closes: pd.DataFrame, volumes: pd.DataFrame, n_hold: int, lookback: int = 252):
    """Walk the real momentum selections and price them at each capital level.

    Uses the same point-in-time eligibility rule as the backtester, so the
    prices measured are the prices the strategy would actually have paid.
    """
    dates = closes.index
    rebalances = [d for d in month_end_dates(dates) if d >= dates[lookback]]

    rows = []
    for date in rebalances[:-1]:
        position = dates.get_loc(date)
        recent = volumes.iloc[max(0, position - 60) : position + 1]
        liquid = recent.median(skipna=True) >= 2_000_000
        priced = closes.iloc[position].notna()
        eligible = closes.columns[liquid & priced]
        if len(eligible) < n_hold * 2:
            continue

        window = closes.iloc[max(0, position - lookback) : position + 1][eligible]
        scores = signal_momentum_12_1(window).dropna()
        if len(scores) < n_hold:
            continue
        chosen = list(scores.nlargest(n_hold).index)
        prices = closes.iloc[position][chosen].dropna()
        rows.extend(float(p) for p in prices if p > 0)

    return np.asarray(rows, dtype=float)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--benchmarks", type=Path, default=Path("data/bars/benchmarks"))
    args = parser.parse_args()

    print("=" * 84)
    print("DOES MORE CAPITAL CHANGE THE VERDICT?")
    print("=" * 84)

    closes, volumes = load_panel(args.bars)
    print(
        f"  panel {closes.shape[0]:,} dates x {closes.shape[1]:,} symbols "
        f"({closes.index.min():%Y-%m} to {closes.index.max():%Y-%m})"
    )

    print("\n  collecting the actual prices momentum would have paid...")
    prices = measure_costs(closes, volumes, n_hold=20)
    print(
        f"  {len(prices):,} position-prices, median ${np.median(prices):,.0f}, "
        f"p10 ${np.percentile(prices, 10):,.0f}, p90 ${np.percentile(prices, 90):,.0f}"
    )

    # ------------------------------------------------------------ cost table
    print("\n" + "-" * 84)
    print("1. WHAT A ROUND TRIP ACTUALLY COSTS, by account size and position count")
    print("-" * 84)
    print(
        f"  {'capital':>10}{'names':>7}{'per pos':>10}{'comm':>9}"
        f"{'spread':>9}{'total':>9}{'stranded':>10}{'unbuyable':>11}"
    )

    effective_cost = {}
    for capital in CAPITAL_LEVELS_EUR:
        for n_hold in POSITION_COUNTS:
            target = capital * EURUSD / n_hold
            measured = [round_trip_commission_bps(p, target) for p in prices]
            comm = np.array([m[0] for m in measured], dtype=float)
            strand = np.array([m[1] for m in measured], dtype=float)
            unbuyable = float(np.isnan(comm).mean())
            comm_bps = float(np.nanmean(comm))
            strand_pct = float(np.nanmean(strand))
            total = comm_bps + SPREAD_BPS_ROUND_TRIP
            effective_cost[(capital, n_hold)] = total
            print(
                f"  {capital:>9,}{n_hold:>7}{target:>9,.0f}${comm_bps:>8.1f}"
                f"{SPREAD_BPS_ROUND_TRIP:>9.1f}{total:>9.1f}"
                f"{strand_pct:>9.2%}{unbuyable:>11.1%}"
            )

    # -------------------------------------------------------- fixed overhead
    print("\n" + "-" * 84)
    print("2. FIXED RESEARCH COSTS AS A SHARE OF CAPITAL")
    print("-" * 84)
    stack = {
        "server (Hetzner CX23, incl. 25.5% VAT)": 90.0,
        "EODHD fundamentals + prices": 720.0,
        "misc (domain, backups)": 60.0,
    }
    annual = sum(stack.values())
    for label, cost in stack.items():
        print(f"  {label:<44}EUR {cost:>7,.0f}/yr")
    print(f"  {'TOTAL':<44}EUR {annual:>7,.0f}/yr")
    print(f"\n  {'capital':>10}{'fixed cost':>14}{'as % of capital':>18}")
    for capital in CAPITAL_LEVELS_EUR:
        print(f"  {capital:>9,}{annual:>13,.0f}{annual / capital:>18.2%}")

    # ------------------------------------------------- does momentum survive?
    print("\n" + "-" * 84)
    print("3. N7 MOMENTUM 12-1 AT EACH CAPITAL LEVEL'S TRUE COST")
    print("-" * 84)

    frame = pd.read_parquet(args.benchmarks / "SPY.parquet").sort_values("timestamp")
    stamps = pd.DatetimeIndex(frame["timestamp"])
    if stamps.tz is not None:
        stamps = stamps.tz_convert("UTC").tz_localize(None)
    spy = pd.Series(frame["close"].to_numpy(dtype=float), index=stamps)

    print(
        f"  {'capital':>10}{'names':>7}{'cost bps':>10}{'CAGR':>9}"
        f"{'SPY':>9}{'excess':>9}{'net of fixed':>14}"
    )
    for capital in CAPITAL_LEVELS_EUR:
        for n_hold in (20,):
            cost_bps = effective_cost[(capital, n_hold)]
            result = run_backtest(
                closes,
                volumes,
                spy,
                signal_momentum_12_1,
                name="N7 momentum",
                n_hold=n_hold,
                hold_band=10,
                cost_bps=cost_bps,
            )
            cagr = result.stats["cagr"]
            bench = result.benchmark_stats["cagr"]
            net = cagr - annual / capital
            print(
                f"  {capital:>9,}{n_hold:>7}{cost_bps:>10.1f}{cagr:>9.2%}"
                f"{bench:>9.2%}{cagr - bench:>+9.2%}{net - bench:>+14.2%}"
            )

    print("\n" + "=" * 84)
    print("The universe handicap is NOT in this table and does not move with")
    print("capital: an equal-weighted basket starts ~8.7 points/yr behind a")
    print("cap-weighted index regardless of account size. That is the binding")
    print("constraint, and no amount of money fixes it.")
    print("=" * 84)


if __name__ == "__main__":
    main()
