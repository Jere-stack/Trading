#!/usr/bin/env python
"""N10: Board Hesitation Breadth -- does board lateness lead the market?

Pre-registered in research/ledger.jsonl with kill criteria fixed before any
result was computed. Tests run CHEAPEST KILL FIRST, in the registered order,
because there is no point costing a backtest for a signal that is coincident.

    1. LEAD-LAG      if the peak correlation sits at lag <= 0, the index reacts
                     to the market rather than anticipating it. Dead, and no
                     parameter repairs it.
    2. PLACEBO       declaration dates shuffled within each firm. Preserves
                     every firm's declaration count and the date distribution,
                     destroys the ordering. Must show nothing.
    3. COVERAGE BIAS declaration-date coverage is 80.4% and lower in older
                     records. If the missing fifth concentrates in firms that
                     later delisted, the signal is manufactured.
    4. OVERLAY       only if the first three pass.

Run:  .venv/bin/python -m scripts.study_board_hesitation
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab.research.board_hesitation import (
    board_hesitation_breadth,
    lead_lag_profile,
)


def load_spy(path: Path) -> pd.Series:
    frame = pd.read_parquet(path).sort_values("timestamp")
    stamps = pd.DatetimeIndex(frame["timestamp"])
    if stamps.tz is not None:
        stamps = stamps.tz_convert("UTC").tz_localize(None)
    return pd.Series(frame["close"].to_numpy(dtype=float), index=stamps)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dividends", type=Path, default=Path("data/dividends/us-universe/dividends.parquet")
    )
    parser.add_argument("--benchmark", type=Path, default=Path("data/bars/benchmarks/SPY.parquet"))
    args = parser.parse_args()

    print("=" * 80)
    print("N10  BOARD HESITATION BREADTH")
    print("=" * 80)

    dividends = pd.read_parquet(args.dividends)
    spy = load_spy(args.benchmark)
    print(f"\n{len(dividends):,} dividend records, {dividends['symbol'].nunique():,} payers")
    print(f"declaration-date coverage {dividends['declaration_date'].notna().mean():.1%}")

    breadth = board_hesitation_breadth(dividends)
    usable = breadth.raw.dropna()
    print(f"\nbreadth series {usable.index.min():%Y-%m} to {usable.index.max():%Y-%m}")
    print(f"  eligible firms: {breadth.eligible.min():,} to {breadth.eligible.max():,}")
    print(
        f"  breadth: mean {usable.mean():.3%}, sd {usable.std():.3%}, "
        f"range {usable.min():.3%} to {usable.max():.3%}"
    )

    # The eligible count must not collapse, or the index measures a shrinking club.
    recent = breadth.eligible.loc["2012":]
    print(f"  eligible over the test window: {recent.min():,} to {recent.max():,}")

    # ------------------------------------------------------ 1. LEAD-LAG (the gate)
    print("\n" + "-" * 80)
    print("1. LEAD-LAG  --  positive lag means the return comes AFTER the signal")
    print("-" * 80)
    adjusted = breadth.seasonally_adjusted().dropna()
    print(
        f"   seasonally adjusted series: {len(adjusted):,} days, "
        f"{adjusted.index.min():%Y-%m} to {adjusted.index.max():%Y-%m}"
    )

    profile = lead_lag_profile(adjusted, spy, months=12)
    if profile.empty:
        print("   too little overlap to compute a profile")
        return
    print(f"\n   {'lag':>5}{'n':>6}{'corr':>9}")
    for _, row in profile.iterrows():
        marker = ""
        if row["correlation"] == profile["correlation"].min():
            marker = "  <- most negative"
        if row["correlation"] == profile["correlation"].max():
            marker = "  <- most positive"
        print(f"   {int(row['lag_months']):>5}{int(row['n']):>6}{row['correlation']:>9.3f}{marker}")

    strongest = profile.iloc[profile["correlation"].abs().idxmax()]
    lag = int(strongest["lag_months"])
    print(f"\n   strongest |correlation| at lag {lag:+d} months: {strongest['correlation']:+.3f}")
    if lag <= 0:
        print("\n   *** KILL CRITERION MET ***")
        print("   The strongest relationship is at a non-positive lag: the index is")
        print("   coincident or lagging, not leading. It reacts to the market rather")
        print("   than anticipating it. No threshold or weighting repairs this.")
    else:
        print(f"\n   Signal LEADS by {lag} months. Continuing to the placebo.")

    # ------------------------------------------------------------- 2. PLACEBO
    print("\n" + "-" * 80)
    print("2. PLACEBO  --  declaration dates shuffled within each firm")
    print("-" * 80)
    placebo = board_hesitation_breadth(dividends, shuffle_seed=17)
    placebo_adjusted = placebo.seasonally_adjusted().dropna()
    placebo_profile = lead_lag_profile(placebo_adjusted, spy, months=12)
    if placebo_profile.empty:
        print("   placebo produced no usable series")
    else:
        real_peak = profile["correlation"].abs().max()
        fake_peak = placebo_profile["correlation"].abs().max()
        print(f"   real    peak |correlation|: {real_peak:.3f}")
        print(f"   placebo peak |correlation|: {fake_peak:.3f}")
        if fake_peak >= real_peak * 0.7:
            print("\n   *** KILL CRITERION MET ***")
            print("   The placebo reproduces most of the signal, so the measure is")
            print("   picking up the calendar and the universe, not board behaviour.")
        else:
            print("\n   Placebo is materially weaker. The ordering carries the signal.")

    # ------------------------------------------------------- 3. COVERAGE BIAS
    print("\n" + "-" * 80)
    print("3. COVERAGE BIAS  --  do firms with missing declaration dates differ?")
    print("-" * 80)
    per_firm = dividends.groupby("symbol").agg(
        records=("ex_date", "size"),
        coverage=("declaration_date", lambda s: s.notna().mean()),
        last_ex=("ex_date", "max"),
    )
    universe_end = pd.to_datetime(dividends["ex_date"]).max()
    per_firm["stopped_paying"] = per_firm["last_ex"] < (universe_end - pd.Timedelta(days=400))
    low = per_firm[per_firm["coverage"] < 0.5]
    high = per_firm[per_firm["coverage"] >= 0.5]
    print(
        f"   firms with <50% coverage: {len(low):,}  -- {low['stopped_paying'].mean():.1%} "
        "later stopped paying"
    )
    print(
        f"   firms with >=50% coverage: {len(high):,}  -- "
        f"{high['stopped_paying'].mean():.1%} later stopped paying"
    )
    gap = low["stopped_paying"].mean() - high["stopped_paying"].mean()
    print(f"   gap: {gap:+.1%}")
    if abs(gap) > 0.10:
        print("\n   *** WARNING ***")
        print("   Missing declaration dates are concentrated in firms that stopped")
        print("   paying. Excluding them biases the index by construction.")
    else:
        print("\n   Coverage is not strongly related to survival.")

    print("\n" + "=" * 80)
    print(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
