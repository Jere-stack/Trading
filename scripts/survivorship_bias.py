#!/usr/bin/env python
"""Measure survivorship bias empirically, on real data.

Run:  .venv/bin/python scripts/survivorship_bias.py --dataset helsinki-full

Throughout this project a figure of "1-4% per year of spurious return" has been
cited for survivorship bias. That number came from the literature, not from
measurement. This script measures it directly on a complete exchange universe
where every delisted name is present -- the specific thing the EODHD
subscription buys.

Method, deliberately strategy-free:

    Compare the equal-weighted return of the FULL universe (every company that
    was listed, including those that later died) against the SURVIVORS-ONLY
    universe (only companies still listed today -- what a free source or IBKR
    would give you).

No trading rule is involved, so any difference is pure selection effect.

**The universe must first be filtered to what is actually tradable.** The naive
version of this measurement -- equal-weighting every listed name -- produced a
headline driven entirely by two sub-cent stocks: Savosolar at EUR 0.0093 showed
a single-day move of +10,156%, and Efore +4,850%. At those price levels one tick
is a 1% move and unadjusted corporate actions are common. Neither is a return
anyone could have earned, and neither passes the system's own `min_price` floor.
Measuring bias on stocks the risk engine would reject measures nothing useful.

The filters below therefore mirror the live constraints: a price floor, a
liquidity floor, and a minimum history. Both filtered and naive results are
printed, because the gap between them is itself the lesson.

One honest caveat on direction. Delisting is not synonymous with failure.
Companies leave an exchange through bankruptcy, which biases survivor returns
UP, and through acquisition at a premium, which biases them DOWN. Which
dominates is an empirical question per market, not a known constant -- so the
sign of the result is a finding, not an error.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab.data.store import BarStore


def filter_tradable(
    frame: pd.DataFrame, *, min_price: float, min_adv: float, min_bars: int
) -> tuple[set[str], dict[str, str]]:
    """Restrict to names the risk engine would actually permit.

    Mirrors `PositionLimits.min_price` and the capacity constraint. A symbol is
    judged on its MEDIAN price and traded value over its own life, so a name
    that was investable for years and later collapsed is still included --
    excluding it would itself be survivorship bias, which is the thing being
    measured.
    """
    keep: set[str] = set()
    dropped: dict[str, str] = {}
    for symbol, group in frame.groupby("symbol", observed=True):
        name = str(symbol)
        if len(group) < min_bars:
            dropped[name] = f"only {len(group)} bars"
            continue
        median_price = float(group["close"].median())
        if median_price < min_price:
            dropped[name] = f"median price {median_price:.4f} below floor"
            continue
        median_value = float((group["close"] * group["volume"]).median())
        if median_value < min_adv:
            dropped[name] = f"median daily value {median_value:,.0f} too thin"
            continue
        keep.add(name)
    return keep, dropped


def build_panel(frame: pd.DataFrame) -> pd.DataFrame:
    """Wide panel of daily returns: rows are dates, columns are symbols."""
    frame = frame.sort_values(["symbol", "timestamp"])
    wide = frame.pivot_table(index="timestamp", columns="symbol", values="close")
    return wide.pct_change(fill_method=None)


def equal_weighted(returns: pd.DataFrame) -> pd.Series:
    """Equal-weighted daily return across whatever is alive on each date.

    Averaging only over non-NaN columns is what makes this fair: a company that
    had not yet listed, or has already died, is simply not in that day's average.
    """
    if returns.empty:
        return pd.Series(dtype=float)
    return returns.mean(axis=1, skipna=True).fillna(0.0)


def annualised(series: pd.Series, periods: int = 252) -> float:
    if series.empty:
        return 0.0
    total = float((1.0 + series).prod())
    years = len(series) / periods
    if years <= 0 or total <= 0:
        return 0.0
    return total ** (1.0 / years) - 1.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="helsinki-full")
    parser.add_argument("--root", default="data")
    parser.add_argument("--survivor-window-days", type=int, default=30)
    parser.add_argument(
        "--min-price",
        type=float,
        default=1.0,
        help="Price floor. Sub-cent stocks produce percentage moves that are "
        "tick artifacts, not returns.",
    )
    parser.add_argument("--min-adv", type=float, default=50_000.0)
    parser.add_argument("--min-bars", type=int, default=250)
    args = parser.parse_args()

    store = BarStore(Path(args.root))
    raw = store.read(args.dataset)
    metadata = store.metadata(args.dataset)

    print(__doc__)
    print("=" * 76)
    print(f"DATASET: {args.dataset}")
    print("=" * 76)
    print(f"  provider          {metadata.provider}")
    print(f"  adjustment        {metadata.adjustment.value}")
    print(f"  includes delisted {metadata.includes_delisted}")
    print(f"  bars              {len(raw):,}")
    print(f"  symbols           {raw['symbol'].nunique()}")
    print(
        f"  period            {raw['timestamp'].min():%Y-%m-%d} -> "
        f"{raw['timestamp'].max():%Y-%m-%d}"
    )

    # ---- filter to what the system would actually trade
    tradable, excluded = filter_tradable(
        raw, min_price=args.min_price, min_adv=args.min_adv, min_bars=args.min_bars
    )
    print()
    print("=" * 76)
    print("TRADABILITY FILTER")
    print("=" * 76)
    print(
        f"  price >= {args.min_price:.2f}, median daily value >= "
        f"{args.min_adv:,.0f}, >= {args.min_bars} bars"
    )
    print(f"  kept {len(tradable)} of {raw['symbol'].nunique()} symbols, excluded {len(excluded)}")
    for symbol, reason in list(excluded.items())[:5]:
        print(f"    {symbol}: {reason}")
    if len(excluded) > 5:
        print(f"    ... and {len(excluded) - 5} more")

    frame = raw[raw["symbol"].isin(tradable)]
    if frame.empty:
        print("\n  Nothing survived the filter; loosen it or use a wider universe.")
        return

    # ---- split by whether each name is still listed
    last_seen = frame.groupby("symbol", observed=True)["timestamp"].max()
    cutoff = last_seen.max() - pd.Timedelta(days=args.survivor_window_days)
    survivors = set(last_seen[last_seen >= cutoff].index)
    dead = set(last_seen.index) - survivors

    print()
    print("=" * 76)
    print("UNIVERSE COMPOSITION (tradable names only)")
    print("=" * 76)
    print(f"  survivors (still listed)     {len(survivors):>5}")
    print(f"  delisted / acquired          {len(dead):>5}")
    total = len(survivors) + len(dead)
    if total:
        print(f"  share of universe that died  {len(dead) / total:>5.1%}")
    if not dead:
        print("\n  No delisted names survive the filter; bias cannot be measured.")
        return

    returns = build_panel(frame)
    full = equal_weighted(returns)
    survivor_only = equal_weighted(returns[sorted(survivors)])
    gap = annualised(survivor_only) - annualised(full)

    print()
    print("=" * 76)
    print("THE MEASUREMENT")
    print("=" * 76)
    print(f"  {'universe':<34} {'CAGR':>9} {'total':>11} {'vol':>8}")
    print("  " + "-" * 64)
    for label, series in (
        ("FULL (what actually happened)", full),
        ("SURVIVORS ONLY (biased)", survivor_only),
    ):
        print(
            f"  {label:<34} {annualised(series):>8.2%} "
            f"{float((1.0 + series).prod() - 1.0):>10.1%} "
            f"{float(series.std() * np.sqrt(252)):>7.1%}"
        )
    print("  " + "-" * 64)
    print(f"  {'SURVIVORSHIP BIAS':<34} {gap:>8.2%} per year")

    direction = (
        "OVERSTATED, because the losers were excluded"
        if gap > 0
        else "UNDERSTATED, because premium acquisitions were excluded"
    )
    magnitude = "larger than" if abs(gap) > 0.02 else "comparable to"
    print(f"""
  A backtest on the survivors-only universe would have had its return
  {direction},
  by {abs(gap):.2%} per year. No strategy produced it; it is pure selection.

  Against the hypothesis register, where the best candidates net 1-3% a year
  after costs, a bias of {abs(gap):.2%} is {magnitude} the entire edge being
  hunted. That is why a survivors-only universe is CRITICAL, not a caveat.""")

    # ---- contrast with the naive, unfiltered version
    naive_returns = build_panel(raw)
    naive_last = raw.groupby("symbol", observed=True)["timestamp"].max()
    naive_survivors = sorted(naive_last[naive_last >= cutoff].index)
    naive_full = annualised(equal_weighted(naive_returns))
    naive_surv = annualised(equal_weighted(naive_returns[naive_survivors]))
    print(f"""
  For contrast, the SAME measurement WITHOUT the tradability filter:

      full {naive_full:+.2%}/yr, survivors {naive_surv:+.2%}/yr,
      apparent bias {naive_surv - naive_full:+.2%}/yr

  That version is dominated by sub-cent stocks whose percentage moves are tick
  artifacts rather than returns. It is the wrong answer, and the size of the
  discrepancy is why the filter is not optional.""")

    # ---- how the gap accumulates
    print()
    print("=" * 76)
    print("BY YEAR")
    print("=" * 76)
    yearly = pd.DataFrame({"full": full, "survivors": survivor_only})
    yearly["year"] = yearly.index.year
    print(f"  {'year':<6} {'full':>9} {'survivors':>11} {'bias':>9} {'alive':>7}")
    print("  " + "-" * 46)
    for year, group in yearly.groupby("year"):
        f = float((1.0 + group["full"]).prod() - 1.0)
        s = float((1.0 + group["survivors"]).prod() - 1.0)
        alive = int(returns.loc[returns.index.year == year].notna().any().sum())
        print(f"  {year:<6} {f:>8.1%} {s:>10.1%} {s - f:>8.1%} {alive:>7}")

    print("""
  The bias is not uniform. It concentrates in the years when companies actually
  left the exchange -- which are the crisis and takeover years a strategy most
  needs testing against, and precisely the years a survivors-only universe
  erases.""")


if __name__ == "__main__":
    main()
