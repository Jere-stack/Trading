#!/usr/bin/env python
"""Attack the two candidates that survived the lead-lag scan out of sample.

`listing_rate` (-0.240 in sample, -0.205 out, at +11 months) and
`avg_correlation` (-0.382 / -0.390, at +5 months) both held. Neither is
believed yet, for two reasons that have to be settled before anything is built.

**1. Persistence masquerading as lead.** Both series are heavily
autocorrelated. If a signal is high today it is high next month, so a purely
COINCIDENT relationship bleeds into every nearby lag and shows up as a lead
that is not there. The diagnostic is the shape of the profile: a real lead is
ASYMMETRIC around zero -- stronger at positive lags than at the mirror-image
negative ones. A symmetric hump is persistence.

**2. Effective sample size.** Fifteen years of monthly observations is 180
rows, but an AR(1) with monthly autocorrelation 0.9 carries roughly
n*(1-r)/(1+r) independent observations -- about 9. A correlation of -0.2 on
nine observations is indistinguishable from zero, and reporting it with three
decimal places does not change that.

Run:  .venv/bin/python -m scripts.stress_lead_lag
"""

from __future__ import annotations

import argparse
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from tradelab.research.board_hesitation import lead_lag_profile
from tradelab.research.market_state import average_correlation, listing_churn
from tradelab.research.screening import screen_price_series
from tradelab.research.universe import us_common_stocks


def load_closes(bar_dir: Path, reference: Path) -> pd.DataFrame:
    """Close prices only.

    Both survivors need nothing else, and loading five panels across 10,519
    symbols is enough memory to get the process killed outright.
    """
    universe = us_common_stocks(pd.read_parquet(reference))
    closes = {}
    for path in sorted(bar_dir.glob("*.parquet")):
        if path.stem.upper() not in universe:
            continue
        try:
            frame = pd.read_parquet(path, columns=["timestamp", "close"])
        except Exception:
            continue
        values = frame["close"].to_numpy(dtype=float)
        if not screen_price_series(values).ok:
            continue
        stamps = pd.DatetimeIndex(frame["timestamp"])
        if stamps.tz is not None:
            stamps = stamps.tz_convert("UTC").tz_localize(None)
        closes[path.stem] = pd.Series(values, index=stamps)
    print(f"  {len(closes):,} symbols loaded")
    return pd.DataFrame(closes).sort_index().astype("float32")


def effective_n(series: pd.Series) -> tuple[int, float, float]:
    """Independent observations after accounting for autocorrelation.

    n_eff = n * (1 - r) / (1 + r) for an AR(1) with lag-1 autocorrelation r.
    """
    values = series.dropna()
    n = len(values)
    if n < 3:
        return n, 0.0, float(n)
    r = float(values.autocorr(lag=1))
    factor = (1 - r) / (1 + r) if r > -1 else 1.0
    return n, r, max(1.0, n * factor)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/bars/us-universe"))
    parser.add_argument("--reference", type=Path, default=Path("data/reference/us_symbols.parquet"))
    parser.add_argument("--benchmark", type=Path, default=Path("data/bars/benchmarks/SPY.parquet"))
    args = parser.parse_args()

    print("=" * 80)
    print("STRESS  --  are the two survivors leading, or merely persistent?")
    print("=" * 80)

    close = load_closes(args.bars, args.reference)
    spy = pd.read_parquet(args.benchmark).sort_values("timestamp")
    stamps = pd.DatetimeIndex(spy["timestamp"])
    if stamps.tz is not None:
        stamps = stamps.tz_convert("UTC").tz_localize(None)
    spy = pd.Series(spy["close"].to_numpy(dtype=float), index=stamps)

    listing_rate, _ = listing_churn(close)
    survivors = {
        "listing_rate": (listing_rate, 11),
        "avg_correlation [control]": (average_correlation(close), 5),
    }

    for name, (series, claimed_lag) in survivors.items():
        print("\n" + "-" * 80)
        print(f"{name}  --  claimed lead of {claimed_lag} months")
        print("-" * 80)

        profile = lead_lag_profile(series.dropna(), spy, months=12)
        if profile.empty:
            print("  no usable profile")
            continue
        table = profile.set_index("lag_months")["correlation"]

        print(f"\n  {'lag':>5}{'corr':>9}   {'mirror':>7}{'corr':>9}   {'asymmetry':>11}")
        asymmetries = []
        for lag in range(1, 13):
            if lag not in table.index or -lag not in table.index:
                continue
            forward, backward = float(table[lag]), float(table[-lag])
            gap = abs(forward) - abs(backward)
            asymmetries.append(gap)
            flag = "  <- claimed" if lag == claimed_lag else ""
            print(f"  {lag:>+5}{forward:>+9.3f}   {-lag:>+7}{backward:>+9.3f}{gap:>+11.3f}{flag}")
        at_zero = float(table.get(0, float("nan")))
        print(f"\n  lag 0 (contemporaneous): {at_zero:+.3f}")
        claimed = float(table.get(claimed_lag, float("nan")))
        mirror = float(table.get(-claimed_lag, float("nan")))
        print(
            f"  claimed lead {claimed_lag:+d}: {claimed:+.3f}   "
            f"mirror {-claimed_lag:+d}: {mirror:+.3f}"
        )
        if abs(claimed) <= abs(at_zero):
            print("\n  *** The contemporaneous correlation is at least as strong as the")
            print("      claimed lead. This is a coincident signal; the lead is bleed.")
        elif abs(claimed) <= abs(mirror):
            print("\n  *** The mirror lag is as strong as the claimed lead. The profile is")
            print("      symmetric, which is persistence rather than anticipation.")
        else:
            print("\n  Profile is asymmetric in the right direction. Not yet refuted.")

        # ------------------------------------------------------- effective n
        monthly = series.resample("ME").last().dropna()
        n, r, n_eff = effective_n(monthly)
        se = 1.0 / math.sqrt(max(n_eff - 3, 1))
        print(f"\n  monthly observations {n}, lag-1 autocorrelation {r:.3f}")
        print(f"  EFFECTIVE independent observations: {n_eff:.1f}")
        print(f"  standard error on a correlation at that n: +/-{se:.3f}")
        t_stat = abs(claimed) / se if se > 0 else 0.0
        print(f"  claimed correlation {claimed:+.3f} is {t_stat:.1f} standard errors from zero")
        if t_stat < 2.0:
            print("\n  *** NOT DISTINGUISHABLE FROM ZERO at the effective sample size.")

    print("\n" + "=" * 80)
    print(f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")


if __name__ == "__main__":
    main()
